"""Reglas de inventario: costo promedio ponderado, lotes y salida FEFO."""

from decimal import ROUND_HALF_UP, Decimal

from django.core.exceptions import ValidationError
from django.db import transaction

from catalogo.models import Producto

from .models import Lote, Movimiento

CUATRO = Decimal("0.0001")


def _bloquear(producto):
    return Producto.objects.select_for_update().get(pk=producto.pk)


@transaction.atomic
def registrar_entrada(producto, cantidad, costo_unitario, tipo=Movimiento.Tipo.ENTRADA_COMPRA,
                      lote_numero=None, caducidad=None, referencia="", usuario=None):
    """Entrada en unidad base. Recalcula costo promedio ponderado."""
    cantidad, costo_unitario = Decimal(cantidad), Decimal(costo_unitario)
    if cantidad <= 0:
        raise ValidationError("La cantidad de entrada debe ser positiva.")
    if tipo not in Movimiento.ENTRADAS:
        raise ValidationError(f"{tipo} no es un tipo de entrada.")
    p = _bloquear(producto)

    lote = None
    if p.maneja_lote:
        if not lote_numero:
            raise ValidationError(f"{p.sku} requiere número de lote.")
        if p.maneja_caducidad and not caducidad:
            raise ValidationError(f"{p.sku} requiere fecha de caducidad.")
        lote, _ = Lote.objects.select_for_update().get_or_create(
            producto=p, numero=lote_numero, defaults={"caducidad": caducidad}
        )
        lote.existencia += cantidad
        lote.save(update_fields=["existencia"])

    nueva_existencia = p.existencia + cantidad
    if p.existencia <= 0:
        nuevo_costo = costo_unitario
    else:
        nuevo_costo = (p.existencia * p.costo_promedio + cantidad * costo_unitario) / nueva_existencia
    p.existencia = nueva_existencia
    p.costo_promedio = nuevo_costo.quantize(CUATRO, ROUND_HALF_UP)
    p.save(update_fields=["existencia", "costo_promedio"])

    return Movimiento.objects.create(
        producto=p, lote=lote, tipo=tipo, cantidad=cantidad, costo_unitario=costo_unitario,
        existencia_resultante=p.existencia, costo_promedio_resultante=p.costo_promedio,
        referencia=referencia, usuario=usuario,
    )


@transaction.atomic
def registrar_salida(producto, cantidad, tipo=Movimiento.Tipo.SALIDA_VENTA, lote=None, referencia="", usuario=None):
    """Salida en unidad base al costo promedio. Si maneja lote y no se indica, toma FEFO
    (primero lo que caduca antes). Devuelve la lista de movimientos (uno por lote tocado)."""
    cantidad = Decimal(cantidad)
    if cantidad <= 0:
        raise ValidationError("La cantidad de salida debe ser positiva.")
    if tipo in Movimiento.ENTRADAS:
        raise ValidationError(f"{tipo} no es un tipo de salida.")
    p = _bloquear(producto)
    if cantidad > p.existencia:
        raise ValidationError(f"Existencia insuficiente de {p.sku}: hay {p.existencia}, se piden {cantidad}.")

    if not p.maneja_lote:
        porciones = [(None, cantidad)]
    elif lote is not None:
        if lote.producto_id != p.pk or lote.existencia < cantidad:
            raise ValidationError(f"El lote {lote.numero} no tiene {cantidad} disponibles.")
        porciones = [(lote, cantidad)]
    else:
        porciones, pendiente = [], cantidad
        lotes = (Lote.objects.select_for_update().filter(producto=p, existencia__gt=0)
                 .order_by(models_nulls_last("caducidad"), "id"))
        for lt in lotes:
            toma = min(lt.existencia, pendiente)
            porciones.append((lt, toma))
            pendiente -= toma
            if pendiente == 0:
                break
        if pendiente > 0:
            raise ValidationError(f"Los lotes de {p.sku} no cubren {cantidad}; revisa existencia por lote.")

    movimientos = []
    for lt, cant in porciones:
        if lt is not None:
            lt.existencia -= cant
            lt.save(update_fields=["existencia"])
        p.existencia -= cant
        movimientos.append(Movimiento.objects.create(
            producto=p, lote=lt, tipo=tipo, cantidad=-cant, costo_unitario=p.costo_promedio,
            existencia_resultante=p.existencia, costo_promedio_resultante=p.costo_promedio,
            referencia=referencia, usuario=usuario,
        ))
    p.save(update_fields=["existencia"])
    return movimientos


def models_nulls_last(campo):
    from django.db.models import F
    return F(campo).asc(nulls_last=True)
