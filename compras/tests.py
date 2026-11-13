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


from catalogo.models import Aplicacion, Vehiculo  # noqa: E402
from compras.aplicaciones import buscar, interpretar, opciones, piezas_para  # noqa: E402


def v(texto):
    return [(x.armadora, x.modelo, x.motor, x.anio_desde, x.anio_hasta) for x in interpretar(texto)]


class InterpretarAplicacionTests(TestCase):
    """Textos tomados de las listas MORSA reales (Gonher, Fram, Autoprime, Motorfil)."""

    def test_gonher_arrastra_armadora_y_separa_motores(self):
        self.assertEqual(v("NISSAN. TSURU L4 1.6 (90-04) - SENTRA L4 1.6 (97-00) / L4 2.0 (01-06)"), [
            ("Nissan", "Tsuru", "L4 1.6 L", 1990, 2004),
            ("Nissan", "Sentra", "L4 1.6 L", 1997, 2000),
            ("Nissan", "Sentra", "L4 2.0 L", 2001, 2006)])

    def test_autoprime_anios_completos_y_romanos(self):
        self.assertEqual(v("NISS. TSURU III L4 1.6L 1995-2008,NISS. TSURU III L4 1.6L 2009-18"), [
            ("Nissan", "Tsuru III", "L4 1.6 L", 1995, 2008),
            ("Nissan", "Tsuru III", "L4 1.6 L", 2009, 2018)])

    def test_motorfil_anios_sin_parentesis_y_varios_modelos(self):
        self.assertEqual(v("FD CONTOUR 95-00 2.5L MYSTIQUE 95-00 2.5L GA-128 CA7737 MOTORFIL"), [
            ("Ford", "Contour", "2.5 L", 1995, 2000), ("Ford", "Mystique", "2.5 L", 1995, 2000)])
        self.assertEqual(v("SUZUKI SX-4 4L 2.0 07-09."), [("Suzuki", "SX-4", "L4 2.0 L", 2007, 2009)])

    def test_formatos_raros(self):
        self.assertEqual(v("FORD F450 7.3L V8 00/02"), [("Ford", "F450", "V8 7.3 L", 2000, 2002)])
        self.assertEqual(v("Toyota Raize 2025-22 L3-1.0L"), [("Toyota", "Raize", "L3 1.0 L", 2022, 2025)])
        self.assertEqual(v("CHEV. HHR 4L 2.2 - 2.4 (06-11)"), [("Chevrolet", "HHR", "L4 2.2/2.4 L", 2006, 2011)])
        self.assertEqual(v("Chrysler Cirrus L4 2.4L 1999-00 / Dodge Stratus L4 2.4L 1998-00"), [
            ("Chrysler", "Cirrus", "L4 2.4 L", 1999, 2000), ("Dodge", "Stratus", "L4 2.4 L", 1998, 2000)])

    def test_trampas_de_nombres(self):
        self.assertEqual(v("TOYOTA. LAND CRUISER V8 4.7 (98-07)")[0][:2], ("Toyota", "Land Cruiser"))
        self.assertEqual(v("CHEV. CHEVY L4 1.6L 1994-2012")[0][:2], ("Chevrolet", "Chevy"))
        self.assertEqual(v("CAJA AIRE AUX NISSAN TSURU III L4 1.6 (93-17)")[0][:2], ("Nissan", "Tsuru III"))

    def test_sin_vehiculo(self):
        self.assertEqual(v("FILTRO PARA AIRE"), [])
        self.assertEqual(v("VW GOLF / JETTA A2 CBI 1.8"), [])  # sin años no se adivina
        self.assertEqual(v(""), [])


class BuscarPorVehiculoTests(TestCase):
    def setUp(self):
        importar("MORSA", [lista([
            ["GP-37", "Aceite", "FILTRO PARA ACEITE", "GONHER", "NISSAN. TSURU L4 1.6 (90-04) - SENTRA L4 1.6 (97-00)", 66.17, ""],
            ["AP-FAS4967", "Aceite", "FILTRO PARA ACEITE", "AUTOPRIME", "NISS. TSURU III L4 1.6L 2009-2018", 58.54, ""],
            ["PH966B", "Aceite", "FILTRO PARA ACEITE", "FRAM", "NISSAN. TSURU L4 1.6 (92-08)", 52.43, ""],
            ["CA6900", "Aire", "FILTRO PARA AIRE", "FRAM", "NISSAN. TSURU L4 1.6 (97-09)", 65.64, ""],
            ["GA-114", "Aire", "FILTRO PARA AIRE", "GONHER", "VW. SEDAN L4 1.6 (93-03)", 112, ""],
        ])])

    def test_busca_por_modelo_y_anio(self):
        self.assertEqual({a.partida.codigo for a in buscar("tsuru 2003")}, {"GP-37", "PH966B", "CA6900"})
        self.assertEqual({a.partida.codigo for a in buscar("nissan tsuru 2015")}, {"AP-FAS4967"})
        self.assertEqual({a.partida.codigo for a in buscar("vocho 1999")}, {"GA-114"})
        self.assertEqual({a.partida.codigo for a in buscar("tsuru 1.6 03")}, {"GP-37", "PH966B", "CA6900"})
        self.assertFalse(buscar("tsuru 1985").exists())

    def test_piezas_agrupadas_mas_baratas_primero(self):
        g = piezas_para("tsuru 2003")
        self.assertEqual([p.codigo for p in g["ACEITE"]], ["PH966B", "GP-37"])
        self.assertEqual([p.codigo for p in g["AIRE"]], ["CA6900"])

    def test_reimportar_no_duplica_vehiculos(self):
        antes = buscar("tsuru").count()
        importar("MORSA", [lista([["GP-37", "Aceite", "FILTRO PARA ACEITE", "GONHER",
                                   "NISSAN. TSURU L4 1.6 (90-17)", 69.50, ""]])])
        self.assertEqual(buscar("tsuru").count(), antes)
        self.assertIn("GP-37", {a.partida.codigo for a in buscar("tsuru 2015")})

    def test_selector_del_mostrador(self):
        self.assertEqual(opciones(2003), [("Nissan", 3), ("Volkswagen", 1)])
        self.assertEqual(opciones(2003, "Nissan"), [("Tsuru", 3)])
        self.assertEqual(opciones(2003, "Nissan", "Tsuru"), [("L4 1.6 L", 3)])

    def test_al_surtir_la_pieza_pasa_al_catalogo_con_sus_vehiculos(self):
        producto = agregar_a_catalogo(PartidaListaProveedor.objects.get(codigo="GP-37"))
        self.assertEqual(
            sorted((a.vehiculo.modelo, a.vehiculo.anio_desde, a.vehiculo.anio_hasta) for a in producto.aplicaciones.all()),
            [("Sentra", 1997, 2000), ("Tsuru", 1990, 2004)])
        agregar_a_catalogo(PartidaListaProveedor.objects.get(codigo="PH966B"))
        self.assertEqual(Vehiculo.objects.filter(modelo="Tsuru").count(), 2)  # rangos distintos, vehículos distintos
        self.assertEqual(Aplicacion.objects.get(producto=producto, vehiculo__modelo="Tsuru").posicion, "Filtro de aceite")
