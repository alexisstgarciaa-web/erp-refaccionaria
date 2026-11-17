"""Mandar correo por la API de Brevo (https://developers.brevo.com/reference/sendtransacemail).

Va por HTTPS, así que sirve en Render gratis, que no deja salir por SMTP. Se usa con EMAIL_BACKEND en settings
cuando hay BREVO_API_KEY; el resto del sistema manda correo con lo normal de Django (EmailMessage).
"""
import base64
import json
from email.utils import getaddresses, parseaddr
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from django.conf import settings
from django.core.mail.backends.base import BaseEmailBackend

API = "https://api.brevo.com/v3/smtp/email"


class ErrorCorreo(Exception):
    pass


def _persona(texto):
    nombre, correo = parseaddr(texto)
    return {"email": correo, **({"name": nombre} if nombre else {})}


def _datos(msg):
    datos = {
        "sender": _persona(msg.from_email),
        "to": [_persona(x) for x in msg.to],
        "subject": msg.subject,
        "textContent": msg.body,
    }
    if msg.cc:
        datos["cc"] = [_persona(x) for x in msg.cc]
    if msg.reply_to:
        datos["replyTo"] = _persona(msg.reply_to[0])
    adjuntos = []
    for adjunto in msg.attachments:
        if isinstance(adjunto, tuple):
            nombre, contenido, _ = adjunto
            if isinstance(contenido, str):
                contenido = contenido.encode()
        else:  # MIMEBase
            nombre, contenido = adjunto.get_filename(), adjunto.get_payload(decode=True)
        adjuntos.append({"name": nombre, "content": base64.b64encode(contenido).decode()})
    if adjuntos:
        datos["attachment"] = adjuntos
    return datos


class BrevoBackend(BaseEmailBackend):
    def send_messages(self, email_messages):
        enviados = 0
        for msg in email_messages:
            if not getaddresses(msg.to):
                continue
            pedido = Request(API, data=json.dumps(_datos(msg)).encode(), method="POST", headers={
                "api-key": settings.BREVO_API_KEY, "accept": "application/json", "content-type": "application/json"})
            try:
                with urlopen(pedido, timeout=settings.EMAIL_TIMEOUT) as r:
                    r.read()
            except HTTPError as e:  # 400 remitente sin validar, 401 clave mala…
                if not self.fail_silently:
                    detalle = e.read().decode(errors="replace")[:300]
                    raise ErrorCorreo(f"Brevo respondió {e.code}: {detalle}") from e
                continue
            except OSError:
                if not self.fail_silently:
                    raise
                continue
            enviados += 1
        return enviados
