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

from .models import Abono, Cliente, Devolucion, DevolucionLinea, FormaPago, Turno, Venta, VentaLinea
from .templatetags.erp import pesos

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


def abrir_turno(usuario, fondo_inicial=0, por=None):
    """usuario: la sesión de caja dueña del turno; por: quién tecleó su NIP al abrir."""
    if Turno.objects.filter(usuario=usuario, cerrado__isnull=True).exists():
        raise ValidationError("Ya tienes un turno abierto; ciérralo antes de abrir otro.")
    return Turno.objects.create(usuario=usuario, fondo_inicial=fondo_inicial, abierto_por=por)


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
            raise ValidationError(f"La venta de {pesos(total)} excede el crédito disponible de {cliente} ({pesos(disponible)}).")
        venta.estado, venta.saldo = Venta.Estado.CREDITO, total
        venta.vence = timezone.localdate() + timedelta(days=cliente.dias_credito)
    venta.total, venta.iva, venta.costo_total = total, _m(total - total / (1 + IVA)), _m(costo)
    venta.save()
    return venta


def por_que_no_cancelar(venta):
    """None si la venta se puede cancelar (siempre con NIP de administrador); si no, la razón.
    Una venta de contado de un turno ya cerrado no se cancela: ese dinero ya se contó en su corte.
    Para esa va una devolución o una garantía."""
    if venta.estado == Venta.Estado.CANCELADA:
        return "Ya está cancelada."
    if venta.abonos.exists():
        return "Tiene abonos registrados; no se puede cancelar."
    if venta.devoluciones.exists():
        return "Ya tiene devoluciones o garantías; no se puede cancelar completa."
    if venta.forma_pago != FormaPago.CREDITO and venta.turno.cerrado is not None:
        return "Es de contado y su turno ya se cerró: ese dinero ya se contó en el corte."
    return None


@transaction.atomic
def cancelar_venta(venta, usuario=None, motivo=""):
    """Regresa el inventario al costo con el que salió. No se cancela una venta con abonos."""
    venta = Venta.objects.select_for_update().get(pk=venta.pk)
    if venta.estado == Venta.Estado.CANCELADA:
        raise ValidationError(f"{venta.folio} ya está cancelada.")
    if venta.abonos.exists():
        raise ValidationError(f"{venta.folio} tiene abonos; registra una devolución en su lugar.")
    if venta.devoluciones.exists():
        raise ValidationError(f"{venta.folio} ya tiene devoluciones o garantías; no se puede cancelar completa.")
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
    venta.cancelada, venta.motivo_cancelacion = timezone.now(), (motivo or "").strip()[:200]
    venta.cancelada_por = usuario if usuario and usuario.pk else None
    venta.save(update_fields=["estado", "saldo", "cancelada", "cancelada_por", "motivo_cancelacion"])
    return venta


@transaction.atomic
def registrar_abono(venta, monto, forma_pago, turno, usuario=None):
    venta = Venta.objects.select_for_update().get(pk=venta.pk)
    monto = _m(monto)
    if venta.estado != Venta.Estado.CREDITO:
        raise ValidationError(f"{venta.folio} no tiene saldo pendiente.")
    if monto <= 0 or monto > venta.saldo:
        raise ValidationError(f"El abono debe ser mayor a 0 y hasta {pesos(venta.saldo)}.")
    if forma_pago == FormaPago.CREDITO:
        raise ValidationError("Un abono no puede ser a crédito.")
    abono = Abono.objects.create(venta=venta, turno=turno, monto=monto, forma_pago=forma_pago, usuario=usuario)
    venta.saldo -= monto
    if venta.saldo == 0:
        venta.estado = Venta.Estado.PAGADA
    venta.save(update_fields=["saldo", "estado"])
    return abono


def disponible_para_devolver(linea):
    """Lo vendido en ese renglón menos lo que ya se devolvió (la garantía no cuenta: se cambia por otra igual)."""
    devuelto = (linea.devuelto.filter(devolucion__tipo=Devolucion.Tipo.DEVOLUCION).aggregate(s=Sum("cantidad"))["s"]
                or Decimal(0))
    return linea.cantidad - devuelto


def _siguiente_folio(tipo):
    ultimo = Devolucion.objects.filter(tipo=tipo).order_by("-pk").values_list("folio", flat=True).first()
    return f"{tipo}-{int(ultimo.split('-')[1]) + 1 if ultimo else 1:06d}"


