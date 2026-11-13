"""Importación de listas de precios de proveedor y alta de productos desde ellas."""

from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path

from django.db import transaction

from catalogo.models import Familia, Marca, Producto, UnidadMedida

from .models import PartidaListaProveedor, ProductoProveedor, Proveedor

ENCABEZADOS = {"código": "codigo", "codigo": "codigo", "tipo": "tipo", "descripción": "descripcion",
               "descripcion": "descripcion", "marca": "marca", "aplicación": "aplicacion",
               "aplicacion": "aplicacion", "precio morsa": "costo", "precio": "costo", "costo": "costo",
               "equivalencias": "equivalencia", "equivalencia": "equivalencia"}
TIPOS = {"aceite": "ACEITE", "aire": "AIRE", "gasolina": "GASOLINA"}
FAMILIAS = {"ACEITE": "Filtros de aceite", "AIRE": "Filtros de aire", "GASOLINA": "Filtros de gasolina",
            "OTRO": "Otros filtros"}


def _texto(v):
    return "" if v is None else str(v).strip()


def _costo(v):
    if v in (None, ""):
        return None
    try:
        c = Decimal(str(v)).quantize(Decimal("0.0001"))
    except InvalidOperation:
        return None
    return c if c > 0 else None


def leer_excel(ruta):
    """Lee todas las hojas de un .xlsx y devuelve dicts por fila. Ubica la fila de encabezados
    buscando 'Código', así tolera títulos y filas vacías arriba."""
    import openpyxl

    wb = openpyxl.load_workbook(ruta, read_only=True, data_only=True)
    for ws in wb.worksheets:
        mapa = None
        for fila in ws.iter_rows(values_only=True):
            if mapa is None:
                norm = [_texto(c).lower() for c in fila]
                if "código" in norm or "codigo" in norm:
                    mapa = {i: ENCABEZADOS[n] for i, n in enumerate(norm) if n in ENCABEZADOS}
                continue
            reg = {campo: fila[i] if i < len(fila) else None for i, campo in mapa.items()}
            if _texto(reg.get("codigo")):
                yield reg
    wb.close()


@transaction.atomic
def importar(proveedor_nombre, rutas, fecha_lista=None):
    """Alta o actualización (por proveedor + código) de las partidas. No crea productos.
    Si la partida ya está ligada a un producto, actualiza el costo en ProductoProveedor."""
    fecha_lista = fecha_lista or date.today()
    proveedor, _ = Proveedor.objects.get_or_create(nombre=proveedor_nombre)
    existentes = {p.codigo: p for p in PartidaListaProveedor.objects.filter(proveedor=proveedor)}
    res = {"nuevas": 0, "actualizadas": 0, "sin_costo": 0, "costos_cambiados": 0}
    nuevas = []
    for ruta in rutas:
        for reg in leer_excel(ruta):
            codigo = _texto(reg["codigo"])
            datos = dict(
                tipo=TIPOS.get(_texto(reg.get("tipo")).lower(), "OTRO"),
                descripcion=_texto(reg.get("descripcion"))[:200],
                marca=_texto(reg.get("marca")) or "SIN MARCA",
                aplicacion=_texto(reg.get("aplicacion")),
                costo=_costo(reg.get("costo")),
                equivalencia=(_texto(reg.get("equivalencia")) if _texto(reg.get("equivalencia")) != codigo else "")[:60],
                fecha_lista=fecha_lista,
                archivo_origen=Path(ruta).name[:200],
            )
            if datos["costo"] is None:
                res["sin_costo"] += 1
            p = existentes.get(codigo)
            if p is None:
                p = PartidaListaProveedor(proveedor=proveedor, codigo=codigo, **datos)
                nuevas.append(p)
                existentes[codigo] = p
                res["nuevas"] += 1
                continue
            if p.pk is None:  # repetido dentro de la misma carga: gana la última fila
                for k, v in datos.items():
                    setattr(p, k, v)
                continue
            if p.costo != datos["costo"]:
                res["costos_cambiados"] += 1
            for k, v in datos.items():
                setattr(p, k, v)
            p.save()
            res["actualizadas"] += 1
            if p.producto_id and p.costo:
                ProductoProveedor.objects.filter(proveedor=proveedor, producto_id=p.producto_id).update(costo=p.costo)
    PartidaListaProveedor.objects.bulk_create(nuevas, batch_size=500)
    return res


@transaction.atomic
def agregar_a_catalogo(partida):
    """Convierte una partida en Producto de mi catálogo (si no existe) y registra al proveedor con su costo."""
    if partida.producto_id:
        return partida.producto
    marca, _ = Marca.objects.get_or_create(nombre=partida.marca.title() if partida.marca.isupper() else partida.marca)
    familia, _ = Familia.objects.get_or_create(nombre=FAMILIAS[partida.tipo])
    pza, _ = UnidadMedida.objects.get_or_create(clave="PZA", defaults={"nombre": "Pieza"})
    producto = Producto.objects.filter(marca=marca, numero_parte=partida.codigo).first()
    if producto is None:
        nombre = (partida.descripcion or FAMILIAS[partida.tipo]).capitalize()
        producto = Producto.objects.create(sku=partida.codigo, nombre=f"{nombre} {marca.nombre}", marca=marca,
                                           numero_parte=partida.codigo, familia=familia, unidad_base=pza)
    ProductoProveedor.objects.update_or_create(
        proveedor=partida.proveedor, producto=producto,
        defaults={"clave_proveedor": partida.codigo, "unidad_compra": pza, "costo": partida.costo or 0},
    )
    partida.producto = producto
    partida.save(update_fields=["producto"])
    return producto
