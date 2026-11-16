"""Comas en las cifras, RFC genérico y dirección por código postal."""
import gzip
import tempfile
from decimal import Decimal as D
from io import StringIO
from pathlib import Path

from django.contrib.admin import helpers
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.urls import reverse

from catalogo.models import Precio
from ventas import mostrador as m
from ventas.cifras import CifraInput, cifra
from ventas.models import RFC_GENERICO, Cliente, CodigoPostal, FormaPago, Venta
from ventas.servicios import registrar_abono, registrar_venta
from ventas.templatetags.erp import miles, pesos
from ventas.tests import Base

TSV = (
    "cp\tcolonia\ttipo\tmunicipio\testado\tciudad\n"
    "76000\tCentro\tColonia\tQuerétaro\tQuerétaro\tSantiago de Querétaro\n"
    "76000\tLa Cruz\tFraccionamiento\tQuerétaro\tQuerétaro\tSantiago de Querétaro\n"
    "76000\tÁlamos\tColonia\tQuerétaro\tQuerétaro\tSantiago de Querétaro\n"
    "76030\tJardines de Querétaro\tColonia\tQuerétaro\tQuerétaro\tSantiago de Querétaro\n"
)
# el TXT oficial de Correos de México: una nota, el encabezado con |, y viene en latin-1
SEPOMEX_TXT = (
    "El Catálogo Nacional de Códigos Postales, es elaborado por Correos de México.\n"
    "d_codigo|d_asenta|d_tipo_asenta|D_mnpio|d_estado|d_ciudad|d_CP|c_estado\n"
    "64000|Monterrey Centro|Colonia|Monterrey|Nuevo León|Monterrey|64001|19\n"
    "64000|Obispado|Colonia|Monterrey|Nuevo León|Monterrey|64001|19\n"
)


def archivo_tsv(carpeta):
    ruta = Path(carpeta) / "cp.tsv.gz"
    with gzip.open(ruta, "wt", encoding="utf-8") as f:
        f.write(TSV)
    return str(ruta)