@transaction.atomic
def registrar_devolucion(venta, tipo, cantidades, autorizo, motivo, turno=None, forma_reembolso=None,
                         regresa_anaquel=True):
    """cantidades: {id de VentaLinea: cantidad en la unidad vendida}.
    Devolución: regresa la pieza (al anaquel si regresa_anaquel) y el dinero: primero baja el saldo si la venta
    es a crédito y lo demás sale de la caja abierta (turno) en forma_reembolso.
    Garantía: sale otra pieza igual sin cobrar y la mala queda por reclamar al proveedor."""
    venta = Venta.objects.select_for_update().get(pk=venta.pk)
    if venta.estado == Venta.Estado.CANCELADA:
        raise ValidationError(f"{venta.folio} está cancelada.")
    motivo = (motivo or "").strip()
    if not motivo:
        raise ValidationError("Escribe el motivo.")
    lineas = {ln.pk: ln for ln in venta.lineas.select_related("producto", "unidad")}
    partidas = []
    for pk, cant in cantidades.items():
        cant = Decimal(cant)
        if cant == 0:
            continue
        ln = lineas.get(pk)
        if ln is None or cant < 0:
            raise ValidationError("Revisa las cantidades.")
        if not ln.unidad.permite_fraccion and cant != cant.to_integral_value():
            raise ValidationError(f"{ln.producto.sku} se maneja por pieza completa.")
        if cant > disponible_para_devolver(ln):
            raise ValidationError(f"De {ln.producto.sku} solo quedan {disponible_para_devolver(ln).normalize()} "
                                  "por devolver en esta venta.")
        partidas.append((ln, cant))
    if not partidas:
        raise ValidationError("Escribe cuántas piezas regresa el cliente.")
    importe = _m(sum((ln.precio_unitario * cant for ln, cant in partidas), Decimal(0)))
    d = Devolucion(folio=_siguiente_folio(tipo), tipo=tipo, venta=venta, autorizo=autorizo, motivo=motivo[:200],
                   importe=importe)
    if tipo == Devolucion.Tipo.DEVOLUCION:
        d.regresa_anaquel = bool(regresa_anaquel)
        d.a_saldo = min(importe, venta.saldo) if venta.estado == Venta.Estado.CREDITO else Decimal(0)
        d.reembolso = importe - d.a_saldo
        if d.reembolso > 0:
            if turno is None or turno.cerrado:
                raise ValidationError("Abre la caja para regresar el dinero: el reembolso sale del corte de tu turno.")
            if forma_reembolso not in (FormaPago.EFECTIVO, FormaPago.TARJETA, FormaPago.TRANSFERENCIA):
                raise ValidationError("Elige cómo le regresas el dinero.")
            d.turno, d.forma_reembolso = turno, forma_reembolso
    elif tipo == Devolucion.Tipo.GARANTIA:
        d.reclamo = Devolucion.Reclamo.PENDIENTE
    else:
        raise ValidationError("Tipo inválido.")
    d.save()
    for ln, cant in partidas:
        DevolucionLinea.objects.create(devolucion=d, venta_linea=ln, cantidad=cant, importe=_m(ln.precio_unitario * cant))
        factor = ln.producto.factor_a_base(ln.unidad)
        base = cant * factor
        if tipo == Devolucion.Tipo.GARANTIA:
            if ln.producto.existencia < base:
                raise ValidationError(f"No hay {ln.producto.sku} en anaquel para cambiarla; haz una devolución "
                                      "o pídela al proveedor.")
            registrar_salida(ln.producto, base, tipo=Movimiento.Tipo.SALIDA_GARANTIA, referencia=d.folio, usuario=autorizo)
        elif d.regresa_anaquel:
            lote = None
            if ln.producto.maneja_lote:
                mov = Movimiento.objects.filter(referencia=venta.folio, producto=ln.producto).exclude(lote=None).first()
                lote = mov.lote if mov else None
            registrar_entrada(ln.producto, base, ln.costo_unitario / factor, Movimiento.Tipo.DEVOLUCION_CLIENTE,
                              lote_numero=lote.numero if lote else None, caducidad=lote.caducidad if lote else None,
                              referencia=d.folio, usuario=autorizo)
    if d.a_saldo:
        venta.saldo -= d.a_saldo
        if venta.saldo == 0:
            venta.estado = Venta.Estado.PAGADA
        venta.save(update_fields=["saldo", "estado"])
    return d


def esperado_en_caja(turno):
    """Lo que debería haber por forma de pago: ventas de contado no canceladas + abonos − reembolsos de
    devoluciones (+ fondo en efectivo)."""
    res = {}
    for fp in (FormaPago.EFECTIVO, FormaPago.TARJETA, FormaPago.TRANSFERENCIA):
        v = turno.ventas.filter(forma_pago=fp).exclude(estado=Venta.Estado.CANCELADA).aggregate(s=Sum("total"))["s"] or 0
        a = turno.abonos.filter(forma_pago=fp).aggregate(s=Sum("monto"))["s"] or 0
        d = turno.devoluciones.filter(forma_reembolso=fp).aggregate(s=Sum("reembolso"))["s"] or 0  # lo que se regresó
        res[fp] = Decimal(v) + Decimal(a) - Decimal(d)
    res[FormaPago.EFECTIVO] += turno.fondo_inicial
    return res


@transaction.atomic
def cerrar_turno(turno, efectivo, tarjeta, transferencia, por=None):
    turno = Turno.objects.select_for_update().get(pk=turno.pk)
    if turno.cerrado:
        raise ValidationError("El turno ya está cerrado.")
    esp = esperado_en_caja(turno)
    turno.efectivo_contado, turno.tarjeta_contado, turno.transferencia_contado = map(_m, (efectivo, tarjeta, transferencia))
    turno.diferencia = (turno.efectivo_contado - esp[FormaPago.EFECTIVO]) + (turno.tarjeta_contado - esp[FormaPago.TARJETA]) \
        + (turno.transferencia_contado - esp[FormaPago.TRANSFERENCIA])
    turno.cerrado, turno.cerrado_por = timezone.now(), por
    turno.save()
    return turno, esp
