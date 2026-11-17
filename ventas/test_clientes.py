"""Clientes cuando son miles: buscador en el ticket, búsqueda sin acentos ni guiones, frecuentes primero,
últimos atendidos, adeudo vencido y alta rápida sin duplicados."""
from datetime import timedelta
from decimal import Decimal as D

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.urls import reverse
from django.utils import timezone

from ventas import clientes as cli
from ventas import mostrador as m
from ventas import nip as nips
from ventas.models import Cliente, FormaPago, Venta, texto_busqueda
from ventas.servicios import registrar_venta
from ventas.tests import Base


class BusquedaTests(Base):
    def setUp(self):
        super().setUp()
        self.ramirez = Cliente.objects.create(nombre="Taller Ramírez, S.A.", telefono="442-123-4567", lista_precio=self.mostrador)
        self.pena = Cliente.objects.create(nombre="Refaccionaria Peña", telefono="4429876543", rfc="PEÑA800101AB1",
                                           lista_precio=self.mostrador)

    def test_texto_limpio(self):
        self.assertEqual(texto_busqueda("  Taller RAMÍREZ, S.A. de C.V. "), "taller ramirez s a de c v")
        self.assertEqual(self.ramirez.busqueda, "taller ramirez s a xaxx010101000 4421234567")
        self.ramirez.telefono = "55 1111 2222"
        self.ramirez.save(update_fields=["telefono"])  # aunque se guarde solo un campo, la búsqueda se actualiza
        self.ramirez.refresh_from_db()
        self.assertTrue(self.ramirez.busqueda.endswith("5511112222"))

    def test_sin_acentos_mayusculas_ni_guiones(self):
        for texto in ("ramirez", "RAMÍREZ", "ramirez taller", "Taller   Ramírez,", "4421234567", "442 123 4567",
                      "123-4567", "(442) 123 4567"):
            with self.subTest(texto=texto):
                self.assertEqual(cli.buscar_clientes(texto), [self.ramirez])
        self.assertEqual(cli.buscar_clientes("pena"), [self.pena])
        self.assertEqual(cli.buscar_clientes("442 987 6543"), [self.pena])
        self.assertEqual(cli.buscar_clientes("peña800101"), [self.pena])  # por RFC
        self.assertEqual(cli.buscar_clientes("ramirez peña"), [])  # todas las palabras deben estar

    def test_numero_corto_es_folio(self):
        self.assertEqual(cli.buscar_clientes(str(self.pena.pk)), [self.pena])
        self.assertEqual(cli.buscar_clientes(self.pena.folio), [self.pena])

    def test_los_que_mas_compran_primero(self):
        otro = Cliente.objects.create(nombre="Taller Ramírez Norte", lista_precio=self.mostrador)
        registrar_venta(self.turno, self.ramirez, [{"producto": self.filtro, "cantidad": 1}], FormaPago.EFECTIVO)
        for _ in range(2):
            registrar_venta(self.turno, otro, [{"producto": self.filtro, "cantidad": 1}], FormaPago.EFECTIVO)
        self.assertEqual(cli.buscar_clientes("ramirez"), [otro, self.ramirez])
        # las canceladas no cuentan
        Venta.objects.filter(cliente=otro).update(estado=Venta.Estado.CANCELADA)
        self.assertEqual(cli.buscar_clientes("ramirez"), [self.ramirez, otro])

    def test_inactivos_no_salen(self):
        self.ramirez.activo = False
        self.ramirez.save()
        self.assertEqual(cli.buscar_clientes("ramirez"), [])

    def test_ultimos_atendidos_con_el_campo_vacio(self):
        self.client.force_login(self.user)
        for c in (self.ramirez, self.publico, self.pena, self.ramirez):
            registrar_venta(self.turno, c, [{"producto": self.filtro, "cantidad": 1}], FormaPago.EFECTIVO)
        r = self.client.get(reverse("clientes_buscar"), {"q": ""}).json()
        self.assertEqual(r["clientes"], [])
        self.assertEqual([c["pk"] for c in r["recientes"]], [self.ramirez.pk, self.pena.pk])  # sin Público en general

    def test_listas_buscan_igual(self):
        self.client.force_login(self.user)
        v = registrar_venta(self.turno, self.ramirez, [{"producto": self.filtro, "cantidad": 1}], FormaPago.EFECTIVO)
        otra = registrar_venta(self.turno, self.pena, [{"producto": self.filtro, "cantidad": 1}], FormaPago.EFECTIVO)
        for q in ("ramirez", self.ramirez.folio, "123-4567"):
            with self.subTest(q=q):
                r = self.client.get(reverse("remisiones"), {"q": q})
                self.assertContains(r, v.folio)
                self.assertNotContains(r, otra.folio)
        self.assertContains(self.client.get(reverse("remisiones"), {"q": v.folio}), v.folio)

    def test_administracion_busca_sin_acentos(self):
        admin = get_user_model().objects.create_superuser("jefe", password="x")
        self.client.force_login(admin)
        r = self.client.get(reverse("admin:ventas_cliente_changelist"), {"q": "ramirez"})
        self.assertContains(r, "Taller Ramírez")
        self.assertNotContains(r, "Refaccionaria Peña")


