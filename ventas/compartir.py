"""Mandar una cotización al cliente por WhatsApp o correo.

El cliente recibe un enlace de solo lectura (sin entrar al sistema) firmado con la SECRET_KEY: no se puede adivinar
ni cambiar para ver otra cotización, y deja de servir a los ENLACE_DIAS. Los mensajes se abren en el WhatsApp o el
correo de quien atiende, ya escritos; nada sale desde el servidor.
"""
from urllib.parse import quote

from django.core import signing

from .models import TicketGuardado, solo_digitos
from .templatetags.erp import cant, pesos

SAL = "ventas.cotizacion.enlace"
ENLACE_DIAS = 60
RENGLONES = 12  # en el mensaje; el enlace trae todo


def token(g):
    return signing.dumps(g.pk, salt=SAL)


def cotizacion_de(texto):
    """La cotización de un enlace, o None si está alterado o ya venció."""
    try:
        pk = signing.loads(texto, salt=SAL, max_age=ENLACE_DIAS * 86400)
    except signing.BadSignature:  # también SignatureExpired
        return None
    return (TicketGuardado.objects.select_related("cliente", "usuario")
            .filter(pk=pk, tipo=TicketGuardado.Tipo.COTIZACION).first())


def whatsapp(telefono):
    """Número para wa.me: 52 + los 10 dígitos de México. Con «+» adelante se respeta tal cual (extranjero).
    Si no se puede armar, cadena vacía."""
    telefono = (telefono or "").strip()
    d = solo_digitos(telefono)
    if telefono.startswith("+") and 8 <= len(d) <= 15:
        return "52" + d[3:] if len(d) == 13 and d.startswith("521") else d
    if len(d) == 10:
        return "52" + d
    if len(d) == 12 and d.startswith("52"):
        return d
    if len(d) == 13 and d[:3] in ("521", "044", "045"):  # prefijos de celular de antes
        return "52" + d[3:]
    return ""


def mensaje(g, lineas, enlace, negocio, negritas=True):
    """Texto de la cotización para WhatsApp (negritas con *) o correo."""
    from .mostrador import cliente_mostrador

    b = (lambda t: f"*{t}*") if negritas else (lambda t: t)
    saludo = "Hola" if not g.nombre and g.cliente_id == cliente_mostrador().pk else f"Hola, {g.para}"
    texto = [f"{saludo}. Le comparto la cotización {b(g.folio)} de {negocio}" + (f" para {g.vehiculo}" if g.vehiculo else "") + ":", ""]
    for d in lineas[:RENGLONES]:
        pedido = " (sobre pedido)" if d.get("pedido") else ""
        texto.append(f"• {cant(d['cantidad'])} × {d['descripcion']} {d['codigo']}{pedido}: {pesos(d['importe'])}")
    if len(lineas) > RENGLONES:
        resto = len(lineas) - RENGLONES
        texto.append(f"• y {resto} producto{'s' if resto > 1 else ''} más (vienen en el enlace)")
    texto += ["", f"{b('Total: ' + pesos(g.total))} con IVA",
              f"Precios válidos hasta el {fecha_larga(g.vence)}, sujetos a existencia.", "",
              f"Puede verla o descargarla aquí: {enlace}"]
    return "\n".join(texto)


MESES = ("enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre",
         "noviembre", "diciembre")


def fecha_larga(f):
    return f"{f.day} de {MESES[f.month - 1]}"


def enlace_whatsapp(telefono, texto):
    numero = whatsapp(telefono)
    return f"https://wa.me/{numero}?text={quote(texto, safe='')}"


def enlace_correo(correo, asunto, texto):
    return f"mailto:{quote((correo or '').strip(), safe='@')}?subject={quote(asunto, safe='')}&body={quote(texto, safe='')}"
