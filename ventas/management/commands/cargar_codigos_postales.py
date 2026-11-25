"""Carga el Catálogo Nacional de Códigos Postales de Correos de México (SEPOMEX) en CodigoPostal.

Sin argumentos usa ventas/datos/codigos_postales.tsv.gz, que va en el repositorio. Cuando Correos
de México publique uno más nuevo, se descarga su «CPdescarga.txt» (formato TXT) y se carga con
--archivo. Si la tabla ya tiene lo mismo que el archivo no hace nada, por eso build.sh lo corre en
cada publicación sin tardarse.
"""

import gzip
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from ventas.models import CodigoPostal

INCLUIDO = Path(__file__).resolve().parents[2] / "datos" / "codigos_postales.tsv.gz"
CAMPOS = ["cp", "colonia", "tipo", "municipio", "estado", "ciudad"]
# columnas del TXT oficial de Correos de México, en el mismo orden que CAMPOS
SEPOMEX = ["d_codigo", "d_asenta", "d_tipo_asenta", "D_mnpio", "d_estado", "d_ciudad"]


def _limpio(texto):
    return " ".join((texto or "").split())


def leer(ruta):
    """Filas (cp, colonia, tipo, municipio, estado, ciudad) sin repetir, del TSV incluido o del TXT de SEPOMEX."""
    ruta = Path(ruta)
    if ruta.suffix == ".gz":
        with gzip.open(ruta, "rt", encoding="utf-8") as f:
            lineas = f.read().splitlines()
        separador, columnas = "\t", CAMPOS
    else:
        crudo = ruta.read_bytes()
        try:
            texto = crudo.decode("utf-8")
        except UnicodeDecodeError:
            texto = crudo.decode("latin-1")  # el TXT de Correos de México viene en ISO-8859-1
        lineas = texto.splitlines()
        separador, columnas = "|", SEPOMEX
    inicio = next((i for i, ln in enumerate(lineas) if ln.split(separador)[0].strip() == columnas[0]), None)
    if inicio is None:
        raise CommandError(f"No encontré el encabezado ({columnas[0]}) en {ruta}.")
    encabezado = [c.strip() for c in lineas[inicio].split(separador)]
    try:
        idx = [encabezado.index(c) for c in columnas]
    except ValueError as e:
        raise CommandError(f"A {ruta} le falta una columna: {e}") from e
    filas = set()
    for ln in lineas[inicio + 1:]:
        partes = ln.split(separador)
        if len(partes) < len(encabezado):
            continue
        fila = tuple(_limpio(partes[i]) for i in idx)
        if len(fila[0]) == 5 and fila[0].isdigit() and fila[1] and fila[3] and fila[4]:
            filas.add(fila)
    return sorted(filas)


class Command(BaseCommand):
    help = "Carga el catálogo de códigos postales de Correos de México (colonia, municipio y estado por CP)."

    def add_arguments(self, parser):
        parser.add_argument("--archivo", default=str(INCLUIDO),
                            help="TSV.gz incluido (por defecto) o el CPdescarga.txt oficial de Correos de México")
        parser.add_argument("--forzar", action="store_true", help="Recargar aunque ya esté completo")

    def handle(self, *args, archivo, forzar, **opciones):
        filas = leer(archivo)
        if not filas:
            raise CommandError(f"{archivo} no trae códigos postales.")
        if not forzar and CodigoPostal.objects.count() == len(filas):
            self.stdout.write(f"Códigos postales al día ({len(filas):,} colonias).")
            return
        with transaction.atomic():
            CodigoPostal.objects.all().delete()
            CodigoPostal.objects.bulk_create((CodigoPostal(**dict(zip(CAMPOS, f))) for f in filas), batch_size=2000)
        n_cp = len({f[0] for f in filas})
        self.stdout.write(self.style.SUCCESS(f"Cargados {n_cp:,} códigos postales con {len(filas):,} colonias."))
