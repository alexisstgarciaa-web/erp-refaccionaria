from django.conf import settings
from django.db.models import F


def negocio(request):
    ctx = {"negocio": getattr(settings, "NEGOCIO_NOMBRE", "Refaccionaria")}
    if getattr(request, "user", None) is not None and request.user.is_authenticated:
        from .models import PedidoCliente

        # pedidos de clientes que ya están en anaquel: hay que avisarles
        ctx["pedidos_listos"] = PedidoCliente.objects.filter(estado=PedidoCliente.Estado.PEDIDO,
                                                             producto__existencia__gte=F("cantidad")).count()
    return ctx
