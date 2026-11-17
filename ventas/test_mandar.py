"""Mandar la cotización o la remisión al cliente en PDF, por WhatsApp o correo, sin salir de la página."""
import base64
import json
import time
from io import BytesIO
from unittest import mock
from urllib.error import HTTPError

from django.conf import settings
from django.core import mail
from django.core.mail import EmailMessage
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse

from catalogo.models import ListaPrecio
from ventas import compartir, correo, pdf, views
from ventas import mostrador as m
from ventas import test_mostrador
from ventas.models import Cliente, TicketGuardado, Venta

SERVIDOR = {"CORREO_SERVIDOR": True, "CORREO_REMITENTE": "Refaccionaria López <ventas@refalopez.mx>",
            "DEFAULT_FROM_EMAIL": "Refaccionaria López <ventas@refalopez.mx>", "NEGOCIO_CORREO": "mostrador@refalopez.mx"}


class NumeroWhatsappTests(TestCase):
    def test_celular_de_mexico_en_como_venga(self):
        for escrito in ("4421234567", "442 123 4567", "(442) 123-45-67", "52 442 123 4567", "+52 442 123 4567",
                        "+52 1 442 123 4567", "521 442 123 4567", "044 442 123 4567"):
            self.assertEqual(compartir.whatsapp(escrito), "524421234567", escrito)

    def test_extranjero_con_mas_y_lo_que_no_alcanza(self):
        self.assertEqual(compartir.whatsapp("+1 (210) 555-0134"), "12105550134")
        for malo in ("", "12345", "442 123 456", "1 442 123 4567 99"):
            self.assertEqual(compartir.whatsapp(malo), "", malo)


