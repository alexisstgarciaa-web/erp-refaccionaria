"""Reglas del punto de venta: precio por lista, venta con salida de inventario, crédito y corte de caja."""

from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from catalogo.models import Precio
from inventario.models import Movimiento
from inventario.servicios import registrar_entrada, registrar_salida

from .models import Abono, Cliente, FormaPago, Turno, Venta, VentaLinea

CENT = Decimal("0.01")
IVA = Decimal("0.16")


def _m(x):
    return Decimal(x).quantize(CENT, ROUND_HALF_UP)


def precio_de(producto, lista, unidad=None):
    unidad = unidad or producto.unidad_base
    try:
        return Precio.objects.get(producto=producto, lista=lista, unidad=unidad).precio
    except Precio.DoesNotExist:
        raise ValidationError(f"{producto.sku} no tiene precio en la lista {lista} por {unidad}.")


def abrir_turno(usuario, fondo_inicial=0):
    if Turno.objects.filter(usuario=usuario, cerrado__isnull=True).exists():
        raise ValidationError("Ya tienes un turno abierto; ciérralo antes de abrir otro.")
    return Turno.objects.create(usuario=usuario, fondo_inicial=fondo_inicial)


@transaction.atomic
def registrar_venta(turno, cliente, partidas, forma_pago, usuario=None):
    """partidas: [{producto, cantidad, unidad?, precio?}]. El precio sale de la lista del cliente, salvo que
    la partida traiga uno (con IVA) tecleado en mostrador.
    Todo o nada: si una pieza no tiene existencia o precio, no se registra nada."""
    if turno.cerrado:
        raise ValidationError("El turno está cerrado.")
    if not partidas:
        raise ValidationError("La venta no tiene productos.")
    if forma_pago not in FormaPago.values:
        raise ValidationError(f"Forma de pago desconocida: {forma_pago}.")

    venta = Venta.objects.create(folio=f"TMP-{timezone.now().timestamp()}", turno=turno, cliente=cliente,
                                 lista=cliente.lista_precio, forma_pago=forma_pago,
                                 estado=Venta.Estado.PAGADA, usuario=usuario or turno.usuario)
    venta.folio = f"VTA-{venta.pk:06d}"
    total = costo = Decimal(0)
    for pt in partidas:
        producto, cantidad = pt["producto"], Decimal(str(pt["cantidad"]))
        if cantidad <= 0:
            raise ValidationError(f"Cantidad inválida para {producto.sku}.")
        unidad = pt.get("unidad") or producto.unidad_base
        if not unidad.permite_fraccion and cantidad != cantidad.to_integral_value():
            raise ValidationError(f"{producto.sku} se vende por {unidad} completa.")
        if pt.get("precio") is not None:
            precio = Decimal(str(pt["precio"]))
            if precio <= 0:
                raise ValidationError(f"Precio inválido para {producto.sku}.")
        else:
            precio = precio_de(producto, cliente.lista_precio, unidad)
        factor = producto.factor_a_base(unidad)
        movs = registrar_salida(producto, cantidad * factor, Movimiento.Tipo.SALIDA_VENTA,
                                referencia=venta.folio, usuario=venta.usuario)
        costo_u = movs[0].costo_unitario * factor
        importe = _m(precio * cantidad)
        VentaLinea.objects.create(venta=venta, producto=producto, unidad=unidad, cantidad=cantidad,
                                  cantidad_base=cantidad * factor, precio_unitario=precio, importe=importe,
                                  costo_unitario=costo_u)
        total += importe
        costo += costo_u * cantidad

    if forma_pago == FormaPago.CREDITO:
        if cliente.dias_credito <= 0:
            raise ValidationError(f"{cliente} no tiene crédito autorizado.")
        disponible = cliente.limite_credito - cliente.saldo()
        if total > disponible:
            raise ValidationError(f"La venta de ${total} excede el crédito disponible de {cliente} (${disponible}).")
        venta.estado, venta.saldo = Venta.Estado.CREDITO, total
        venta.vence = timezone.localdate() + timedelta(days=cliente.dias_credito)
    venta.total, venta.iva, venta.costo_total = total, _m(total - total / (1 + IVA)), _m(costo)
    venta.save()
    return venta


