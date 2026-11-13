from decimal import Decimal

from django.conf import settings
from django.db import models
from django.db.models import Sum

from catalogo.models import ListaPrecio, Producto, UnidadMedida


class FormaPago(models.TextChoices):
    EFECTIVO = "EFECTIVO", "Efectivo"
    TARJETA = "TARJETA", "Tarjeta"
    TRANSFERENCIA = "TRANSF", "Transferencia"
    CREDITO = "CREDITO", "Crédito"


class Cliente(models.Model):
    nombre = models.CharField(max_length=150)
    rfc = models.CharField(max_length=13, blank=True)
    telefono = models.CharField(max_length=20, blank=True)
    lista_precio = models.ForeignKey(ListaPrecio, on_delete=models.PROTECT, help_text="Mostrador, Taller…")
    dias_credito = models.PositiveSmallIntegerField(default=0, help_text="0 = solo contado")
    limite_credito = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    activo = models.BooleanField(default=True)

    class Meta:
        ordering = ["nombre"]

    def __str__(self):
        return self.nombre

    def saldo(self):
        return self.ventas.filter(estado=Venta.Estado.CREDITO).aggregate(s=Sum("saldo"))["s"] or Decimal(0)


class Turno(models.Model):
    """Turno de caja: se abre con un fondo y se cierra con el conteo por forma de pago."""

    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="turnos")
    abierto = models.DateTimeField(auto_now_add=True)
    fondo_inicial = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    cerrado = models.DateTimeField(null=True, blank=True)
    efectivo_contado = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    tarjeta_contado = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    transferencia_contado = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    diferencia = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True,
                                     help_text="Contado − esperado, todas las formas de pago")

    class Meta:
        ordering = ["-abierto"]
        constraints = [models.UniqueConstraint(fields=["usuario"], condition=models.Q(cerrado__isnull=True),
                                               name="un_turno_abierto_por_usuario")]

    def __str__(self):
        return f"Turno {self.pk} · {self.usuario} · {self.abierto:%d/%m %H:%M}"


class Venta(models.Model):
    class Estado(models.TextChoices):
        PAGADA = "PAGADA", "Pagada"
        CREDITO = "CREDITO", "A crédito"
        CANCELADA = "CANCELADA", "Cancelada"

    folio = models.CharField(max_length=20, unique=True)
    fecha = models.DateTimeField(auto_now_add=True, db_index=True)
    turno = models.ForeignKey(Turno, on_delete=models.PROTECT, related_name="ventas")
    cliente = models.ForeignKey(Cliente, on_delete=models.PROTECT, related_name="ventas")
    lista = models.ForeignKey(ListaPrecio, on_delete=models.PROTECT)
    forma_pago = models.CharField(max_length=10, choices=FormaPago.choices)
    estado = models.CharField(max_length=10, choices=Estado.choices)
    total = models.DecimalField(max_digits=12, decimal_places=2, default=0, help_text="Con IVA")
    iva = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    costo_total = models.DecimalField(max_digits=12, decimal_places=2, default=0, help_text="Sin IVA")
    saldo = models.DecimalField(max_digits=12, decimal_places=2, default=0, help_text="Pendiente de cobro si es a crédito")
    vence = models.DateField(null=True, blank=True)
    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT)

    class Meta:
        ordering = ["-fecha"]

    def __str__(self):
        return self.folio

    @property
    def margen(self):
        """Margen bruto sobre la venta sin IVA."""
        subtotal = self.total - self.iva
        return (subtotal - self.costo_total) / subtotal if subtotal else Decimal(0)


class VentaLinea(models.Model):
    venta = models.ForeignKey(Venta, on_delete=models.CASCADE, related_name="lineas")
    producto = models.ForeignKey(Producto, on_delete=models.PROTECT)
    unidad = models.ForeignKey(UnidadMedida, on_delete=models.PROTECT)
    cantidad = models.DecimalField(max_digits=12, decimal_places=3)
    cantidad_base = models.DecimalField(max_digits=12, decimal_places=3)
    precio_unitario = models.DecimalField(max_digits=12, decimal_places=2, help_text="Con IVA, por la unidad vendida")
    importe = models.DecimalField(max_digits=12, decimal_places=2)
    costo_unitario = models.DecimalField(max_digits=12, decimal_places=4, help_text="Costo promedio sin IVA al vender")


class Abono(models.Model):
    venta = models.ForeignKey(Venta, on_delete=models.PROTECT, related_name="abonos")
    turno = models.ForeignKey(Turno, on_delete=models.PROTECT, related_name="abonos")
    fecha = models.DateTimeField(auto_now_add=True)
    monto = models.DecimalField(max_digits=12, decimal_places=2)
    forma_pago = models.CharField(max_length=10, choices=[c for c in FormaPago.choices if c[0] != FormaPago.CREDITO])
