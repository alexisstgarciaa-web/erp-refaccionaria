"""Trae a la base actual todo lo que había en la base anterior (el cambio de la base de Render a la de Neon).

Se usa una sola vez: en el servidor se pone DATABASE_URL_ANTERIOR con la cadena de conexión de la base vieja y
build.sh corre este comando en la siguiente publicación. Copia tal cual todas las tablas (productos, listas de
proveedor, inventario, clientes, ventas, usuarios, NIPs…) y deja anotado que ya se hizo, para no repetirlo.

Para no encimar nada, solo trabaja si la base actual sigue como recién creada (sin productos, listas, ventas ni
NIPs) y la anterior sí tiene datos. Todo va en una sola transacción: si algo falla, la base actual queda como
estaba. Nunca detiene la publicación: si no puede, lo dice y sigue."""

import os
import re
from urllib.parse import urlsplit

from django.apps import apps
from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.core.management.color import no_style
from django.db import connection, transaction

VARIABLE = "DATABASE_URL_ANTERIOR"
ANOTACION = "erp_traslado"  # tabla donde queda anotado que ya se trajo
NEGOCIO = ("catalogo", "inventario", "compras", "ventas")
# Lo que una base recién creada ya trae o se crea solo al primer uso: no cuenta como «ya tiene datos»
DE_FABRICA = {"ventas_codigopostal", "ventas_cliente", "catalogo_listaprecio"}


class NoSePuede(Exception):
    """No se trae nada, con el motivo en palabras."""


def _sin_secretos(texto, url):
    """El mensaje de un error, sin la cadena de conexión ni su contraseña."""
    texto = str(texto).replace(url, "«la cadena»")
    try:
        clave = urlsplit(url).password
    except ValueError:
        clave = None
    if clave:
        texto = texto.replace(clave, "***")
    return re.sub(r"://[^@\s/]+@", "://***@", texto)


def _id(nombre):
    return '"' + nombre.replace('"', '""') + '"'


def _migraciones(cur):
    cur.execute("SELECT app, name FROM django_migrations")
    return set(cur.fetchall())


def _columnas(cur, tabla):
    cur.execute("SELECT column_name FROM information_schema.columns WHERE table_schema = current_schema() "
                "AND table_name = %s AND is_generated = 'NEVER' ORDER BY ordinal_position", [tabla])
    return [c for (c,) in cur.fetchall()]


def _filas(cur, tabla):
    cur.execute(f"SELECT count(*) FROM {_id(tabla)}")
    return cur.fetchone()[0]


def _con_datos(cur, tablas):
    """Tablas del negocio con filas, sin contar lo que una base nueva ya trae."""
    return {t: n for t in tablas if t.split("_")[0] in NEGOCIO and t not in DE_FABRICA and (n := _filas(cur, t))}


