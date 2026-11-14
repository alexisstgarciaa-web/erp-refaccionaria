from django.contrib import admin

from . import models as m


class ConversionInline(admin.TabularInline):
    model = m.ConversionUnidad
    extra = 0


class AplicacionInline(admin.TabularInline):
    model = m.Aplicacion
    fk_name = "producto"
    extra = 0
    autocomplete_fields = ["vehiculo"]


class EspecificacionInline(admin.StackedInline):
    model = m.EspecificacionLubricante
    extra = 0


class PrecioInline(admin.TabularInline):
    model = m.Precio
    extra = 0


@admin.register(m.Producto)
class ProductoAdmin(admin.ModelAdmin):
    list_display = ["sku", "nombre", "marca", "nivel", "familia", "existencia", "costo_promedio", "activo"]
    list_filter = ["nivel", "familia", "marca", "maneja_caducidad", "activo"]
    search_fields = ["sku", "nombre", "numero_parte", "codigo_barras",
                     "grupo_equivalencia__referencias__numero_parte"]
    readonly_fields = ["existencia", "costo_promedio"]
    autocomplete_fields = ["grupo_equivalencia"]
    inlines = [PrecioInline, ConversionInline, EspecificacionInline, AplicacionInline]


class ReferenciaInline(admin.TabularInline):
    model = m.ReferenciaCruzada
    extra = 1


@admin.register(m.GrupoEquivalencia)
class GrupoAdmin(admin.ModelAdmin):
    search_fields = ["descripcion", "referencias__numero_parte", "productos__numero_parte"]
    inlines = [ReferenciaInline]


@admin.register(m.Vehiculo)
class VehiculoAdmin(admin.ModelAdmin):
    list_display = ["armadora", "modelo", "anio_desde", "anio_hasta", "motor"]
    list_filter = ["armadora"]
    search_fields = ["armadora", "modelo", "motor"]


class KitComponenteInline(admin.TabularInline):
    model = m.KitComponente
    extra = 1
    autocomplete_fields = ["producto"]


@admin.register(m.Kit)
class KitAdmin(admin.ModelAdmin):
    search_fields = ["clave", "nombre"]
    inlines = [KitComponenteInline]


@admin.register(m.Precio)
class PrecioAdmin(admin.ModelAdmin):
    list_display = ["producto", "lista", "unidad", "precio"]
    list_filter = ["lista"]
    search_fields = ["producto__sku", "producto__nombre"]


for modelo in (m.Marca, m.Familia, m.UnidadMedida, m.ListaPrecio):
    admin.site.register(modelo, search_fields=["nombre"])