class MandarTests(TestCase):
    setUp = test_mostrador.MostradorTests.setUp  # mismo catálogo, cajero y NIP

    def taller(self, **campos):
        datos = {"nombre": "Taller Ramírez", "telefono": "442-123-45-67", "correo": "ramirez@taller.mx", **campos}
        return Cliente.objects.create(lista_precio=ListaPrecio.objects.get(nombre="Mostrador"), **datos)

    def cotizar(self, cliente=None):
        if cliente:
            self.client.post(reverse("ticket_cliente"), {"cliente": cliente.pk})
        self.client.post(reverse("ticket_codigo"), {"codigo": "PH966B", "cantidad": "2"})
        self.client.post(reverse("ticket_codigo"), {"codigo": "GP-37"})  # sobre pedido
        r = self.client.post(reverse("ticket_cotizar"), {"luego": "enviar", "next": "/"}, HTTP_X_MANDAR="1")
        return r, TicketGuardado.objects.filter(tipo="COT").latest("pk")

    def vender(self, cliente=None):
        if cliente:
            self.client.post(reverse("ticket_cliente"), {"cliente": cliente.pk})
        self.client.post(reverse("caja"), {"nip": test_mostrador.NIP, "accion": "abrir", "fondo": "500"})
        self.client.post(reverse("ticket_codigo"), {"codigo": "PH966B"})
        r = self.client.post(reverse("ticket_cobrar"), {"nip": test_mostrador.NIP, "forma_pago": "EFECTIVO"}, follow=True)
        return r, Venta.objects.latest("pk")

    def ventana(self, tipo, folio):
        return self.client.get(reverse("mandar", args=[tipo, folio]))

    def test_cotizar_y_mandar_abre_la_ventana_sin_salir_del_mostrador(self):
        r, g = self.cotizar(self.taller())
        self.assertEqual(r.json(), {"folio": g.folio, "mandar": f"/mandar/cotizacion/{g.folio}/"})
        # sin JavaScript: regresa al mostrador y la ventana se abre sola
        r = self.client.post(reverse("ticket_cotizar"), {"luego": "enviar", "next": "/"})
        nueva = TicketGuardado.objects.filter(tipo="COT").latest("pk")
        self.assertRedirects(r, "/", fetch_redirect_response=False)
        self.assertContains(self.client.get("/"), f'data-mandar-abrir="/mandar/cotizacion/{nueva.folio}/"')
        self.assertNotContains(self.client.get("/"), "data-mandar-abrir")  # solo una vez
        # «Imprimir» sigue abriendo la hoja
        r = self.client.post(reverse("ticket_cotizar"), {"luego": "imprimir"})
        self.assertRedirects(r, reverse("cotizacion", args=[TicketGuardado.objects.filter(tipo="COT").latest("pk").folio])
                             + "?imprimir=1", fetch_redirect_response=False)
        # ticket vacío: el error llega a la ventana, no a otra página
        self.client.post(reverse("ticket_vaciar"))
        r = self.client.post(reverse("ticket_cotizar"), {"luego": "enviar"}, HTTP_X_MANDAR="1")
        self.assertEqual((r.status_code, r.json()), (400, {"error": "El ticket está vacío."}))

    def test_la_ventana_trae_el_pdf_y_el_celular_y_correo_del_cliente(self):
        _, g = self.cotizar(self.taller())
        r = self.ventana("cotizacion", g.folio)
        self.assertContains(r, f'data-pdf="/mandar/cotizacion/{g.folio}/pdf/"')
        self.assertContains(r, f'data-archivo="Cotizacion-{g.folio}.pdf"')
        self.assertContains(r, 'data-numero="524421234567"')
        self.assertContains(r, 'data-correo="ramirez@taller.mx"')
        self.assertContains(r, "Celular: <b>442-123-45-67</b>")
        self.assertContains(r, 'href="whatsapp://send?phone=524421234567"')
        self.assertContains(r, f"Hola, Taller Ramírez. Le comparto la cotización {g.folio} de {settings.NEGOCIO_NOMBRE} por $301 con IVA, válida hasta el")
        # sin cuenta de correo en el servidor: el correo va por el menú de compartir
        self.assertContains(r, "Abrir mi correo con el PDF")
        self.assertNotContains(r, "data-correo-servidor")

    def test_publico_en_general_y_celular_incompleto(self):
        _, g = self.cotizar()
        r = self.ventana("cotizacion", g.folio)
        self.assertContains(r, "Sin celular registrado")
        self.assertContains(r, 'data-numero=""')
        self.assertContains(r, "Hola. Le comparto la cotización")
        self.assertNotContains(r, "whatsapp://")
        beto = Cliente.objects.create(nombre="Don Beto", telefono="12345", lista_precio=ListaPrecio.objects.get(nombre="Mostrador"))
        _, g = self.cotizar(beto)
        r = self.ventana("cotizacion", g.folio)
        self.assertContains(r, "(le faltan dígitos)")
        self.assertNotContains(r, "whatsapp://")

    def test_pdf_de_la_cotizacion(self):
        _, g = self.cotizar(self.taller())
        r = self.client.get(reverse("documento_pdf", args=["cotizacion", g.folio]))
        self.assertEqual(r["Content-Type"], "application/pdf")
        self.assertEqual(r["Content-Disposition"], f'inline; filename="Cotizacion-{g.folio}.pdf"')
        self.assertTrue(r.content.startswith(b"%PDF"))
        r = self.client.get(reverse("documento_pdf", args=["cotizacion", g.folio]), {"descargar": "1"})
        self.assertTrue(r["Content-Disposition"].startswith("attachment;"))
        ctx = views._cotizacion_ctx(RequestFactory().get("/"), g, formato="carta")
        texto = pdf.cotizacion(ctx, "Refaccionaria López", comprimir=False).decode("latin-1")
        for va in (g.folio, "Taller Ram", "PH966B", "GP-37", "SOBRE PEDIDO", "$301", "Precios v"):
            self.assertIn(va, texto, va)

    def test_el_ticket_de_la_venta_tambien_se_manda(self):
        r, venta = self.vender(self.taller())
        self.assertContains(r, f'data-mandar="/mandar/remision/{venta.folio}/"')  # junto a «Imprimir remisión»
        r = self.ventana("remision", venta.folio)
        self.assertContains(r, f'data-archivo="Remision-{venta.folio}.pdf"')
        self.assertContains(r, f"Le comparto su remisión {venta.folio} de {settings.NEGOCIO_NOMBRE} por $98. Gracias por su compra.")
        r = self.client.get(reverse("documento_pdf", args=["remision", venta.folio]))
        self.assertTrue(r.content.startswith(b"%PDF"))
        texto = pdf.remision(views._remision_ctx(venta), "Refaccionaria López", comprimir=False).decode("latin-1")
        for va in (venta.folio, "Taller Ram", "PH966B", "$98", "Efectivo"):
            self.assertIn(va, texto, va)
        # también desde Remisiones y desde la venta
        self.assertContains(self.client.get(reverse("remisiones")), f'data-mandar="/mandar/remision/{venta.folio}/"')
        self.assertContains(self.client.get(reverse("venta", args=[venta.folio])), f'data-mandar="/mandar/remision/{venta.folio}/"')

    def test_solo_con_sesion_y_solo_lo_que_existe(self):
        _, g = self.cotizar()
        self.assertEqual(self.client.get(reverse("mandar", args=["factura", g.folio])).status_code, 404)
        self.assertEqual(self.client.get(reverse("mandar", args=["remision", "VTA-999999"])).status_code, 404)
        self.client.logout()
        for url in (reverse("mandar", args=["cotizacion", g.folio]), reverse("documento_pdf", args=["cotizacion", g.folio])):
            self.assertEqual(self.client.get(url).status_code, 302, url)

    @override_settings(**SERVIDOR)
    def test_correo_desde_el_servidor_con_el_pdf_adjunto(self):
        cliente = self.taller(correo="")
        _, g = self.cotizar(cliente)
        r = self.ventana("cotizacion", g.folio)
        self.assertContains(r, "data-correo-servidor")
        self.assertContains(r, "Sale de Refaccionaria López &lt;ventas@refalopez.mx&gt; con el PDF adjunto.")
        self.assertContains(r, 'name="guardar" value="1" checked')
        url = reverse("mandar_correo", args=["cotizacion", g.folio])
        r = self.client.post(url, {"correo": " Compras@Taller.MX ", "guardar": "1"})
        self.assertEqual(r.json(), {"ok": True, "mensaje": "Listo: se mandó a compras@taller.mx con el PDF. "
                                                          "Quedó guardado en el cliente."})
        cliente.refresh_from_db()
        self.assertEqual(cliente.correo, "compras@taller.mx")
        msg = mail.outbox[0]
        self.assertEqual(msg.to, ["compras@taller.mx"])
        self.assertEqual(msg.reply_to, ["mostrador@refalopez.mx"])
        self.assertEqual(msg.subject, f"Cotización {g.folio} · {settings.NEGOCIO_NOMBRE}")
        self.assertTrue(msg.body.startswith("Hola, Taller Ramírez:\n\nLe comparto la cotización"))
        self.assertIn("Va en el PDF adjunto.", msg.body)
        nombre, contenido, tipo = msg.attachments[0]
        self.assertEqual((nombre, tipo), (f"Cotizacion-{g.folio}.pdf", "application/pdf"))
        self.assertTrue(contenido.startswith(b"%PDF"))
        # correo mal escrito, o el servidor de correo no contesta: se avisa y no se guarda nada
        r = self.client.post(url, {"correo": "otro@", "guardar": "1"})
        self.assertEqual((r.status_code, r.json()["ok"]), (400, False))
        with mock.patch.object(EmailMessage, "send", side_effect=OSError("timeout")), self.assertLogs("ventas.views", "ERROR"):
            r = self.client.post(url, {"correo": "nuevo@taller.mx", "guardar": "1"})
        self.assertEqual(r.status_code, 502)
        self.assertIn("No salió el correo", r.json()["error"])
        cliente.refresh_from_db()
        self.assertEqual(cliente.correo, "compras@taller.mx")
        self.assertEqual(len(mail.outbox), 1)

    def test_sin_cuenta_de_correo_no_intenta_mandar(self):
        _, g = self.cotizar(self.taller())
        r = self.client.post(reverse("mandar_correo", args=["cotizacion", g.folio]), {"correo": "a@b.mx"})
        self.assertEqual(r.status_code, 503)
        self.assertEqual(mail.outbox, [])


