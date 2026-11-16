"""Remisiones › Nueva: cliente por folio o celular, partidas con descuento, borradores, emitir y cobrar en caja."""
from decimal import Decimal as D

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.urls import reverse

from compras.listas import importar
from compras.tests import lista
from ventas import nip as nips
from ventas import remisiones as rem
from ventas.models import Cliente, FormaPago, RemisionBorrador, Venta
from ventas.servicios import (cancelar_venta, cobrar_remision, emitir_remision, esperado_en_caja, por_que_no_cancelar,
                              registrar_devolucion, registrar_venta)
from ventas.tests import Base

NIP, NIP_ADMIN = "4821", "7305"


class RemisionesBase(Base):
    def setUp(self):
        super().setUp()
        nips.asignar(self.user, NIP)
        self.admin = get_user_model().objects.create_user("alexis", is_staff=True)
        nips.asignar(self.admin, NIP_ADMIN)
        self.client.force_login(self.user)
        self.clave_filtro = f"producto:{self.filtro.pk}"
        self.clave_aceite = f"producto:{self.aceite.pk}"

    def forma(self, cliente, filas, accion="emitir", condiciones="CONTADO", nip=NIP, **extra):
        datos = {"cliente": cliente.pk if cliente else "", "condiciones": condiciones, "accion": accion, "nip": nip,
                 "orden_compra": "", "vehiculo": "", "observaciones": "", **extra}
        for campo in rem.CAMPOS_LINEA:
            datos[campo] = [f.get(campo, "") for f in filas] + [""]  # la fila vacía del final
        return datos

    def emitir(self, cliente, filas, **kw):
        return self.client.post(reverse("remision_nueva"), self.forma(cliente, filas, **kw))


class BuscarClienteTests(RemisionesBase):
    def test_por_folio_celular_o_nombre(self):
        self.taller.telefono = "442 123-4567"
        self.taller.save()
        folio = self.taller.folio
        self.assertEqual(folio, f"CTE-{self.taller.pk:04d}")
        for texto in (folio, folio.lower(), f"cte{self.taller.pk}", str(self.taller.pk), "4421234567", "123 4567",
                      "hermanos ruiz"):
            with self.subTest(texto=texto):
                self.assertIn(self.taller, rem.buscar_clientes(texto))
        self.assertEqual(rem.buscar_clientes("CTE-9999"), [])
        self.assertEqual(rem.buscar_clientes(""), [])
        self.taller.activo = False
        self.taller.save()
        self.assertEqual(rem.buscar_clientes(folio), [])

    def test_la_busqueda_da_su_credito_disponible(self):
        registrar_venta(self.turno, self.taller, [{"producto": self.filtro, "cantidad": 2}], FormaPago.CREDITO)  # 214
        r = self.client.get(reverse("clientes_buscar"), {"q": self.taller.folio})
        c = r.json()["clientes"][0]
        self.assertEqual((c["folio"], c["dias_credito"], c["saldo"], c["disponible_txt"]),
                         (self.taller.folio, 15, "$214", "$786"))

    def test_remisiones_se_buscan_por_folio_del_cliente(self):
        v = registrar_venta(self.turno, self.taller, [{"producto": self.filtro, "cantidad": 1}], FormaPago.EFECTIVO)
        otra = registrar_venta(self.turno, self.publico, [{"producto": self.filtro, "cantidad": 1}], FormaPago.EFECTIVO)
        r = self.client.get(reverse("remisiones"), {"q": self.taller.folio})
        self.assertContains(r, v.folio)
        self.assertNotContains(r, otra.folio)


