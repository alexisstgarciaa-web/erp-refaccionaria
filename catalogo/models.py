from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models


class Marca(models.Model):
    nombre = models.CharField(max_length=80, unique=True)

    class Meta:
        ordering = ["nombre"]

    def __str__(self):
        return self.nombre


class Familia(models.Model):
    """Agrupador para reportes y ABC: filtros de aceite, bujías, aceite motor, anticongelante..."""

    nombre = models.CharField(max_length=80, unique=True)
    padre = models.ForeignKey("self", null=True, blank=True, on_delete=models.PROTECT, related_name="subfamilias")

    class Meta:
        ordering = ["nombre"]

    def __str__(self):
        return self.nombre


class UnidadMedida(models.Model):
    clave = models.CharField(max_length=10, unique=True)  # PZA, L, GAL, CUB, TAM, CJA
    nombre = models.CharField(max_length=40)
    permite_fraccion = models.BooleanField(default=False, help_text="Litros sí; piezas no.")

    def __str__(self):
        return self.clave


class Producto(models.Model):
    sku = models.CharField(max_length=40, unique=True)
    nombre = models.CharField(max_length=200)
    marca = models.ForeignKey(Marca, on_delete=models.PROTECT)
    numero_parte = models.CharField(max_length=60, help_text="Número de parte del fabricante")
    familia = models.ForeignKey(Familia, on_delete=models.PROTECT)
    unidad_base = models.ForeignKey(
        UnidadMedida, on_delete=models.PROTECT, help_text="Unidad en que se lleva el inventario (ej. L para granel)"
    )
    codigo_barras = models.CharField(max_length=40, blank=True, db_index=True)
    maneja_lote = models.BooleanField(default=False)
    maneja_caducidad = models.BooleanField(default=False, help_text="Químicos: anticongelante, líquido de frenos, aditivos")
    stock_minimo = models.DecimalField(max_digits=12, decimal_places=3, default=0)
    stock_maximo = models.DecimalField(max_digits=12, decimal_places=3, default=0)
    # Datos calculados y mantenidos por inventario.servicios
    existencia = models.DecimalField(max_digits=12, decimal_places=3, default=0, editable=False)
    costo_promedio = models.DecimalField(max_digits=12, decimal_places=4, default=0, editable=False)
    activo = models.BooleanField(default=True)
    grupo_equivalencia = models.ForeignKey(
        "GrupoEquivalencia", null=True, blank=True, on_delete=models.SET_NULL, related_name="productos"
    )

    class Meta:
        ordering = ["sku"]
        constraints = [models.UniqueConstraint(fields=["marca", "numero_parte"], name="uniq_marca_numero_parte")]

    def __str__(self):
        return f"{self.sku} · {self.nombre}"

    def clean(self):
        if self.maneja_caducidad and not self.maneja_lote:
            raise ValidationError("Un producto con caducidad debe manejar lote.")

    def factor_a_base(self, unidad):
        """Cuántas unidades base contiene 1 `unidad` (ej. 1 TAM = 208 L)."""
        if unidad.pk == self.unidad_base_id:
            return Decimal(1)
        try:
            return self.conversiones.get(unidad=unidad).factor
        except ConversionUnidad.DoesNotExist:
            raise ValidationError(f"{self.sku} no tiene conversión definida para {unidad}.")

    def equivalentes(self):
        """Productos intercambiables que tengo en catálogo (otras marcas)."""
        if not self.grupo_equivalencia_id:
            return Producto.objects.none()
        return self.grupo_equivalencia.productos.exclude(pk=self.pk)


class ConversionUnidad(models.Model):
    producto = models.ForeignKey(Producto, on_delete=models.CASCADE, related_name="conversiones")
    unidad = models.ForeignKey(UnidadMedida, on_delete=models.PROTECT)
    factor = models.DecimalField(max_digits=12, decimal_places=4, help_text="Unidades base por 1 de esta unidad")

    class Meta:
        constraints = [models.UniqueConstraint(fields=["producto", "unidad"], name="uniq_conversion")]

    def __str__(self):
        return f"1 {self.unidad} = {self.factor} {self.producto.unidad_base}"


class GrupoEquivalencia(models.Model):
    """Conjunto de piezas intercambiables entre marcas (ej. filtro Gonher GP-10 = Interfil OF-2 = Wix 51515)."""

    descripcion = models.CharField(max_length=120)

    def __str__(self):
        return self.descripcion


