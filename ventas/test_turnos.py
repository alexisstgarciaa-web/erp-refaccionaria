"""Turnos de clientes y caja separada: el cliente toma su turno (los registrados pasan antes), un vendedor lo
llama y registra la venta sin cobrar, y la caja cobra junto todo lo del turno."""
from decimal import Decimal as D

from django.contrib.auth import get_user_model
from django.core import signing
from django.core.cache import caches
from django.core.exceptions import ValidationError
from django.urls import reverse
from django.utils import timezone

from ventas import mostrador as m
from ventas import nip as nips
from ventas import turnos
from ventas.models import Nip, Pase, TicketGuardado, Venta
from ventas.servicios import cobrar_en_caja, dar_pase, emitir_remision, esperado_en_caja
from ventas.tests import Base
from ventas.views import SAL_TURNO

NIP, NIP_VENDEDOR = "4821", "5094"


class TurnosBase(Base):
    """caja1 atiende la caja (ya abierta, con fondo de 500); Pedro solo vende."""

    def setUp(self):
        super().setUp()
        caches["pantalla"].clear()
        nips.asignar(self.user, NIP)
        self.pedro = get_user_model().objects.create_user("pedro", first_name="Pedro")
        nips.asignar(self.pedro, NIP_VENDEDOR)
        Nip.objects.filter(usuario=self.pedro).update(cobra=False)
        self.taller.telefono = "442 123-4567"
        self.taller.save()
        self.client.force_login(self.user)

    def vender(self, nip=NIP_VENDEDOR, cantidad="1", **datos):
        """Lo que hace el vendedor: teclea la pieza y manda a caja con su NIP."""
        self.client.post(reverse("ticket_codigo"), {"codigo": "GP-37", "cantidad": cantidad})
        return self.client.post(reverse("ticket_a_caja"), {"nip": nip, **datos}, follow=True)

    def cobrar(self, *folios, nip=NIP, forma="EFECTIVO", **datos):
        return self.client.post(reverse("caja_cobrar"), {"nip": nip, "forma_pago": forma, "folio": list(folios), **datos},
                                follow=True)


class TomarTurnoTests(TurnosBase):
    def test_numeros_del_dia_y_cliente_registrado(self):
        r = self.client.post(reverse("turnos"), {"nombre": "  Juan   Pérez "}, follow=True)
        uno = Pase.objects.get()
        self.assertEqual((uno.numero, uno.nombre, uno.cliente, uno.dia), (1, "Juan Pérez", None, timezone.localdate()))
        self.assertContains(r, "Turno dado")
        self.assertContains(r, reverse("turno_papel", args=[uno.pk]))  # su papelito
        self.assertContains(r, 'data-imprimir-al-abrir="turno"')
        # con su número de cliente (como sea que lo teclee) o su celular queda a su nombre
        for i, texto in enumerate((self.taller.folio, f"cte{self.taller.pk}", str(self.taller.pk), "442 123 4567",
                                   "+52 442 123 4567"), start=2):
            with self.subTest(texto=texto):
                r = self.client.post(reverse("turnos"), {"numero_cliente": texto}, follow=True)
                p = Pase.objects.latest("pk")
                self.assertEqual((p.numero, p.cliente, p.quien), (i, self.taller, "Taller Hermanos Ruiz"))
                self.assertContains(r, "pasa antes")
        # el papelito
        r = self.client.get(reverse("turno_papel", args=[p.pk]))
        self.assertContains(r, f'<b class="papel-numero">{p.numero}</b>', html=True)
        self.assertContains(r, "Taller Hermanos Ruiz")

    def test_numero_que_no_es_de_un_cliente(self):
        otro = self.taller.__class__.objects.create(nombre="Taller Gemelo", lista_precio=self.mostrador, telefono="4421234567")
        for malo in ("9999", "CTE-9999", "442 000 0000", "taller", "4421234567",  # ese celular lo tienen dos
                     str(self.publico.pk)):  # «Público en general» no cuenta como cliente registrado
            with self.subTest(malo=malo):
                r = self.client.post(reverse("turnos"), {"numero_cliente": malo, "nombre": "Juan"}, follow=True)
                self.assertContains(r, "No encontramos ese número de cliente ni ese celular")
                self.assertContains(r, f'value="{malo}"')  # no hay que volver a teclearlo
        self.assertFalse(Pase.objects.exists())
        otro.activo = False
        otro.save()
        self.assertEqual(turnos.cliente_de_numero("4421234567"), self.taller)

    def test_cada_dia_empieza_de_nuevo(self):
        ayer = timezone.localdate() - timezone.timedelta(days=1)
        Pase.objects.create(dia=ayer, numero=57)
        self.assertEqual(dar_pase().numero, 1)
        self.assertEqual(dar_pase().numero, 2)
        self.assertEqual([p.numero for p in turnos.en_espera()], [1, 2])  # el de ayer ya no está en la fila


