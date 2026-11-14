from decimal import Decimal

from django import template

register = template.Library()


@register.filter
def pesos(valor):
    """1234.5 → $1,234.50 ; 908 → $908"""
    if valor in (None, ""):
        return ""
    v = Decimal(valor)
    return f"${v:,.0f}" if v == v.to_integral_value() else f"${v:,.2f}"


@register.filter
def cant(valor):
    """Decimal('4.000') → 4 ; Decimal('3.600') → 3.6"""
    if valor in (None, ""):
        return ""
    v = Decimal(valor)
    return f"{int(v):,}" if v == v.to_integral_value() else f"{v.normalize():,f}"


@register.filter
def por(a, b):
    return Decimal(a or 0) * Decimal(b or 0)


@register.filter
def pasos(definicion, actual):
    """'marca:Marca,anio:Año' + 'anio' → [('hecho', 'Marca'), ('actual', 'Año')] para la guía de pasos."""
    res, visto = [], False
    for parte in definicion.split(","):
        clave, nombre = parte.split(":")
        if clave == actual:
            res.append(("actual", nombre))
            visto = True
        else:
            res.append(("" if visto else "hecho", nombre))
    return res
