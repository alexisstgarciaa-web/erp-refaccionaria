from django.conf import settings
from django.db.models import F


def negocio(request):
    ctx = {"negocio": getattr(settings, "NEGOCIO_NOMBRE", "Refaccionaria")}
    if getattr(request, "user", None) is not None and request.user.is_authenticated:
        from .models import PedidoCliente, Venta

        # pedidos de clientes que ya están en anaquel: hay que avisarles
        ctx["pedidos_listos"] = PedidoCliente.objects.filter(estado=PedidoCliente.Estado.PEDIDO,
                                                             producto__existencia__gte=F("cantidad")).count()
        # remisiones de contado que Caja todavía no cobra
        ctx["remisiones_por_cobrar"] = Venta.objects.filter(estado=Venta.Estado.POR_COBRAR).count()
    return ctx
