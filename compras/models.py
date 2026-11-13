from django.conf import settings
from django.db import models

from catalogo.models import Producto, UnidadMedida


class Proveedor(models.Model):
    nombre = models.CharField(max_length=150)
    rfc = models.CharField(max_length=13, blank=True)
    contacto = models.CharField(max_length=100, blank=True)
    telefono = models.CharField(max_length=20, blank=True)
    correo = models.EmailField(blank=True)
    dias_credito = models.PositiveSmallIntegerField(default=0)
    tiempo_entrega_dias = models.PositiveSmallIntegerField(default=1, help_text="Lead time promedio")
    activo = models.BooleanField(default=True)

    def __str__(self):
        return self.nombre


class ProductoProveedor(models.Model):
    """Condiciones de compra de un producto con un proveedor."""

    proveedor = models.ForeignKey(Proveedor, on_delete=models.CASCADE, related_name="productos")
    producto = models.ForeignKey(Producto, on_delete=models.CASCADE, related_name="proveedores")
    clave_proveedor = models.CharField(max_length=60, blank=True)
    unidad_compra = models.ForeignKey(UnidadMedida, on_delete=models.PROTECT)
    costo = models.DecimalField(max_digits=12, decimal_places=4, help_text="Sin IVA, por unidad de compra")
    multiplo_compra = models.DecimalField(max_digits=10, decimal_places=3, default=1, help_text="Ej. caja de 12")
    tiempo_entrega_dias = models.PositiveSmallIntegerField(null=True, blank=True, help_text="Vacío = el del proveedor")
    preferente = models.BooleanField(default=False)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["proveedor", "producto"], name="uniq_producto_proveedor")]


class OrdenCompra(models.Model):
    class Estado(models.TextChoices):
        BORRADOR = "BORRADOR", "Borrador"
        ENVIADA = "ENVIADA", "Enviada"
        PARCIAL = "PARCIAL", "Recibida parcial"
        RECIBIDA = "RECIBIDA", "Recibida"
        CANCELADA = "CANCELADA", "Cancelada"

    folio = models.CharField(max_length=20, unique=True)
    proveedor = models.ForeignKey(Proveedor, on_delete=models.PROTECT, related_name="ordenes")
    fecha = models.DateField(auto_now_add=True)
    fecha_esperada = models.DateField(null=True, blank=True)
    estado = models.CharField(max_length=10, choices=Estado.choices, default=Estado.BORRADOR)
    es_emergencia = models.BooleanField(default=False, help_text="Para medir compras de emergencia")
    notas = models.TextField(blank=True)
    creada_por = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT)

    def __str__(self):
        return self.folio

    def actualizar_estado(self):
        lineas = list(self.lineas.all())
        if lineas and all(l.pendiente <= 0 for l in lineas):
            self.estado = self.Estado.RECIBIDA
        elif any(l.cantidad_recibida > 0 for l in lineas):
            self.estado = self.Estado.PARCIAL
        self.save(update_fields=["estado"])


class OrdenCompraLinea(models.Model):
    orden = models.ForeignKey(OrdenCompra, on_delete=models.CASCADE, related_name="lineas")
    producto = models.ForeignKey(Producto, on_delete=models.PROTECT)
    unidad = models.ForeignKey(UnidadMedida, on_delete=models.PROTECT)
    cantidad = models.DecimalField(max_digits=12, decimal_places=3)
    costo_unitario = models.DecimalField(max_digits=12, decimal_places=4, help_text="Sin IVA, por la unidad de la línea")
    cantidad_recibida = models.DecimalField(max_digits=12, decimal_places=3, default=0)

    @property
    def pendiente(self):
        return self.cantidad - self.cantidad_recibida


class Recepcion(models.Model):
    orden = models.ForeignKey(OrdenCompra, on_delete=models.PROTECT, related_name="recepciones")
    fecha = models.DateTimeField(auto_now_add=True)
    factura_proveedor = models.CharField(max_length=60, blank=True)
    recibio = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT)


class RecepcionLinea(models.Model):
    recepcion = models.ForeignKey(Recepcion, on_delete=models.CASCADE, related_name="lineas")
    linea_orden = models.ForeignKey(OrdenCompraLinea, on_delete=models.PROTECT)
    cantidad = models.DecimalField(max_digits=12, decimal_places=3, help_text="En la unidad de la línea de la OC")
    lote = models.CharField(max_length=60, blank=True)
    caducidad = models.DateField(null=True, blank=True)