class CifrasTests(Base):
    def test_filtros(self):
        self.assertEqual((miles(D("1234.5")), miles(D("908")), miles(D("1500.00"))), ("1,234.50", "908", "1,500"))
        self.assertEqual((pesos(D("1234567.891")), pesos(D("-1500"))), ("$1,234,567.89", "-$1,500"))
        self.assertEqual((miles(None), pesos("")), ("", ""))
        # en la Administración: dinero con centavos, costos y cantidades sin ceros de más
        self.assertEqual((cifra(D("1234.5")), cifra(D("85.3450"), 4), cifra(D("1500.000"), 3)), ("1,234.50", "85.345", "1,500"))

    def test_administracion_con_comas(self):
        campo = Venta._meta.get_field("total")
        self.assertEqual(helpers.display_for_field(D("12345.5"), campo, "-"), "12,345.50")
        self.assertEqual(helpers.display_for_field(None, Venta._meta.get_field("saldo"), "-"), "-")
        # los años y números enteros no llevan coma
        from catalogo.models import Vehiculo
        self.assertEqual(helpers.display_for_field(2015, Vehiculo._meta.get_field("anio_desde"), "-"), "2015")
        caja = CifraInput(2)
        self.assertEqual(caja.format_value(D("25000.00")), "25,000.00")
        self.assertEqual(caja.value_from_datadict({"limite": "$25,000.50"}, {}, "limite"), "25000.50")
        admin = get_user_model().objects.create_superuser("alexis", password="x")
        self.client.force_login(admin)
        Cliente.objects.filter(pk=self.taller.pk).update(limite_credito=D("25000"))
        r = self.client.get(reverse("admin:ventas_cliente_changelist"))
        self.assertContains(r, "25,000.00")
        r = self.client.get(reverse("admin:ventas_cliente_change", args=[self.taller.pk]))
        self.assertContains(r, 'value="25,000.00"')
        self.assertContains(r, 'data-cifra="pesos"')
        # al guardar se quitan las comas
        datos = {"nombre": self.taller.nombre, "rfc": RFC_GENERICO, "lista_precio": self.taller_lp.pk, "activo": "on",
                 "dias_credito": "15", "limite_credito": "30,500.50", "codigo_postal": "", "colonia": "", "municipio": "",
                 "estado": "", "calle": "", "numero_exterior": "", "numero_interior": ""}
        r = self.client.post(reverse("admin:ventas_cliente_change", args=[self.taller.pk]), datos)
        self.assertEqual(r.status_code, 302)
        self.taller.refresh_from_db()
        self.assertEqual(self.taller.limite_credito, D("30500.50"))

    def test_campos_y_mensajes_con_comas(self):
        Precio.objects.filter(producto=self.filtro, lista=self.mostrador).update(precio=D("1234.50"))
        self.client.force_login(self.user)
        r = self.client.get(reverse("ticket_sugerir"), {"q": "GP-37"})
        self.assertEqual(r.json()["sugerencias"][0]["precio"], "1,234.50")
        # el precio tecleado con comas se entiende
        self.client.post(reverse("ticket_codigo"), {"codigo": "GP-37", "cantidad": "1", "precio": "1,300"})
        ticket = m.Ticket(self.client.session)
        self.assertEqual(ticket.lineas()[0].precio, D("1300"))
        r = self.client.get(reverse("mostrador"))
        self.assertContains(r, 'value="1,300"')
        # mensajes de dinero con comas
        Precio.objects.filter(producto=self.filtro, lista=self.taller_lp).update(precio=D("1500"))
        Cliente.objects.filter(pk=self.taller.pk).update(limite_credito=D("100000"))
        self.taller.refresh_from_db()
        venta = registrar_venta(self.turno, self.taller, [{"producto": self.filtro, "cantidad": 2}], FormaPago.CREDITO)
        with self.assertRaisesMessage(ValidationError, "hasta $3,000."):
            registrar_abono(venta, venta.saldo + 1, FormaPago.EFECTIVO, self.turno)
        Cliente.objects.filter(pk=self.taller.pk).update(limite_credito=D("4000"))
        self.taller.refresh_from_db()
        with self.assertRaisesMessage(ValidationError, "La venta de $1,500 excede el crédito disponible de Taller Hermanos Ruiz ($1,000)."):
            registrar_venta(self.turno, self.taller, [{"producto": self.filtro, "cantidad": 1}], FormaPago.CREDITO)


class RfcGenericoTests(Base):
    def test_por_default_y_al_dejarlo_vacio(self):
        self.assertEqual(self.publico.rfc, RFC_GENERICO)
        c = Cliente.objects.create(nombre="Sin RFC", lista_precio=self.mostrador, rfc="")
        self.assertEqual(c.rfc, RFC_GENERICO)
        c.rfc = " tara800101ab1 "
        c.save()
        self.assertEqual(c.rfc, "TARA800101AB1")
        self.assertEqual(m.cliente_mostrador().rfc, RFC_GENERICO)

    def test_alta_rapida(self):
        self.client.force_login(self.user)
        url = reverse("ticket_cliente_nuevo")
        r = self.client.get(reverse("mostrador"))
        self.assertContains(r, f'name="rfc" value="{RFC_GENERICO}"')
        self.client.post(url, {"nombre": "Don Chuy", "rfc": ""})
        self.assertEqual(Cliente.objects.get(nombre="Don Chuy").rfc, RFC_GENERICO)
        self.client.post(url, {"nombre": "Refacciones Lupita", "rfc": "rlu 900101 ab1"})
        self.assertEqual(Cliente.objects.get(nombre="Refacciones Lupita").rfc, "RLU900101AB1")