class EmitirTests(RemisionesBase):
    def test_de_contado_queda_por_cobrar_en_caja_y_saca_inventario(self):
        r = self.emitir(self.publico, [{"clave": self.clave_filtro, "codigo": "GP-37", "cantidad": "2"},
                                       {"clave": self.clave_aceite, "codigo": "5W30-G", "cantidad": "4.5"}],
                        orden_compra="OC-778", observaciones="Recibe Juan", vehiculo="Tsuru 2015")
        v = Venta.objects.get()
        self.assertRedirects(r, reverse("venta", args=[v.folio]))
        self.assertEqual((v.folio, v.origen, v.estado, v.forma_pago, v.turno), ("REM-000001", "REMISION", "POR_COBRAR", "", None))
        self.assertEqual((v.total, v.saldo), (D("851.50"), D("851.50")))  # 2 × 122 + 4.5 × 135
        self.assertEqual((v.orden_compra, v.observaciones, v.vehiculo, v.usuario), ("OC-778", "Recibe Juan", "Tsuru 2015", self.user))
        self.filtro.refresh_from_db()
        self.aceite.refresh_from_db()
        self.assertEqual((self.filtro.existencia, self.aceite.existencia), (D("8"), D("203.5")))
        # no entra al corte hasta que se cobra
        self.assertEqual(esperado_en_caja(self.turno)[FormaPago.EFECTIVO], D("500"))
        caja = self.client.get(reverse("caja"))
        self.assertContains(caja, "Remisiones por cobrar")
        self.assertContains(caja, v.folio)
        self.assertContains(caja, 'title="Remisiones de contado por cobrar">1<')

    def test_el_folio_rem_lleva_su_propia_serie(self):
        registrar_venta(self.turno, self.publico, [{"producto": self.filtro, "cantidad": 1}], FormaPago.EFECTIVO)
        a = emitir_remision(self.publico, [{"producto": self.filtro, "cantidad": 1}], "CONTADO", self.user)
        b = emitir_remision(self.taller, [{"producto": self.filtro, "cantidad": 1}], "CREDITO", self.user)
        self.assertEqual((a.folio, b.folio), ("REM-000001", "REM-000002"))
        self.assertTrue(Venta.objects.filter(folio__startswith="VTA-").exists())

    def test_a_credito_va_a_por_cobrar_con_su_vencimiento(self):
        self.emitir(self.taller, [{"clave": self.clave_filtro, "cantidad": "2"}], condiciones="CREDITO")
        v = Venta.objects.get()
        self.assertEqual((v.estado, v.forma_pago, v.saldo, v.total), ("CREDITO", "CREDITO", D("214.00"), D("214.00")))  # lista Taller
        self.assertIsNotNone(v.vence)
        self.assertEqual(self.taller.saldo(), D("214.00"))

    def test_credito_no_pasa_del_limite_ni_se_da_a_quien_no_tiene(self):
        r = self.emitir(self.taller, [{"clave": self.clave_filtro, "cantidad": "10"}], condiciones="CREDITO")  # 1,070 > 1,000
        self.assertEqual(r.status_code, 400)
        self.assertContains(r, "excede el crédito disponible", status_code=400)
        r = self.emitir(self.publico, [{"clave": self.clave_filtro, "cantidad": "1"}], condiciones="CREDITO")
        self.assertContains(r, "no tiene crédito autorizado", status_code=400)
        self.assertFalse(Venta.objects.exists())
        self.filtro.refresh_from_db()
        self.assertEqual(self.filtro.existencia, D("10"))

    def test_sin_cliente_sin_partidas_o_sin_nip_no_se_emite(self):
        r = self.emitir(None, [{"clave": self.clave_filtro, "cantidad": "1"}])
        self.assertContains(r, "Elige al cliente", status_code=400)
        r = self.emitir(self.publico, [])
        self.assertContains(r, "Agrega al menos una partida", status_code=400)
        r = self.emitir(self.publico, [{"clave": self.clave_filtro, "cantidad": "1"}], nip="0000")
        self.assertEqual(r.status_code, 400)
        r = self.emitir(self.publico, [{"clave": self.clave_filtro, "cantidad": ""}])
        self.assertContains(r, "Escribe la cantidad de GP-37", status_code=400)
        self.assertFalse(Venta.objects.exists())

    def test_codigo_tecleado_sin_elegir_de_la_lista(self):
        self.emitir(self.publico, [{"codigo": "gp-37", "cantidad": "1"}])
        self.assertEqual(Venta.objects.get().lineas.get().producto, self.filtro)
        r = self.emitir(self.publico, [{"codigo": "NO-EXISTE", "cantidad": "1"}])
        self.assertContains(r, "No encontré «NO-EXISTE»", status_code=400)

    def test_descuento_por_partida_y_precio_tecleado(self):
        self.emitir(self.publico, [{"clave": self.clave_filtro, "cantidad": "2", "descuento": "10"},
                                   {"clave": self.clave_aceite, "cantidad": "1", "precio": "1,140.00", "descuento": "5"}])
        v = Venta.objects.get()
        filtro, aceite = v.lineas.order_by("pk")
        self.assertEqual((filtro.precio_unitario, filtro.descuento, filtro.importe), (D("109.80"), D("10"), D("219.60")))
        self.assertEqual((aceite.precio_unitario, aceite.descuento), (D("1083.00"), D("5")))
        self.assertEqual(v.total, D("1302.60"))
        r = self.emitir(self.publico, [{"clave": self.clave_filtro, "cantidad": "1", "descuento": "100"}])
        self.assertContains(r, "va de 0 a 99%", status_code=400)

    def test_abajo_del_costo_solo_un_administrador(self):
        filas = [{"clave": self.clave_filtro, "cantidad": "1", "precio": "100", "descuento": "40"}]  # 60 < 66.17
        r = self.emitir(self.publico, filas)
        self.assertContains(r, "queda abajo del costo", status_code=400)
        self.assertNotContains(r, "66.17", status_code=400)
        self.client.force_login(self.admin)
        r = self.emitir(self.publico, filas, nip=NIP_ADMIN, follow=True)
        self.assertEqual(Venta.objects.get().total, D("60.00"))

    def test_lo_sobre_pedido_no_se_remisiona(self):
        importar("MORSA", [lista([["PH966B", "Aceite", "FILTRO PARA ACEITE", "FRAM", "NISSAN. TSURU L4 1.6 (90-04)", 52.43, ""]])])
        todo = self.client.get(reverse("ticket_sugerir"), {"q": "PH966"}).json()["sugerencias"]
        self.assertTrue(any(s["clave"].startswith("partida:") for s in todo))
        solo = self.client.get(reverse("ticket_sugerir"), {"q": "PH966", "para": "remision"}).json()["sugerencias"]
        self.assertEqual(solo, [])
        clave = next(s["clave"] for s in todo if s["clave"].startswith("partida:"))
        r = self.emitir(self.publico, [{"clave": clave, "codigo": "PH966B", "cantidad": "1"}])
        self.assertContains(r, "va sobre pedido", status_code=400)

    def test_sugerencias_y_precios_con_la_lista_del_cliente(self):
        s = self.client.get(reverse("ticket_sugerir"), {"q": "GP-37", "cliente": self.taller.pk, "para": "remision"}).json()
        self.assertEqual(s["sugerencias"][0]["precio"], "107")
        r = self.client.get(reverse("remision_precios"), {"cliente": self.publico.pk, "clave": [self.clave_filtro]}).json()
        self.assertEqual(r["filas"][self.clave_filtro]["precio"], "122")


