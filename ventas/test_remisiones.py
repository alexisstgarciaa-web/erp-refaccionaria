"""Remisiones: detalle de una venta, abonos y cancelación desde la pantalla."""
from datetime import timedelta
from decimal import Decimal as D

from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone

from ventas.models import FormaPago, Venta
from ventas.servicios import abrir_turno, cerrar_turno, esperado_en_caja, registrar_venta
from ventas.tests import Base


def existencia(producto):
    producto.refresh_from_db()
    return producto.existencia


class RemisionesTests(Base):
    def setUp(self):
        super().setUp()
        self.user.set_password("x")
        self.user.save()
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
        r = self.client.post(url, {"monto": "100", "forma_pago": FormaPago.TRANSFERENCIA}, follow=True)
        self.assertContains(r, "Resta $114.")
        r = self.client.post(url, {"monto": "500", "forma_pago": FormaPago.EFECTIVO}, follow=True)
        self.assertContains(r, "hasta $114")  # no se abona más que el saldo
        r = self.client.post(url, {"monto": "$114", "forma_pago": FormaPago.CREDITO}, follow=True)  # crédito se ignora
        self.assertContains(r, "quedó liquidada")
        v.refresh_from_db()
        self.assertEqual((v.estado, v.saldo), (Venta.Estado.PAGADA, D("0")))
        self.assertEqual(sorted(v.abonos.values_list("forma_pago", flat=True)), [FormaPago.EFECTIVO, FormaPago.TRANSFERENCIA])
        esp = esperado_en_caja(self.turno)
        self.assertEqual(esp[FormaPago.TRANSFERENCIA], D("100"))
        self.assertEqual(esp[FormaPago.EFECTIVO], D("500") + D("114"))  # fondo + abono

    def test_abono_pide_caja_abierta(self):
        v = self.vender(self.taller, FormaPago.CREDITO)
        cerrar_turno(self.turno, 500, 0, 0)
        self.assertContains(self.client.get(reverse("venta", args=[v.folio])), "abre la caja")
        r = self.client.post(reverse("venta_abonar", args=[v.folio]), {"monto": "50"}, follow=True)
        self.assertContains(r, "Abre la caja para registrar el abono")
        self.assertFalse(v.abonos.exists())

    def test_cancelar_contado_en_su_turno(self):
        antes = existencia(self.filtro)
        v = self.vender()
        url = reverse("venta_cancelar", args=[v.folio])
        # sin motivo o sin confirmar no cancela
        self.assertContains(self.client.post(url, {"confirmo": "1"}, follow=True), "Escribe el motivo")
        self.assertContains(self.client.post(url, {"motivo": "No era la pieza"}, follow=True), "Marca la casilla")
        v.refresh_from_db()
        self.assertEqual(v.estado, Venta.Estado.PAGADA)
        r = self.client.post(url, {"motivo": "No era la pieza", "confirmo": "1"}, follow=True)
        self.assertContains(r, "Devuelve $244 al cliente")
        self.assertContains(r, "«No era la pieza»")
        v.refresh_from_db()
        self.assertEqual((v.estado, v.cancelada_por, v.motivo_cancelacion), (Venta.Estado.CANCELADA, self.user, "No era la pieza"))
        self.assertEqual(existencia(self.filtro), antes)
        self.assertEqual(esperado_en_caja(self.turno)[FormaPago.EFECTIVO], D("500"))  # ya no cuenta en el corte
        # una cancelada ya no ofrece cancelar
        self.assertNotContains(self.client.get(reverse("venta", args=[v.folio])), 'name="confirmo"')

    def test_quien_puede_cancelar(self):
        contado = self.vender()
        credito = self.vender(self.taller, FormaPago.CREDITO)
        cerrar_turno(self.turno, 744, 0, 0)
        # contado de un turno cerrado: nadie la cancela aquí, ese dinero ya se contó
        r = self.client.get(reverse("venta", args=[contado.folio]))
        self.assertContains(r, "su turno ya se cerró")
        self.assertNotContains(r, 'name="confirmo"')
        # crédito de un turno cerrado: el cajero no; el administrador sí
        r = self.client.post(reverse("venta_cancelar", args=[credito.folio]), {"motivo": "x", "confirmo": "1"}, follow=True)
        self.assertContains(r, "Solo el administrador")
        admin = get_user_model().objects.create_user("alexis", is_staff=True)
        self.client.force_login(admin)
        r = self.client.post(reverse("venta_cancelar", args=[credito.folio]), {"motivo": "Lo devolvió", "confirmo": "1"}, follow=True)
        self.assertContains(r, "cancelada")
        credito.refresh_from_db()
        self.assertEqual((credito.estado, credito.saldo), (Venta.Estado.CANCELADA, D("0")))
        self.assertEqual(self.taller.saldo(), 0)
        # otro cajero no cancela ventas de un turno que no es suyo
        otro = get_user_model().objects.create_user("caja2")
        t2 = abrir_turno(otro)
        v = self.vender(turno=t2)
        self.client.force_login(self.user)
        self.assertContains(self.client.get(reverse("venta", args=[v.folio])), "Solo el administrador")

    def test_con_abonos_no_se_cancela(self):
        v = self.vender(self.taller, FormaPago.CREDITO)
        self.client.post(reverse("venta_abonar", args=[v.folio]), {"monto": "10", "forma_pago": FormaPago.EFECTIVO})
        r = self.client.get(reverse("venta", args=[v.folio]))
        self.assertContains(r, "Tiene abonos")
        self.assertNotContains(r, 'name="confirmo"')
