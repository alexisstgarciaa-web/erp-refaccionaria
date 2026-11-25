"""Turnos de clientes: la fila (primero los registrados), a quién llama cada vendedor, lo que la caja tiene por
cobrar agrupado por turno y lo que sale en la pantalla de llamados.
En el código el turno del cliente se llama «pase», porque «turno» ya es el de caja."""

from dataclasses import dataclass, field
from datetime import timedelta
from decimal import Decimal

from django.core.cache import caches
from django.core.exceptions import ValidationError
from django.db.models import Prefetch
from django.utils import timezone
from django.utils.crypto import salted_hmac

from .clientes import pk_de_folio
from .models import Cliente, Pase, Venta, solo_digitos, texto_busqueda
from .servicios import dar_pase

MINUTOS_PAGADO = 20  # cuánto se queda «ya pagó · entregar» en el mostrador
LLAMADOS = 5  # cuántos llamados recientes salen en la pantalla


def cliente_de_numero(texto):
    """El cliente registrado con ese número (CTE-0012 o 12) o ese celular de 10 dígitos. None si no hay nada
    escrito; ValidationError si lo escrito no es de un cliente (o el celular es de varios).
    Solo coincidencias exactas: la pantalla de turnos la usa el cliente y no debe servir para ver a los demás."""
    texto = " ".join((texto or "").split())
    if not texto:
        return None
    digitos = solo_digitos(texto)
    pk = pk_de_folio(texto)
    if pk is None and digitos and len(digitos) <= 6 and texto.replace(" ", "").isdigit():
        pk = int(digitos)
    if pk is not None:
        encontrados = list(Cliente.objects.filter(pk=pk, activo=True)[:1])
    elif len(digitos) >= 10:
        encontrados = list(Cliente.objects.filter(activo=True, busqueda__endswith=digitos[-10:])[:2])
    else:
        encontrados = []
    publico = _publico()
    encontrados = [c for c in encontrados if c.pk != publico]
    if len(encontrados) != 1:
        raise ValidationError("No encontramos ese número de cliente ni ese celular. Revísalo o toma tu turno sin número.")
    return encontrados[0]


def tomar(numero_cliente="", nombre=""):
    """El cliente toma su turno al llegar. Con su número de cliente queda ligado a él y pasa antes."""
    pase = dar_pase(nombre, cliente=cliente_de_numero(numero_cliente))
    refrescar_pantalla()
    return pase


def en_espera():
    """La fila de hoy: primero los clientes registrados y luego los demás, cada grupo en su orden de llegada."""
    pases = Pase.objects.filter(dia=timezone.localdate(), llamado__isnull=True, cerrado__isnull=True,
                                ventas__isnull=True).select_related("cliente")
    return sorted(pases, key=lambda p: (p.cliente_id is None, p.numero))


def llamar(pase, usuario):
    """Un vendedor llama ese turno, o lo vuelve a llamar si ya era suyo: sale en la pantalla de llamados.
    Si otro vendedor lo llamó antes, ValidationError."""
    if pase.cerrado or pase.dia != timezone.localdate():
        raise ValidationError(f"El turno {pase.numero} ya no está en la fila.")
    usuario = usuario if usuario and usuario.pk else None
    ahora = timezone.now()
    # lo toma solo si nadie lo ha llamado (o si ya era de esta misma sesión de mostrador)
    libres = Pase.objects.filter(pk=pase.pk, cerrado__isnull=True)
    tomado = (libres.filter(llamado__isnull=True) | libres.filter(atiende=usuario)).update(llamado=ahora, atiende=usuario)
    if not tomado:
        pase.refresh_from_db()
        raise ValidationError(f"El turno {pase.numero} ya lo llamó {_persona(pase.atiende) or 'otro vendedor'}.")
    pase.llamado, pase.atiende = ahora, usuario
    refrescar_pantalla()
    return pase


def llamar_siguiente(usuario):
    """Llama al que sigue en la fila (primero los clientes registrados). None si no hay nadie esperando."""
    for pase in en_espera():
        try:
            return llamar(pase, usuario)
        except ValidationError:
            continue  # otro vendedor lo llamó en ese instante: va el que sigue
    return None


def cerrar(pase):
    """No llegó o se fue sin comprar: sale de la fila. Con remisiones ya no se cierra (eso lo resuelve la caja)."""
    if pase.ventas.exists():
        raise ValidationError(f"El turno {pase.numero} ya tiene remisiones: se cobra en caja o las cancela un administrador.")
    if not pase.cerrado:
        pase.cerrado = timezone.now()
        pase.save(update_fields=["cerrado"])
        refrescar_pantalla()
    return pase


def _publico():
    from .mostrador import cliente_mostrador

    return cliente_mostrador().pk


def _nombre(pase, ventas, publico):
    """Cómo reconocer al del turno: el cliente registrado, el nombre que dio o el cliente de sus remisiones."""
    if pase is not None and pase.quien:
        return pase.quien
    return next((str(v.cliente) for v in ventas if v.cliente_id != publico), "")


def _persona(usuario):
    return (usuario.get_short_name() or usuario.get_username()) if usuario else ""


