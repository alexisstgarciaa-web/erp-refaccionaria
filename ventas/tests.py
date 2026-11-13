from datetime import date
from decimal import Decimal as D

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase

from catalogo.models import ConversionUnidad, Familia, ListaPrecio, Marca, Precio, Producto, UnidadMedida
from inventario.servicios import registrar_entrada
from ventas.models import Cliente, FormaPago, Venta
from ventas.servicios import abrir_turno, cancelar_venta, cerrar_turno, registrar_abono, registrar_venta


class Base(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("caja1")
        pza = UnidadMedida.objects.create(clave="PZA", nombre="Pieza")
        self.lt = UnidadMedida.objects.create(clave="L", nombre="Litro", permite_fraccion=True)
        tam = UnidadMedida.objects.create(clave="TAM", nombre="Tambor")
        fam = Familia.objects.create(nombre="Filtros")
        self.mostrador = ListaPrecio.objects.create(nombre="Mostrador")
        self.taller_lp = ListaPrecio.objects.create(nombre="Taller")
        self.filtro = Producto.objects.create(sku="GP-37", nombre="Filtro aceite", marca=Marca.objects.create(nombre="Gonher"),
                                              numero_parte="GP-37", familia=fam, unidad_base=pza, nivel="PRE")
        self.aceite = Producto.objects.create(sku="5W30-G", nombre="Aceite granel", marca=Marca.objects.create(nombre="Mobil"),
                                              numero_parte="5W30-G", familia=fam, unidad_base=self.lt)
        self.anticong = Producto.objects.create(sku="ANT", nombre="Anticongelante", marca=Marca.objects.create(nombre="Prestone"),
                                                numero_parte="ANT", familia=fam, unidad_base=pza, maneja_lote=True, maneja_caducidad=True)
        ConversionUnidad.objects.create(producto=self.aceite, unidad=tam, factor=208)
        for lista, pf, pa, pan in ((self.mostrador, 122, 135, 249), (self.taller_lp, 107, 119, 219)):
            Precio.objects.create(lista=lista, producto=self.filtro, unidad=pza, precio=pf)
            Precio.objects.create(lista=lista, producto=self.aceite, unidad=self.lt, precio=pa)
            Precio.objects.create(lista=lista, producto=self.anticong, unidad=pza, precio=pan)
        registrar_entrada(self.filtro, 10, D("66.17"))
        registrar_entrada(self.aceite, 208, D("100"))
        registrar_entrada(self.anticong, 3, 150, lote_numero="B", caducidad=date(2027, 6, 1))
        registrar_entrada(self.anticong, 2, 150, lote_numero="A", caducidad=date(2026, 12, 3))
        self.publico = Cliente.objects.create(nombre="Público en general", lista_precio=self.mostrador)
        self.taller = Cliente.objects.create(nombre="Taller Hermanos Ruiz", lista_precio=self.taller_lp,
                                             dias_credito=15, limite_credito=1000)
        self.turno = abrir_turno(self.user, fondo_inicial=500)


class VentaTests(Base):
    def test_venta_de_contado_descuenta_inventario_y_guarda_margen(self):
        v = registrar_venta(self.turno, self.publico, [{"producto": self.filtro, "cantidad": 1},
                                                       {"producto": self.aceite, "cantidad": "3.6"}], FormaPago.EFECTIVO)
        self.assertEqual(v.folio, f"VTA-{v.pk:06d}")
        self.assertEqual(v.total, D("608.00"))  # 122 + 3.6 × 135
        self.assertEqual(v.iva, D("83.86"))
        self.assertEqual(v.costo_total, D("426.17"))  # 66.17 + 3.6 × 100
        self.filtro.refresh_from_db(); self.aceite.refresh_from_db()
        self.assertEqual((self.filtro.existencia, self.aceite.existencia), (D("9"), D("204.4")))
        self.assertEqual(round(v.margen, 3), D("0.187"))

    def test_precio_sale_de_la_lista_del_cliente(self):
        v = registrar_venta(self.turno, self.taller, [{"producto": self.filtro, "cantidad": 2}], FormaPago.EFECTIVO)
        self.assertEqual(v.total, D("214.00"))

    def test_quimico_sale_por_lote_que_caduca_antes(self):
        registrar_venta(self.turno, self.publico, [{"producto": self.anticong, "cantidad": 3}], FormaPago.TARJETA)
        self.assertEqual({l.numero: l.existencia for l in self.anticong.lotes.all()}, {"A": D("0"), "B": D("2")})

    def test_todo_o_nada_si_falta_existencia(self):
        with self.assertRaises(ValidationError):
            registrar_venta(self.turno, self.publico, [{"producto": self.filtro, "cantidad": 1},
                                                       {"producto": self.filtro, "cantidad": 50}], FormaPago.EFECTIVO)
        self.filtro.refresh_from_db()
        self.assertEqual(self.filtro.existencia, D("10"))
        self.assertFalse(Venta.objects.exists())

    def test_no_vende_piezas_fraccionadas(self):
        with self.assertRaises(ValidationError):
            registrar_venta(self.turno, self.publico, [{"producto": self.filtro, "cantidad": "1.5"}], FormaPago.EFECTIVO)

    def test_cancelar_regresa_inventario_al_mismo_lote(self):
        v = registrar_venta(self.turno, self.publico, [{"producto": self.anticong, "cantidad": 1},
                                                       {"producto": self.filtro, "cantidad": 2}], FormaPago.EFECTIVO)
        cancelar_venta(v)
        self.filtro.refresh_from_db()
        self.assertEqual(self.filtro.existencia, D("10"))
        self.assertEqual(self.anticong.lotes.get(numero="A").existencia, D("2"))
        with self.assertRaises(ValidationError):
            cancelar_venta(v)


class CreditoYCajaTests(Base):
    def test_credito_con_limite_y_abonos(self):
        with self.assertRaises(ValidationError):
            registrar_venta(self.turno, self.publico, [{"producto": self.filtro, "cantidad": 1}], FormaPago.CREDITO)
        v = registrar_venta(self.turno, self.taller, [{"producto": self.filtro, "cantidad": 8}], FormaPago.CREDITO)
        self.assertEqual((v.estado, v.saldo), (Venta.Estado.CREDITO, D("856.00")))
        self.assertIsNotNone(v.vence)
        with self.assertRaises(ValidationError):  # 856 + 214 > 1000
            registrar_venta(self.turno, self.taller, [{"producto": self.filtro, "cantidad": 2}], FormaPago.CREDITO)
        registrar_abono(v, 500, FormaPago.EFECTIVO, self.turno)
        registrar_abono(v, "356", FormaPago.TRANSFERENCIA, self.turno)
        v.refresh_from_db()
        self.assertEqual((v.estado, v.saldo), (Venta.Estado.PAGADA, D("0")))

    def test_corte_de_caja(self):
        registrar_venta(self.turno, self.publico, [{"producto": self.filtro, "cantidad": 1}], FormaPago.EFECTIVO)
        registrar_venta(self.turno, self.publico, [{"producto": self.aceite, "cantidad": 4}], FormaPago.TARJETA)
        cancelada = registrar_venta(self.turno, self.publico, [{"producto": self.filtro, "cantidad": 1}], FormaPago.EFECTIVO)
        cancelar_venta(cancelada)
        turno, esperado = cerrar_turno(self.turno, efectivo=610, tarjeta=540, transferencia=0)
        self.assertEqual(esperado[FormaPago.EFECTIVO], D("622"))  # fondo 500 + 122
        self.assertEqual(esperado[FormaPago.TARJETA], D("540"))
        self.assertEqual(turno.diferencia, D("-12.00"))
        with self.assertRaises(ValidationError):
            registrar_venta(turno, self.publico, [{"producto": self.filtro, "cantidad": 1}], FormaPago.EFECTIVO)

    def test_un_turno_abierto_por_usuario(self):
        with self.assertRaises(ValidationError):
            abrir_turno(self.user)