class FilaTests(TurnosBase):
    def test_los_registrados_pasan_antes(self):
        a, b = turnos.tomar(nombre="Ana"), turnos.tomar(nombre="Beto")
        c = turnos.tomar(self.taller.folio)
        d = turnos.tomar(nombre="Dora")
        self.assertEqual(turnos.en_espera(), [c, a, b, d])
        r = self.client.get(reverse("mostrador"))
        self.assertContains(r, f"Llamar al siguiente <b>{c.numero}</b>")
        self.assertEqual([turnos.llamar_siguiente(self.pedro).numero for _ in range(4)], [3, 1, 2, 4])
        self.assertIsNone(turnos.llamar_siguiente(self.pedro))

    def test_un_turno_no_lo_llaman_dos(self):
        a = turnos.tomar(nombre="Ana")
        turnos.llamar(a, self.pedro)
        with self.assertRaisesMessage(ValidationError, "El turno 1 ya lo llamó Pedro."):
            turnos.llamar(Pase.objects.get(pk=a.pk), self.user)
        primera = Pase.objects.get(pk=a.pk).llamado
        turnos.llamar(Pase.objects.get(pk=a.pk), self.pedro)  # el mismo sí lo puede volver a llamar
        self.assertGreaterEqual(Pase.objects.get(pk=a.pk).llamado, primera)
        turnos.cerrar(a)
        with self.assertRaisesMessage(ValidationError, "ya no está en la fila"):
            turnos.llamar(Pase.objects.get(pk=a.pk), self.pedro)

    def test_llamar_desde_el_mostrador(self):
        r = self.client.post(reverse("turno_llamar_siguiente"), follow=True)
        self.assertContains(r, "No hay nadie esperando turno.")
        ana, ruiz = turnos.tomar(nombre="Ana"), turnos.tomar(self.taller.folio)
        r = self.client.post(reverse("turno_llamar_siguiente"), follow=True)
        t = m.Ticket(self.client.session)
        self.assertEqual((t.pase, t.cliente), (ruiz, self.taller))  # el ticket ya empieza con el cliente registrado
        self.assertContains(r, '<b class="tira-numero">2</b>', html=True)
        self.assertContains(r, "Volver a llamar")
        ruiz.refresh_from_db()
        self.assertEqual(ruiz.atiende, self.user)
        # con piezas en el ticket no se llama a otro
        self.client.post(reverse("ticket_codigo"), {"codigo": "GP-37"})
        r = self.client.post(reverse("turno_llamar_siguiente"), follow=True)
        self.assertContains(r, "Manda a caja o pon en espera el ticket del turno 2 antes de llamar a otro.")
        self.assertEqual(m.Ticket(self.client.session).pase, ruiz)
        # se fue sin comprar: el turno sale de la fila y el siguiente empieza como público en general
        self.client.post(reverse("ticket_vaciar"))
        self.client.post(reverse("turno_terminar"))
        ruiz.refresh_from_db()
        t = m.Ticket(self.client.session)
        self.assertIsNotNone(ruiz.cerrado)
        self.assertEqual((t.pase, t.cliente), (None, self.publico))
        # llamar a uno en particular; al llamar al siguiente sin haberle vendido, el anterior se cierra
        beto = turnos.tomar(nombre="Beto")
        self.client.post(reverse("turno_llamar", args=[beto.pk]))
        self.assertEqual(m.Ticket(self.client.session).pase, beto)
        self.client.post(reverse("turno_llamar_siguiente"))
        beto.refresh_from_db()
        self.assertIsNotNone(beto.cerrado)
        self.assertEqual(m.Ticket(self.client.session).pase, ana)

    def test_en_espera_el_turno_se_va_con_el_ticket(self):
        ana = turnos.tomar(nombre="Ana")
        self.client.post(reverse("turno_llamar_siguiente"))
        self.client.post(reverse("ticket_codigo"), {"codigo": "GP-37"})
        self.client.post(reverse("ticket_espera"), {"nombre": "Ana"})
        g = TicketGuardado.objects.get()
        self.assertEqual(g.pase, ana)
        self.assertIsNone(m.Ticket(self.client.session).pase)
        self.client.post(reverse("ticket_retomar", args=[g.folio]))
        self.assertEqual(m.Ticket(self.client.session).pase, ana)
        self.client.post(reverse("ticket_a_caja"), {"nip": NIP_VENDEDOR})
        self.assertEqual(Venta.objects.get().pase, ana)

    def test_quitar_de_la_fila(self):
        ana = turnos.tomar(nombre="Ana")
        r = self.client.post(reverse("turno_quitar", args=[ana.pk]), follow=True)
        self.assertContains(r, "El turno 1 salió de la fila.")
        self.assertEqual(turnos.en_espera(), [])
        # con remisiones ya no se quita: eso lo resuelve la caja
        self.vender()
        venta = Venta.objects.get()
        r = self.client.post(reverse("turno_quitar", args=[venta.pase.pk]), follow=True)
        self.assertContains(r, "ya tiene remisiones")