class ReferenciaCruzada(models.Model):
    """Número de parte de otra marca que NO manejo, para que el mostrador encuentre mi equivalente."""

    grupo = models.ForeignKey(GrupoEquivalencia, on_delete=models.CASCADE, related_name="referencias")
    marca = models.CharField(max_length=80)
    numero_parte = models.CharField(max_length=60, db_index=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["marca", "numero_parte"], name="uniq_referencia")]

    def __str__(self):
        return f"{self.marca} {self.numero_parte}"


class Vehiculo(models.Model):
    armadora = models.CharField(max_length=60)  # Nissan
    modelo = models.CharField(max_length=60)  # Tsuru
    anio_desde = models.PositiveSmallIntegerField()
    anio_hasta = models.PositiveSmallIntegerField()
    motor = models.CharField(max_length=60, blank=True)  # 1.6L L4 GA16DE

    class Meta:
        ordering = ["armadora", "modelo", "anio_desde"]

    def __str__(self):
        return f"{self.armadora} {self.modelo} {self.anio_desde}-{self.anio_hasta} {self.motor}".strip()


class Aplicacion(models.Model):
    """Qué producto le queda a qué vehículo. Se puede ligar al grupo de equivalencia para no repetir por marca."""

    vehiculo = models.ForeignKey(Vehiculo, on_delete=models.CASCADE, related_name="aplicaciones")
    producto = models.ForeignKey(Producto, null=True, blank=True, on_delete=models.CASCADE, related_name="aplicaciones")
    grupo = models.ForeignKey(
        GrupoEquivalencia, null=True, blank=True, on_delete=models.CASCADE, related_name="aplicaciones"
    )
    posicion = models.CharField(max_length=60, blank=True, help_text="Ej. aceite, aire, cabina, combustible")
    cantidad = models.DecimalField(max_digits=8, decimal_places=3, default=1, help_text="Ej. 4 bujías, 3.8 L de aceite")
    notas = models.CharField(max_length=200, blank=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(producto__isnull=False) | models.Q(grupo__isnull=False),
                name="aplicacion_producto_o_grupo",
            )
        ]


class EspecificacionLubricante(models.Model):
    producto = models.OneToOneField(Producto, on_delete=models.CASCADE, related_name="especificacion")
    viscosidad = models.CharField(max_length=20, blank=True)  # 5W-30
    tipo_base = models.CharField(
        max_length=20,
        blank=True,
        choices=[("MINERAL", "Mineral"), ("SEMI", "Semisintético"), ("SINTETICO", "Sintético")],
    )
    norma_api = models.CharField(max_length=20, blank=True)  # SP
    norma_ilsac = models.CharField(max_length=20, blank=True)  # GF-6A
    otras_normas = models.CharField(max_length=200, blank=True)  # dexos1, VW 502.00


class Kit(models.Model):
    """Paquete de afinación: se vende como uno, descuenta cada componente."""

    clave = models.CharField(max_length=40, unique=True)
    nombre = models.CharField(max_length=200)
    vehiculo = models.ForeignKey(Vehiculo, null=True, blank=True, on_delete=models.SET_NULL)

    def __str__(self):
        return self.nombre


class KitComponente(models.Model):
    kit = models.ForeignKey(Kit, on_delete=models.CASCADE, related_name="componentes")
    producto = models.ForeignKey(Producto, on_delete=models.PROTECT)
    cantidad = models.DecimalField(max_digits=10, decimal_places=3)


class ListaPrecio(models.Model):
    nombre = models.CharField(max_length=40, unique=True)  # Mostrador, Taller, Mayoreo

    def __str__(self):
        return self.nombre


class Precio(models.Model):
    lista = models.ForeignKey(ListaPrecio, on_delete=models.CASCADE, related_name="precios")
    producto = models.ForeignKey(Producto, on_delete=models.CASCADE, related_name="precios")
    unidad = models.ForeignKey(UnidadMedida, on_delete=models.PROTECT)
    precio = models.DecimalField(max_digits=12, decimal_places=2, help_text="Precio con IVA")

    class Meta:
        constraints = [models.UniqueConstraint(fields=["lista", "producto", "unidad"], name="uniq_precio")]
