from django.conf import settings
from django.db import models

from catalogo.models import Producto


class Lote(models.Model):
    producto = models.ForeignKey(Producto, on_delete=models.PROTECT, related_name="lotes")
    numero = models.CharField(max_length=60)
    caducidad = models.DateField(null=True, blank=True)
    existencia = models.DecimalField(max_digits=12, decimal_places=3, default=0)

    class Meta:
        ordering = ["caducidad", "id"]
        constraints = [models.UniqueConstraint(fields=["producto", "numero"], name="uniq_lote")]

    def __str__(self):
        return f"{self.producto.sku} lote {self.numero} cad {self.caducidad or '-'}"


class Movimiento(models.Model):
    """Kardex. Todo cambio de existencia pasa por inventario.servicios, nunca se edita a mano."""

    class Tipo(models.TextChoices):
        ENTRADA_COMPRA = "ENT_COMPRA", "Entrada por compra"
        SALIDA_VENTA = "SAL_VENTA", "Salida por venta"
        DEVOLUCION_CLIENTE = "DEV_CLIENTE", "Devolución de cliente"
        DEVOLUCION_PROVEEDOR = "DEV_PROV", "Devolución a proveedor"
        AJUSTE_POSITIVO = "AJ_POS", "Ajuste positivo"
        AJUSTE_NEGATIVO = "AJ_NEG", "Ajuste negativo"
        MERMA = "MERMA", "Merma / caducado"

    ENTRADAS = {Tipo.ENTRADA_COMPRA, Tipo.DEVOLUCION_CLIENTE, Tipo.AJUSTE_POSITIVO}

    fecha = models.DateTimeField(auto_now_add=True, db_index=True)
    producto = models.ForeignKey(Producto, on_delete=models.PROTECT, related_name="movimientos")
    lote = models.ForeignKey(Lote, null=True, blank=True, on_delete=models.PROTECT)
    tipo = models.CharField(max_length=12, choices=Tipo.choices)
    cantidad = models.DecimalField(max_digits=12, decimal_places=3, help_text="En unidad base; positiva entra, negativa sale")
    costo_unitario = models.DecimalField(max_digits=12, decimal_places=4)
    existencia_resultante = models.DecimalField(max_digits=12, decimal_places=3)
    costo_promedio_resultante = models.DecimalField(max_digits=12, decimal_places=4)
    referencia = models.CharField(max_length=60, blank=True)  # OC-0001, VTA-0001
    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT)

    class Meta:
        ordering = ["fecha", "id"]