class CajaTests(TurnosBase):
    def test_el_vendedor_no_cobra_y_la_caja_cobra_junto_lo_del_turno(self):
        ana = turnos.tomar(nombre="Ana")
        self.client.post(reverse("turno_llamar_siguiente"))
        r = self.vender()
        v1 = Venta.objects.get()
        self.assertEqual((v1.pase, v1.estado, v1.usuario, v1.turno), (ana, "POR_COBRAR", self.pedro, None))
        self.assertContains(r, '<p class="a-caja-turno"><span>Turno</span><b>1</b></p>', html=True)
        self.assertNotContains(r, "Imprimir su turno")  # ya trae su papelito: no se imprime nada más
        # otra remisión para el mismo turno, hecha en Remisiones › Nueva por otro vendedor
        v2 = emitir_remision(self.publico, [{"producto": self.aceite, "cantidad": 2}], "CONTADO", self.user, pase=ana)
        self.assertEqual(esperado_en_caja(self.turno)["EFECTIVO"], D("500"))  # nada entra al corte hasta cobrar
        (cuenta,) = turnos.por_cobrar()
        self.assertEqual((cuenta.pase, cuenta.ventas, cuenta.total, cuenta.nombre), (ana, [v1, v2], D("392.00"), "Ana"))
        r = self.client.get(reverse("caja"))
        self.assertContains(r, 'data-turno="1"')
        self.assertContains(r, "2 remisiones")
        self.assertContains(r, "Cobrar el turno 1 · $392")
        self.assertContains(r, 'title="Remisiones de contado por cobrar">2<')
        self.assertEqual(self.client.get(reverse("caja_por_cobrar")).context["cuentas"][0].clave, f"t{ana.pk}")
        # el mostrador ve que su cliente está en caja
        self.assertContains(self.client.get(reverse("turnos_tira")), "En caja")
        # la cajera cobra todo en un solo pago
        r = self.cobrar(v1.folio, v2.folio, paga_con="500")
        v1.refresh_from_db()
        v2.refresh_from_db()
        for v in (v1, v2):
            self.assertEqual((v.estado, v.forma_pago, v.turno, v.cobrada_por, v.saldo),
                             ("PAGADA", "EFECTIVO", self.turno, self.user, D("0")))
        self.assertEqual(v1.usuario, self.pedro)  # la venta sigue siendo de quien la hizo
        self.assertEqual(esperado_en_caja(self.turno)["EFECTIVO"], D("892.00"))
        self.assertContains(r, "Cambio $108")
        self.assertContains(r, 'aria-label="Cobrado"')
        # el ticket: el único papel además del turno
        for v in (v1, v2):
            self.assertContains(r, reverse("remision", args=[v.folio]) + "?formato=ticket")
        self.assertContains(r, 'data-imprimir-al-abrir="ticket"')
        ticket = self.client.get(reverse("remision", args=[v1.folio]), {"formato": "ticket"})
        self.assertContains(ticket, "Con este ticket se entrega la mercancía")
        self.assertEqual(ticket["X-Frame-Options"], "SAMEORIGIN")  # Caja lo imprime sin abrir otra pestaña
        self.assertEqual(self.client.get(reverse("caja"))["X-Frame-Options"], "DENY")  # lo demás no se deja enmarcar
        self.assertEqual(turnos.por_cobrar(), [])
        self.assertNotContains(self.client.get(reverse("caja")), 'aria-label="Cobrado"')  # solo una vez
        # el mostrador ve que ya pagó, para entregarle
        tira = self.client.get(reverse("turnos_tira"))
        self.assertContains(tira, "Ya pagó · entrégale")
        self.assertEqual([p.numero for p in tira.context["tira"]["pagados"]], [1])

    def test_sin_turno_se_le_da_uno_al_mandarlo_a_caja(self):
        turnos.tomar(nombre="Ana")  # está esperando: no es el de este ticket
        r = self.vender(nombre="Beto")
        v = Venta.objects.get()
        self.assertEqual((v.pase.numero, v.pase.nombre, v.pase.llamado), (2, "Beto", None))
        self.assertContains(r, "Imprimir su turno")  # llegó directo al mostrador: su papelito se le da ahí
        self.assertEqual([p.numero for p in turnos.en_espera()], [1])  # el 2 ya compró: no está en la fila de espera
        (cuenta,) = turnos.por_cobrar()
        self.assertEqual((cuenta.nombre, cuenta.busca), ("Beto", f"2 beto {v.folio.lower().replace('-', ' ')} "
                                                                  f"{self.publico.folio.lower().replace('-', ' ')} publico en general"))

    def test_solo_cobra_quien_puede_cobrar(self):
        self.vender()
        v = Venta.objects.get()
        r = self.cobrar(v.folio, nip=NIP_VENDEDOR)
        self.assertContains(r, "Ese NIP no cobra en caja")
        v.refresh_from_db()
        self.assertEqual(v.estado, "POR_COBRAR")
        # tampoco abre ni cierra la caja; un administrador sí puede todo
        r = self.client.post(reverse("caja"), {"nip": NIP_VENDEDOR, "accion": "cerrar", "efectivo": "500", "tarjeta": "0",
                                               "transferencia": "0"}, follow=True)
        self.assertContains(r, "Ese NIP no cobra en caja")
        self.turno.refresh_from_db()
        self.assertIsNone(self.turno.cerrado)
        self.pedro.is_staff = True
        self.pedro.save()
        self.cobrar(v.folio, nip=NIP_VENDEDOR, forma="TARJETA")
        v.refresh_from_db()
        self.assertEqual((v.estado, v.forma_pago, v.cobrada_por), ("PAGADA", "TARJETA", self.pedro))

    def test_cobro_todo_o_nada(self):
        ana = dar_pase("Ana")
        v1 = emitir_remision(self.publico, [{"producto": self.filtro, "cantidad": 1}], "CONTADO", self.pedro, pase=ana)
        v2 = emitir_remision(self.publico, [{"producto": self.filtro, "cantidad": 1}], "CONTADO", self.pedro, pase=ana)
        cobrar_en_caja([v2.folio], self.turno, "EFECTIVO", self.user)
        with self.assertRaisesMessage(ValidationError, f"{v2.folio} ya no está por cobrar."):
            cobrar_en_caja([v1.folio, v2.folio], self.turno, "EFECTIVO", self.user)  # otra caja ya cobró una
        v1.refresh_from_db()
        self.assertEqual(v1.estado, "POR_COBRAR")
        for folios, forma, error in (([], "EFECTIVO", "No hay nada que cobrar."),
                                     ([v1.folio, "VTA-999999"], "EFECTIVO", "ya no existe"),
                                     ([v1.folio], "CREDITO", "Elige cómo paga"),
                                     ([v1.folio], "", "Elige cómo paga")):
            with self.assertRaisesMessage(ValidationError, error):
                cobrar_en_caja(folios, self.turno, forma, self.user)
        self.assertEqual(self.cobrar(v1.folio, v1.folio).status_code, 200)  # el folio repetido se cobra una vez
        self.assertEqual(esperado_en_caja(self.turno)["EFECTIVO"], D("744.00"))

    def test_remision_nueva_con_el_turno_del_cliente(self):
        ana = turnos.tomar(nombre="Ana")
        datos = {"cliente": self.publico.pk, "condiciones": "CONTADO", "accion": "emitir", "nip": NIP_VENDEDOR,
                 "clave": [f"producto:{self.filtro.pk}", ""], "codigo": ["GP-37", ""], "cantidad": ["1", ""],
                 "precio": ["", ""], "descuento": ["", ""], "descripcion": ["", ""], "existencia": ["", ""]}
        r = self.client.post(reverse("remision_nueva"), {**datos, "turno": "9"})
        self.assertContains(r, "Hoy no hay un turno 9 abierto", status_code=400)
        r = self.client.post(reverse("remision_nueva"), {**datos, "turno": "1"}, follow=True)
        self.assertEqual(Venta.objects.get().pase, ana)
        self.assertContains(r, "Pasa a caja con el turno 1.")
        r = self.client.post(reverse("remision_nueva"), datos, follow=True)  # sin turno: el que sigue
        self.assertEqual(Venta.objects.latest("pk").pase.numero, 2)
        # a crédito no pasa por caja ni lleva turno
        self.client.post(reverse("remision_nueva"), {**datos, "cliente": self.taller.pk, "condiciones": "CREDITO", "turno": "1"})
        v = Venta.objects.latest("pk")
        self.assertEqual((v.estado, v.pase), ("CREDITO", None))
        self.assertEqual(Pase.objects.count(), 2)


