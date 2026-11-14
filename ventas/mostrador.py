"""Lógica de la pantalla de mostrador: vehículo elegido → piezas de la afinación → tres niveles → ticket.

Las opciones de cada pieza salen de dos lados:
  1. Mis productos (catálogo) que le quedan al vehículo, por su nivel Económico / Estándar / Premium.
  2. Las partidas de la lista del proveedor que aún no surto. Se reparten en tercios por costo
     (la más barata es Económico) y se ofrecen "sobre pedido".
Mis productos mandan: una partida solo llena un nivel que mi catálogo deja vacío.
"""

from dataclasses import dataclass
from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from catalogo.models import ListaPrecio, Precio, Producto
import re

from compras.aplicaciones import ARMADORAS, coincide
from compras.listas import agregar_a_catalogo
from compras.models import OrdenCompra, OrdenCompraLinea, PartidaListaProveedor

from .models import Cliente, FormaPago
from .servicios import registrar_venta

NIVELES = [("ECO", "Económico"), ("STD", "Estándar"), ("PRE", "Premium")]
IVA = Decimal("1.16")


@dataclass(frozen=True)
class Posicion:
    clave: str
    n: int
    nombre: str
    familia: str  # familia de mi catálogo
    tipo_partida: str | None  # tipo en la lista del proveedor (None: el proveedor no lo trae)
    cantidad: Decimal  # lo que normalmente lleva
    x: float  # lugar del número en el diagrama del motor, en %
    y: float
    generica: bool = False  # le queda a cualquier vehículo (aceite de motor): no se filtra por aplicación


POSICIONES = [
    Posicion("aceite", 1, "Filtro de aceite", "Filtros de aceite", "ACEITE", Decimal(1), 58.9, 90.9),
    Posicion("aire", 2, "Filtro de aire", "Filtros de aire", "AIRE", Decimal(1), 12.9, 29.1),
    Posicion("gasolina", 3, "Filtro de gasolina", "Filtros de gasolina", "GASOLINA", Decimal(1), 83, 15),
    Posicion("bujias", 4, "Bujías", "Bujías", None, Decimal(4), 48.6, 48.6),
    Posicion("motor", 5, "Aceite de motor", "Aceites de motor", None, Decimal(4), 70, 31.8, generica=True),
]
POR_CLAVE = {p.clave: p for p in POSICIONES}


def sobreprecio():
    """Precio sugerido de lo que se vende sobre pedido = costo × (1 + sobreprecio) + IVA."""
    return Decimal(str(getattr(settings, "SOBREPRECIO_PEDIDO", "0.37")))


def precio_sugerido(costo):
    return (Decimal(costo) * (1 + sobreprecio()) * IVA).quantize(Decimal(1), ROUND_HALF_UP)


@dataclass
class Vehiculo:
    anio: int
    armadora: str
    modelo: str
    litros: str = ""

    def q(self, prefijo=""):
        return coincide(self.anio, self.armadora, self.modelo, self.litros or None, prefijo)

    def __str__(self):
        return f"{self.armadora} {self.modelo} {self.anio}" + (f" · {self.litros} L" if self.litros else "")


@dataclass
class Opcion:
    clave: str  # "producto:12" o "partida:345"
    codigo: str
    marca: str
    existencia: Decimal | None  # None: no lo surto
    precio: Decimal | None  # con IVA, por unidad
    unidad: str
    rango: str  # "Tsuru 1990–2004": de dónde sale que le queda
    nivel: str = ""
    proveedor: str = ""
    advertencia: str = ""  # "CAJA AIRE AUX": lo que el proveedor escribe antes de la armadora

    @property
    def sobre_pedido(self):
        return not self.existencia or self.existencia <= 0


def _rango(filas):
    f = filas[0] if filas else None
    return f"{f.modelo} {f.anio_desde}–{f.anio_hasta}" if f else ""


def _de_producto(p, lista, veh, pos):
    precio = Precio.objects.filter(producto=p, lista=lista, unidad=p.unidad_base).values_list("precio", flat=True).first()
    filas = [a.vehiculo for a in p.aplicaciones.select_related("vehiculo").filter(veh.q("vehiculo__"))]
    if not filas:
        for partida in p.partidas_proveedor.all():
            filas = list(partida.vehiculos.filter(veh.q()))
            if filas:
                break
    prov = p.proveedores.select_related("proveedor").order_by("-preferente", "costo").first()
    return Opcion(f"producto:{p.pk}", p.numero_parte or p.sku, p.marca.nombre, p.existencia, precio,
                  p.unidad_base.clave, "Cualquier vehículo" if pos.generica else _rango(filas), p.nivel,
                  prov.proveedor.nombre if prov else "")


_ARMADORA_RE = re.compile(r"\b(" + "|".join(sorted(map(re.escape, ARMADORAS), key=len, reverse=True)) + r")\b")


def _advertencia(texto):
    """Lo que va antes de la primera armadora suele ser una aclaración importante
    ("CAJA AIRE AUX", "TRAS.", "(CARTUCHO)"). Se muestra en la tarjeta para no confundir la pieza."""
    texto = (texto or "").upper()
    m = _ARMADORA_RE.search(texto)
    previo = texto[: m.start()].strip(" .-,()") if m else ""
    return previo if len(previo) > 2 and not previo.startswith(("FILTRO PARA", "F AIRE", "F ACEITE")) else ""


