"""Remisiones › Nueva: buscar al cliente por folio, celular o nombre, y leer las partidas del formulario."""

import re
from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.db.models import Q

from catalogo.models import Producto

from . import mostrador as m
from .models import Cliente
from .templatetags.erp import cant, miles, pesos

CAMPOS_LINEA = ("clave", "codigo", "descripcion", "existencia", "cantidad", "precio", "descuento")


def _digitos(texto):
    return re.sub(r"\D", "", texto or "")


def pk_de_folio(texto):
    """«CTE-0012», «cte12» o «C-12» → 12; None si no es un folio de cliente."""
    folio = re.fullmatch(r"(?i)\s*c(?:te)?[-\s]*0*(\d{1,6})\s*", texto or "")
    return int(folio.group(1)) if folio else None


def buscar_clientes(texto, limite=8):
    """«CTE-0012», «cte12» o «12» buscan por folio; 7 números o más, por celular (aunque esté escrito con
    espacios o guiones); lo demás por nombre o RFC. Solo clientes activos."""
    t = (texto or "").strip()
    if not t:
        return []
    activos = Cliente.objects.filter(activo=True).select_related("lista_precio")
    folio = pk_de_folio(t)
    if folio is not None:
        return list(activos.filter(pk=folio))
    digitos = _digitos(t)
    if digitos and len(digitos) >= len(t.replace(" ", "").replace("-", "")) - 1:  # casi puros números
        res = list(activos.filter(pk=int(digitos))) if len(digitos) <= 6 else []
        if len(digitos) >= 4:  # el celular se guarda como lo tecleen: se compara solo con sus números
            res += [c for c in activos.exclude(telefono="") if digitos in _digitos(c.telefono) and c not in res]
        return res[:limite]
    return list(activos.filter(Q(nombre__icontains=t) | Q(rfc__icontains=t)).order_by("nombre")[:limite])


def cliente_json(c):
    disponible = c.limite_credito - c.saldo() if c.dias_credito else Decimal(0)
    return {"pk": c.pk, "folio": c.folio, "nombre": c.nombre, "telefono": c.telefono, "rfc": c.rfc,
            "rfc_generico": c.rfc_generico, "domicilio": c.domicilio, "lista": str(c.lista_precio),
            "dias_credito": c.dias_credito, "limite": pesos(c.limite_credito), "saldo": pesos(c.saldo()),
            "disponible": str(disponible), "disponible_txt": pesos(disponible)}


def leer_lineas(post):
    """Las filas del formulario como se teclearon; las vacías se descartan."""
    columnas = {k: post.getlist(k) for k in CAMPOS_LINEA}
    n = max((len(v) for v in columnas.values()), default=0)
    filas = []
    for i in range(n):
        fila = {k: (columnas[k][i] if i < len(columnas[k]) else "").strip() for k in CAMPOS_LINEA}
        if fila["clave"] or fila["codigo"]:
            filas.append(fila)
    return filas


def _numero(texto, defecto=None):
    try:
        return Decimal(str(texto).replace(",", "").replace("$", "").replace("%", "").strip())
    except (InvalidOperation, ValueError):
        return defecto


def resolver_producto(fila, lista):
    """El producto de la fila: por su clave, o por el código tecleado si es exacto."""
    if fila["clave"]:
        obj = m.resolver(fila["clave"])
    else:
        encontrados, directo = m.buscar_codigo(fila["codigo"], lista)
        if not (directo and encontrados):
            raise ValidationError(f"No encontré «{fila['codigo']}»: elígelo de la lista que sale al teclearlo.")
        obj = m.resolver(encontrados[0].opcion.clave)
    if not isinstance(obj, Producto):
        raise ValidationError(f"{fila['codigo'] or obj} va sobre pedido: la remisión solo lleva lo que hay en existencia.")
    return obj


def total_tecleado(filas):
    """Total de las filas con cantidad y precio (para el borrador; el de la remisión lo calcula el servidor)."""
    total = Decimal(0)
    for f in filas:
        c, p, d = _numero(f["cantidad"], Decimal(0)), _numero(f["precio"], Decimal(0)), _numero(f["descuento"], Decimal(0))
        if 0 <= d < 100:
            total += (c * p * (100 - d) / 100).quantize(Decimal("0.01"))
    return total


def partidas(filas, cliente, precio_tecleado):
    """[{producto, cantidad, precio, descuento}] para emitir. precio_tecleado(obj, texto, descuento) aplica la
    regla de precio abajo del costo; vacío es el de la lista del cliente."""
    res = []
    for f in filas:
        prod = resolver_producto(f, cliente.lista_precio)
        cantidad = _numero(f["cantidad"])
        if cantidad is None or cantidad <= 0:
            raise ValidationError(f"Escribe la cantidad de {prod.sku}.")
        descuento = _numero(f["descuento"] or 0)
        if descuento is None or not 0 <= descuento < 100:
            raise ValidationError(f"El descuento de {prod.sku} va de 0 a 99%.")
        res.append({"producto": prod, "cantidad": cantidad, "descuento": descuento,
                    "precio": precio_tecleado(prod, f["precio"], descuento)})
    if not res:
        raise ValidationError("Agrega al menos una partida.")
    return res


def fila_de(producto, cliente):
    """Datos para pintar una fila nueva (al cambiar de cliente se recalcula el precio de lista)."""
    op = m.encontrado(producto, cliente.lista_precio).opcion
    return {"clave": op.clave, "codigo": op.codigo, "descripcion": producto.nombre,
            "existencia": cant(op.existencia) if op.existencia else "0", "precio": miles(op.precio) if op.precio else ""}
