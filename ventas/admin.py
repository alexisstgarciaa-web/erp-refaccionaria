from django import forms
from django.contrib import admin
from django.core.exceptions import ValidationError
from django.urls import reverse

from . import nip as nips
from .cifras import ConComas
from .models import Abono, Cliente, CodigoPostal, Nip, PedidoCliente, TicketGuardado, Turno, Venta, VentaLinea


@admin.register(Cliente)
class ClienteAdmin(ConComas, admin.ModelAdmin):
    list_display = ["folio", "nombre", "rfc", "telefono", "municipio", "lista_precio", "dias_credito", "limite_credito", "activo"]
    list_display_links = ["folio", "nombre"]
    search_fields = ["nombre", "rfc", "telefono", "colonia", "municipio"]
    list_filter = ["lista_precio", "activo", "estado"]
    fieldsets = [
        (None, {"fields": ["nombre", "rfc", "telefono", "correo", "lista_precio", "activo"]}),
        ("Dirección", {"fields": ["codigo_postal", "colonia", "municipio", "estado", "calle",
                                  ("numero_exterior", "numero_interior")]}),
        ("Crédito", {"fields": ["dias_credito", "limite_credito"]}),
    ]

    class Media:
        js = ["ventas/cp.js"]

    @admin.display(description="folio", ordering="pk")
    def folio(self, obj):
        return obj.folio

    def get_search_results(self, request, queryset, search_term):
        """«CTE-0012» o «cte12» busca por folio; lo demás también sin acentos ni guiones («ramirez», «442-123»)."""
        from .clientes import filtro, pk_de_folio

        folio = pk_de_folio(search_term)
        if folio is not None:
            return queryset.filter(pk=folio), False
        res, duplicados = super().get_search_results(request, queryset, search_term)
        if search_term.strip():
            res = res | queryset.filter(filtro(search_term))
        return res, duplicados

    def formfield_for_dbfield(self, db_field, request, **kwargs):
        campo = super().formfield_for_dbfield(db_field, request, **kwargs)
        if db_field.name == "codigo_postal":
            campo.widget.attrs.update({"data-cp": reverse("codigo_postal", args=["00000"]), "inputmode": "numeric",
                                       "autocomplete": "off", "size": 6})
        return campo


@admin.register(CodigoPostal)
class CodigoPostalAdmin(admin.ModelAdmin):
    """Solo consulta: el catálogo se carga con manage.py cargar_codigos_postales."""

    list_display = ["cp", "colonia", "tipo", "municipio", "estado"]
    search_fields = ["=cp", "colonia", "municipio"]
    list_filter = ["estado"]
    list_per_page = 50

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class LineaInline(ConComas, admin.TabularInline):
    model = VentaLinea
    extra = 0
    can_delete = False
    readonly_fields = [f.name for f in VentaLinea._meta.fields]


class AbonoInline(ConComas, admin.TabularInline):
    model = Abono
    extra = 0
    can_delete = False
    readonly_fields = ["fecha", "monto", "forma_pago", "turno"]


@admin.register(Venta)
class VentaAdmin(ConComas, admin.ModelAdmin):
    """Las ventas se registran desde el mostrador; aquí solo se consultan."""

    list_display = ["folio", "fecha", "cliente", "origen", "forma_pago", "estado", "total", "saldo"]
    list_filter = ["estado", "origen", "forma_pago", "lista"]
    search_fields = ["folio", "cliente__nombre", "orden_compra"]
    inlines = [LineaInline, AbonoInline]
    readonly_fields = [f.name for f in Venta._meta.fields]

    def has_add_permission(self, request):
        return False


@admin.register(Turno)
class TurnoAdmin(ConComas, admin.ModelAdmin):
    list_display = ["id", "usuario", "abierto", "fondo_inicial", "cerrado", "diferencia"]
    readonly_fields = ["abierto", "cerrado", "diferencia"]


@admin.register(PedidoCliente)
class PedidoClienteAdmin(ConComas, admin.ModelAdmin):
    """Se crean al cobrar en mostrador; se entregan o cancelan desde Pedidos."""

    list_display = ["fecha", "cliente", "contacto", "codigo", "cantidad", "precio", "estado"]
    list_filter = ["estado"]
    search_fields = ["cliente__nombre", "codigo", "contacto"]
    readonly_fields = [f.name for f in PedidoCliente._meta.fields]

    def has_add_permission(self, request):
        return False


@admin.register(TicketGuardado)
class TicketGuardadoAdmin(ConComas, admin.ModelAdmin):
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
class NipAdmin(ConComas, admin.ModelAdmin):
    """El administrador asigna o cambia el NIP de cada persona. Nunca se muestra: solo se reemplaza."""

    form = NipForm
    list_display = ["usuario", "es_admin", "cambiado"]
    search_fields = ["usuario__username", "usuario__first_name", "usuario__last_name"]

    @admin.display(boolean=True, description="Administrador")
    def es_admin(self, obj):
        return obj.usuario.is_staff

    def get_readonly_fields(self, request, obj=None):
        return ["usuario"] if obj else []
