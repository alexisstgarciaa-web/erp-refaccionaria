from decimal import Decimal as D

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from catalogo.models import UnidadMedida
from compras.listas import agregar_a_catalogo, importar
from compras.models import OrdenCompra, OrdenCompraLinea, PartidaListaProveedor, Proveedor
from compras.tests import lista


class AdminComprasTests(TestCase):
    def setUp(self):
        self.admin = get_user_model().objects.create_superuser("alexis", password="x")
        self.client.force_login(self.admin)

    def test_importar_lista_desde_el_admin(self):
        ruta = lista([["GP-37", "Aceite", "FILTRO PARA ACEITE", "GONHER", "NISSAN. TSURU L4 1.6 (90-04)", 66.17, ""]])
        archivo = SimpleUploadedFile("Lista MORSA filtros GONHER.xlsx", ruta.read_bytes())
        r = self.client.post(reverse("admin:compras_partida_importar"),
                             {"proveedor": "MORSA", "fecha": "2026-11-13", "archivos": [archivo]}, follow=True)
        self.assertContains(r, "1 partidas nuevas")
        p = PartidaListaProveedor.objects.get()
        self.assertEqual((p.archivo_origen, p.vehiculos.count()), ("Lista MORSA filtros GONHER.xlsx", 1))

    def test_rechaza_archivo_que_no_es_excel(self):
        r = self.client.post(reverse("admin:compras_partida_importar"),
                             {"proveedor": "MORSA", "fecha": "2026-11-13",
                              "archivos": [SimpleUploadedFile("lista.csv", b"a,b")]})
        self.assertContains(r, "no es .xlsx")
        self.assertFalse(PartidaListaProveedor.objects.exists())

    def test_recibir_todo_da_entrada_al_inventario(self):
        importar("MORSA", [lista([["CA6900", "Aire", "FILTRO PARA AIRE", "FRAM", "NISSAN. TSURU L4 1.6 (97-09)", 65.64, ""]])])
        producto = agregar_a_catalogo(PartidaListaProveedor.objects.get())
        oc = OrdenCompra.objects.create(folio="OC-00001", proveedor=Proveedor.objects.get(nombre="MORSA"), es_emergencia=True)
        OrdenCompraLinea.objects.create(orden=oc, producto=producto, unidad=UnidadMedida.objects.get(clave="PZA"),
                                        cantidad=2, costo_unitario=D("65.64"))
        self.client.post(reverse("admin:compras_ordencompra_changelist"),
                         {"action": "recibir_todo", "_selected_action": [oc.pk]})
        producto.refresh_from_db()
        oc.refresh_from_db()
        self.assertEqual((producto.existencia, oc.estado), (D("2"), OrdenCompra.Estado.RECIBIDA))