class TicketTests(Base):
    def setUp(self):
        super().setUp()
        nips.asignar(self.user, "4821")
        self.client.force_login(self.user)

    def test_el_ticket_ya_no_lista_a_todos(self):
        Cliente.objects.bulk_create([Cliente(nombre=f"Cliente Prueba {i:04d}", lista_precio=self.mostrador) for i in range(300)])
        r = self.client.get(reverse("mostrador"))
        self.assertContains(r, 'data-clientes="/clientes/buscar/"')
        self.assertNotContains(r, "Cliente Prueba 0150")
        self.assertNotContains(r, '<select id="cliente"')
        self.assertNotContains(r, "Público en general</button>")  # ya es Público en general

    def test_elegir_cliente_y_regresar_a_publico(self):
        r = self.client.post(reverse("ticket_cliente"), {"cliente": self.taller.pk, "next": reverse("mostrador")}, follow=True)
        self.assertEqual(m.Ticket(self.client.session).cliente, self.taller)
        self.assertContains(r, f'<b>Taller Hermanos Ruiz</b> <span class="mono">{self.taller.folio}</span>')
        self.assertContains(r, "Crédito 15 días · disponible $1,000")
        self.assertContains(r, "Público en general</button>")
        self.client.post(reverse("ticket_cliente"), {"cliente": m.cliente_mostrador().pk})
        self.assertEqual(m.Ticket(self.client.session).cliente, self.publico)

    def test_adeudo_vencido_a_la_vista(self):
        v = registrar_venta(self.turno, self.taller, [{"producto": self.filtro, "cantidad": 2}], FormaPago.CREDITO)  # 214
        self.assertEqual(cli.credito(self.taller), (D("214.00"), D("786.00"), D("0")))
        Venta.objects.filter(pk=v.pk).update(vence=timezone.localdate() - timedelta(days=2))
        self.assertEqual(cli.cliente_json(self.taller)["vencido_txt"], "$214")
        r = self.client.post(reverse("ticket_cliente"), {"cliente": self.taller.pk, "next": reverse("mostrador")}, follow=True)
        self.assertContains(r, "Debe $214 vencido")
        r = self.client.get(reverse("clientes_buscar"), {"q": "hermanos"}).json()
        self.assertEqual(r["clientes"][0]["vencido_txt"], "$214")


class SinDuplicadosTests(Base):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.user)
        self.ramirez = Cliente.objects.create(nombre="Taller Ramírez", telefono="442 123 4567", rfc="TARA800101AB1",
                                              lista_precio=self.mostrador)

    def alta(self, **datos):
        base = {"nombre": "", "telefono": "", "rfc": "", "lista": "", "dias": "0", "limite": "0", "next": reverse("mostrador")}
        return self.client.post(reverse("ticket_cliente_nuevo"), {**base, **datos}, follow=True)

    def test_mismo_celular_rfc_o_nombre_elige_al_que_ya_esta(self):
        for datos, por in (({"nombre": "Juan Ramírez", "telefono": "442-123-4567"}, "ese teléfono"),
                           ({"nombre": "Otro Nombre", "rfc": "tara800101ab1"}, "ese RFC"),
                           ({"nombre": "taller ramirez"}, "ese nombre")):
            with self.subTest(por=por):
                r = self.alta(**datos)
                self.assertContains(r, f"ya estaba dado de alta con {por}; lo dejé elegido")
                self.assertEqual(Cliente.objects.count(), 3)  # Público, Hermanos Ruiz y Ramírez
                self.assertEqual(m.Ticket(self.client.session).cliente, self.ramirez)

    def test_otros_datos_si_se_dan_de_alta(self):
        self.alta(nombre="Juan Ramírez", telefono="4421234568")  # otro número
        self.alta(nombre="Pedro López", telefono="123")  # número corto: no se compara
        self.assertEqual(Cliente.objects.count(), 5)

    def test_rfc_repetido_en_administracion(self):
        otro = Cliente(nombre="Copia", rfc="TARA800101AB1", lista_precio=self.mostrador)
        with self.assertRaisesMessage(ValidationError, "Ese RFC ya es de Taller Ramírez"):
            otro.full_clean()
        Cliente(nombre="Sin RFC", lista_precio=self.mostrador).full_clean()  # el genérico se puede repetir