class DireccionTests(Base):
    def setUp(self):
        super().setUp()
        with tempfile.TemporaryDirectory() as carpeta:
            call_command("cargar_codigos_postales", archivo=archivo_tsv(carpeta), stdout=StringIO())

    def test_carga_una_vez_y_acepta_el_txt_oficial(self):
        self.assertEqual(CodigoPostal.objects.count(), 4)
        salida = StringIO()
        with tempfile.TemporaryDirectory() as carpeta:
            call_command("cargar_codigos_postales", archivo=archivo_tsv(carpeta), stdout=salida)
            self.assertIn("al día", salida.getvalue())
            txt = Path(carpeta) / "CPdescarga.txt"
            txt.write_bytes(SEPOMEX_TXT.encode("latin-1"))
            call_command("cargar_codigos_postales", archivo=str(txt), stdout=StringIO())
        self.assertEqual(list(CodigoPostal.objects.values_list("cp", "colonia", "estado")),
                         [("64000", "Monterrey Centro", "Nuevo León"), ("64000", "Obispado", "Nuevo León")])

    def test_consulta_por_cp(self):
        self.client.force_login(self.user)
        r = self.client.get(reverse("codigo_postal", args=["76000"])).json()
        self.assertEqual((r["estado"], r["municipio"]), ("Querétaro", "Querétaro"))
        self.assertEqual(r["colonias"], ["Álamos", "Centro", "La Cruz"])  # en orden, sin importar el acento
        self.assertEqual(self.client.get(reverse("codigo_postal", args=["99999"])).json(),
                         {"cp": "99999", "encontrado": False, "colonias": []})
        self.client.logout()
        self.assertEqual(self.client.get(reverse("codigo_postal", args=["76000"])).status_code, 302)

    def test_alta_rapida_con_direccion_y_remision(self):
        self.client.force_login(self.user)
        url = reverse("ticket_cliente_nuevo")
        r = self.client.get(reverse("mostrador"))
        self.assertContains(r, 'name="codigo_postal"')
        self.assertContains(r, 'data-cp="/cp/00000/"')
        self.client.post(url, {"nombre": "Taller Ramírez", "codigo_postal": "76000", "colonia": "Centro",
                               "municipio": "Querétaro", "estado": "Querétaro", "calle": "Av. Juárez",
                               "numero_exterior": "120", "numero_interior": "B"})
        c = Cliente.objects.get(nombre="Taller Ramírez")
        self.assertEqual(c.domicilio, ["Av. Juárez 120 int. B", "Col. Centro, C.P. 76000, Querétaro, Querétaro"])
        # CP mal escrito: no se da de alta
        r = self.client.post(url, {"nombre": "Otro", "codigo_postal": "7600", "next": reverse("mostrador")}, follow=True)
        self.assertContains(r, "El código postal lleva 5 números")
        self.assertFalse(Cliente.objects.filter(nombre="Otro").exists())
        # la remisión lleva la dirección
        venta = registrar_venta(self.turno, c, [{"producto": self.filtro, "cantidad": 1}], FormaPago.EFECTIVO, self.user)
        r = self.client.get(reverse("remision", args=[venta.folio]))
        self.assertContains(r, "Av. Juárez 120 int. B")
        self.assertContains(r, "C.P. 76000")
        self.assertContains(r, f"RFC {RFC_GENERICO}")

    def test_admin_del_cliente(self):
        admin = get_user_model().objects.create_superuser("alexis", password="x")
        self.client.force_login(admin)
        r = self.client.get(reverse("admin:ventas_cliente_add"))
        self.assertContains(r, 'data-cp="/cp/00000/"')
        self.assertContains(r, "ventas/cp.js")
        self.assertEqual(self.client.get(reverse("admin:ventas_codigopostal_changelist"), {"q": "76000"}).status_code, 200)


class TecladoTests(Base):
    def test_enter_pasa_de_campo_en_pantallas_y_administracion(self):
        self.client.force_login(self.user)
        r = self.client.get(reverse("mostrador"))
        self.assertContains(r, "ventas/campos.js")
        # la captura y el cobro conservan su propio Enter
        self.assertContains(r, '<form class="captura" method="post" data-enter="normal"')
        self.assertContains(r, 'id="cobro-form" data-enter="normal"')
        admin = get_user_model().objects.create_superuser("alexis", password="x")
        self.client.force_login(admin)
        self.assertContains(self.client.get(reverse("admin:ventas_cliente_add")), "ventas/campos.js")
