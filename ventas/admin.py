from django.contrib import admin

from .models import Abono, Cliente, PedidoCliente, TicketGuardado, Turno, Venta, VentaLinea


@admin.register(Cliente)
class ClienteAdmin(admin.ModelAdmin):
    list_display = ["nombre", "lista_precio", "dias_credito", "limite_credito", "activo"]
    search_fields = ["nombre", "rfc", "telefono"]
    list_filter = ["lista_precio", "activo"]


class LineaInline(admin.TabularInline):
    model = VentaLinea
    extra = 0
    can_delete = False
    readonly_fields = [f.name for f in VentaLinea._meta.fields]


class AbonoInline(admin.TabularInline):
    model = Abono
    extra = 0
    can_delete = False
    readonly_fields = ["fecha", "monto", "forma_pago", "turno"]


@admin.register(Venta)
class VentaAdmin(admin.ModelAdmin):
    """Las ventas se registran desde el mostrador; aquí solo se consultan."""

    list_display = ["folio", "fecha", "cliente", "forma_pago", "estado", "total", "saldo"]
    list_filter = ["estado", "forma_pago", "lista"]
    search_fields = ["folio", "cliente__nombre"]
    inlines = [LineaInline, AbonoInline]
    readonly_fields = [f.name for f in Venta._meta.fields]

    def has_add_permission(self, request):
        return False


@admin.register(Turno)
class TurnoAdmin(admin.ModelAdmin):
    list_display = ["id", "usuario", "abierto", "fondo_inicial", "cerrado", "diferencia"]
    readonly_fields = ["abierto", "cerrado", "diferencia"]


@admin.register(PedidoCliente)
class PedidoClienteAdmin(admin.ModelAdmin):
    """Se crean al cobrar en mostrador; se entregan o cancelan desde Pedidos."""

    list_display = ["fecha", "cliente", "contacto", "codigo", "cantidad", "precio", "estado"]
    list_filter = ["estado"]
    search_fields = ["cliente__nombre", "codigo", "contacto"]
    readonly_fields = [f.name for f in PedidoCliente._meta.fields]

    def has_add_permission(self, request):
        return False


@admin.register(TicketGuardado)
class TicketGuardadoAdmin(admin.ModelAdmin):
    """Cotizaciones y tickets en espera, para consulta."""

    list_display = ["folio", "tipo", "fecha", "cliente", "nombre", "total", "retomado"]
    list_filter = ["tipo"]
    search_fields = ["folio", "cliente__nombre", "nombre"]
    readonly_fields = [f.name for f in TicketGuardado._meta.fields]

    def has_add_permission(self, request):
        return False
