"""Pantalla de mostrador: vehículo → niveles → ticket → cobro (venta + pedido al proveedor)."""
from decimal import Decimal as D

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from catalogo.models import ListaPrecio, Precio
from compras.listas import agregar_a_catalogo, importar
from compras.models import OrdenCompra, PartidaListaProveedor
from compras.tests import lista
from inventario.servicios import registrar_entrada
from ventas import mostrador as m
from ventas.models import Cliente, Turno, Venta

TSURU = "NISSAN. TSURU L4 1.6 (90-04)"


class MostradorTests(TestCase):
    def setUp(self):
        importar("MORSA", [lista([
            ["15208-H8916", "Aceite", "FILTRO PARA ACEITE", "MOTORFIL", TSURU, 18.23, ""],
            ["PH966B", "Aceite", "FILTRO PARA ACEITE", "FRAM", TSURU, 52.43, ""],
            ["AP-FAC3682", "Aceite", "FILTRO PARA ACEITE", "AUTOPRIME", TSURU, 54.79, ""],
            ["GP-37", "Aceite", "FILTRO PARA ACEITE", "GONHER", TSURU, 66.17, ""],
            ["CA6900", "Aire", "FILTRO PARA AIRE", "FRAM", "NISSAN. TSURU L4 1.6 (97-09)", 65.64, ""],
            ["G5237", "Gasolina", "FILTRO PARA GASOLINA", "FRAM", "NISSAN. TSURU L4 1.6 (95-17)", 49.34, ""],
        ])])
        self.mostrador = m.cliente_mostrador().lista_precio
        # El Fram de aceite ya lo surto: nivel Estándar, 10 en anaquel, $98
        self.fram = agregar_a_catalogo(PartidaListaProveedor.objects.get(codigo="PH966B"))
        self.fram.nivel = "STD"
        self.fram.save()
        Precio.objects.create(lista=self.mostrador, producto=self.fram, unidad=self.fram.unidad_base, precio=98)
        registrar_entrada(self.fram, 10, D("52.43"))
        self.user = get_user_model().objects.create_user("caja1", password="x")
        self.client.force_login(self.user)
        self.veh = m.Vehiculo(2003, "Nissan", "Tsuru", "1.6")

    def test_niveles_mis_productos_mandan_y_la_lista_llena_huecos(self):
        t, otras = m.opciones_de(self.veh, m.POR_CLAVE["aceite"], self.mostrador)
        self.assertEqual(t["STD"].codigo, "PH966B")
        self.assertFalse(t["STD"].sobre_pedido)
        self.assertEqual((t["ECO"].codigo, t["PRE"].codigo), ("15208-H8916", "GP-37"))
        self.assertTrue(t["PRE"].sobre_pedido)
        self.assertEqual(t["PRE"].precio, D("105"))  # 66.17 × 1.37 × 1.16 = 105.16, redondeado
        self.assertEqual([o.codigo for o in otras], ["AP-FAC3682"])

    def test_selector_y_busqueda(self):
        r = self.client.get(reverse("mostrador"), {"q": "tsuru 2003"})
        self.assertRedirects(r, reverse("mostrador") + "?anio=2003&armadora=Nissan&modelo=Tsuru", fetch_redirect_response=False)
        r = self.client.get(reverse("mostrador"), {"anio": 2003, "armadora": "Nissan", "modelo": "Tsuru"})
        self.assertContains(r, "Nissan Tsuru 2003")
        self.assertContains(r, "PH966B")
        self.assertContains(r, "Hoja de afinación")
        self.assertContains(r, 'class="sello pedido"')
        self.assertContains(r, "Llevar estándar")

    def test_cobrar_vende_lo_que_hay_y_pide_lo_que_no(self):
        abrir = self.client.post(reverse("caja"), {"accion": "abrir", "fondo": "500"})
        self.assertRedirects(abrir, reverse("mostrador"), fetch_redirect_response=False)
        base = {"anio": 2003, "armadora": "Nissan", "modelo": "Tsuru", "litros": "1.6"}
        fram = f"producto:{self.fram.pk}"
        ca = f"partida:{PartidaListaProveedor.objects.get(codigo='CA6900').pk}"
        r = self.client.post(reverse("ticket_agregar"), {"clave": fram, "pos": "aceite", "nivel": "STD", **base,
                                                         "next": "/?anio=2003&armadora=Nissan&modelo=Tsuru"})
        self.assertEqual(r["Location"], "/?anio=2003&armadora=Nissan&modelo=Tsuru")  # regresa a la misma hoja
        self.client.post(reverse("ticket_agregar"), {"clave": ca, "pos": "aire", "nivel": "PRE", **base})
        self.client.post(reverse("ticket_cantidad", args=[0]), {"cantidad": "2"})

        r = self.client.post(reverse("ticket_cobrar"), {"forma_pago": "EFECTIVO", **base}, follow=True)
        venta = Venta.objects.get()
        self.assertEqual((venta.total, venta.lineas.get().cantidad), (D("196.00"), D("2")))
        self.fram.refresh_from_db()
        self.assertEqual(self.fram.existencia, D("8"))
        oc = OrdenCompra.objects.get()
        self.assertTrue(oc.es_emergencia)
        self.assertEqual(oc.estado, OrdenCompra.Estado.BORRADOR)
        self.assertIn("Nissan Tsuru 2003", oc.notas)
        linea = oc.lineas.get()
        self.assertEqual((linea.producto.numero_parte, linea.producto.nivel, linea.costo_unitario), ("CA6900", "PRE", D("65.64")))
        # el precio cotizado queda en mi lista para venderlo cuando llegue
        self.assertEqual(Precio.objects.get(producto=linea.producto, lista=self.mostrador).precio, D("104"))  # 65.64 × 1.37 × 1.16
        self.assertContains(r, venta.folio)
        self.assertContains(r, oc.folio)
        self.assertEqual(self.client.session["ticket"]["lineas"], [])

    def test_sin_caja_abierta_no_cobra(self):
        self.client.post(reverse("ticket_agregar"), {"clave": f"producto:{self.fram.pk}", "pos": "aceite"})
        r = self.client.post(reverse("ticket_cobrar"), {"forma_pago": "EFECTIVO"})
        self.assertRedirects(r, reverse("caja"), fetch_redirect_response=False)
        self.assertFalse(Venta.objects.exists())

    def test_credito_solo_a_clientes_con_credito(self):
        self.client.post(reverse("caja"), {"accion": "abrir", "fondo": "0"})
        self.client.post(reverse("ticket_agregar"), {"clave": f"producto:{self.fram.pk}", "pos": "aceite"})
        self.client.post(reverse("ticket_cobrar"), {"forma_pago": "CREDITO"})
        self.assertFalse(Venta.objects.exists())
        taller = Cliente.objects.create(nombre="Taller Ruiz", lista_precio=self.mostrador, dias_credito=15, limite_credito=1000)
        self.client.post(reverse("ticket_cliente"), {"cliente": taller.pk})
        self.client.post(reverse("ticket_cobrar"), {"forma_pago": "CREDITO"})
        self.assertEqual(Venta.objects.get().estado, Venta.Estado.CREDITO)

    def test_corte_de_caja(self):
        self.client.post(reverse("caja"), {"accion": "abrir", "fondo": "500"})
        self.client.post(reverse("ticket_agregar"), {"clave": f"producto:{self.fram.pk}", "pos": "aceite"})
        self.client.post(reverse("ticket_cobrar"), {"forma_pago": "EFECTIVO"})
        r = self.client.post(reverse("caja"), {"accion": "cerrar", "efectivo": "590", "tarjeta": "0", "transferencia": "0"},
                             follow=True)
        turno = Turno.objects.get()
        self.assertIsNotNone(turno.cerrado)
        self.assertEqual(turno.diferencia, D("-8.00"))  # esperaba 500 + 98
        self.assertContains(r, "Corte del turno")

    def test_pide_entrar(self):
        self.client.logout()
        r = self.client.get(reverse("mostrador"))
        self.assertEqual(r.status_code, 302)
        self.assertIn(reverse("entrar"), r["Location"])

    def test_llevar_afinacion_completa_de_un_nivel(self):
        base = {"anio": 2003, "armadora": "Nissan", "modelo": "Tsuru", "litros": "1.6"}
        self.client.post(reverse("ticket_afinacion"), {"nivel": "STD", **base})
        lineas = self.client.session["ticket"]["lineas"]
        self.assertEqual(sorted((ln["pos"], ln["nivel"]) for ln in lineas),
                         [("aceite", "STD"), ("aire", "STD"), ("gasolina", "STD")])
        # cambiar a Premium reemplaza solo las piezas que tienen opción Premium
        self.client.post(reverse("ticket_afinacion"), {"nivel": "PRE", **base})
        lineas = self.client.session["ticket"]["lineas"]
        self.assertEqual(len(lineas), 3)
        self.assertEqual({ln["pos"]: ln["nivel"] for ln in lineas}["aceite"], "PRE")
