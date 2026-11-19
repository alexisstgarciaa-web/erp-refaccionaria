import re
import unicodedata

from django.db import migrations, models


def texto_busqueda(texto):
    t = unicodedata.normalize("NFKD", (texto or "").casefold())
    t = "".join(c for c in t if not unicodedata.combining(c))
    return " ".join(re.sub(r"[^0-9a-z&]+", " ", t).split())


def solo_digitos(texto):
    return re.sub(r"\D", "", texto or "")


def llenar(apps, schema_editor):
    Cliente = apps.get_model("ventas", "Cliente")
    for c in Cliente.objects.all().only("pk", "nombre", "rfc", "telefono"):
        c.busqueda = f"{texto_busqueda(f'{c.nombre} {c.rfc}')} {solo_digitos(c.telefono)}".strip()[:300]
        c.save(update_fields=["busqueda"])


class Migration(migrations.Migration):
    dependencies = [("ventas", "0009_remisiones")]

    operations = [
        migrations.AddField(
            model_name="cliente",
            name="busqueda",
            field=models.CharField(blank=True, editable=False, max_length=300,
                                   help_text="Nombre, RFC y teléfono sin acentos ni signos, para el buscador"),
        ),
        migrations.RunPython(llenar, migrations.RunPython.noop),
    ]