def de_hoy():
    """Los turnos de hoy por dónde van: espera (en orden de llamado), atendiendo, caja y pagados (los que pagaron
    hace poco, para entregarles). Cada pase lleva .ventas_vivas, .nombre_visible, .total_caja y .pagado."""
    hoy, ahora = timezone.localdate(), timezone.now()
    vivas = Venta.objects.exclude(estado=Venta.Estado.CANCELADA).select_related("cliente", "usuario")
    pases = Pase.objects.filter(dia=hoy).select_related("cliente", "atiende").prefetch_related(
        Prefetch("ventas", queryset=vivas, to_attr="ventas_vivas"))
    res = {"espera": [], "atendiendo": [], "caja": [], "pagados": []}
    publico = _publico()
    for p in pases:
        p.nombre_visible = _nombre(p, p.ventas_vivas, publico)
        p.con = _persona(p.atiende)
        por_cobrar = [v for v in p.ventas_vivas if v.estado == Venta.Estado.POR_COBRAR]
        if por_cobrar:
            p.total_caja = sum((v.total for v in por_cobrar), Decimal(0))
            res["caja"].append(p)
        elif p.ventas_vivas:
            p.pagado = max((v.cobrada or v.fecha) for v in p.ventas_vivas)
            if ahora - p.pagado <= timedelta(minutes=MINUTOS_PAGADO):
                res["pagados"].append(p)
        elif p.cerrado:
            continue
        elif p.llamado:
            res["atendiendo"].append(p)
        else:
            res["espera"].append(p)
    res["espera"].sort(key=lambda p: (p.cliente_id is None, p.numero))
    res["atendiendo"].sort(key=lambda p: p.llamado)
    res["pagados"].sort(key=lambda p: p.pagado, reverse=True)
    return res


@dataclass
class Cuenta:
    """Lo que un cliente tiene por pagar en caja: las remisiones de su turno (o una remisión suelta, sin turno)."""

    pase: Pase | None
    publico: int | None = None  # el cliente «Público en general», que no sirve de nombre
    ventas: list = field(default_factory=list)

    @property
    def clave(self):
        return f"t{self.pase.pk}" if self.pase else f"v{self.ventas[0].pk}"

    @property
    def total(self):
        return sum((v.total for v in self.ventas), Decimal(0))

    @property
    def nombre(self):
        return _nombre(self.pase, self.ventas, self.publico) or "Público en general"

    @property
    def vendedores(self):
        return ", ".join(dict.fromkeys(_persona(v.usuario) for v in self.ventas if v.usuario))

    @property
    def desde(self):
        return min(v.fecha for v in self.ventas)

    @property
    def de_otro_dia(self):
        return self.pase is not None and self.pase.dia != timezone.localdate()

    @property
    def busca(self):
        """Lo que la cajera puede teclear para encontrarlo: número de turno, nombre, folio de cliente o remisión."""
        partes = [str(self.pase.numero) if self.pase else "", self.nombre]
        for v in self.ventas:
            partes += [v.folio, v.cliente.folio, str(v.cliente)]
        return texto_busqueda(*partes)


def por_cobrar():
    """Todo lo que la caja tiene por cobrar, agrupado por turno, del que lleva más tiempo esperando al más reciente."""
    ventas = (Venta.objects.filter(estado=Venta.Estado.POR_COBRAR)
              .select_related("cliente", "usuario", "pase", "pase__cliente")
              .prefetch_related("lineas__producto", "lineas__unidad").order_by("fecha", "pk"))
    cuentas, publico = {}, _publico()
    for v in ventas:
        clave = ("t", v.pase_id) if v.pase_id else ("v", v.pk)
        if clave not in cuentas:
            cuentas[clave] = Cuenta(v.pase, publico)
        cuentas[clave].ventas.append(v)
    return sorted(cuentas.values(), key=lambda c: c.desde)


# Pantalla de llamados -------------------------------------------------------------------------------------------
# La ve la gente en la sala y se actualiza sola todo el día. Para que eso no tenga despierta la base (ni de noche,
# si se queda prendida), lo que muestra se guarda en un archivo cada vez que cambia la fila y la pantalla lee de
# ahí: no pide sesión ni consulta la base. Su dirección lleva una clave que no se adivina.

def clave_pantalla():
    return salted_hmac("erp.turnos.pantalla", "llamados").hexdigest()[:20]


def refrescar_pantalla():
    """Vuelve a calcular lo que muestra la pantalla de llamados y lo deja guardado."""
    hoy = timezone.localdate()
    llamados = (Pase.objects.filter(dia=hoy, llamado__isnull=False, cerrado__isnull=True)
                .select_related("cliente", "atiende").order_by("-llamado")[:LLAMADOS])
    datos = {"dia": hoy.isoformat(), "espera": len(en_espera()),
             "llamados": [{"id": p.pk, "numero": p.numero, "nombre": p.quien, "con": _persona(p.atiende),
                           "t": int(p.llamado.timestamp())} for p in llamados]}
    caches["pantalla"].set("llamados", datos, 6 * 3600)
    return datos


def pantalla():
    datos = caches["pantalla"].get("llamados")
    if datos is None or datos.get("dia") != timezone.localdate().isoformat():
        datos = refrescar_pantalla()
    return datos
