"""Mandar la cotización al cliente por WhatsApp o correo, con un enlace que abre sin entrar al sistema."""
import time
from unittest import mock
from urllib.parse import quote

from django.test import TestCase
from django.urls import reverse

from catalogo.models import ListaPrecio
from ventas import compartir
from ventas import mostrador as m
from ventas.models import Cliente, TicketGuardado
from ventas import test_mostrador


class NumeroWhatsappTests(TestCase):
    def test_celular_de_mexico_en_como_venga(self):
        for escrito in ("4421234567", "442 123 4567", "(442) 123-45-67", "52 442 123 4567", "+52 442 123 4567",
                        "+52 1 442 123 4567", "521 442 123 4567", "044 442 123 4567"):
            self.assertEqual(compartir.whatsapp(escrito), "524421234567", escrito)

    def test_extranjero_con_mas_y_lo_que_no_alcanza(self):
        self.assertEqual(compartir.whatsapp("+1 (210) 555-0134"), "12105550134")
        for malo in ("", "12345", "442 123 456", "1 442 123 4567 99"):
            self.assertEqual(compartir.whatsapp(malo), "", malo)


class EnviarCotizacionTests(TestCase):
    setUp = test_mostrador.MostradorTests.setUp  # mismo catálogo, cajero y NIP

    def cotizar(self, cliente=None, **extra):
        if cliente:
            self.client.post(reverse("ticket_cliente"), {"cliente": cliente.pk})
        self.client.post(reverse("ticket_codigo"), {"codigo": "PH966B", "cantidad": "2"})
        self.client.post(reverse("ticket_codigo"), {"codigo": "GP-37"})  # sobre pedido
        r = self.client.post(reverse("ticket_cotizar"), {"luego": "enviar", **extra})
        g = TicketGuardado.objects.filter(tipo="COT").latest("pk")
        return r, g

    def taller(self, **campos):
        return Cliente.objects.create(nombre="Taller Ramírez", telefono="442-123-45-67", correo="ramirez@taller.mx",
                                      lista_precio=ListaPrecio.objects.get(nombre="Mostrador"), **campos)

    def test_cotizar_y_mandar_abre_la_hoja_lista_para_whatsapp_y_correo(self):
        r, g = self.cotizar(self.taller())
        self.assertRedirects(r, reverse("cotizacion", args=[g.folio]) + "?enviar=1#enviar", fetch_redirect_response=False)
        r = self.client.get(reverse("cotizacion", args=[g.folio]), {"enviar": "1"})
        envio = r.context["envio"]
        self.assertTrue(envio["enlace"].startswith("http://testserver/cotizacion/"))
        # el mensaje trae folio, piezas, total, vigencia y el enlace
        texto = envio["texto_wa"]
        self.assertIn("Hola, Taller Ramírez", texto)
        self.assertIn(f"*{g.folio}*", texto)
        self.assertIn("• 2 × ", texto)
        self.assertIn("(sobre pedido)", texto)
        self.assertIn("*Total: $301* con IVA", texto)
        self.assertIn("Precios válidos hasta el", texto)
        self.assertTrue(texto.endswith(envio["enlace"]))
        self.assertNotIn("*", envio["texto_correo"])  # en el correo no van los asteriscos de WhatsApp
        # botones con el celular y el correo del cliente ya puestos
        self.assertEqual(envio["whatsapp"], "https://wa.me/524421234567?text=" + quote(texto, safe=""))
        self.assertTrue(envio["mailto"].startswith("mailto:ramirez@taller.mx?subject=Cotizaci%C3%B3n%20" + g.folio))
        self.assertContains(r, 'id="enviar-wa" href="https://wa.me/524421234567?text=')
        self.assertContains(r, 'id="enviar-tel" type="tel" inputmode="tel" value="442-123-45-67"')
        self.assertContains(r, 'autofocus')
        self.assertContains(r, '<script id="enviar-datos" type="application/json">')
        self.assertNotContains(r, 'setTimeout(() => window.print()')  # para mandarla no salta la impresora

    def test_sin_celular_whatsapp_deja_elegir_contacto_y_con_celular_mal_avisa(self):
        _, g = self.cotizar()
        r = self.client.get(reverse("cotizacion", args=[g.folio]))
        self.assertTrue(r.context["envio"]["whatsapp"].startswith("https://wa.me/?text="))
        self.assertContains(r, "Sin número, WhatsApp te deja elegir el contacto.")
        self.assertIn("Hola. Le comparto", r.context["envio"]["texto_wa"])  # Público en general: sin nombre
        beto = Cliente.objects.create(nombre="Don Beto", telefono="12345", lista_precio=ListaPrecio.objects.get(nombre="Mostrador"))
        _, g = self.cotizar(beto)
        r = self.client.get(reverse("cotizacion", args=[g.folio]))
        self.assertEqual(r.context["envio"]["whatsapp"], "")
        self.assertContains(r, 'aria-disabled="true"')
        self.assertContains(r, "El celular lleva 10 dígitos")

    def test_imprimir_sigue_igual(self):
        self.client.post(reverse("ticket_codigo"), {"codigo": "PH966B"})
        r = self.client.post(reverse("ticket_cotizar"), {"luego": "imprimir"})
        g = TicketGuardado.objects.get(tipo="COT")
        self.assertRedirects(r, reverse("cotizacion", args=[g.folio]) + "?imprimir=1", fetch_redirect_response=False)

    def test_el_cliente_abre_el_enlace_sin_entrar_y_sin_ver_nada_mas(self):
        _, g = self.cotizar(self.taller())
        enlace = self.client.get(reverse("cotizacion", args=[g.folio])).context["envio"]["enlace"]
        self.client.logout()
        r = self.client.get(enlace)
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, g.folio)
        self.assertContains(r, "Taller Ramírez")
        self.assertContains(r, "$301")
        self.assertContains(r, "Imprimir o guardar PDF")
        self.assertContains(r, '<meta name="robots" content="noindex, nofollow">')
        self.assertEqual(r["X-Robots-Tag"], "noindex, nofollow")
        for interno in ("Volver al mostrador", "Mandar al cliente", "Ticket 80 mm", "enviar-datos", "/cotizaciones/"):
            self.assertNotContains(r, interno)
        # la hoja interna sí pide entrar
        self.assertEqual(self.client.get(reverse("cotizacion", args=[g.folio])).status_code, 302)

    def test_enlace_alterado_de_otro_tipo_o_vencido_no_abre(self):
        _, g = self.cotizar()
        token = compartir.token(g)
        self.client.logout()
        otro = compartir.signing.dumps(g.pk + 1, salt="otra cosa")
        espera = TicketGuardado.objects.create(tipo="ESP", folio="ESP-000009", cliente=m.cliente_mostrador())
        for malo in (token[:-2] + "xx", otro, compartir.token(espera)):
            r = self.client.get(reverse("cotizacion_publica", args=[malo]))
            self.assertEqual(r.status_code, 404, malo)
            self.assertContains(r, "Este enlace ya no abre la cotización", status_code=404)
        hace = time.time() - (compartir.ENLACE_DIAS + 1) * 86400
        with mock.patch("django.core.signing.time.time", return_value=hace):
            viejo = compartir.token(g)
        self.assertEqual(self.client.get(reverse("cotizacion_publica", args=[viejo])).status_code, 404)
        self.assertEqual(self.client.get(reverse("cotizacion_publica", args=[token])).status_code, 200)

    def test_alta_rapida_guarda_el_correo(self):
        r = self.client.post(reverse("ticket_cliente_nuevo"), {"nombre": "Flotilla Sur", "telefono": "4425550000",
                                                              "correo": "Compras@FlotillaSur.mx ", "next": "/"}, follow=True)
        self.assertContains(r, "dado de alta")
        self.assertEqual(Cliente.objects.get(nombre="Flotilla Sur").correo, "compras@flotillasur.mx")
        r = self.client.post(reverse("ticket_cliente_nuevo"), {"nombre": "Otro", "correo": "otro@", "next": "/"}, follow=True)
        self.assertContains(r, "Revisa el correo")
        self.assertFalse(Cliente.objects.filter(nombre="Otro").exists())
