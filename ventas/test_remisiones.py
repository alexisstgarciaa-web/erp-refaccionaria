"""Remisiones: detalle de una venta, abonos y cancelación desde la pantalla."""
from datetime import timedelta
from decimal import Decimal as D

from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone

from ventas import nip as nips
from ventas.models import FormaPago, Venta
from ventas.servicios import abrir_turno, cerrar_turno, esperado_en_caja, registrar_venta
from ventas.tests import Base


NIP, NIP_ADMIN = "4821", "7305"


def existencia(producto):
    producto.refresh_from_db()
    return producto.existencia


class RemisionesTests(Base):
    def setUp(self):
        super().setUp()
        self.user.set_password("x")
        self.user.save()
        nips.asignar(self.user, NIP)
        self.admin = get_user_model().objects.create_user("alexis", is_staff=True)
        nips.asignar(self.admin, NIP_ADMIN)
        self.client.force_login(self.user)

    def vender(self, cliente=None, forma=FormaPago.EFECTIVO, cantidad=2, turno=None):
        return registrar_venta(turno or self.turno, cliente or self.publico, [{"producto": self.filtro, "cantidad": cantidad}],
                               forma, self.user)

    def test_detalle_y_por_cobrar(self):
        contado = self.vender()
        credito = self.vender(self.taller, FormaPago.CREDITO)
        r = self.client.get(reverse("venta", args=[credito.folio]))
        self.assertContains(r, "Cobranza")
        self.assertContains(r, "Registrar abono")
        self.assertContains(r, "Filtro aceite")
        self.assertContains(r, "Cancelar venta")
        # la de contado no muestra cobranza
        self.assertNotContains(self.client.get(reverse("venta", args=[contado.folio])), "Registrar abono")
        # «Por cobrar» deja solo las de crédito con saldo, y la lista lleva al detalle
        r = self.client.get(reverse("remisiones"), {"ver": "por-cobrar"})
        self.assertEqual(list(r.context["ventas"]), [credito])
        self.assertContains(r, reverse("venta", args=[credito.folio]))
        self.assertEqual(r.context["saldo_total"], credito.saldo)
        # una vencida se marca
        Venta.objects.filter(pk=credito.pk).update(vence=timezone.localdate() - timedelta(days=3))
        self.assertContains(self.client.get(reverse("remisiones"), {"ver": "por-cobrar"}), "Vencida")

    def test_abono_parcial_y_liquidar(self):
        v = self.vender(self.taller, FormaPago.CREDITO)  # 2 × 107 = 214
        url = reverse("venta_abonar", args=[v.folio])
        r = self.client.post(url, {"monto": "100", "forma_pago": FormaPago.TRANSFERENCIA, "nip": NIP}, follow=True)
        self.assertContains(r, "Resta $114.")
        r = self.client.post(url, {"monto": "500", "forma_pago": FormaPago.EFECTIVO, "nip": NIP}, follow=True)
        self.assertContains(r, "hasta $114")  # no se abona más que el saldo
        r = self.client.post(url, {"monto": "$114", "forma_pago": FormaPago.CREDITO, "nip": NIP_ADMIN}, follow=True)  # crédito se ignora
        self.assertContains(r, "quedó liquidada")
        v.refresh_from_db()
        self.assertEqual((v.estado, v.saldo), (Venta.Estado.PAGADA, D("0")))
        self.assertEqual(sorted(v.abonos.values_list("forma_pago", flat=True)), [FormaPago.EFECTIVO, FormaPago.TRANSFERENCIA])
        # cada abono guarda quién lo recibió, según su NIP
        self.assertEqual(sorted(v.abonos.values_list("usuario__username", flat=True)), ["alexis", "caja1"])
        self.assertContains(self.client.post(url, {"monto": "1"}, follow=True), "Teclea tu NIP")
        esp = esperado_en_caja(self.turno)
        self.assertEqual(esp[FormaPago.TRANSFERENCIA], D("100"))
        self.assertEqual(esp[FormaPago.EFECTIVO], D("500") + D("114"))  # fondo + abono

    def test_abono_pide_caja_abierta(self):
        v = self.vender(self.taller, FormaPago.CREDITO)
        cerrar_turno(self.turno, 500, 0, 0)
        self.assertContains(self.client.get(reverse("venta", args=[v.folio])), "abre la caja")
        r = self.client.post(reverse("venta_abonar", args=[v.folio]), {"monto": "50", "nip": NIP}, follow=True)
        self.assertContains(r, "Abre la caja para registrar el abono")
        self.assertFalse(v.abonos.exists())

    def test_cancelar_solo_con_nip_de_administrador(self):
        antes = existencia(self.filtro)
        v = self.vender()
        url = reverse("venta_cancelar", args=[v.folio])
        r = self.client.get(reverse("venta", args=[v.folio]))
        self.assertContains(r, 'data-nip="admin"')
        # sin motivo, sin NIP o con el NIP de un cajero no cancela
        self.assertContains(self.client.post(url, {"nip": NIP_ADMIN}, follow=True), "Escribe el motivo")
        self.assertContains(self.client.post(url, {"motivo": "No era la pieza"}, follow=True), "Teclea el NIP de administrador")
        self.assertContains(self.client.post(url, {"motivo": "No era la pieza", "nip": NIP}, follow=True),
                            "esto lo autoriza un administrador")
        self.assertContains(self.client.post(url, {"motivo": "No era la pieza", "nip": "1111"}, follow=True), "NIP equivocado")
        v.refresh_from_db()
        self.assertEqual(v.estado, Venta.Estado.PAGADA)
        # el cajero sigue en su sesión; el administrador autoriza con su NIP
        r = self.client.post(url, {"motivo": "No era la pieza", "nip": NIP_ADMIN}, follow=True)
        self.assertContains(r, "Devuelve $244 al cliente")
        self.assertContains(r, "por alexis: «No era la pieza»")
        v.refresh_from_db()
        self.assertEqual((v.estado, v.cancelada_por, v.motivo_cancelacion), (Venta.Estado.CANCELADA, self.admin, "No era la pieza"))
        self.assertEqual(existencia(self.filtro), antes)
        self.assertEqual(esperado_en_caja(self.turno)[FormaPago.EFECTIVO], D("500"))  # ya no cuenta en el corte
        self.assertNotContains(self.client.get(reverse("venta", args=[v.folio])), 'class="cancelar-form"')

    def test_que_se_puede_cancelar(self):
        contado = self.vender()
        credito = self.vender(self.taller, FormaPago.CREDITO)
        cerrar_turno(self.turno, 744, 0, 0)
        # contado de un turno cerrado: no se cancela, ese dinero ya se contó
        r = self.client.get(reverse("venta", args=[contado.folio]))
        self.assertContains(r, "su turno ya se cerró")
        self.assertNotContains(r, 'class="cancelar-form"')
        self.assertContains(self.client.post(reverse("venta_cancelar", args=[contado.folio]),
                                             {"motivo": "x", "nip": NIP_ADMIN}, follow=True), "su turno ya se cerró")
        # crédito sin abonos de un turno cerrado: sí, con NIP de administrador
        r = self.client.post(reverse("venta_cancelar", args=[credito.folio]), {"motivo": "Lo devolvió", "nip": NIP_ADMIN}, follow=True)
        self.assertContains(r, "cancelada")
        credito.refresh_from_db()
        self.assertEqual((credito.estado, credito.saldo), (Venta.Estado.CANCELADA, D("0")))
        self.assertEqual(self.taller.saldo(), 0)

    def test_nip_equivocado_varias_veces_bloquea(self):
        v = self.vender()
        url = reverse("venta_cancelar", args=[v.folio])
        for _ in range(5):
            self.client.post(url, {"motivo": "x", "nip": "1357"})
        r = self.client.post(url, {"motivo": "x", "nip": NIP_ADMIN}, follow=True)
        self.assertContains(r, "Demasiados NIP equivocados")
        v.refresh_from_db()
        self.assertEqual(v.estado, Venta.Estado.PAGADA)

    def test_con_abonos_no_se_cancela(self):
        v = self.vender(self.taller, FormaPago.CREDITO)
        self.client.post(reverse("venta_abonar", args=[v.folio]), {"monto": "10", "forma_pago": FormaPago.EFECTIVO, "nip": NIP})
        r = self.client.get(reverse("venta", args=[v.folio]))
        self.assertContains(r, "Tiene abonos")
        self.assertNotContains(r, 'name="confirmo"')

    def test_devolucion_de_contado_de_un_turno_cerrado(self):
        v = self.vender(cantidad=2)  # 2 × 122 = 244
        ln = v.lineas.get()
        cerrar_turno(self.turno, 744, 0, 0)
        hoy = abrir_turno(self.user, fondo_inicial=300)
        antes = existencia(self.filtro)
        url = reverse("venta_devolver", args=[v.folio])
        datos = {"tipo": "DEV", f"cant_{ln.pk}": "1", "motivo": "No era para su motor", "forma_reembolso": FormaPago.EFECTIVO,
                 "regresa": "1"}
        r = self.client.get(reverse("venta", args=[v.folio]))
        self.assertContains(r, "usa <a href=\"#devolver\">devolución o garantía</a>")
        self.assertContains(self.client.post(url, {**datos, "nip": NIP}, follow=True), "esto lo autoriza un administrador")
        r = self.client.post(url, {**datos, "nip": NIP_ADMIN}, follow=True)
        self.assertContains(r, "Devolución DEV-000001 por $122.")
        self.assertContains(r, "Regrésale $122 en efectivo")
        self.assertEqual(existencia(self.filtro), antes + 1)
        self.assertEqual(esperado_en_caja(hoy)[FormaPago.EFECTIVO], D("300") - D("122"))  # sale del corte de hoy
        # con una devolución ya no se cancela completa (se regresaría dos veces)
        self.assertContains(self.client.get(reverse("venta", args=[v.folio])), "Ya tiene devoluciones o garantías")
        # no se devuelve más de lo que se vendió
        r = self.client.post(url, {**datos, f"cant_{ln.pk}": "2", "nip": NIP_ADMIN}, follow=True)
        self.assertContains(r, "solo quedan 1 por devolver")
        # dañada: no regresa al anaquel
        datos.pop("regresa")
        self.client.post(url, {**datos, "nip": NIP_ADMIN})
        self.assertEqual(existencia(self.filtro), antes + 1)
        self.assertEqual(v.devoluciones.count(), 2)
        self.assertContains(self.client.get(reverse("devoluciones")), "DEV-000002")

    def test_devolucion_sin_caja_abierta_no_regresa_dinero(self):
        v = self.vender()
        cerrar_turno(self.turno, 744, 0, 0)
        ln = v.lineas.get()
        r = self.client.post(reverse("venta_devolver", args=[v.folio]),
                             {"tipo": "DEV", f"cant_{ln.pk}": "1", "motivo": "x", "forma_reembolso": "EFECTIVO", "nip": NIP_ADMIN},
                             follow=True)
        self.assertContains(r, "Abre la caja para regresar el dinero")
        self.assertFalse(v.devoluciones.exists())

    def test_devolucion_a_credito_baja_el_saldo_primero(self):
        v = self.vender(self.taller, FormaPago.CREDITO)  # 2 × 107 = 214
        self.client.post(reverse("venta_abonar", args=[v.folio]), {"monto": "100", "forma_pago": "EFECTIVO", "nip": NIP})
        ln = v.lineas.get()
        r = self.client.post(reverse("venta_devolver", args=[v.folio]),
                             {"tipo": "DEV", f"cant_{ln.pk}": "2", "motivo": "Se equivocó de modelo", "regresa": "1",
                              "forma_reembolso": "TRANSF", "nip": NIP_ADMIN}, follow=True)
        self.assertContains(r, "Bajó $114 del saldo.")
        self.assertContains(r, "Regrésale $100 en transferencia")
        v.refresh_from_db()
        self.assertEqual((v.estado, v.saldo), (Venta.Estado.PAGADA, D("0")))
        self.assertEqual(self.taller.saldo(), 0)

    def test_garantia_cambia_la_pieza_y_queda_por_reclamar(self):
        v = self.vender(cantidad=1)
        cerrar_turno(self.turno, 622, 0, 0)
        ln = v.lineas.get()
        antes = existencia(self.filtro)
        r = self.client.post(reverse("venta_devolver", args=[v.folio]),
                             {"tipo": "GAR", f"cant_{ln.pk}": "1", "motivo": "Fuga en la junta", "nip": NIP_ADMIN}, follow=True)
        self.assertContains(r, "Garantía GAR-000001")
        self.assertEqual(existencia(self.filtro), antes - 1)  # sale la nueva, sin cobrar
        g = v.devoluciones.get()
        self.assertEqual((g.reembolso, g.reclamo), (D("0"), "PENDIENTE"))
        self.assertEqual(self.filtro.movimientos.filter(referencia="GAR-000001").get().tipo, "SAL_GARANTIA")
        r = self.client.get(reverse("devoluciones"), {"ver": "por-reclamar"})
        self.assertContains(r, "GAR-000001")
        r = self.client.post(reverse("devolucion_reclamo", args=[g.folio]), {"reclamo": "REPUESTA"}, follow=True)
        self.assertContains(r, "GAR-000001: el proveedor la repuso.")
        g.refresh_from_db()
        self.assertEqual(g.reclamo, "REPUESTA")
        self.assertNotContains(self.client.get(reverse("devoluciones"), {"ver": "por-reclamar"}), "GAR-000001")
        # sin existencia no hay garantía
        self.filtro.refresh_from_db()
        from inventario.servicios import registrar_salida
        registrar_salida(self.filtro, self.filtro.existencia)
        r = self.client.post(reverse("venta_devolver", args=[v.folio]),
                             {"tipo": "GAR", f"cant_{ln.pk}": "1", "motivo": "Otra vez", "nip": NIP_ADMIN}, follow=True)
        self.assertContains(r, "No hay GP-37 en anaquel para cambiarla")
        self.assertEqual(v.devoluciones.count(), 1)

    def test_administracion_asigna_nips(self):
        su = get_user_model().objects.create_superuser("dueño", password="x")
        self.client.force_login(su)
        url = reverse("admin:ventas_nip_add")
        self.assertEqual(self.client.get(url).status_code, 200)
        r = self.client.post(url, {"usuario": su.pk, "nip": "2468", "repetir": "2469"})
        self.assertContains(r, "Los dos NIP no coinciden")
        r = self.client.post(url, {"usuario": su.pk, "nip": NIP, "repetir": NIP})
        self.assertContains(r, "ya lo tiene otra persona")
        r = self.client.post(url, {"usuario": su.pk, "nip": "2468", "repetir": "2468"})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(nips.quien(self.client.session, "2468"), su)
        # cambiarlo: solo se reemplaza, nunca se muestra
        n = su.nip
        r = self.client.get(reverse("admin:ventas_nip_change", args=[n.pk]))
        self.assertNotContains(r, n.huella)
        self.client.post(reverse("admin:ventas_nip_change", args=[n.pk]), {"nip": "80642", "repetir": "80642"})
        self.assertEqual(nips.quien(self.client.session, "80642"), su)