class BrevoTests(TestCase):
    @override_settings(BREVO_API_KEY="xkeysib-prueba", EMAIL_BACKEND="ventas.correo.BrevoBackend")
    def test_manda_por_https_con_el_pdf_adjunto(self):
        respuesta = mock.MagicMock()
        respuesta.__enter__.return_value = BytesIO(b'{"messageId": "<1@brevo>"}')
        with mock.patch("ventas.correo.urlopen", return_value=respuesta) as abrir:
            msg = EmailMessage("Cotización COT-000001", "Hola", "Refaccionaria López <ventas@refalopez.mx>",
                               ["cliente@taller.mx"], reply_to=["mostrador@refalopez.mx"])
            msg.attach("Cotizacion-COT-000001.pdf", b"%PDF-1.4 prueba", "application/pdf")
            self.assertEqual(msg.send(), 1)
        pedido = abrir.call_args.args[0]
        self.assertEqual(pedido.full_url, "https://api.brevo.com/v3/smtp/email")
        self.assertEqual(pedido.get_header("Api-key"), "xkeysib-prueba")
        datos = json.loads(pedido.data)
        self.assertEqual(datos["sender"], {"email": "ventas@refalopez.mx", "name": "Refaccionaria López"})
        self.assertEqual(datos["to"], [{"email": "cliente@taller.mx"}])
        self.assertEqual(datos["replyTo"], {"email": "mostrador@refalopez.mx"})
        self.assertEqual((datos["subject"], datos["textContent"]), ("Cotización COT-000001", "Hola"))
        self.assertEqual(datos["attachment"][0]["name"], "Cotizacion-COT-000001.pdf")
        self.assertEqual(base64.b64decode(datos["attachment"][0]["content"]), b"%PDF-1.4 prueba")

    @override_settings(BREVO_API_KEY="mala", EMAIL_BACKEND="ventas.correo.BrevoBackend")
    def test_clave_mala_avisa(self):
        error = HTTPError("https://api.brevo.com/v3/smtp/email", 401, "Unauthorized", {}, BytesIO(b'{"code":"unauthorized"}'))
        with mock.patch("ventas.correo.urlopen", side_effect=error), self.assertRaises(correo.ErrorCorreo):
            EmailMessage("A", "B", "x@y.mx", ["z@y.mx"]).send()