class Command(BaseCommand):
    help = "Trae todo de la base anterior (DATABASE_URL_ANTERIOR) a la actual, una sola vez y solo si la actual está vacía."

    def handle(self, *args, **opciones):
        url = (os.environ.get(VARIABLE) or "").strip()
        if not url:
            self.stdout.write("Traslado de base: no hay base anterior configurada, no se trae nada.")
            return
        try:
            total = self._traer(url)
        except NoSePuede as e:
            self.stdout.write(f"Traslado de base: no se trae nada. {e}")
        except Exception as e:  # nunca detiene la publicación
            self.stdout.write(self.style.ERROR(
                f"Traslado de base: NO se pudo y la base actual quedó como estaba. {type(e).__name__}: {_sin_secretos(e, url)}"))
        else:
            self.stdout.write(self.style.SUCCESS(f"Traslado de base: listo, {total:,} filas traídas de la base anterior."))

    def _traer(self, url):
        if connection.vendor != "postgresql":
            raise NoSePuede("La base actual no es PostgreSQL.")
        import psycopg

        with connection.cursor() as cur:
            cur.execute("SELECT to_regclass(%s)", [ANOTACION])
            if cur.fetchone()[0]:
                cur.execute(f"SELECT to_char(min(hecho), 'YYYY-MM-DD') FROM {ANOTACION}")
                hecho = cur.fetchone()[0]
                if hecho:
                    raise NoSePuede(f"Ya se trajo el {hecho}; no se repite.")
            tablas = sorted(connection.introspection.django_table_names(only_existing=True))
            ocupadas = _con_datos(cur, tablas)
            if ocupadas:
                raise NoSePuede("La base actual ya tiene datos (" + ", ".join(f"{t}: {n:,}" for t, n in ocupadas.items())
                                + ") y no se enciman.")
            aqui = _migraciones(cur)
            cur.execute("SELECT current_database(), inet_server_addr()::text, inet_server_port()")
            esta = cur.fetchone()

        with psycopg.connect(url, connect_timeout=30, client_encoding="utf8", application_name="erp-traslado") as origen:
            origen.read_only = True
            origen.isolation_level = psycopg.IsolationLevel.REPEATABLE_READ
            with origen.cursor() as o:
                # igual que pg_dump, para que fechas y números se lean igual del otro lado
                o.execute("SET datestyle TO ISO; SET intervalstyle TO postgres; SET extra_float_digits TO 3; "
                          "SET statement_timeout TO 0; SET lock_timeout TO '60s'")
                origen.commit()
                o.execute("SELECT current_database(), inet_server_addr()::text, inet_server_port()")
                if o.fetchone() == esta:
                    raise NoSePuede(f"{VARIABLE} apunta a la misma base que DATABASE_URL: ahí va la cadena de la base vieja.")
                o.execute("SELECT to_regclass('django_migrations')")
                if not o.fetchone()[0]:
                    raise NoSePuede("La base anterior no es de este sistema (no tiene sus tablas).")
                alla = _migraciones(o)
                if alla - aqui:
                    raise NoSePuede("La base anterior es de una versión más nueva del sistema que la publicada: "
                                    + ", ".join(sorted(f"{a}.{n}" for a, n in alla - aqui)[:6]))
                en_origen = sorted(t for t in tablas if self._existe(o, t))
                if not _con_datos(o, en_origen):
                    raise NoSePuede("La base anterior no tiene productos, listas ni ventas.")
                origen.commit()

                # Si lo publicado ya tiene cambios de tablas que la base vieja no conoció, la actual (vacía) se regresa
                # a como está la vieja; al final se vuelven a aplicar, ya con los datos adentro.
                adelantadas = sorted({a for a, _ in aqui - alla})
                try:
                    for app in adelantadas:
                        hasta = max((n for a, n in alla if a == app), default="zero")
                        self.stdout.write(f"Traslado de base: {app} regresa a {hasta} para recibir los datos.")
                        call_command("migrate", app, hasta, interactive=False, verbosity=0)
                    return self._copiar(origen, o)
                finally:
                    if adelantadas:
                        call_command("migrate", interactive=False, verbosity=0)

    @staticmethod
    def _existe(cur, tabla):
        cur.execute("SELECT to_regclass(%s)", [_id(tabla)])
        return bool(cur.fetchone()[0])

    def _copiar(self, origen, o):
        with connection.cursor() as cur:
            aqui = _migraciones(cur)
        if _migraciones(o) != aqui:
            raise NoSePuede("No se pudieron emparejar las tablas de las dos bases.")
        origen.commit()  # la foto de la base vieja empieza aquí
        tablas = sorted(connection.introspection.django_table_names(only_existing=True))
        faltan = [t for t in tablas if not self._existe(o, t)]
        if faltan:
            raise NoSePuede("A la base anterior le faltan tablas: " + ", ".join(faltan))
        total = 0
        with transaction.atomic(), connection.cursor() as cur:
            cur.execute("SET LOCAL statement_timeout TO 0")
            antes = {t: n for t in tablas if (n := _filas(cur, t))}
            cur.execute("TRUNCATE " + ", ".join(_id(t) for t in tablas) + " RESTART IDENTITY CASCADE")
            for tabla in tablas:
                columnas = _columnas(o, tabla)
                if set(columnas) != set(_columnas(cur, tabla)):
                    raise NoSePuede(f"La tabla {tabla} no tiene las mismas columnas en las dos bases.")
                lista = ", ".join(_id(c) for c in columnas)
                with o.copy(f"COPY (SELECT {lista} FROM {_id(tabla)}) TO STDOUT") as sale, \
                        cur.copy(f"COPY {_id(tabla)} ({lista}) FROM STDIN") as entra:
                    for pedazo in sale:
                        entra.write(pedazo)
                esperadas, llegaron = _filas(o, tabla), _filas(cur, tabla)
                if esperadas != llegaron:
                    raise NoSePuede(f"La tabla {tabla} llegó incompleta ({llegaron:,} de {esperadas:,}).")
                if llegaron or antes.get(tabla):
                    self.stdout.write(f"  {tabla}: {llegaron:,}" + (f" (había {antes[tabla]:,})" if antes.get(tabla) else ""))
                total += llegaron
            # los consecutivos (id) siguen después del último que vino
            modelos = [m for m in apps.get_models(include_auto_created=True) if m._meta.db_table in tablas]
            for sql in connection.ops.sequence_reset_sql(no_style(), modelos):
                cur.execute(sql)
            cur.execute(f"CREATE TABLE IF NOT EXISTS {ANOTACION} (hecho timestamptz NOT NULL DEFAULT now(), filas bigint NOT NULL)")
            cur.execute(f"INSERT INTO {ANOTACION} (filas) VALUES (%s)", [total])
        return total
