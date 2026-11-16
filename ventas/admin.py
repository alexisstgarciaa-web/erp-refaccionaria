from django import forms
from django.contrib import admin
from django.core.exceptions import ValidationError

from . import nip as nips

from .models import Abono, Cliente, Nip, PedidoCliente, TicketGuardado, Turno, Venta, VentaLinea


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


class NipForm(forms.ModelForm):
    nip = forms.CharField(label="NIP nuevo", max_length=6, widget=forms.PasswordInput(render_value=False),
                          help_text="De 4 a 6 números, distinto al de los demás. Con él la persona firma cobros, abonos "
                                    "y cortes; si es administrador, también autoriza cancelaciones y crédito.")
    repetir = forms.CharField(label="Repite el NIP", max_length=6, widget=forms.PasswordInput(render_value=False))

    class Meta:
        model = Nip
        fields = ["usuario"]

    def clean(self):
        datos = super().clean()
        usuario = datos.get("usuario") or getattr(self.instance, "usuario", None)
        if datos.get("nip") and datos.get("nip") != datos.get("repetir"):
            raise ValidationError("Los dos NIP no coinciden.")
        if usuario and datos.get("nip"):
            self.instance.huella = nips.revisar(usuario, datos["nip"])
        return datos


@admin.register(Nip)
class NipAdmin(admin.ModelAdmin):
    """El administrador asigna o cambia el NIP de cada persona. Nunca se muestra: solo se reemplaza."""

    form = NipForm
    list_display = ["usuario", "es_admin", "cambiado"]
    search_fields = ["usuario__username", "usuario__first_name", "usuario__last_name"]

    @admin.display(boolean=True, description="Administrador")
    def es_admin(self, obj):
        return obj.usuario.is_staff

    def get_readonly_fields(self, request, obj=None):
        return ["usuario"] if obj else []
