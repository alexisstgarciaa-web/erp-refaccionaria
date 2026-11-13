from django.contrib import admin

from . import models as m


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
