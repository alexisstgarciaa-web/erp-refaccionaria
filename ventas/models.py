import re
import unicodedata
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator
from django.db import models
from django.db.models import Sum

from catalogo.models import ListaPrecio, Producto, UnidadMedida


class FormaPago(models.TextChoices):
    EFECTIVO = "EFECTIVO", "Efectivo"
    TARJETA = "TARJETA", "Tarjeta"
    TRANSFERENCIA = "TRANSF", "Transferencia"
    CREDITO = "CREDITO", "Crédito"


RFC_GENERICO = "XAXX010101000"  # público en general (SAT)
rfc_valido = RegexValidator(r"^[A-ZÑ&]{3,4}\d{6}[A-Z0-9]{3}$",
                            "El RFC va como XAXX010101000: 3 o 4 letras, 6 números de la fecha y 3 de homoclave.")
cp_valido = RegexValidator(r"^\d{5}$", "El código postal lleva 5 números.")


def texto_busqueda(*partes):
    """Minúsculas, sin acentos ni signos, para buscar como sea que se teclee: «Taller Ramírez, S.A.» → «taller ramirez s a»."""
    t = unicodedata.normalize("NFKD", " ".join(p or "" for p in partes).casefold())
    t = "".join(c for c in t if not unicodedata.combining(c))
    return " ".join(re.sub(r"[^0-9a-z&]+", " ", t).split())


def solo_digitos(texto):
    return re.sub(r"\D", "", texto or "")


class Cliente(models.Model):
    nombre = models.CharField(max_length=150)
    rfc = models.CharField("RFC", max_length=13, default=RFC_GENERICO, validators=[rfc_valido],
                           help_text=f"Si no da RFC se queda el genérico {RFC_GENERICO}.")
    telefono = models.CharField(max_length=20, blank=True)
    correo = models.EmailField(blank=True, help_text="Para mandarle cotizaciones")
    calle = models.CharField(max_length=120, blank=True)
    numero_exterior = models.CharField("núm. exterior", max_length=20, blank=True)
    numero_interior = models.CharField("núm. interior", max_length=20, blank=True)
    codigo_postal = models.CharField("código postal", max_length=5, blank=True, validators=[cp_valido],
                                     help_text="Al teclearlo se llenan estado y municipio y se eligen las colonias.")
    colonia = models.CharField(max_length=120, blank=True)
    municipio = models.CharField("municipio o alcaldía", max_length=120, blank=True)
    estado = models.CharField(max_length=60, blank=True)
    lista_precio = models.ForeignKey(ListaPrecio, on_delete=models.PROTECT, help_text="Mostrador, Taller…")
    dias_credito = models.PositiveSmallIntegerField(default=0, help_text="0 = solo contado")
    limite_credito = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    activo = models.BooleanField(default=True)
    busqueda = models.CharField(max_length=300, blank=True, editable=False,
                                help_text="Nombre, RFC y teléfono sin acentos ni signos, para el buscador")

    class Meta:
        ordering = ["nombre"]

    def __str__(self):
        return self.nombre

    @property
    def folio(self):
        """Número de cliente para buscarlo en mostrador y remisiones: CTE-0012."""
        return f"CTE-{self.pk:04d}" if self.pk else ""

    def save(self, *args, **kwargs):
        self.rfc = "".join((self.rfc or "").upper().split()) or RFC_GENERICO
        self.busqueda = f"{texto_busqueda(self.nombre, self.rfc)} {solo_digitos(self.telefono)}".strip()[:300]
        campos = kwargs.get("update_fields")
        if campos is not None and {"nombre", "rfc", "telefono"} & set(campos):
            kwargs["update_fields"] = {*campos, "busqueda"}
        super().save(*args, **kwargs)

    def clean(self):
        super().clean()
        rfc = "".join((self.rfc or "").upper().split())
        if rfc and rfc != RFC_GENERICO:
            otro = Cliente.objects.filter(rfc=rfc, activo=True).exclude(pk=self.pk).first()
            if otro:
                raise ValidationError({"rfc": f"Ese RFC ya es de {otro} ({otro.folio})."})

    @property
    def rfc_generico(self):
        return self.rfc == RFC_GENERICO

    @property
    def domicilio(self):
        """Calle y número en una línea, y colonia, CP, municipio y estado en otra (las que haya)."""
        numero = " ".join(x for x in (self.numero_exterior, f"int. {self.numero_interior}" if self.numero_interior else "") if x)
        calle = " ".join(x for x in (self.calle, numero) if x)
        resto = ", ".join(x for x in (f"Col. {self.colonia}" if self.colonia else "",
                                      f"C.P. {self.codigo_postal}" if self.codigo_postal else "",
                                      self.municipio, self.estado) if x)
        return [x for x in (calle, resto) if x]

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
    abierto_por = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT,
                                    related_name="+", help_text="Quién tecleó su NIP al abrir")
    cerrado_por = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT,
                                    related_name="+", help_text="Quién tecleó su NIP al hacer el corte")

    class Meta:
        ordering = ["-abierto"]
        constraints = [models.UniqueConstraint(fields=["usuario"], condition=models.Q(cerrado__isnull=True),
                                               name="un_turno_abierto_por_usuario")]

    def __str__(self):
        return f"Turno {self.pk} · {self.usuario} · {self.abierto:%d/%m %H:%M}"