def _de_partida(p, veh):
    return Opcion(f"partida:{p.pk}", p.codigo, p.marca.title() if p.marca.isupper() else p.marca, None,
                  precio_sugerido(p.costo), "PZA", _rango(list(p.vehiculos.filter(veh.q()))), "", p.proveedor.nombre,
                  _advertencia(p.aplicacion))


def _tercios(partidas):
    """Reparte por costo (ya ordenadas de menor a mayor): 1 → Estándar, 2 → Económico y Premium."""
    n = len(partidas)
    if n == 0:
        return {}
    if n == 1:
        return {"STD": partidas}
    if n == 2:
        return {"ECO": partidas[:1], "PRE": partidas[1:]}
    a, b = round(n / 3), round(2 * n / 3)
    return {"ECO": partidas[:a], "STD": partidas[a:b], "PRE": partidas[b:]}


def opciones_de(veh, pos, lista):
    """Devuelve ({nivel: Opcion | None}, [otras opciones]) para una pieza del vehículo."""
    productos = Producto.objects.filter(activo=True, familia__nombre=pos.familia).select_related("marca", "unidad_base")
    if not pos.generica:
        productos = productos.filter(veh.q("aplicaciones__vehiculo__") | veh.q("partidas_proveedor__vehiculos__"))
    productos = list(productos.distinct())
    partidas = []
    if pos.tipo_partida:
        partidas = list(PartidaListaProveedor.objects.filter(tipo=pos.tipo_partida, producto__isnull=True,
                                                             costo__isnull=False)
                        .filter(veh.q("vehiculos__")).distinct().select_related("proveedor").order_by("costo", "codigo"))

    tarjetas, otras, usados = {k: None for k, _ in NIVELES}, [], set()
    for k, _ in NIVELES:
        mios = sorted((p for p in productos if p.nivel == k), key=lambda p: (p.existencia <= 0, p.sku))
        if mios:
            tarjetas[k] = _de_producto(mios[0], lista, veh, pos)
            usados.add(mios[0].pk)
    for k, grupo in _tercios(partidas).items():
        if tarjetas[k] is None:
            tarjetas[k] = _de_partida(grupo[0], veh)
            usados.add(("partida", grupo[0].pk))
    otras += [_de_producto(p, lista, veh, pos) for p in productos if p.pk not in usados]
    otras += [_de_partida(p, veh) for p in partidas if ("partida", p.pk) not in usados]
    return tarjetas, otras


def resolver(clave):
    """'producto:12' → Producto; 'partida:5' → PartidaListaProveedor."""
    tipo, _, pk = clave.partition(":")
    modelo = {"producto": Producto, "partida": PartidaListaProveedor}.get(tipo)
    if not modelo or not pk.isdigit():
        raise ValidationError("Opción inválida.")
    return modelo.objects.get(pk=pk)


# ---------------------------------------------------------------------------------------------
# Ticket en la sesión
# ---------------------------------------------------------------------------------------------

def cliente_mostrador():
    lista, _ = ListaPrecio.objects.get_or_create(nombre="Mostrador")
    cliente, _ = Cliente.objects.get_or_create(nombre="Público en general", defaults={"lista_precio": lista})
    return cliente


@dataclass
class Linea:
    i: int
    pos: Posicion | None
    objeto: object  # Producto o PartidaListaProveedor
    opcion: Opcion
    cantidad: Decimal
    nivel: str = ""

    @property
    def sobre_pedido(self):
        return self.opcion.existencia is None or self.opcion.existencia < self.cantidad

    @property
    def importe(self):
        return (self.opcion.precio or 0) * self.cantidad

    @property
    def fraccion(self):
        return isinstance(self.objeto, Producto) and self.objeto.unidad_base.permite_fraccion


