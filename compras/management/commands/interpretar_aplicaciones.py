from django.core.management.base import BaseCommand

from compras.aplicaciones import generar
from compras.models import PartidaListaProveedor


class Command(BaseCommand):
    help = "Vuelve a interpretar el texto de aplicación de todas las partidas (vehículos por año)."

    def add_arguments(self, parser):
        parser.add_argument("--proveedor", help="Solo este proveedor, ej. MORSA")

    def handle(self, proveedor=None, **_):
        qs = PartidaListaProveedor.objects.exclude(tipo=PartidaListaProveedor.Tipo.OTRO)
        if proveedor:
            qs = qs.filter(proveedor__nombre__iexact=proveedor)
        n = generar(qs)
        total = qs.count()
        con = qs.filter(vehiculos__isnull=False).distinct().count()
        self.stdout.write(self.style.SUCCESS(
            f"{n:,} vehículos · {con:,} de {total:,} partidas con vehículo ({con / max(total, 1):.0%})"))
        sin = qs.filter(vehiculos__isnull=True).values_list("marca", flat=True)
        resumen = {}
        for marca in sin:
            resumen[marca] = resumen.get(marca, 0) + 1
        if resumen:
            self.stdout.write("Sin vehículo por marca: " + ", ".join(f"{k} {v}" for k, v in sorted(resumen.items(), key=lambda x: -x[1])))
