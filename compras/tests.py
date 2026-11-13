import tempfile
from decimal import Decimal as D
from pathlib import Path

import openpyxl
from django.test import TestCase

from compras.listas import agregar_a_catalogo, importar
from compras.models import PartidaListaProveedor, ProductoProveedor


def lista(filas):
    """Crea un .xlsx con el mismo formato de las listas MORSA: título arriba y encabezados en la fila 4."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append([])
    ws.append([None, "Lista de precios MORSA · prueba"])
    ws.append([])
    ws.append([None, "Código", "Tipo", "Descripción", "Marca", "Aplicación", "Precio MORSA", "Equivalencias"])
    for f in filas:
        ws.append([None, *f])
    ruta = Path(tempfile.mkdtemp()) / "lista.xlsx"
    wb.save(ruta)
    return ruta


class ImportarListaTests(TestCase):
    def test_importa_y_actualiza_costos_sin_duplicar(self):
        r1 = lista([["GP-37", "Aceite", "FILTRO PARA ACEITE", "GONHER", "NISSAN TSURU L4 1.6 (92-17)", 66.17, "GP-37"],
                    ["CH10066", "Aceite", "FILTRO PARA ACEITE", "FRAM", "MINI COOPER", 64.7388, "XG10066"],
                    ["X-1", None, "VALVULA PCV", "TOMCO", "", None, "X-1"]])
        res = importar("MORSA", [r1])
        self.assertEqual((res["nuevas"], res["sin_costo"]), (3, 1))
        fram = PartidaListaProveedor.objects.get(codigo="CH10066")
        self.assertEqual((fram.tipo, fram.equivalencia, fram.costo), ("ACEITE", "XG10066", D("64.7388")))
        self.assertEqual(PartidaListaProveedor.objects.get(codigo="GP-37").equivalencia, "")
        self.assertEqual(PartidaListaProveedor.objects.get(codigo="X-1").tipo, "OTRO")

        producto = agregar_a_catalogo(PartidaListaProveedor.objects.get(codigo="GP-37"))
        r2 = lista([["GP-37", "Aceite", "FILTRO PARA ACEITE", "GONHER", "NISSAN TSURU L4 1.6 (92-17)", 69.50, "GP-37"]])
        res = importar("MORSA", [r2])
        self.assertEqual((res["nuevas"], res["actualizadas"], res["costos_cambiados"]), (0, 1, 1))
        self.assertEqual(PartidaListaProveedor.objects.count(), 3)
        self.assertEqual(ProductoProveedor.objects.get(producto=producto).costo, D("69.5000"))

    def test_agregar_a_catalogo_crea_producto_una_vez(self):
        importar("MORSA", [lista([["GA-114", "Aire", "FILTRO PARA AIRE", "GONHER", "TSURU", 112, "GA-114"]])])
        p = PartidaListaProveedor.objects.get()
        a = agregar_a_catalogo(p)
        b = agregar_a_catalogo(p)
        self.assertEqual(a.pk, b.pk)
        self.assertEqual((a.marca.nombre, a.familia.nombre, a.numero_parte), ("Gonher", "Filtros de aire", "GA-114"))
