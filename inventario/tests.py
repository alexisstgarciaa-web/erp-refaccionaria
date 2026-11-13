from datetime import date
from decimal import Decimal as D

from django.core.exceptions import ValidationError
from django.test import TestCase

from catalogo.models import ConversionUnidad, Familia, Marca, Producto, UnidadMedida
from compras.models import OrdenCompra, OrdenCompraLinea, Proveedor
from compras.servicios import recibir
from inventario.servicios import registrar_entrada, registrar_salida


class Base(TestCase):
    def setUp(self):
        self.pza = UnidadMedida.objects.create(clave="PZA", nombre="Pieza")
        self.lt = UnidadMedida.objects.create(clave="L", nombre="Litro", permite_fraccion=True)
        self.tam = UnidadMedida.objects.create(clave="TAM", nombre="Tambor")
        fam = Familia.objects.create(nombre="General")
        self.filtro = Producto.objects.create(sku="GP10", nombre="Filtro aceite", marca=Marca.objects.create(nombre="Gonher"),
                                              numero_parte="GP-10", familia=fam, unidad_base=self.pza)
        self.anticong = Producto.objects.create(sku="ANT1", nombre="Anticongelante", marca=Marca.objects.create(nombre="Prestone"),
                                                numero_parte="AF-1", familia=fam, unidad_base=self.pza,
                                                maneja_lote=True, maneja_caducidad=True)
        self.granel = Producto.objects.create(sku="ACE5W30", nombre="Aceite 5W-30 granel", marca=Marca.objects.create(nombre="Mobil"),
                                              numero_parte="5W30-G", familia=fam, unidad_base=self.lt)
        ConversionUnidad.objects.create(producto=self.granel, unidad=self.tam, factor=D("208"))

    def refresh(self, p):
        p.refresh_from_db()
        return p


class CostoPromedioTests(Base):
    def test_promedio_ponderado(self):
        registrar_entrada(self.filtro, 10, D("50"))
        registrar_entrada(self.filtro, 30, D("60"))
        p = self.refresh(self.filtro)
        self.assertEqual(p.existencia, D("40"))
        self.assertEqual(p.costo_promedio, D("57.5000"))
        registrar_salida(p, 15)
        p = self.refresh(p)
        self.assertEqual(p.existencia, D("25"))
        self.assertEqual(p.costo_promedio, D("57.5000"))

    def test_no_vende_sin_existencia(self):
        registrar_entrada(self.filtro, 2, 50)
        with self.assertRaises(ValidationError):
            registrar_salida(self.filtro, 3)


class FefoTests(Base):
    def test_salida_toma_primero_lo_que_caduca_antes(self):
        registrar_entrada(self.anticong, 5, 80, lote_numero="B", caducidad=date(2028, 1, 1))
        registrar_entrada(self.anticong, 4, 80, lote_numero="A", caducidad=date(2027, 3, 1))
        movs = registrar_salida(self.anticong, 6)
        self.assertEqual([(m.lote.numero, m.cantidad) for m in movs], [("A", D("-4")), ("B", D("-2"))])
        self.assertEqual(self.anticong.lotes.get(numero="B").existencia, D("3"))

    def test_caducidad_obligatoria(self):
        with self.assertRaises(ValidationError):
            registrar_entrada(self.anticong, 5, 80, lote_numero="X")


class RecepcionTests(Base):
    def test_recepcion_parcial_con_conversion_a_granel(self):
        oc = OrdenCompra.objects.create(folio="OC-1", proveedor=Proveedor.objects.create(nombre="Mobil dist"))
        linea = OrdenCompraLinea.objects.create(orden=oc, producto=self.granel, unidad=self.tam,
                                                cantidad=2, costo_unitario=D("20800"))
        recibir(oc, [{"linea": linea, "cantidad": 1}])
        p = self.refresh(self.granel)
        self.assertEqual(p.existencia, D("208"))
        self.assertEqual(p.costo_promedio, D("100.0000"))  # 20,800 / 208 L
        oc.refresh_from_db()
        self.assertEqual(oc.estado, OrdenCompra.Estado.PARCIAL)
        registrar_salida(p, D("4.5"))  # venta por litro
        recibir(oc, [{"linea": linea, "cantidad": 1}])
        oc.refresh_from_db()
        self.assertEqual(oc.estado, OrdenCompra.Estado.RECIBIDA)
        self.assertEqual(self.refresh(p).existencia, D("411.5"))

    def test_no_recibe_mas_de_lo_pedido(self):
        oc = OrdenCompra.objects.create(folio="OC-2", proveedor=Proveedor.objects.create(nombre="X"))
        linea = OrdenCompraLinea.objects.create(orden=oc, producto=self.filtro, unidad=self.pza, cantidad=5, costo_unitario=50)
        with self.assertRaises(ValidationError):
            recibir(oc, [{"linea": linea, "cantidad": 6}])