class Venta(models.Model):
    """Una venta del mostrador (folio VTA) o una remisión hecha en Remisiones › Nueva (folio REM)."""

    class Estado(models.TextChoices):
        PAGADA = "PAGADA", "Pagada"
        CREDITO = "CREDITO", "A crédito"
        POR_COBRAR = "POR_COBRAR", "Por cobrar en caja"
        CANCELADA = "CANCELADA", "Cancelada"

    class Origen(models.TextChoices):
        MOSTRADOR = "MOSTRADOR", "Mostrador"
        REMISION = "REMISION", "Remisión"

    folio = models.CharField(max_length=20, unique=True)
    fecha = models.DateTimeField(auto_now_add=True, db_index=True)
    origen = models.CharField(max_length=10, choices=Origen.choices, default=Origen.MOSTRADOR)
    turno = models.ForeignKey(Turno, on_delete=models.PROTECT, related_name="ventas", null=True, blank=True,
                              help_text="Turno de caja donde entró el dinero; vacío en remisiones a crédito o sin cobrar")
    cliente = models.ForeignKey(Cliente, on_delete=models.PROTECT, related_name="ventas")
    lista = models.ForeignKey(ListaPrecio, on_delete=models.PROTECT)
    forma_pago = models.CharField(max_length=10, choices=FormaPago.choices, blank=True,
                                  help_text="Vacía mientras una remisión de contado está por cobrar en caja")
    estado = models.CharField(max_length=10, choices=Estado.choices)
    total = models.DecimalField(max_digits=12, decimal_places=2, default=0, help_text="Con IVA")
    iva = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    costo_total = models.DecimalField(max_digits=12, decimal_places=2, default=0, help_text="Sin IVA")
    saldo = models.DecimalField(max_digits=12, decimal_places=2, default=0, help_text="Pendiente de cobro si es a crédito")
    vence = models.DateField(null=True, blank=True)
    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT)
    vehiculo = models.CharField(max_length=120, blank=True, help_text="Vehículo atendido en mostrador, para la remisión")
    pedido = models.JSONField(default=list, blank=True,
                              help_text="Lo que quedó sobre pedido: [{codigo, descripcion, cantidad, precio, importe}]")
    cancelada = models.DateTimeField(null=True, blank=True)
    cancelada_por = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT,
                                      related_name="+")
    motivo_cancelacion = models.CharField(max_length=200, blank=True)
    orden_compra = models.CharField("orden de compra del cliente", max_length=40, blank=True)
    observaciones = models.CharField(max_length=300, blank=True)
    cobrada = models.DateTimeField(null=True, blank=True, help_text="Cuándo se cobró en caja una remisión de contado")
    cobrada_por = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT,
                                    related_name="+")

    class Meta:
        ordering = ["-fecha"]

    def __str__(self):
        return self.folio

    @property
    def es_remision(self):
        return self.origen == self.Origen.REMISION

    @property
    def condiciones(self):
        """Contado o crédito, aunque la de contado todavía no se cobre."""
        return "Crédito" if self.forma_pago == FormaPago.CREDITO else "Contado"

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
    precio_unitario = models.DecimalField(max_digits=12, decimal_places=2,
                                          help_text="Con IVA, por la unidad vendida, ya con el descuento")
    descuento = models.DecimalField(max_digits=5, decimal_places=2, default=0,
                                    help_text="% de descuento que ya trae el precio unitario")
    importe = models.DecimalField(max_digits=12, decimal_places=2)
    costo_unitario = models.DecimalField(max_digits=12, decimal_places=4, help_text="Costo promedio sin IVA al vender")


class Abono(models.Model):
    venta = models.ForeignKey(Venta, on_delete=models.PROTECT, related_name="abonos")
    turno = models.ForeignKey(Turno, on_delete=models.PROTECT, related_name="abonos")
    fecha = models.DateTimeField(auto_now_add=True)
    monto = models.DecimalField(max_digits=12, decimal_places=2)
    forma_pago = models.CharField(max_length=10, choices=[c for c in FormaPago.choices if c[0] != FormaPago.CREDITO])
    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+",
                                help_text="Quién lo recibió (su NIP)")