class PantallasDelClienteTests(TurnosBase):
    """Las dos pantallas que van en un aparato aparte, sin sesión: tomar turno y llamados."""

    def setUp(self):
        super().setUp()
        self.kiosco = reverse("turnos_kiosco", args=[turnos.clave_kiosco()])
        self.pantalla = reverse("turnos_pantalla", args=[turnos.clave_pantalla()])
        self.datos = reverse("turnos_pantalla_datos", args=[turnos.clave_pantalla()])

    def test_dejar_el_aparato_cierra_la_sesion(self):
        r = self.client.get(reverse("turnos"))
        self.assertContains(r, self.kiosco)
        self.assertContains(r, self.pantalla)
        r = self.client.post(reverse("turnos_dejar", args=["tomar"]), {"papel": "no"})
        self.assertRedirects(r, self.kiosco + "?papel=no", fetch_redirect_response=False)
        self.assertNotIn("_auth_user_id", self.client.session)
        self.assertEqual(self.client.get(reverse("mostrador")).status_code, 302)  # ese aparato ya no entra al sistema
        self.client.force_login(self.user)
        r = self.client.post(reverse("turnos_dejar", args=["llamados"]))
        self.assertRedirects(r, self.pantalla, fetch_redirect_response=False)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_tomar_turno_sin_sesion(self):
        self.client.logout()
        r = self.client.get(self.kiosco)
        self.assertContains(r, "¿Tienes número de cliente?")
        self.assertNotContains(r, "Mostrador")  # sin menú: de ahí no se llega al sistema
        # contestó que sí pero no lo tecleó, o no es de un cliente
        r = self.client.post(self.kiosco, {"tiene": "si", "numero_cliente": " "})
        self.assertContains(r, "Teclea tu número de cliente o tu celular")
        r = self.client.post(self.kiosco, {"tiene": "si", "numero_cliente": "9999"})
        self.assertContains(r, "No encontramos ese número de cliente ni ese celular")
        self.assertNotContains(r, "Taller Hermanos Ruiz")
        self.assertFalse(Pase.objects.exists())
        # con su número: turno a su nombre, y pasa antes
        r = self.client.post(self.kiosco, {"tiene": "si", "numero_cliente": self.taller.folio})
        ruiz = Pase.objects.get()
        self.assertEqual(ruiz.cliente, self.taller)
        self.assertTrue(r["Location"].startswith(self.kiosco + "?t="))
        r = self.client.get(r["Location"])
        self.assertContains(r, '<p class="kiosco-numero">1</p>', html=True)
        self.assertContains(r, "Cliente registrado: pasas antes")
        papel = r.context["papel_url"]
        r = self.client.get(papel)
        self.assertContains(r, '<b class="papel-numero">1</b>', html=True)
        self.assertEqual(r["X-Frame-Options"], "SAMEORIGIN")  # se imprime desde un marco oculto de la misma pantalla
        # contestó que no: lo que haya quedado en el número no cuenta
        self.client.post(self.kiosco, {"tiene": "no", "numero_cliente": self.taller.folio, "nombre": "Ana"})
        ana = Pase.objects.latest("pk")
        self.assertEqual((ana.numero, ana.cliente, ana.nombre), (2, None, "Ana"))
        # sin la clave no hay pantalla, y el papelito de otro no se saca cambiando la dirección
        self.assertEqual(self.client.get(reverse("turnos_kiosco", args=["x" * 20])).status_code, 404)
        self.assertEqual(self.client.post(reverse("turnos_kiosco", args=["x" * 20]), {"tiene": "no"}).status_code, 404)
        self.assertEqual(self.client.get(papel[:-4] + "abc/").status_code, 404)
        otra = signing.dumps(ana.pk, salt="otra-cosa")
        self.assertEqual(self.client.get(reverse("turno_papel_kiosco", args=[turnos.clave_kiosco(), otra])).status_code, 404)
        self.assertRedirects(self.client.get(self.kiosco, {"t": otra}), self.kiosco, fetch_redirect_response=False)
        self.assertEqual(self.client.get(reverse("turno_papel", args=[ana.pk])).status_code, 302)  # la del personal pide sesión
        self.assertEqual(signing.loads(papel.rstrip("/").rsplit("/", 1)[1], salt=SAL_TURNO), ruiz.pk)

    def test_pantalla_de_llamados_sin_sesion_y_sin_tocar_la_base(self):
        ana = turnos.tomar(nombre="Ana")
        ruiz = turnos.tomar(self.taller.folio)
        self.client.post(reverse("turno_llamar_siguiente"))  # caja1 llama al registrado
        self.client.logout()
        r = self.client.get(self.pantalla)
        self.assertContains(r, self.datos)
        self.assertEqual(self.client.get(reverse("turnos_pantalla", args=["x" * 20])).status_code, 404)
        self.assertEqual(self.client.get(reverse("turnos_pantalla_datos", args=["x" * 20])).status_code, 404)
        with self.assertNumQueries(0):
            d = self.client.get(self.datos).json()
        self.assertEqual(d["espera"], 1)
        (llamado,) = d["llamados"]
        self.assertEqual((llamado["id"], llamado["numero"], llamado["nombre"], llamado["con"]),
                         (ruiz.pk, 2, "Taller Hermanos Ruiz", "caja1"))
        # otro vendedor llama a Ana: sale primero; si la vuelve a llamar, cambia la marca de tiempo para repetir el aviso
        turnos.llamar(ana, self.pedro)
        d = self.client.get(self.datos).json()
        self.assertEqual([(x["numero"], x["con"]) for x in d["llamados"]], [(1, "Pedro"), (2, "caja1")])
        self.assertEqual(d["espera"], 0)
        # el que se fue ya no sale
        turnos.cerrar(ana)
        self.assertEqual([x["numero"] for x in self.client.get(self.datos).json()["llamados"]], [2])
        self.assertEqual(self.client.get(self.datos)["Cache-Control"], "no-store")

    def test_la_pantalla_empieza_limpia_cada_dia(self):
        caches["pantalla"].set("llamados", {"dia": "2020-01-01", "espera": 9, "llamados": [{"id": 1, "numero": 9}]})
        self.assertEqual(turnos.pantalla(), {"dia": timezone.localdate().isoformat(), "espera": 0, "llamados": []})