class EnlaceDeAntesTests(TestCase):
    """Las cotizaciones que ya se mandaron con enlace siguen abriendo hasta que vence."""
    setUp = test_mostrador.MostradorTests.setUp

    def test_abre_sin_entrar_y_sin_nada_interno(self):
        self.client.post(reverse("ticket_codigo"), {"codigo": "PH966B"})
        self.client.post(reverse("ticket_cotizar"), {"luego": "imprimir"})
        g = TicketGuardado.objects.get(tipo="COT")
        token = compartir.token(g)
        self.client.logout()
        r = self.client.get(reverse("cotizacion_publica", args=[token]))
        self.assertContains(r, g.folio)
        self.assertContains(r, "Imprimir o guardar PDF")
        self.assertEqual(r["X-Robots-Tag"], "noindex, nofollow")
        for interno in ("Volver al mostrador", "Mandar por WhatsApp", "mandar.js", "Ticket 80 mm"):
            self.assertNotContains(r, interno)
        espera = TicketGuardado.objects.create(tipo="ESP", folio="ESP-000009", cliente=m.cliente_mostrador())
        for malo in (token[:-2] + "xx", compartir.token(espera)):
            self.assertContains(self.client.get(reverse("cotizacion_publica", args=[malo])),
                                "Este enlace ya no abre la cotización", status_code=404)
        with mock.patch("django.core.signing.time.time", return_value=time.time() - (compartir.ENLACE_DIAS + 1) * 86400):
            viejo = compartir.token(g)
        self.assertEqual(self.client.get(reverse("cotizacion_publica", args=[viejo])).status_code, 404)

    def test_la_hoja_interna_abre_la_ventana_de_mandar(self):
        self.client.post(reverse("ticket_codigo"), {"codigo": "PH966B"})
        self.client.post(reverse("ticket_cotizar"), {"luego": "imprimir"})
        g = TicketGuardado.objects.get(tipo="COT")
        r = self.client.get(reverse("cotizacion", args=[g.folio]))
        self.assertContains(r, f'data-mandar="/mandar/cotizacion/{g.folio}/"')
        self.assertNotContains(r, "data-mandar-abrir")
        r = self.client.get(reverse("cotizacion", args=[g.folio]), {"enviar": "1"})
        self.assertContains(r, f'data-mandar-abrir="/mandar/cotizacion/{g.folio}/"')
        self.assertContains(self.client.get(reverse("cotizaciones")), f'data-mandar="/mandar/cotizacion/{g.folio}/"')