class TicketGuardado(models.Model):
    """Un ticket guardado para después: una cotización que se imprime o un ticket en espera mientras
    se atiende a otro cliente. Guarda los renglones tal cual (para regresarlos al ticket) y una copia
    con descripción y precio del momento (para imprimir la cotización aunque luego cambien los precios)."""

    class Tipo(models.TextChoices):
        COTIZACION = "COT", "Cotización"
        ESPERA = "ESP", "En espera"

    tipo = models.CharField(max_length=3, choices=Tipo.choices)
    folio = models.CharField(max_length=20, unique=True, null=True, blank=True)
    fecha = models.DateTimeField(auto_now_add=True, db_index=True)
    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    cliente = models.ForeignKey(Cliente, on_delete=models.PROTECT, related_name="guardados")
    nombre = models.CharField(max_length=150, blank=True, help_text="A nombre de quién, si es público en general")
    vehiculo = models.CharField(max_length=120, blank=True)
    vehiculo_params = models.JSONField(default=dict, blank=True, help_text="armadora, anio, modelo, litros")
    lineas = models.JSONField(default=list, help_text="Renglones del ticket como estaban en la sesión")
    detalle = models.JSONField(default=list, help_text="[{i, codigo, descripcion, cantidad, precio, importe, pedido}]")
    total = models.DecimalField(max_digits=12, decimal_places=2, default=0, help_text="Con IVA, incluye lo sobre pedido")
    retomado = models.DateTimeField(null=True, blank=True, help_text="Cuando se pasó al ticket o se descartó")

    class Meta:
        ordering = ["-fecha"]

    def __str__(self):
        return self.folio or f"{self.get_tipo_display()} {self.pk}"

    @property
    def para(self):
        return self.nombre or str(self.cliente)

    @property
    def vence(self):
        from datetime import timedelta

        from django.utils import timezone

        return timezone.localtime(self.fecha).date() + timedelta(days=settings.COTIZACION_VIGENCIA_DIAS)