class TableroTests(TurnosBase):
    def test_por_donde_va_cada_turno(self):
        ana, beto, caro, dora = (turnos.tomar(nombre=n) for n in ("Ana", "Beto", "Caro", "Dora"))
        turnos.llamar(beto, self.pedro)
        emitir_remision(self.publico, [{"producto": self.filtro, "cantidad": 1}], "CONTADO", self.pedro, pase=caro)
        v = emitir_remision(self.publico, [{"producto": self.filtro, "cantidad": 1}], "CONTADO", self.pedro, pase=dora)
        cobrar_en_caja([v.folio], self.turno, "EFECTIVO", self.user)
        hoy = turnos.de_hoy()
        self.assertEqual({k: [p.numero for p in v] for k, v in hoy.items()},
                         {"espera": [1], "atendiendo": [2], "caja": [3], "pagados": [4]})
        self.assertEqual((hoy["caja"][0].total_caja, hoy["atendiendo"][0].con), (D("122.00"), "Pedro"))
        r = self.client.get(reverse("turnos_tablero"))
        for texto in ("Ana", "Beto", "Caro", "Dora", "Pedro", "$122"):
            self.assertContains(r, texto)
        # el que se lo lleva a crédito no pasa por caja: no sale como «ya pagó»
        eva = turnos.tomar(self.taller.folio)
        emitir_remision(self.taller, [{"producto": self.filtro, "cantidad": 1}], "CREDITO", self.pedro, pase=eva)
        self.assertEqual({k: [p.numero for p in v] for k, v in turnos.de_hoy().items()},
                         {"espera": [1], "atendiendo": [2], "caja": [3], "pagados": [4]})
        # pasado el rato de entrega, el que pagó ya no estorba en la pantalla
        Venta.objects.filter(pk=v.pk).update(cobrada=timezone.now() - timezone.timedelta(minutes=turnos.MINUTOS_PAGADO + 1))
        self.assertEqual(turnos.de_hoy()["pagados"], [])
