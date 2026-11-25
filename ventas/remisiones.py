"""Remisiones › Nueva: leer las partidas del formulario (al cliente lo busca ventas/clientes.py)."""

from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError

from catalogo.models import Producto

from . import mostrador as m
from .templatetags.erp import cant, miles

CAMPOS_LINEA = ("clave", "codigo", "descripcion", "existencia", "cantidad", "precio", "descuento")


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
