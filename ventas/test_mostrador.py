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
        self.assertRedirects(r, reverse("mostrador") + "?armadora=Nissan&anio=2003&modelo=Tsuru", fetch_redirect_response=False)
        r = self.client.get(reverse("mostrador"), {"armadora": "Nissan", "anio": 2003, "modelo": "Tsuru"})
        self.assertContains(r, "Nissan Tsuru <span>2003</span>")
        self.assertContains(r, "PH966B")
        self.assertContains(r, "Afinación estándar")
        self.assertContains(r, 'class="estado pedido"')
        self.assertContains(r, "Total a cobrar")
        # el vehículo queda en recientes
        self.assertEqual(self.client.session["recientes"][0]["modelo"], "Tsuru")

    def test_listas_marca_anio_modelo_motor(self):
        r = self.client.get(reverse("mostrador"))
        self.assertContains(r, '<option value="Nissan">Nissan</option>')
        self.assertContains(r, '<select name="anio" disabled>')  # sin marca no hay años
        # cada lista sale filtrada por la anterior
        d = self.client.get(reverse("vehiculo_listas"), {"armadora": "Nissan"}).json()
        self.assertIn(2003, d["anios"])
        self.assertEqual(d["modelos"], [])
        d = self.client.get(reverse("vehiculo_listas"), {"armadora": "Nissan", "anio": 2003}).json()
        self.assertEqual(d["modelos"], ["Tsuru"])
        d = self.client.get(reverse("vehiculo_listas"), {"armadora": "Nissan", "anio": 2003, "modelo": "Tsuru"}).json()
        self.assertEqual(d["motores"], ["1.6"])
        self.assertEqual(d["sel"]["litros"], "1.6")  # un solo motor: se elige solo
        # si cambian la marca, el año y el modelo que ya no cuadran se sueltan
        r = self.client.get(reverse("mostrador"), {"armadora": "Nissan", "anio": 1960, "modelo": "Tsuru"})
        self.assertEqual(r.context["sel"], {"armadora": "Nissan", "anio": None, "modelo": "", "litros": ""})
        self.assertEqual(r.context["paso"], "anio")
        r = self.client.get(reverse("mostrador"), {"armadora": "Marca que no existe", "anio": 2003})
        self.assertEqual(r.context["paso"], "marca")

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

    def test_captura_por_codigo(self):
        # sin guiones ni mayúsculas: es mi producto PH966B, entra directo
        r = self.client.post(reverse("ticket_codigo"), {"codigo": "ph-966b", "cantidad": "1", "next": "/"})
        self.assertRedirects(r, "/", fetch_redirect_response=False)
        self.assertEqual(self.client.session["ticket"]["lineas"],
                         [{"clave": f"producto:{self.fram.pk}", "pos": "", "cantidad": "1", "nivel": ""}])
        # el mismo código otra vez suma a su renglón; «2*» es la cantidad
        self.client.post(reverse("ticket_codigo"), {"codigo": "2*PH966B"})
        self.assertEqual(self.client.session["ticket"]["lineas"][0]["cantidad"], "3")
        # un código que solo está en la lista del proveedor entra sobre pedido
        self.client.post(reverse("ticket_codigo"), {"codigo": "GP37"})
        gp = PartidaListaProveedor.objects.get(codigo="GP-37")
        self.assertEqual(self.client.session["ticket"]["lineas"][1]["clave"], f"partida:{gp.pk}")
        # varias coincidencias por nombre: se muestran para elegir, con la cantidad
        r = self.client.post(reverse("ticket_codigo"), {"codigo": "filtro aceite", "cantidad": "2",
                                                        "next": "/?armadora=Nissan"})
        self.assertEqual(r["Location"], "/?armadora=Nissan&codigo=filtro+aceite&cantidad=2")
        r = self.client.get(r["Location"])
        self.assertContains(r, "Elige: «filtro aceite»")
        self.assertContains(r, "AP-FAC3682")
        self.assertNotContains(r, "CA6900")
        self.assertEqual(r.context["aqui"], "/?armadora=Nissan")  # al elegir no repite la búsqueda
        # lo que no existe avisa y no agrega nada
        r = self.client.post(reverse("ticket_codigo"), {"codigo": "ZZZ-999"}, follow=True)
        self.assertContains(r, "No encontré «ZZZ-999»")
        self.assertEqual(len(self.client.session["ticket"]["lineas"]), 2)

    def test_sugerencias_mientras_se_teclea(self):
        url = reverse("ticket_sugerir")
        d = self.client.get(url, {"q": "ph9"}).json()["sugerencias"]
        self.assertEqual(d[0]["codigo"], "PH966B")  # mi producto, con existencia y precio de lista
        self.assertEqual((d[0]["clave"], d[0]["hay"], d[0]["precio"], d[0]["precio_txt"]),
                         (f"producto:{self.fram.pk}", True, "98", "$98"))
        d = self.client.get(url, {"q": "gp 3"}).json()["sugerencias"]  # sin guion y con espacio
        self.assertEqual([s["codigo"] for s in d], ["GP-37"])
        self.assertFalse(d[0]["hay"])
        # contiene, no solo empieza con: «6900» encuentra CA6900
        self.assertIn("CA6900", [s["codigo"] for s in self.client.get(url, {"q": "6900"}).json()["sugerencias"]])
        # por descripción y aplicación
        d = self.client.get(url, {"q": "filtro aire"}).json()["sugerencias"]
        self.assertEqual([s["codigo"] for s in d], ["CA6900"])
        self.assertEqual(self.client.get(url, {"q": "p"}).json()["sugerencias"], [])  # muy corto

    def test_cantidad_y_precio_tecleados(self):
        url = reverse("ticket_codigo")
        # eligió la sugerencia: viene la clave, con su cantidad y un precio con IVA distinto al de lista
        self.client.post(url, {"codigo": "PH966B", "clave": f"producto:{self.fram.pk}", "cantidad": "2",
                               "precio": "$120"})
        self.assertEqual(self.client.session["ticket"]["lineas"],
                         [{"clave": f"producto:{self.fram.pk}", "pos": "", "cantidad": "2", "nivel": "", "precio": "120.00"}])
        r = self.client.get(reverse("mostrador"))
        self.assertContains(r, "precio especial")
        self.assertEqual(r.context["total"], D("240"))
        # abajo del costo (52.43 sin IVA) no lo deja quien atiende el mostrador, y no dice el costo
        r = self.client.post(url, {"codigo": "GP-37", "precio": "50"}, follow=True)
        self.assertContains(r, "queda abajo del costo")
        self.assertNotContains(r, "66.17")
        self.assertEqual(len(self.client.session["ticket"]["lineas"]), 1)
        # cambiar el precio desde el renglón del ticket; vacío regresa al de la lista
        self.client.post(reverse("ticket_precio", args=[0]), {"precio": "110"})
        self.assertEqual(self.client.session["ticket"]["lineas"][0]["precio"], "110.00")
        self.client.post(reverse("ticket_precio", args=[0]), {"precio": "40"})  # abajo del costo: se queda en 110
        self.assertEqual(self.client.session["ticket"]["lineas"][0]["precio"], "110.00")
        # un administrador sí puede, con aviso
        self.user.is_staff = True
        self.user.save()
        r = self.client.post(reverse("ticket_precio", args=[0]), {"precio": "55"}, follow=True)
        self.assertContains(r, "Ojo: $55 queda abajo del costo")
        self.client.post(reverse("ticket_precio", args=[0]), {"precio": ""})
        self.assertNotIn("precio", self.client.session["ticket"]["lineas"][0])
        # al cobrar se respeta el precio tecleado, también en lo que queda sobre pedido
        self.client.post(reverse("ticket_precio", args=[0]), {"precio": "115.50"})
        self.client.post(url, {"codigo": "GP-37", "cantidad": "1", "precio": "130"})
        self.client.post(reverse("caja"), {"accion": "abrir", "fondo": "500"})
        self.client.post(reverse("ticket_cobrar"), {"forma_pago": "EFECTIVO"})
        venta = Venta.objects.get()
        self.assertEqual(venta.lineas.get().precio_unitario, D("115.50"))
        self.assertEqual(venta.total, D("231.00"))
        self.assertEqual((venta.pedido[0]["codigo"], venta.pedido[0]["precio"]), ("GP-37", "130.00"))

    def test_leer_captura(self):
        self.assertEqual(m.leer_captura("4*PH966B"), ("PH966B", D("4")))
        self.assertEqual(m.leer_captura("3 x GP-37"), ("GP-37", D("3")))
        self.assertEqual(m.leer_captura("15208X123", "2"), ("15208X123", D("2")))  # la x pegada es parte del código
        self.assertEqual(m.leer_captura("CA6900", ""), ("CA6900", D("1")))

    def test_remision_de_la_venta(self):
        self.client.post(reverse("caja"), {"accion": "abrir", "fondo": "500"})
        base = {"anio": 2003, "armadora": "Nissan", "modelo": "Tsuru", "litros": "1.6"}
        ca = f"partida:{PartidaListaProveedor.objects.get(codigo='CA6900').pk}"
        self.client.post(reverse("ticket_agregar"), {"clave": f"producto:{self.fram.pk}", "pos": "aceite"})
        self.client.post(reverse("ticket_agregar"), {"clave": ca, "pos": "aire"})
        r = self.client.post(reverse("ticket_cobrar"), {"forma_pago": "EFECTIVO", **base}, follow=True)
        venta = Venta.objects.get()
        self.assertEqual(venta.vehiculo, "Nissan Tsuru 2003 · 1.6 L")
        self.assertEqual([p["codigo"] for p in venta.pedido], ["CA6900"])
        # al volver al mostrador se ofrece imprimir la remisión
        self.assertContains(r, reverse("remision", args=[venta.folio]) + "?imprimir=1")
        r = self.client.get(reverse("remision", args=[venta.folio]))
        for texto in (venta.folio, "Remisión", "Nissan Tsuru 2003", "PH966B", "Quedó sobre pedido", "CA6900",
                      "$98", "IVA 16%", "no es un comprobante fiscal", "Recibí de conformidad"):
            self.assertContains(r, texto)
        self.assertContains(self.client.get(reverse("remision", args=[venta.folio]), {"formato": "ticket"}), "doc-ticket")
        # se encuentra en Remisiones por folio o por cliente
        self.assertContains(self.client.get(reverse("remisiones"), {"q": "público"}), venta.folio)
        self.assertNotContains(self.client.get(reverse("remisiones"), {"q": "nadie"}), venta.folio)
        self.assertEqual(self.client.get(reverse("remision", args=["VTA-999999"])).status_code, 404)
