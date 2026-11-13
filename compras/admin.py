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


@admin.register(m.PartidaListaProveedor)
class PartidaListaProveedorAdmin(admin.ModelAdmin):
    list_display = ["codigo", "marca", "tipo", "costo", "proveedor", "fecha_lista", "producto"]
    list_filter = ["proveedor", "tipo", "marca"]
    search_fields = ["codigo", "equivalencia", "aplicacion", "marca"]
    readonly_fields = ["producto"]
    actions = ["agregar_a_catalogo"]

    @admin.action(description="Agregar a mi catálogo (empezar a surtir)")
    def agregar_a_catalogo(self, request, queryset):
        from .listas import agregar_a_catalogo
        n = 0
        for partida in queryset.select_related("proveedor"):
            agregar_a_catalogo(partida)
            n += 1
        self.message_user(request, f"{n} productos agregados al catálogo.")
