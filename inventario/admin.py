from django.contrib import admin

from .models import Lote, Movimiento


@admin.register(Lote)
class LoteAdmin(admin.ModelAdmin):
    list_display = ["producto", "numero", "caducidad", "existencia"]
    list_filter = ["caducidad"]
    search_fields = ["producto__sku", "numero"]


@admin.register(Movimiento)
class MovimientoAdmin(admin.ModelAdmin):
    """Kardex de solo lectura: los movimientos se generan desde compras, ventas y ajustes."""

    list_display = ["fecha", "producto", "tipo", "cantidad", "costo_unitario", "existencia_resultante", "referencia"]
    list_filter = ["tipo"]
    search_fields = ["producto__sku", "referencia"]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