@transaction.atomic
def cancelar_venta(venta, usuario=None):
    """Regresa el inventario al costo con el que salió. No se cancela una venta con abonos."""
    venta = Venta.objects.select_for_update().get(pk=venta.pk)
    if venta.estado == Venta.Estado.CANCELADA:
        raise ValidationError(f"{venta.folio} ya está cancelada.")
    if venta.abonos.exists():
        raise ValidationError(f"{venta.folio} tiene abonos; registra una devolución en su lugar.")
    for ln in venta.lineas.select_related("producto", "unidad"):
        factor = ln.producto.factor_a_base(ln.unidad)
        lote = None
        if ln.producto.maneja_lote:
            mov = Movimiento.objects.filter(referencia=venta.folio, producto=ln.producto).exclude(lote=None).first()
            lote = mov.lote if mov else None
        registrar_entrada(ln.producto, ln.cantidad_base, ln.costo_unitario / factor, Movimiento.Tipo.DEVOLUCION_CLIENTE,
                          lote_numero=lote.numero if lote else None, caducidad=lote.caducidad if lote else None,
                          referencia=f"CAN-{venta.folio}", usuario=usuario)
    venta.estado, venta.saldo = Venta.Estado.CANCELADA, 0
    venta.save(update_fields=["estado", "saldo"])
    return venta


@transaction.atomic
def registrar_abono(venta, monto, forma_pago, turno):
    venta = Venta.objects.select_for_update().get(pk=venta.pk)
    monto = _m(monto)
    if venta.estado != Venta.Estado.CREDITO:
        raise ValidationError(f"{venta.folio} no tiene saldo pendiente.")
    if monto <= 0 or monto > venta.saldo:
        raise ValidationError(f"El abono debe ser mayor a 0 y hasta ${venta.saldo}.")
    if forma_pago == FormaPago.CREDITO:
        raise ValidationError("Un abono no puede ser a crédito.")
    abono = Abono.objects.create(venta=venta, turno=turno, monto=monto, forma_pago=forma_pago)
    venta.saldo -= monto
    if venta.saldo == 0:
        venta.estado = Venta.Estado.PAGADA
    venta.save(update_fields=["saldo", "estado"])
    return abono


def esperado_en_caja(turno):
    """Lo que debería haber por forma de pago: ventas de contado no canceladas + abonos (+ fondo en efectivo)."""
    res = {}
    for fp in (FormaPago.EFECTIVO, FormaPago.TARJETA, FormaPago.TRANSFERENCIA):
        v = turno.ventas.filter(forma_pago=fp).exclude(estado=Venta.Estado.CANCELADA).aggregate(s=Sum("total"))["s"] or 0
        a = turno.abonos.filter(forma_pago=fp).aggregate(s=Sum("monto"))["s"] or 0
        res[fp] = Decimal(v) + Decimal(a)
    res[FormaPago.EFECTIVO] += turno.fondo_inicial
    return res


@transaction.atomic
def cerrar_turno(turno, efectivo, tarjeta, transferencia):
    turno = Turno.objects.select_for_update().get(pk=turno.pk)
    if turno.cerrado:
        raise ValidationError("El turno ya está cerrado.")
    esp = esperado_en_caja(turno)
    turno.efectivo_contado, turno.tarjeta_contado, turno.transferencia_contado = map(_m, (efectivo, tarjeta, transferencia))
    turno.diferencia = (turno.efectivo_contado - esp[FormaPago.EFECTIVO]) + (turno.tarjeta_contado - esp[FormaPago.TARJETA]) \
        + (turno.transferencia_contado - esp[FormaPago.TRANSFERENCIA])
    turno.cerrado = timezone.now()
    turno.save()
    return turno, esp
