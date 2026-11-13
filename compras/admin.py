import tempfile
from pathlib import Path

from django import forms
from django.contrib import admin, messages
from django.core.exceptions import ValidationError
from django.shortcuts import redirect, render
from django.urls import path
from django.utils import timezone

from . import models as m


class VariosArchivos(forms.ClearableFileInput):
    allow_multiple_selected = True


class CampoVariosArchivos(forms.FileField):
    def __init__(self, *args, **kwargs):
        kwargs.setdefault("widget", VariosArchivos(attrs={"accept": ".xlsx"}))
        super().__init__(*args, **kwargs)

    def clean(self, data, initial=None):
        archivos = data if isinstance(data, (list, tuple)) else [data]
        limpios = [super(CampoVariosArchivos, self).clean(a, initial) for a in archivos if a]
        if not limpios:
            raise ValidationError("Elige al menos un archivo .xlsx.")
        for a in limpios:
            if not a.name.lower().endswith(".xlsx"):
                raise ValidationError(f"{a.name} no es .xlsx.")
        return limpios


class ImportarListaForm(forms.Form):
    proveedor = forms.CharField(initial="MORSA", max_length=150)
    fecha = forms.DateField(label="Fecha de la lista", initial=timezone.localdate,
                            widget=forms.DateInput(format="%Y-%m-%d", attrs={"type": "date"}))  # el navegador exige AAAA-MM-DD
    archivos = CampoVariosArchivos(label="Archivos .xlsx")


class ProductoProveedorInline(admin.TabularInline):
    model = m.ProductoProveedor
    extra = 0
    autocomplete_fields = ["producto"]


@admin.register(m.Proveedor)
class ProveedorAdmin(admin.ModelAdmin):
    list_display = ["nombre", "rfc", "dias_credito", "tiempo_entrega_dias", "activo"]
    search_fields = ["nombre", "rfc"]
    inlines = [ProductoProveedorInline]


class LineaInline(admin.TabularInline):
    model = m.OrdenCompraLinea
    extra = 1
    readonly_fields = ["cantidad_recibida"]
    autocomplete_fields = ["producto"]


@admin.register(m.OrdenCompra)
class OrdenCompraAdmin(admin.ModelAdmin):
    list_display = ["folio", "proveedor", "fecha", "estado", "es_emergencia"]
    list_filter = ["estado", "es_emergencia", "proveedor"]
    search_fields = ["folio"]
    inlines = [LineaInline]
    actions = ["recibir_todo"]

    @admin.action(description="Recibir todo lo pendiente (entra al inventario)")
    def recibir_todo(self, request, queryset):
        from .servicios import recibir

        for oc in queryset:
            pendientes = [ln for ln in oc.lineas.select_related("producto") if ln.pendiente > 0]
            if any(ln.producto.maneja_lote for ln in pendientes):
                self.message_user(request, f"{oc.folio} trae productos con lote: recíbela línea por línea.", messages.WARNING)
                continue
            if not pendientes:
                continue
            try:
                recibir(oc, [{"linea": ln, "cantidad": ln.pendiente} for ln in pendientes], usuario=request.user)
                self.message_user(request, f"{oc.folio}: {len(pendientes)} partidas recibidas.", messages.SUCCESS)
            except ValidationError as e:
                self.message_user(request, f"{oc.folio}: {' '.join(e.messages)}", messages.ERROR)


class VehiculoPartidaInline(admin.TabularInline):
    model = m.AplicacionProveedor
    extra = 0
    can_delete = False
    fields = ["armadora", "modelo", "motor", "anio_desde", "anio_hasta"]
    readonly_fields = fields

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(m.PartidaListaProveedor)
class PartidaListaProveedorAdmin(admin.ModelAdmin):
    list_display = ["codigo", "marca", "tipo", "costo", "proveedor", "fecha_lista", "producto"]
    list_filter = ["proveedor", "tipo", "marca"]
    search_fields = ["codigo", "equivalencia", "aplicacion", "marca"]
    search_help_text = "Código, equivalencia o vehículo con año: «tsuru 2003», «sentra 1.8 2005»."
    readonly_fields = ["producto"]
    actions = ["agregar_a_catalogo"]
    inlines = [VehiculoPartidaInline]

    def get_urls(self):
        return [path("importar/", self.admin_site.admin_view(self.importar), name="compras_partida_importar")] + super().get_urls()

    def importar(self, request):
        from .listas import importar

        form = ImportarListaForm(request.POST or None, request.FILES or None)
        if request.method == "POST" and form.is_valid():
            with tempfile.TemporaryDirectory() as carpeta:
                rutas = []
                for a in form.cleaned_data["archivos"]:
                    ruta = Path(carpeta) / Path(a.name).name
                    with open(ruta, "wb") as f:
                        for trozo in a.chunks():
                            f.write(trozo)
                    rutas.append(ruta)
                res = importar(form.cleaned_data["proveedor"], rutas, form.cleaned_data["fecha"])
            self.message_user(request, (
                f"{len(rutas)} archivos · {res['nuevas']} partidas nuevas · {res['actualizadas']} actualizadas "
                f"({res['costos_cambiados']} con costo distinto) · {res['sin_costo']} sin costo · "
                f"{res['vehiculos']:,} vehículos interpretados."), messages.SUCCESS)
            return redirect("admin:compras_partidalistaproveedor_changelist")
        ctx = {**self.admin_site.each_context(request), "form": form, "title": "Importar lista de proveedor",
               "opts": self.model._meta}
        return render(request, "admin/compras/partidalistaproveedor/importar.html", ctx)

    def get_search_results(self, request, queryset, search_term):
        from .aplicaciones import buscar, leer_consulta

        if leer_consulta(search_term)[1]:  # trae año: buscar por vehículo interpretado
            ids = buscar(search_term).values("partida_id")
            return queryset.filter(pk__in=ids), False
        return super().get_search_results(request, queryset, search_term)

    @admin.action(description="Agregar a mi catálogo (empezar a surtir)")
    def agregar_a_catalogo(self, request, queryset):
        from .listas import agregar_a_catalogo
        n = 0
        for partida in queryset.select_related("proveedor"):
            agregar_a_catalogo(partida)
            n += 1
        self.message_user(request, f"{n} productos agregados al catálogo.")


@admin.register(m.AplicacionProveedor)
class AplicacionProveedorAdmin(admin.ModelAdmin):
    list_display = ["armadora", "modelo", "motor", "anio_desde", "anio_hasta", "partida"]
    list_filter = ["armadora", "partida__tipo"]
    search_fields = ["modelo", "modelo_base", "partida__codigo"]
    list_select_related = ["partida", "partida__proveedor"]
