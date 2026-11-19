"""NIP por persona: quién hace cada acción en mostrador y caja, y autorización de administrador."""
import hashlib
import hmac
import time

from django.conf import settings
from django.core.exceptions import ValidationError

from .models import Nip

INTENTOS = 5  # NIPs equivocados seguidos antes de esperar
ESPERA = 120  # segundos


def huella(nip):
    return hmac.new(settings.SECRET_KEY.encode(), f"nip:{nip}".encode(), hashlib.sha256).hexdigest()


def limpio(nip):
    return "".join((nip or "").split())


def revisar(usuario, nip):
    """Huella del NIP si sirve para esta persona; si no, ValidationError con la razón."""
    nip = limpio(nip)
    if not (nip.isdigit() and 4 <= len(nip) <= 6):
        raise ValidationError("El NIP lleva de 4 a 6 números.")
    if len(set(nip)) == 1 or nip in "0123456789" or nip in "9876543210":
        raise ValidationError("Ese NIP es muy fácil de adivinar (números repetidos o seguidos).")
    h = huella(nip)
    if Nip.objects.filter(huella=h).exclude(usuario=usuario).exists():
        raise ValidationError("Ese NIP ya lo tiene otra persona; elige otro.")
    return h


def asignar(usuario, nip):
    Nip.objects.update_or_create(usuario=usuario, defaults={"huella": revisar(usuario, nip)})


def quien(session, nip, admin=False):
    """Usuario activo dueño del NIP. Si no hay o no alcanza (admin=True pide administrador), ValidationError.
    Después de varios NIPs equivocados seguidos en la misma sesión hay que esperar."""
    bloqueo = session.get("nip_bloqueo", 0)
    if bloqueo > time.time():
        raise ValidationError(f"Demasiados NIP equivocados. Espera {int(bloqueo - time.time()) + 1} segundos.")
    nip = limpio(nip)
    if not nip:
        raise ValidationError("Teclea el NIP de administrador." if admin else "Teclea tu NIP.")
    n = Nip.objects.select_related("usuario").filter(huella=huella(nip), usuario__is_active=True).first()
    if n is None and not Nip.objects.exists():
        raise ValidationError("Todavía nadie tiene NIP: el administrador los asigna en Administración › NIPs.")
    if n is None:
        fallos = session.get("nip_fallos", 0) + 1
        session["nip_fallos"] = fallos
        if fallos >= INTENTOS:
            session["nip_fallos"], session["nip_bloqueo"] = 0, time.time() + ESPERA
        raise ValidationError("NIP equivocado.")
    session["nip_fallos"] = 0
    if admin and not n.usuario.is_staff:  # sin decir de quién es: que no sirva para adivinar NIPs ajenos
        raise ValidationError("Ese NIP no es de administrador; esto lo autoriza un administrador.")
    return n.usuario
