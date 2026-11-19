"""Buscar clientes cuando hay miles: por folio (CTE-0012), celular o nombre, sin importar acentos, mayúsculas
ni guiones, con los que más compran primero. Lo usan el ticket del mostrador, Remisiones › Nueva y las listas."""

import re
from datetime import timedelta
from decimal import Decimal

from django.db.models import Count, Q, Sum
from django.utils import timezone

from .models import RFC_GENERICO, Cliente, Venta, solo_digitos, texto_busqueda
from .templatetags.erp import pesos

DIAS_FRECUENTES = 180  # para ordenar: compras de los últimos seis meses


def pk_de_folio(texto):
    """«CTE-0012», «cte12» o «C-12» → 12; None si no es un folio de cliente."""
    folio = re.fullmatch(r"(?i)\s*c(?:te)?[-\s]*0*(\d{1,6})\s*", texto or "")
    return int(folio.group(1)) if folio else None


def filtro(texto, campo="busqueda"):
    """Q para buscar en las listas: cada palabra tecleada debe estar en el nombre, RFC o teléfono del cliente
    («ramirez taller» encuentra «Taller Ramírez»). campo: la ruta al campo, p. ej. «cliente__busqueda»."""
    q = Q()
    for palabra in texto_busqueda(texto).split():
        q &= Q(**{f"{campo}__contains": palabra})
    return q


def coincide(texto, ruta="cliente"):
    """Q para las listas (remisiones, cotizaciones, pedidos, devoluciones): el folio del cliente, o su nombre,
    RFC o teléfono como sea que se teclee. ruta: cómo se llega al cliente, p. ej. «venta__cliente»."""
    folio = pk_de_folio(texto)
    if folio is not None:
        return Q(**{f"{ruta}_id": folio})
    return filtro(texto, f"{ruta}__busqueda")


def buscar_clientes(texto, limite=8):
    """«CTE-0012» o «cte12» buscan el folio; «12» (hasta 3 números) también. Lo demás busca cada palabra en el
    nombre, RFC y teléfono limpios, así «442 123 4567», «4421234567» y «123-4567» encuentran el mismo celular.
    Los que más han comprado salen primero. Solo clientes activos."""
    t = (texto or "").strip()
    if not t:
        return []
    activos = Cliente.objects.filter(activo=True).select_related("lista_precio")
    folio = pk_de_folio(t)
    if folio is not None:
        return list(activos.filter(pk=folio))
    digitos = solo_digitos(t)
    puro_numero = digitos and digitos == re.sub(r"[\s\-.()]", "", t)
    por_folio = list(activos.filter(pk=int(digitos))) if puro_numero and len(digitos) <= 6 else []
    if puro_numero and len(digitos) < 4:  # «12» es un folio, no un pedazo de teléfono
        return por_folio
    q = Q(busqueda__contains=digitos) if puro_numero else filtro(t)
    if not q:
        return por_folio
    desde = timezone.now() - timedelta(days=DIAS_FRECUENTES)
    compras = Count("ventas", filter=Q(ventas__fecha__gte=desde) & ~Q(ventas__estado=Venta.Estado.CANCELADA))
    encontrados = list(activos.filter(q).annotate(compras=compras).order_by("-compras", "nombre")[:limite])
    return (por_folio + [c for c in encontrados if c not in por_folio])[:limite]


def recientes(limite=6, sin=None):
    """Los últimos clientes distintos a los que se les vendió o remisionó (sin Público en general)."""
    vistos = []
    for pk in Venta.objects.exclude(cliente_id=sin).order_by("-fecha").values_list("cliente_id", flat=True)[:300]:
        if pk not in vistos:
            vistos.append(pk)
            if len(vistos) == limite:
                break
    por_pk = Cliente.objects.filter(pk__in=vistos, activo=True).select_related("lista_precio").in_bulk()
    return [por_pk[pk] for pk in vistos if pk in por_pk]


def vencido(cliente):
    """Lo que debe de remisiones a crédito que ya pasaron su vencimiento."""
    return cliente.ventas.filter(estado=Venta.Estado.CREDITO, vence__lt=timezone.localdate()).aggregate(
        s=Sum("saldo"))["s"] or Decimal(0)


def credito(cliente):
    """(saldo, disponible, vencido) de un cliente."""
    saldo = cliente.saldo()
    disponible = cliente.limite_credito - saldo if cliente.dias_credito else Decimal(0)
    return saldo, disponible, vencido(cliente) if saldo else Decimal(0)


def cliente_json(c):
    saldo, disponible, debe_vencido = credito(c)
    return {"pk": c.pk, "folio": c.folio, "nombre": c.nombre, "telefono": c.telefono, "rfc": c.rfc,
            "rfc_generico": c.rfc_generico, "domicilio": c.domicilio, "lista": str(c.lista_precio),
            "dias_credito": c.dias_credito, "limite": pesos(c.limite_credito), "saldo": pesos(saldo),
            "disponible": str(disponible), "disponible_txt": pesos(disponible),
            "vencido": str(debe_vencido), "vencido_txt": pesos(debe_vencido) if debe_vencido else ""}


def duplicado(nombre="", telefono="", rfc=""):
    """El cliente activo que ya tiene ese nombre (sin importar acentos, mayúsculas ni signos), ese celular
    (comparando solo los números, 7 o más) o ese RFC (si no es el genérico), para no darlo de alta dos veces.
    Regresa (cliente, qué coincidió) o (None, "")."""
    limpio = texto_busqueda(nombre)
    if limpio:
        for c in Cliente.objects.filter(filtro(nombre), activo=True):
            if texto_busqueda(c.nombre) == limpio:
                return c, "ese nombre"
    rfc = "".join((rfc or "").upper().split())
    if rfc and rfc != RFC_GENERICO:
        mismo = Cliente.objects.filter(rfc=rfc, activo=True).first()
        if mismo:
            return mismo, "ese RFC"
    digitos = solo_digitos(telefono)
    if len(digitos) >= 7:
        for c in Cliente.objects.filter(activo=True, busqueda__contains=digitos):
            if solo_digitos(c.telefono) == digitos:
                return c, "ese teléfono"
    return None, ""
