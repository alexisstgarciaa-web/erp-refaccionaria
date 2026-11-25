"""Comas de miles en las cifras de la Administración.

Django solo pone separador de miles si USE_THOUSAND_SEPARATOR está prendido, y eso también le
pondría comas a los años (2,015), a los folios numéricos y a los valores de las listas. Por eso
aquí se hace solo para los campos decimales (dinero, cantidades y costos):

- `instalar()` hace que las columnas y los campos de solo lectura muestren 1,234.50.
- `ConComas` (mezclado en cada ModelAdmin o Inline) cambia los campos decimales editables por un
  cuadro de texto que muestra las comas y las quita al guardar.
"""

from decimal import Decimal

from django import forms
from django.db import models


def cifra(valor, decimales=2):
    """Dinero (2 decimales) siempre con centavos: 1,234.50 ; cantidades y costos sin ceros de más: 1,234.5"""
    v = Decimal(valor)
    if decimales <= 2:
        return f"{v:,.{decimales}f}"
    v = v.quantize(Decimal(1).scaleb(-decimales)).normalize()
    return f"{v:,.0f}" if v == v.to_integral_value() else f"{v:,f}"


class CifraInput(forms.TextInput):
    """Cuadro de texto para un decimal: lo muestra con comas y las quita al leerlo."""

    def __init__(self, decimales=2, attrs=None):
        self.decimales = decimales
        base = {"inputmode": "decimal", "autocomplete": "off", "class": "vTextField cifra",
                "data-cifra": "pesos" if decimales == 2 else "exacta"}
        super().__init__({**base, **(attrs or {})})

    class Media:
        js = ["ventas/cifras.js"]

    def format_value(self, value):
        if isinstance(value, (Decimal, int, float)) and not isinstance(value, bool):
            return cifra(value, self.decimales)
        return super().format_value(value)

    def value_from_datadict(self, data, files, name):
        valor = super().value_from_datadict(data, files, name)
        return valor.replace(",", "").replace("$", "").strip() if isinstance(valor, str) else valor


class ConComas:
    """Para ModelAdmin e Inline: los decimales editables se capturan con comas."""

    def formfield_for_dbfield(self, db_field, request, **kwargs):
        if isinstance(db_field, models.DecimalField) and not db_field.choices:
            kwargs.setdefault("widget", CifraInput(db_field.decimal_places))
        return super().formfield_for_dbfield(db_field, request, **kwargs)


def instalar():
    """Columnas y campos de solo lectura de la Administración con comas en los decimales."""
    from django.contrib.admin import helpers, utils
    from django.contrib.admin.templatetags import admin_list

    original = utils.display_for_field
    if getattr(original, "con_comas", False):
        return

    def display_for_field(value, field, empty_value_display, *args, **kwargs):
        if (isinstance(field, models.DecimalField) and not getattr(field, "flatchoices", None)
                and value not in field.empty_values):
            return cifra(value, field.decimal_places)
        return original(value, field, empty_value_display, *args, **kwargs)

    display_for_field.con_comas = True
    for modulo in (utils, helpers, admin_list):
        modulo.display_for_field = display_for_field