class PedidoCliente(models.Model):
    """Lo que un cliente dejó sobre pedido en mostrador: se pidió al proveedor y se cobra al entregar.
    Sirve para avisarle cuando llegue y para no olvidar ninguno."""

    class Estado(models.TextChoices):
        PEDIDO = "PEDIDO", "Pendiente"
        ENTREGADO = "ENTREGADO", "Entregado"
        CANCELADO = "CANCELADO", "Cancelado"

    fecha = models.DateTimeField(auto_now_add=True, db_index=True)
    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    cliente = models.ForeignKey(Cliente, on_delete=models.PROTECT, related_name="pedidos")
    contacto = models.CharField(max_length=120, blank=True, help_text="Nombre o teléfono para avisarle")
    venta = models.ForeignKey(Venta, null=True, blank=True, on_delete=models.SET_NULL, related_name="pedidos_cliente",
                              help_text="La venta de ese día, si se llevó algo más")
    vehiculo = models.CharField(max_length=120, blank=True)
    producto = models.ForeignKey(Producto, on_delete=models.PROTECT, related_name="+")
    linea_oc = models.ForeignKey("compras.OrdenCompraLinea", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    codigo = models.CharField(max_length=60)
    descripcion = models.CharField(max_length=200, blank=True)
    cantidad = models.DecimalField(max_digits=12, decimal_places=3)
    precio = models.DecimalField(max_digits=12, decimal_places=2, help_text="Con IVA por unidad, el que se le dijo al cliente")
    estado = models.CharField(max_length=10, choices=Estado.choices, default=Estado.PEDIDO, db_index=True)
    cerrado = models.DateTimeField(null=True, blank=True, help_text="Cuando se entregó o se canceló")
    venta_entrega = models.ForeignKey(Venta, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        ordering = ["fecha"]

    def __str__(self):
        return f"{self.codigo} × {self.cantidad} para {self.cliente}"

    @property
    def importe(self):
        return self.precio * self.cantidad

    @property
    def hay(self):
        return self.producto.existencia >= self.cantidad


class Nip(models.Model):
    """NIP de 4 a 6 dígitos con el que cada persona firma lo que hace en mostrador y caja (cobrar, abonar,
    abrir y cerrar caja) y con el que un administrador autoriza cancelar, devolver o dar crédito.
    Se guarda su huella (HMAC con la llave del sistema), no el número; no hay dos iguales porque el NIP
    solo dice quién es. Los asigna el administrador en Administración."""

    usuario = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="nip")
    huella = models.CharField(max_length=64, unique=True)
    cambiado = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "NIP"
        verbose_name_plural = "NIPs"

    def __str__(self):
        return f"NIP de {self.usuario}"


class Devolucion(models.Model):
    """Devolución o garantía de piezas de una venta ya cobrada; por ejemplo de un turno cerrado, que ya no se cancela.
    Devolución: la pieza regresa (al anaquel si está en buen estado) y se descuenta del saldo si la venta es a
    crédito; lo demás se le regresa en dinero desde la caja abierta y sale en ese corte.
    Garantía: la pieza salió mala; se le da otra igual sin cobrar y la mala queda para reclamarla al proveedor.
    Las dos las autoriza un administrador con su NIP."""

    class Tipo(models.TextChoices):
        DEVOLUCION = "DEV", "Devolución"
        GARANTIA = "GAR", "Garantía"

    class Reclamo(models.TextChoices):
        PENDIENTE = "PENDIENTE", "Por reclamar al proveedor"
        RECLAMADA = "RECLAMADA", "Reclamada al proveedor"
        REPUESTA = "REPUESTA", "El proveedor la repuso"
        RECHAZADA = "RECHAZADA", "El proveedor la rechazó"

    folio = models.CharField(max_length=20, unique=True)
    tipo = models.CharField(max_length=3, choices=Tipo.choices)
    fecha = models.DateTimeField(auto_now_add=True, db_index=True)
    venta = models.ForeignKey(Venta, on_delete=models.PROTECT, related_name="devoluciones")
    turno = models.ForeignKey(Turno, null=True, blank=True, on_delete=models.PROTECT, related_name="devoluciones",
                              help_text="Caja de la que salió el reembolso")
    autorizo = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    motivo = models.CharField(max_length=200)
    importe = models.DecimalField(max_digits=12, decimal_places=2, help_text="Valor de las piezas, con IVA")
    a_saldo = models.DecimalField(max_digits=12, decimal_places=2, default=0, help_text="Lo que se descontó del saldo")
    reembolso = models.DecimalField(max_digits=12, decimal_places=2, default=0, help_text="Lo que salió de la caja")
    forma_reembolso = models.CharField(max_length=10, blank=True,
                                       choices=[c for c in FormaPago.choices if c[0] != FormaPago.CREDITO])
    regresa_anaquel = models.BooleanField(default=True, help_text="Devolución: la pieza estaba bien y se vuelve a vender")
    reclamo = models.CharField(max_length=10, choices=Reclamo.choices, blank=True, help_text="Garantía: en qué va con el proveedor")

    class Meta:
        ordering = ["-fecha"]
        verbose_name = "devolución o garantía"
        verbose_name_plural = "devoluciones y garantías"

    def __str__(self):
        return self.folio


class DevolucionLinea(models.Model):
    devolucion = models.ForeignKey(Devolucion, on_delete=models.CASCADE, related_name="lineas")
    venta_linea = models.ForeignKey(VentaLinea, on_delete=models.PROTECT, related_name="devuelto")
    cantidad = models.DecimalField(max_digits=12, decimal_places=3, help_text="En la unidad en que se vendió")
    importe = models.DecimalField(max_digits=12, decimal_places=2)


class CodigoPostal(models.Model):
    """Catálogo Nacional de Códigos Postales de Correos de México (SEPOMEX): una fila por colonia.
    Se carga con `manage.py cargar_codigos_postales`."""

    cp = models.CharField(max_length=5, db_index=True)
    colonia = models.CharField(max_length=150)
    tipo = models.CharField(max_length=40, blank=True, help_text="Colonia, Fraccionamiento, Barrio…")
    municipio = models.CharField(max_length=120)
    estado = models.CharField(max_length=60)
    ciudad = models.CharField(max_length=120, blank=True)

    class Meta:
        ordering = ["cp", "colonia"]
        verbose_name = "código postal"
        verbose_name_plural = "códigos postales"

    def __str__(self):
        return f"{self.cp} {self.colonia}, {self.municipio}, {self.estado}"


class RemisionBorrador(models.Model):
    """Remisión que se está armando en Remisiones › Nueva. Se guarda tal cual se tecleó para seguirla
    después; al emitirla se vuelve una Venta con folio REM y el borrador se borra."""

    class Condiciones(models.TextChoices):
        CONTADO = "CONTADO", "Contado"
        CREDITO = "CREDITO", "Crédito"

    creado = models.DateTimeField(auto_now_add=True)
    actualizado = models.DateTimeField(auto_now=True)
    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    cliente = models.ForeignKey(Cliente, null=True, blank=True, on_delete=models.PROTECT, related_name="borradores")
    condiciones = models.CharField(max_length=10, choices=Condiciones.choices, default=Condiciones.CONTADO)
    orden_compra = models.CharField(max_length=40, blank=True)
    vehiculo = models.CharField(max_length=120, blank=True)
    observaciones = models.CharField(max_length=300, blank=True)
    lineas = models.JSONField(default=list, blank=True,
                              help_text="[{clave, codigo, descripcion, existencia, cantidad, precio, descuento}] como se teclearon")
    total = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    class Meta:
        ordering = ["-actualizado"]
        verbose_name = "borrador de remisión"
        verbose_name_plural = "borradores de remisión"

    def __str__(self):
        return f"Borrador {self.pk}"
