"""Mandar una cotización o una remisión al cliente, en PDF, por WhatsApp o correo.

WhatsApp no deja que un sistema mande un archivo a un número sin su API de Business, así que el PDF se pasa al menú
de compartir del aparato (Windows, Android, iPhone) y quien atiende elige WhatsApp y el contacto. El correo sí sale
desde el servidor con el PDF adjunto cuando hay cuenta configurada (settings.CORREO_SERVIDOR).

Los enlaces firmados de antes (cotizacion/<token>/) siguen abriendo la cotización hasta ENLACE_DIAS.
"""
from dataclasses import dataclass
from typing import Callable

from django.core import signing

from .models import Cliente, TicketGuardado, solo_digitos
from .templatetags.erp import pesos

SAL = "ventas.cotizacion.enlace"
ENLACE_DIAS = 60


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
    """Número para WhatsApp: 52 + los 10 dígitos de México. Con «+» adelante se respeta tal cual (extranjero).
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


MESES = ("enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre",
         "noviembre", "diciembre")


def fecha_larga(f):
    return f"{f.day} de {MESES[f.month - 1]}"


@dataclass
class Documento:
    """Lo que hace falta para mandar una cotización o una remisión."""
    tipo: str  # "cotizacion" o "remision" (va en la dirección)
    titulo: str  # "Cotización" o "Remisión"
    folio: str
    cliente: Cliente
    para: str  # a nombre de quién va
    publico: bool  # Público en general: sin celular ni correo que proponer ni guardar
    frase: str  # «Le comparto la cotización COT-000012 de … por $301 con IVA, válida hasta el 24 de noviembre.»
    pdf: Callable[[], bytes]

    @property
    def archivo(self):
        return f"{'Cotizacion' if self.tipo == 'cotizacion' else 'Remision'}-{self.folio}.pdf"

    @property
    def saludo(self):
        return "Hola" if self.publico and self.para == str(self.cliente) else f"Hola, {self.para}"

    @property
    def texto(self):
        """Lo que acompaña al PDF en WhatsApp."""
        return f"{self.saludo}. {self.frase}"

    def asunto(self, negocio):
        return f"{self.titulo} {self.folio} · {negocio}"

    def cuerpo(self, negocio, telefono="", direccion=""):
        firma = [negocio] + ([f"Tel. {telefono}"] if telefono else []) + ([direccion] if direccion else [])
        return "\n".join([f"{self.saludo}:", "", f"{self.frase} Va en el PDF adjunto.", "", "Quedamos a sus órdenes.", *firma])


def de_cotizacion(g, negocio, pdf, publico):
    frase = (f"Le comparto la cotización {g.folio} de {negocio} por {pesos(g.total)} con IVA, "
             f"válida hasta el {fecha_larga(g.vence)}.")
    return Documento("cotizacion", "Cotización", g.folio, g.cliente, g.para, publico, frase, pdf)


def de_remision(venta, negocio, pdf, publico):
    frase = f"Le comparto su remisión {venta.folio} de {negocio} por {pesos(venta.total)}."
    if venta.estado == "CREDITO" and venta.vence:
        frase += f" Queda a crédito, vence el {fecha_larga(venta.vence)}."
    elif venta.estado == "CANCELADA":
        frase += " Esta venta quedó cancelada."
    else:
        frase += " Gracias por su compra."
    return Documento("remision", "Remisión", venta.folio, venta.cliente, str(venta.cliente), publico, frase, pdf)

