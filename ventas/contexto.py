from django.conf import settings
from django.db.models import F
from django.utils.functional import SimpleLazyObject


def negocio(request):
    ctx = {"negocio": getattr(settings, "NEGOCIO_NOMBRE", "Refaccionaria")}
    if getattr(request, "user", None) is not None and request.user.is_authenticated:
        from .models import PedidoCliente, Venta

        # Las insignias del menú se cuentan solo si la página las pinta: los pedazos que se actualizan solos
        # (la fila de turnos, lo que hay por cobrar) no traen menú y no hacen estas consultas cada 5 segundos.
        # pedidos de clientes que ya están en anaquel: hay que avisarles
        ctx["pedidos_listos"] = SimpleLazyObject(lambda: PedidoCliente.objects.filter(
            estado=PedidoCliente.Estado.PEDIDO, producto__existencia__gte=F("cantidad")).count())
        # remisiones de contado que Caja todavía no cobra
        ctx["remisiones_por_cobrar"] = SimpleLazyObject(lambda: Venta.objects.filter(estado=Venta.Estado.POR_COBRAR).count())
    return ctx
