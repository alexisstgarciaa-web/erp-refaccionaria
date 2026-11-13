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