class Ticket:
    def __init__(self, session):
        self.session = session
        self.datos = session.get("ticket") or {"cliente": None, "lineas": []}

    def guardar(self):
        self.session["ticket"] = self.datos
        self.session.modified = True

    @property
    def cliente(self):
        c = Cliente.objects.filter(pk=self.datos["cliente"], activo=True).first() if self.datos["cliente"] else None
        return c or cliente_mostrador()

    def elegir_cliente(self, pk):
        self.datos["cliente"] = Cliente.objects.get(pk=pk, activo=True).pk
        self.guardar()

    def agregar(self, clave, pos=None, cantidad=None, nivel=""):
        resolver(clave)  # valida que exista
        p = POR_CLAVE.get(pos)
        cantidad = str(cantidad or (p.cantidad if p else 1))
        nivel = nivel if nivel in dict(NIVELES) else ""
        lineas = [ln for ln in self.datos["lineas"] if not (p and ln["pos"] == p.clave)]  # una opción por pieza
        lineas.append({"clave": clave, "pos": p.clave if p else "", "cantidad": cantidad, "nivel": nivel})
        self.datos["lineas"] = lineas
        self.guardar()

    def quitar(self, i):
        if 0 <= i < len(self.datos["lineas"]):
            del self.datos["lineas"][i]
            self.guardar()

    def cambiar_cantidad(self, i, cantidad):
        cantidad = Decimal(str(cantidad))
        if cantidad <= 0:
            return self.quitar(i)
        if 0 <= i < len(self.datos["lineas"]):
            self.datos["lineas"][i]["cantidad"] = str(cantidad)
            self.guardar()

    def vaciar(self):
        self.datos["lineas"] = []
        self.guardar()

    def lineas(self, veh=None):
        lista = self.cliente.lista_precio
        res = []
        for i, ln in enumerate(self.datos["lineas"]):
            try:
                obj = resolver(ln["clave"])
            except (ValidationError, Producto.DoesNotExist, PartidaListaProveedor.DoesNotExist):
                continue
            pos = POR_CLAVE.get(ln["pos"])
            v = veh or Vehiculo(0, "", "")
            if isinstance(obj, Producto):
                op = _de_producto(obj, lista, v, pos or POSICIONES[-1])
            else:
                op = _de_partida(obj, v)
            res.append(Linea(i, pos, obj, op, Decimal(ln["cantidad"]), ln.get("nivel", "")))
        return res

    def por_posicion(self):
        return {ln["pos"]: ln for ln in self.datos["lineas"] if ln["pos"]}


# ---------------------------------------------------------------------------------------------
# Cobrar: lo que hay se vende, lo que no hay se pide al proveedor
# ---------------------------------------------------------------------------------------------

def _orden_emergencia(proveedor, usuario):
    hoy = timezone.localdate()
    oc = OrdenCompra.objects.filter(proveedor=proveedor, estado=OrdenCompra.Estado.BORRADOR, es_emergencia=True,
                                    fecha=hoy).first()
    if oc is None:
        oc = OrdenCompra.objects.create(folio=f"TMP-{timezone.now().timestamp()}", proveedor=proveedor,
                                        es_emergencia=True, creada_por=usuario if usuario and usuario.pk else None,
                                        fecha_esperada=hoy + timedelta(days=proveedor.tiempo_entrega_dias or 1))
        oc.folio = f"OC-{oc.pk:05d}"
        oc.save(update_fields=["folio"])
    return oc


def _producto_para_pedir(linea):
    """Si es partida del proveedor, la doy de alta en mi catálogo con el nivel en que se ofreció y el
    precio que se cotizó, para que al llegar se pueda vender igual."""
    obj = linea.objeto
    if isinstance(obj, PartidaListaProveedor):
        producto = agregar_a_catalogo(obj)
        if linea.nivel:
            producto.nivel = linea.nivel
            producto.save(update_fields=["nivel"])
        for lista in ListaPrecio.objects.all():
            Precio.objects.get_or_create(lista=lista, producto=producto, unidad=producto.unidad_base,
                                         defaults={"precio": linea.opcion.precio})
        return producto, obj.proveedor, obj.costo
    pp = obj.proveedores.select_related("proveedor").order_by("-preferente", "costo").first()
    if pp is None:
        raise ValidationError(f"{obj.sku} no tiene existencia ni proveedor para pedirlo.")
    return obj, pp.proveedor, pp.costo


@transaction.atomic
def cobrar(ticket, turno, forma_pago, usuario, veh=None):
    """Registra la venta de lo que hay en anaquel y una orden de compra de emergencia (borrador)
    por lo que va sobre pedido. Lo sobre pedido no se cobra hoy: se cobra al entregarse.
    Devuelve (venta | None, [órdenes])."""
    lineas = ticket.lineas(veh)
    if not lineas:
        raise ValidationError("El ticket está vacío.")
    cliente = ticket.cliente
    if forma_pago == FormaPago.CREDITO and cliente.dias_credito <= 0:
        raise ValidationError(f"{cliente} no tiene crédito autorizado.")
    vender = [ln for ln in lineas if not ln.sobre_pedido]
    for ln in vender:
        if ln.opcion.precio is None:
            raise ValidationError(f"{ln.opcion.codigo} no tiene precio en la lista {cliente.lista_precio}.")
    venta = None
    if vender:
        venta = registrar_venta(turno, cliente, [{"producto": ln.objeto, "cantidad": ln.cantidad} for ln in vender],
                                forma_pago, usuario)
    ordenes = []
    for ln in (ln for ln in lineas if ln.sobre_pedido):
        producto, proveedor, costo = _producto_para_pedir(ln)
        oc = _orden_emergencia(proveedor, usuario)
        OrdenCompraLinea.objects.create(orden=oc, producto=producto, unidad=producto.unidad_base,
                                        cantidad=ln.cantidad, costo_unitario=costo or 0)
        nota = f"Mostrador: {cliente}" + (f" · {veh}" if veh else "") + (f" · {venta.folio}" if venta else "")
        if nota not in oc.notas:
            oc.notas = (oc.notas + "\n" + nota).strip()
            oc.save(update_fields=["notas"])
        if oc not in ordenes:
            ordenes.append(oc)
    ticket.vaciar()
    return venta, ordenes