class BorradorTests(RemisionesBase):
    def test_guardar_seguir_y_emitir_borrador(self):
        filas = [{"clave": self.clave_filtro, "codigo": "GP-37", "descripcion": "Filtro aceite", "cantidad": "3", "precio": "122"}]
        r = self.client.post(reverse("remision_nueva"), self.forma(self.taller, filas, accion="borrador", nip="",
                                                                    orden_compra="OC-1"))
        b = RemisionBorrador.objects.get()
        self.assertRedirects(r, reverse("remision_borrador", args=[b.pk]))
        self.assertEqual((b.cliente, b.total, b.orden_compra, b.usuario, len(b.lineas)), (self.taller, D("366.00"), "OC-1", self.user, 1))
        self.assertFalse(Venta.objects.exists())
        pagina = self.client.get(reverse("remision_borrador", args=[b.pk]))
        self.assertContains(pagina, 'value="GP-37"')
        self.assertContains(pagina, self.taller.folio)
        self.assertContains(self.client.get(reverse("remision_borradores")), "OC-1")
        # se corrige y se emite: el borrador desaparece
        filas[0]["cantidad"] = "1"
        self.client.post(reverse("remision_borrador", args=[b.pk]), self.forma(self.taller, filas, orden_compra="OC-1"))
        v = Venta.objects.get()
        self.assertEqual((v.total, v.orden_compra), (D("122.00"), "OC-1"))
        self.assertFalse(RemisionBorrador.objects.exists())

    def test_borrar_borrador(self):
        b = RemisionBorrador.objects.create(usuario=self.user, cliente=self.publico, lineas=[])
        self.client.post(reverse("remision_borrador_borrar", args=[b.pk]))
        self.assertFalse(RemisionBorrador.objects.exists())

    def test_alta_rapida_regresa_a_la_remision_con_el_cliente(self):
        r = self.client.post(reverse("ticket_cliente_nuevo"), {"nombre": "Taller Ramírez", "telefono": "4425550000", "rfc": "",
                                                               "lista": "", "dias": "0", "limite": "0", "para": "remision",
                                                               "next": reverse("remision_nueva")})
        c = Cliente.objects.get(nombre="Taller Ramírez")
        self.assertRedirects(r, f"{reverse('remision_nueva')}?cliente={c.pk}", fetch_redirect_response=False)
        pagina = self.client.get(r.url)
        self.assertContains(pagina, c.folio)
        self.assertContains(pagina, f'name="cliente" id="rem-cliente" value="{c.pk}"')
        # el ticket del mostrador no cambia de cliente
        self.assertNotEqual(self.client.session.get("ticket", {}).get("cliente"), c.pk)
        # a otro sitio no lo manda
        r = self.client.post(reverse("ticket_cliente_nuevo"), {"nombre": "Otro", "rfc": "", "dias": "0", "limite": "0",
                                                               "para": "remision", "next": "https://ejemplo.com/"})
        self.assertTrue(r.url.startswith(reverse("remision_nueva")))


