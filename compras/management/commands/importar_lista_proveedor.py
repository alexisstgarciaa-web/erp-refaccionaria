from datetime import date
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from compras.listas import importar


class Command(BaseCommand):
    help = "Importa listas de precios de proveedor (.xlsx). Ej.: importar_lista_proveedor MORSA ./listas --fecha 2026-11-13"

    def add_arguments(self, parser):
        parser.add_argument("proveedor")
        parser.add_argument("rutas", nargs="+", help="Archivos .xlsx o carpetas que los contienen")
        parser.add_argument("--fecha", help="Fecha de la lista, AAAA-MM-DD (por omisión, hoy)")

    def handle(self, proveedor, rutas, fecha=None, **_):
        archivos = []
        for r in map(Path, rutas):
            if r.is_dir():
                archivos += sorted(p for p in r.glob("*.xlsx") if not p.name.startswith(("~$", "00 ")))
            elif r.suffix.lower() == ".xlsx":
                archivos.append(r)
            else:
                raise CommandError(f"No es .xlsx ni carpeta: {r}")
        if not archivos:
            raise CommandError("No encontré archivos .xlsx.")
        res = importar(proveedor, archivos, date.fromisoformat(fecha) if fecha else None)
        self.stdout.write(self.style.SUCCESS(
            f"{len(archivos)} archivos · {res['nuevas']} partidas nuevas · {res['actualizadas']} actualizadas "
            f"({res['costos_cambiados']} con costo distinto) · {res['sin_costo']} sin costo"))
