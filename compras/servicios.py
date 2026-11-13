from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction

from inventario.servicios import registrar_entrada

from .models import OrdenCompra, Recepcion, RecepcionLinea


@transaction.atomic
def recibir(orden, partidas, factura_proveedor="", usuario=None):
    """partidas: lista de dicts {linea, cantidad, lote?, caducidad?}. Acepta recepciones parciales;
    convierte a unidad base y da entrada al inventario al costo de la OC."""
    if orden.estado in (OrdenCompra.Estado.CANCELADA, OrdenCompra.Estado.RECIBIDA):
        raise ValidationError(f"La orden {orden.folio} está {orden.get_estado_display().lower()}.")
    rec = Recepcion.objects.create(orden=orden, factura_proveedor=factura_proveedor, recibio=usuario)
    for pt in partidas:
        linea, cant = pt["linea"], Decimal(pt["cantidad"])
        if linea.orden_id != orden.pk:
            raise ValidationError("La línea no pertenece a la orden.")
        if cant <= 0 or cant > linea.pendiente:
            raise ValidationError(f"Cantidad inválida para {linea.producto.sku}: pendiente {linea.pendiente}.")
        RecepcionLinea.objects.create(recepcion=rec, linea_orden=linea, cantidad=cant,
                                      lote=pt.get("lote", ""), caducidad=pt.get("caducidad"))
        factor = linea.producto.factor_a_base(linea.unidad)
        registrar_entrada(linea.producto, cant * factor, linea.costo_unitario / factor,
                          lote_numero=pt.get("lote") or None, caducidad=pt.get("caducidad"),
                          referencia=orden.folio, usuario=usuario)
        linea.cantidad_recibida += cant
        linea.save(update_fields=["cantidad_recibida"])
    orden.actualizar_estado()
    return rec
