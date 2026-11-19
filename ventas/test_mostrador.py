"""Pantalla de mostrador: vehículo → niveles → ticket → cobro (venta + pedido al proveedor)."""
from datetime import timedelta
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
from ventas import nip as nips
from ventas.models import Cliente, PedidoCliente, TicketGuardado, Turno, Venta

TSURU = "NISSAN. TSURU L4 1.6 (90-04)"
NIP, NIP_ADMIN = "4821", "7305"


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
        nips.asignar(self.user, NIP)
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
        # con vehículo elegido hay cómo limpiarlo; sin vehículo, no
        self.assertContains(r, f'<a class="limpiar" href="{reverse("mostrador")}"')
        self.assertContains(r, "Cambiar vehículo")
        self.assertNotContains(self.client.get(reverse("mostrador")), 'class="limpiar"')

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
        abrir = self.client.post(reverse("caja"), {"nip": NIP, "accion": "abrir", "fondo": "500"})
        self.assertRedirects(abrir, reverse("mostrador"), fetch_redirect_response=False)
        base = {"anio": 2003, "armadora": "Nissan", "modelo": "Tsuru", "litros": "1.6"}
        fram = f"producto:{self.fram.pk}"
        ca = f"partida:{PartidaListaProveedor.objects.get(codigo='CA6900').pk}"
        r = self.client.post(reverse("ticket_agregar"), {"clave": fram, "pos": "aceite", "nivel": "STD", **base,
                                                         "next": "/?anio=2003&armadora=Nissan&modelo=Tsuru"})
        self.assertEqual(r["Location"], "/?anio=2003&armadora=Nissan&modelo=Tsuru")  # regresa a la misma hoja
        self.client.post(reverse("ticket_agregar"), {"clave": ca, "pos": "aire", "nivel": "PRE", **base})
        self.client.post(reverse("ticket_cantidad", args=[0]), {"cantidad": "2"})

        r = self.client.post(reverse("ticket_cobrar"), {"nip": NIP, "forma_pago": "EFECTIVO", **base}, follow=True)
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
        r = self.client.post(reverse("ticket_cobrar"), {"nip": NIP, "forma_pago": "EFECTIVO"})
        self.assertRedirects(r, reverse("caja"), fetch_redirect_response=False)
        self.assertFalse(Venta.objects.exists())

    def test_credito_solo_a_clientes_con_credito(self):
        self.client.post(reverse("caja"), {"nip": NIP, "accion": "abrir", "fondo": "0"})
        self.client.post(reverse("ticket_agregar"), {"clave": f"producto:{self.fram.pk}", "pos": "aceite"})
        self.client.post(reverse("ticket_cobrar"), {"nip": NIP, "forma_pago": "CREDITO"})
        self.assertFalse(Venta.objects.exists())
        taller = Cliente.objects.create(nombre="Taller Ruiz", lista_precio=self.mostrador, dias_credito=15, limite_credito=1000)
        self.client.post(reverse("ticket_cliente"), {"cliente": taller.pk})
        self.client.post(reverse("ticket_cobrar"), {"nip": NIP, "forma_pago": "CREDITO"})
        self.assertEqual(Venta.objects.get().estado, Venta.Estado.CREDITO)
        # el siguiente cliente ya no hereda al taller ni su crédito
        self.assertEqual(m.Ticket(self.client.session).cliente.nombre, "Público en general")

    def test_corte_de_caja(self):
        self.client.post(reverse("caja"), {"nip": NIP, "accion": "abrir", "fondo": "500"})
        self.client.post(reverse("ticket_agregar"), {"clave": f"producto:{self.fram.pk}", "pos": "aceite"})
        self.client.post(reverse("ticket_cobrar"), {"nip": NIP, "forma_pago": "EFECTIVO"})
        r = self.client.post(reverse("caja"), {"nip": NIP, "accion": "cerrar", "efectivo": "590", "tarjeta": "0", "transferencia": "0"},
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
        # se confirma en la franja de captura y el renglón sale resaltado; al recargar ya no
        r = self.client.get("/")
        self.assertContains(r, 'class="captura-hecho"')
        self.assertContains(r, "1 × $98 = <b>$98</b>")
        self.assertContains(r, '<span class="renglon-nuevo">Agregado</span>')
        self.assertNotContains(self.client.get("/"), 'class="captura-hecho"')
        # el mismo código otra vez suma a su renglón; «2*» es la cantidad
        self.client.post(reverse("ticket_codigo"), {"codigo": "2*PH966B"})
        self.assertEqual(self.client.session["ticket"]["lineas"][0]["cantidad"], "3")
        self.assertContains(self.client.get("/"), "+2, ya van 3 × $98 = <b>$294</b>")
        self.client.post(reverse("ticket_cantidad", args=[0]), {"cantidad": "4"})
        self.assertContains(self.client.get("/"), '<span class="renglon-nuevo">Cambió</span>')
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
        # el código exacto va primero aunque haya otros que empiezan igual
        PartidaListaProveedor.objects.filter(codigo="15208-H8916").update(codigo="PH966B-X")
        d = self.client.get(url, {"q": "ph966b"}).json()["sugerencias"]
        self.assertEqual([s["codigo"] for s in d][:2], ["PH966B", "PH966B-X"])
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
        self.client.post(reverse("caja"), {"nip": NIP, "accion": "abrir", "fondo": "500"})
        self.client.post(reverse("ticket_cobrar"), {"nip": NIP, "forma_pago": "EFECTIVO"})
        venta = Venta.objects.get()
        self.assertEqual(venta.lineas.get().precio_unitario, D("115.50"))
        self.assertEqual(venta.total, D("231.00"))
        self.assertEqual((venta.pedido[0]["codigo"], venta.pedido[0]["precio"]), ("GP-37", "130.00"))

    def test_vaciar_y_deshacer(self):
        clave = f"producto:{self.fram.pk}"
        self.client.post(reverse("ticket_agregar"), {"clave": clave})
        self.assertContains(self.client.get("/"), "Vaciar ticket")
        self.client.post(reverse("ticket_vaciar"))
        self.assertEqual(self.client.session["ticket"]["lineas"], [])
        self.assertContains(self.client.get("/"), "Vaciaste el ticket.")
        self.client.post(reverse("ticket_deshacer"))
        self.assertEqual([ln["clave"] for ln in self.client.session["ticket"]["lineas"]], [clave])
        # si después de vaciar ya empezó otro ticket, no se ofrece deshacer
        self.client.post(reverse("ticket_vaciar"))
        self.client.post(reverse("ticket_codigo"), {"codigo": "GP-37"})
        self.assertNotContains(self.client.get("/"), "Vaciaste el ticket.")

    def test_paga_con_cambio_y_foco(self):
        html = self.client.get("/").content.decode()
        self.assertRegex(html, r'id="cliente-q"[^>]*autofocus')  # venta nueva: se empieza por el cliente
        self.assertNotRegex(html, r'id="codigo"[^>]*autofocus')
        self.assertNotRegex(html, r'id="q"[^>]*autofocus')
        self.assertLess(html.index('class="panel cliente-panel'), html.index('class="panel vehiculo-panel'))
        # ya elegido (aunque sea Público en general), sigue el código
        self.client.post(reverse("ticket_cliente"), {"cliente": m.cliente_mostrador().pk})
        html = self.client.get("/").content.decode()
        self.assertRegex(html, r'id="codigo"[^>]*autofocus')
        self.assertNotRegex(html, r'id="cliente-q"[^>]*autofocus')
        self.client.post(reverse("caja"), {"nip": NIP, "accion": "abrir", "fondo": "500"})
        self.client.post(reverse("ticket_agregar"), {"clave": f"producto:{self.fram.pk}"})
        html = self.client.get("/").content.decode()
        self.assertContains(self.client.get("/"), 'id="paga-con"')
        self.assertRegex(html, r'id="codigo"[^>]*autofocus')  # con piezas en el ticket, el código
        # no alcanza: no cobra
        r = self.client.post(reverse("ticket_cobrar"), {"nip": NIP, "forma_pago": "EFECTIVO", "paga_con": "50"}, follow=True)
        self.assertContains(r, "Paga con $50 y son $98: faltan $48.")
        self.assertFalse(Venta.objects.exists())
        r = self.client.post(reverse("ticket_cobrar"), {"nip": NIP, "forma_pago": "EFECTIVO", "paga_con": "200"}, follow=True)
        self.assertContains(r, "Cambio $102")
        self.assertEqual(Venta.objects.get().total, D("98"))

    def test_credito_preseleccionado(self):
        taller = Cliente.objects.create(nombre="Taller Ramírez", lista_precio=self.mostrador, dias_credito=15,
                                        limite_credito=3000)
        self.client.post(reverse("ticket_cliente"), {"cliente": taller.pk})
        self.client.post(reverse("ticket_agregar"), {"clave": f"producto:{self.fram.pk}"})
        html = self.client.get("/").content.decode()
        self.assertRegex(html, r'value="CREDITO" checked')
        self.assertNotRegex(html, r'value="EFECTIVO" checked')

    def test_leer_captura(self):
        self.assertEqual(m.leer_captura("4*PH966B"), ("PH966B", D("4")))
        self.assertEqual(m.leer_captura("3 x GP-37"), ("GP-37", D("3")))
        self.assertEqual(m.leer_captura("15208X123", "2"), ("15208X123", D("2")))  # la x pegada es parte del código
        self.assertEqual(m.leer_captura("CA6900", ""), ("CA6900", D("1")))

    def test_remision_de_la_venta(self):
        self.client.post(reverse("caja"), {"nip": NIP, "accion": "abrir", "fondo": "500"})
        base = {"anio": 2003, "armadora": "Nissan", "modelo": "Tsuru", "litros": "1.6"}
        ca = f"partida:{PartidaListaProveedor.objects.get(codigo='CA6900').pk}"
        self.client.post(reverse("ticket_agregar"), {"clave": f"producto:{self.fram.pk}", "pos": "aceite"})
        self.client.post(reverse("ticket_agregar"), {"clave": ca, "pos": "aire"})
        r = self.client.post(reverse("ticket_cobrar"), {"nip": NIP, "forma_pago": "EFECTIVO", **base}, follow=True)
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

    def test_alta_rapida_de_cliente(self):
        url = reverse("ticket_cliente_nuevo")
        r = self.client.post(url, {"nombre": " ", "next": reverse("mostrador")}, follow=True)
        self.assertContains(r, "Escribe el nombre del cliente")
        r = self.client.post(url, {"nombre": "Taller Ramírez", "telefono": "4421234567", "rfc": "tara800101ab1",
                                   "dias": "0", "limite": "0"}, follow=True)
        c = Cliente.objects.get(nombre="Taller Ramírez")
        self.assertEqual((c.rfc, c.telefono, c.dias_credito, c.lista_precio), ("TARA800101AB1", "4421234567", 0, self.mostrador))
        self.assertEqual(m.Ticket(self.client.session).cliente, c)
        self.assertContains(r, "dado de alta y elegido en el ticket")
        self.assertContains(r, f'<b>Taller Ramírez</b> <span class="mono">{c.folio}</span>')
        self.assertContains(r, f'<input type="hidden" name="cliente" value="{c.pk}">', html=True)
        # el formulario pide NIP de administrador solo si le dan crédito
        self.assertContains(r, 'data-nip="admin" data-nip-cuando="dias"')
        # mismo nombre: no se duplica, se elige el que ya está
        self.client.post(url, {"nombre": "taller ramírez"})
        self.assertEqual(Cliente.objects.filter(nombre__iexact="taller ramírez").count(), 1)
        # RFC mal escrito: el formulario se queda abierto con lo tecleado
        r = self.client.post(url, {"nombre": "Juan Pérez", "rfc": "ABC", "next": reverse("mostrador")}, follow=True)
        self.assertContains(r, "El RFC va como XAXX010101000")
        self.assertContains(r, 'value="Juan Pérez"')
        self.assertContains(r, "data-servidor open")
        self.assertFalse(Cliente.objects.filter(nombre="Juan Pérez").exists())
        # crédito: con el NIP del cajero no; con el de un administrador sí
        datos = {"nombre": "Flotilla Sur", "dias": "30", "limite": "5,000", "next": reverse("mostrador")}
        self.assertContains(self.client.post(url, datos, follow=True), "Teclea el NIP de administrador")
        r = self.client.post(url, {**datos, "nip": NIP}, follow=True)
        self.assertContains(r, "esto lo autoriza un administrador")
        self.assertContains(r, 'value="Flotilla Sur"')  # lo tecleado se conserva
        self.assertFalse(Cliente.objects.filter(nombre="Flotilla Sur").exists())
        admin = get_user_model().objects.create_user("alexis", is_staff=True)
        nips.asignar(admin, NIP_ADMIN)
        r = self.client.post(url, {**datos, "nip": NIP_ADMIN}, follow=True)
        self.assertContains(r, "30 días de crédito hasta $5,000")
        f = Cliente.objects.get(nombre="Flotilla Sur")
        self.assertEqual((f.dias_credito, f.limite_credito), (30, D("5000")))
        self.assertContains(self.client.post(url, {"nombre": "Otro", "dias": "15", "limite": "0"}, follow=True), "ponle también un límite")

    def test_ticket_en_espera_y_retomar(self):
        base = {"armadora": "Nissan", "anio": "2003", "modelo": "Tsuru", "litros": "1.6"}
        fram = f"producto:{self.fram.pk}"
        self.client.post(reverse("ticket_agregar"), {"clave": fram, "pos": "aceite", "nivel": "STD", **base})
        taller = Cliente.objects.create(nombre="Taller Ruiz", lista_precio=self.mostrador)
        self.client.post(reverse("ticket_cliente"), {"cliente": taller.pk})
        r = self.client.post(reverse("ticket_espera"), base, follow=True)
        self.assertRedirects(r, reverse("mostrador"))
        self.assertContains(r, "El ticket de Taller Ruiz ($98) quedó en espera")
        self.assertEqual(self.client.session["ticket"]["lineas"], [])
        g = TicketGuardado.objects.get(tipo="ESP")
        self.assertEqual((g.folio, g.cliente, g.vehiculo, g.total), ("ESP-000001", taller, "Nissan Tsuru 2003 · 1.6 L", D("98")))
        self.assertEqual(m.Ticket(self.client.session).cliente.nombre, "Público en general")
        self.assertContains(r, "En espera")
        self.assertContains(r, reverse("ticket_retomar", args=[g.folio]))
        # mientras, se atiende a otro; al retomar, lo de este otro se va a espera para no perderlo
        self.client.post(reverse("ticket_cliente"), {"cliente": m.cliente_mostrador().pk})
        self.client.post(reverse("ticket_codigo"), {"codigo": "PH966B", "cantidad": "2"})
        r = self.client.post(reverse("ticket_retomar", args=[g.folio]))
        self.assertRedirects(r, "/?armadora=Nissan&anio=2003&modelo=Tsuru&litros=1.6", fetch_redirect_response=False)
        t = m.Ticket(self.client.session)
        self.assertEqual((t.cliente, [ln.cantidad for ln in t.lineas()]), (taller, [1]))
        otro = TicketGuardado.objects.get(tipo="ESP", retomado__isnull=True)
        self.assertEqual((otro.cliente.nombre, otro.total), ("Público en general", D("196")))
        g.refresh_from_db()
        self.assertIsNotNone(g.retomado)
        self.assertContains(self.client.post(reverse("ticket_retomar", args=[g.folio]), follow=True), "ya se retomó")
        # descartar
        r = self.client.post(reverse("ticket_descartar", args=[otro.folio]), {"next": "/"}, follow=True)
        self.assertContains(r, "Descartaste el ticket en espera de Público en general")
        self.assertFalse(TicketGuardado.objects.filter(tipo="ESP", retomado__isnull=True).exists())
        self.assertNotContains(r, 'class="espera"')

    def test_cotizacion_imprimible_respeta_sus_precios(self):
        self.client.post(reverse("ticket_codigo"), {"codigo": "PH966B", "cantidad": "2"})
        self.client.post(reverse("ticket_codigo"), {"codigo": "GP-37"})  # no está en anaquel: sobre pedido
        TicketGuardado.objects.create(tipo="ESP", folio="ESP-000007", cliente=m.cliente_mostrador())
        r = self.client.post(reverse("ticket_cotizar"), {"nombre": "Sr. Pérez"})
        g = TicketGuardado.objects.get(tipo="COT")
        self.assertEqual(g.folio, "COT-000001")  # cada tipo lleva su consecutivo
        self.assertRedirects(r, reverse("cotizacion", args=[g.folio]) + "?imprimir=1", fetch_redirect_response=False)
        self.assertEqual(len(self.client.session["ticket"]["lineas"]), 2)  # el ticket se queda
        r = self.client.get(reverse("cotizacion", args=[g.folio]))
        self.assertContains(r, "Cotización")
        self.assertContains(r, "Sr. Pérez")
        self.assertContains(r, "sobre pedido")
        self.assertContains(r, "Precios válidos hasta el")
        self.assertEqual(g.total, D("196") + D("105"))
        # sale en la lista de cotizaciones
        r = self.client.get(reverse("cotizaciones"), {"q": "pérez"})
        self.assertEqual(list(r.context["cotizaciones"]), [g])
        # sube el precio; al pasarla al ticket se respeta lo cotizado mientras está vigente
        Precio.objects.filter(producto=self.fram).update(precio=110)
        self.client.post(reverse("ticket_vaciar"))
        r = self.client.post(reverse("ticket_retomar", args=[g.folio]), follow=True)
        self.assertContains(r, "Van los precios cotizados")
        fram = next(ln for ln in m.Ticket(self.client.session).lineas() if ln.opcion.codigo == "PH966B")
        self.assertEqual(fram.precio, D("98"))
        # vencida: van los precios de hoy
        TicketGuardado.objects.filter(pk=g.pk).update(fecha=g.fecha - timedelta(days=30))
        self.client.post(reverse("ticket_vaciar"))
        r = self.client.post(reverse("ticket_retomar", args=[g.folio]), follow=True)
        self.assertContains(r, "van los precios de hoy")
        fram = next(ln for ln in m.Ticket(self.client.session).lineas() if ln.opcion.codigo == "PH966B")
        self.assertEqual(fram.precio, D("110"))
        # ticket vacío: no hay nada que cotizar
        self.client.post(reverse("ticket_vaciar"))
        self.assertContains(self.client.post(reverse("ticket_cotizar"), {"next": "/"}, follow=True), "El ticket está vacío")

    def test_seguimiento_de_pedidos(self):
        self.client.post(reverse("caja"), {"nip": NIP, "accion": "abrir", "fondo": "0"})
        base = {"anio": 2003, "armadora": "Nissan", "modelo": "Tsuru", "litros": "1.6"}
        ca = f"partida:{PartidaListaProveedor.objects.get(codigo='CA6900').pk}"
        self.client.post(reverse("ticket_agregar"), {"clave": ca, "pos": "aire", "nivel": "PRE", **base})
        self.client.post(reverse("ticket_agregar"), {"clave": f"producto:{self.fram.pk}", "pos": "aceite", **base})
        r = self.client.get(reverse("mostrador"), base)
        self.assertContains(r, 'form="cobro-form"')  # «para avisarle» va con el cobro
        r = self.client.post(reverse("ticket_cobrar"), {"nip": NIP, "forma_pago": "EFECTIVO", "contacto": "Sr. Pérez 442 111 2233", **base},
                             follow=True)
        self.assertContains(r, "Lo ves en Pedidos")
        p = PedidoCliente.objects.get()
        venta, oc = Venta.objects.get(), OrdenCompra.objects.get()
        self.assertEqual((p.codigo, p.cantidad, p.precio, p.contacto, p.venta, p.linea_oc.orden),
                         ("CA6900", 1, D("104"), "Sr. Pérez 442 111 2233", venta, oc))
        r = self.client.get(reverse("pedidos"))
        self.assertContains(r, "Sr. Pérez 442 111 2233")
        self.assertContains(r, f"{oc.folio} sin enviar")
        self.assertNotContains(r, 'class="insignia"')
        # todavía no llega: no se puede entregar
        r = self.client.post(reverse("pedido_entregar", args=[p.pk]), follow=True)
        self.assertContains(r, "Todavía no llega")
        # llega la mercancía
        registrar_entrada(p.producto, 1, D("65.64"))
        r = self.client.get(reverse("pedidos"))
        self.assertContains(r, "Ya está en anaquel")
        self.assertContains(r, '<span class="insignia" title="Ya llegaron: hay que avisar">1</span>', html=True)
        # mientras, alguien más está en el ticket: se aparta en espera
        otro = Cliente.objects.create(nombre="Taller Ruiz", lista_precio=self.mostrador)
        self.client.post(reverse("ticket_cliente"), {"cliente": otro.pk})
        self.client.post(reverse("ticket_codigo"), {"codigo": "PH966B"})
        r = self.client.post(reverse("pedido_entregar", args=[p.pk]), follow=True)
        self.assertContains(r, "con el precio que se le dijo")
        self.assertContains(r, "quedó en espera (Taller Ruiz)")
        t = m.Ticket(self.client.session)
        self.assertEqual((t.cliente.nombre, [(ln.opcion.codigo, ln.precio, ln.pedido) for ln in t.lineas()]),
                         ("Público en general", [("CA6900", D("104"), p.pk)]))
        self.client.post(reverse("ticket_cobrar"), {"nip": NIP, "forma_pago": "EFECTIVO"})
        p.refresh_from_db()
        self.assertEqual(p.estado, PedidoCliente.Estado.ENTREGADO)
        self.assertEqual(p.venta_entrega.total, D("104"))
        self.assertEqual(OrdenCompra.objects.count(), 1)  # no se volvió a pedir
        self.assertContains(self.client.get(reverse("pedidos"), {"ver": "cerrados"}), p.venta_entrega.folio)

    def test_cancelar_pedido_ajusta_la_orden(self):
        self.client.post(reverse("caja"), {"nip": NIP, "accion": "abrir", "fondo": "0"})
        for _ in range(2):
            self.client.post(reverse("ticket_codigo"), {"codigo": "GP-37"})
            self.client.post(reverse("ticket_cobrar"), {"nip": NIP, "forma_pago": "EFECTIVO"})
        p1, p2 = PedidoCliente.objects.order_by("pk")
        self.assertIsNone(p1.venta)  # todo fue sobre pedido: no hubo venta
        oc = OrdenCompra.objects.get()
        self.assertEqual(oc.lineas.count(), 2)
        r = self.client.post(reverse("pedido_cancelar", args=[p1.pk]), follow=True)
        self.assertContains(r, "Teclea tu NIP")
        r = self.client.post(reverse("pedido_cancelar", args=[p1.pk]), {"nip": NIP}, follow=True)
        self.assertContains(r, "se quitó de la orden en borrador")
        p1.refresh_from_db()
        self.assertEqual(p1.estado, PedidoCliente.Estado.CANCELADO)
        self.assertEqual(oc.lineas.count(), 1)
        # si la orden ya se envió, se avisa en vez de tocarla
        OrdenCompra.objects.filter(pk=oc.pk).update(estado=OrdenCompra.Estado.ENVIADA)
        r = self.client.post(reverse("pedido_cancelar", args=[p2.pk]), {"nip": NIP}, follow=True)
        self.assertContains(r, f"{oc.folio} ya se envió")
        self.assertEqual(oc.lineas.count(), 1)

    def test_cobrar_y_caja_piden_nip_y_firman_quien_lo_hace(self):
        self.client.post(reverse("caja"), {"accion": "abrir", "fondo": "0"})
        self.assertFalse(Turno.objects.exists())  # sin NIP no abre
        self.assertContains(self.client.get(reverse("caja")), 'data-nip="vendedor"')
        self.client.post(reverse("caja"), {"nip": NIP, "accion": "abrir", "fondo": "0"})
        t = Turno.objects.get()
        self.assertEqual((t.usuario, t.abierto_por), (self.user, self.user))
        self.client.post(reverse("ticket_agregar"), {"clave": f"producto:{self.fram.pk}", "pos": "aceite"})
        self.assertContains(self.client.get(reverse("mostrador")), 'data-nip="vendedor" data-nip-para="Cobrar $98"')
        r = self.client.post(reverse("ticket_cobrar"), {"forma_pago": "EFECTIVO"}, follow=True)
        self.assertContains(r, "Teclea tu NIP")
        self.assertFalse(Venta.objects.exists())
        self.assertEqual(len(self.client.session["ticket"]["lineas"]), 1)  # el ticket sigue ahí
        # otro vendedor en la misma caja firma la venta con su NIP
        pedro = get_user_model().objects.create_user("pedro", first_name="Pedro")
        nips.asignar(pedro, "5094")
        self.client.post(reverse("ticket_cobrar"), {"forma_pago": "EFECTIVO", "nip": "5094"})
        v = Venta.objects.get()
        self.assertEqual((v.usuario, v.turno), (pedro, t))
        self.assertContains(self.client.get(reverse("remision", args=[v.folio])), "Pedro")
        self.client.post(reverse("caja"), {"nip": "5094", "accion": "cerrar", "efectivo": "98", "tarjeta": "0", "transferencia": "0"})
        t.refresh_from_db()
        self.assertEqual(t.cerrado_por, pedro)

    def test_reglas_del_nip(self):
        from django.core.exceptions import ValidationError

        otro = get_user_model().objects.create_user("pedro")
        for malo in ("12", "1234567", "12a4", "1111", "1234", "6543"):
            with self.assertRaises(ValidationError):
                nips.asignar(otro, malo)
        with self.assertRaises(ValidationError):  # el NIP dice quién es: no se repite
            nips.asignar(otro, NIP)
        nips.asignar(otro, "90817")
        self.assertEqual(nips.quien(self.client.session, " 90817 "), otro)
        otro.is_active = False
        otro.save()
        with self.assertRaises(ValidationError):
            nips.quien({}, "90817")