class CobrarEnCajaTests(RemisionesBase):
    def setUp(self):
        super().setUp()
        self.v = emitir_remision(self.publico, [{"producto": self.filtro, "cantidad": 1}], "CONTADO", self.user)  # 122

    def test_caja_la_cobra_y_entra_a_su_corte(self):
        r = self.client.post(reverse("remision_cobrar", args=[self.v.folio]),
                             {"nip": NIP, "forma_pago": "EFECTIVO", "paga_con": "200", "desde": "caja"}, follow=True)
        self.assertRedirects(r, reverse("caja"))
        self.assertContains(r, "Cambio: $78")
        self.v.refresh_from_db()
        self.assertEqual((self.v.estado, self.v.forma_pago, self.v.saldo, self.v.turno, self.v.cobrada_por),
                         ("PAGADA", "EFECTIVO", 0, self.turno, self.user))
        self.assertEqual(esperado_en_caja(self.turno)[FormaPago.EFECTIVO], D("622"))
        self.assertNotContains(r, "Remisiones por cobrar")
        with self.assertRaisesMessage(ValidationError, "ya no está por cobrar"):
            cobrar_remision(self.v, self.turno, FormaPago.EFECTIVO, self.user)

    def test_no_alcanza_o_sin_forma_o_sin_caja(self):
        r = self.client.post(reverse("remision_cobrar", args=[self.v.folio]), {"nip": NIP, "forma_pago": "EFECTIVO", "paga_con": "100"}, follow=True)
        self.assertContains(r, "faltan $22")
        with self.assertRaisesMessage(ValidationError, "Elige cómo paga"):
            cobrar_remision(self.v, self.turno, FormaPago.CREDITO, self.user)
        self.client.force_login(self.admin)  # sin turno abierto
        r = self.client.post(reverse("remision_cobrar", args=[self.v.folio]), {"nip": NIP_ADMIN, "forma_pago": "TARJETA"}, follow=True)
        self.assertContains(r, "Abre la caja para cobrar")
        self.v.refresh_from_db()
        self.assertEqual(self.v.estado, "POR_COBRAR")

    def test_venta_e_impresion_de_una_pendiente(self):
        Venta.objects.filter(pk=self.v.pk).update(orden_compra="OC-778", observaciones="Entregar en taller")
        r = self.client.get(reverse("venta", args=[self.v.folio]))
        self.assertContains(r, "Cobro en caja")
        self.assertContains(r, reverse("remision_cobrar", args=[self.v.folio]))
        self.assertNotContains(r, 'id="devolver"')
        self.assertContains(r, "como no se cobró, no hay dinero que devolver")
        hoja = self.client.get(reverse("remision", args=[self.v.folio]))
        for texto in ("Contado · pendiente de pago en caja", self.publico.folio, "OC-778", "Entregar en taller", "Recibí de conformidad"):
            self.assertContains(hoja, texto)

    def test_cancelar_o_devolver_una_pendiente(self):
        with self.assertRaisesMessage(ValidationError, "todavía no se cobra"):
            registrar_devolucion(self.v, "DEV", {self.v.lineas.get().pk: 1}, self.admin, "no le quedó")
        self.assertIsNone(por_que_no_cancelar(self.v))
        cancelar_venta(self.v, self.admin, "se equivocó")
        self.v.refresh_from_db()
        self.filtro.refresh_from_db()
        self.assertEqual((self.v.estado, self.v.saldo, self.filtro.existencia), ("CANCELADA", 0, D("10")))
        self.assertEqual(esperado_en_caja(self.turno)[FormaPago.EFECTIVO], D("500"))

    def test_pestanas_cuentan_las_pendientes(self):
        for nombre in ("remisiones", "cotizaciones", "devoluciones", "remision_borradores"):
            with self.subTest(nombre=nombre):
                self.assertContains(self.client.get(reverse(nombre)), "<small>1 · $122</small>", html=True)
