"""PDF de la cotización y de la remisión (el ticket de la venta), para mandarlos al cliente por WhatsApp o correo.

Lleva lo mismo que la hoja impresa (ventas/cotizacion.html y ventas/remision.html) con los colores del sistema:
carbón, blanco y rojo. Se arma con ReportLab, que es puro Python: no pide programas aparte en el servidor.
Las letras son las Helvetica que trae todo lector de PDF, así el archivo pesa poco y se ve igual en cualquier lado.
"""
from io import BytesIO
from xml.sax.saxutils import escape

from django.utils import timezone
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_RIGHT
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from .compartir import MESES
from .templatetags.erp import cant, pesos

CARBON = colors.HexColor("#111214")
ROJO = colors.HexColor("#E10600")
TEXTO = colors.HexColor("#0C0C0D")
TEXTO_2 = colors.HexColor("#43433E")
TEXTO_3 = colors.HexColor("#7A7A72")
RAYA = colors.HexColor("#E3E3DD")
NOTA = colors.HexColor("#F3F3EF")
GIRO = "Afinación · lubricantes · químicos"
MARGEN = 12 * mm
ANCHO = letter[0] - 2 * MARGEN
SIN_ORILLA = [("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0),
              ("TOPPADDING", (0, 0), (-1, -1), 0), ("BOTTOMPADDING", (0, 0), (-1, -1), 0), ("VALIGN", (0, 0), (-1, -1), "TOP")]


def _estilo(nombre, **cambios):
    return ParagraphStyle(nombre, **{"fontName": "Helvetica", "fontSize": 9, "leading": 11.5, "textColor": TEXTO, **cambios})


E = {
    "texto": _estilo("texto"),
    "chico": _estilo("chico", fontSize=8, leading=10, textColor=TEXTO_2),
    "negocio": _estilo("negocio", fontName="Helvetica-BoldOblique", fontSize=17, leading=20),
    "sello": _estilo("sello", fontName="Helvetica-BoldOblique", fontSize=17, leading=17, textColor=colors.white, alignment=TA_CENTER),
    "titulo": _estilo("titulo", fontName="Helvetica-BoldOblique", fontSize=22, leading=25, alignment=TA_RIGHT),
    "folio": _estilo("folio", fontName="Courier-Bold", fontSize=13, leading=16, textColor=ROJO, alignment=TA_RIGHT),
    "fecha": _estilo("fecha", fontSize=8, leading=10, textColor=TEXTO_2, alignment=TA_RIGHT),
    "etiqueta": _estilo("etiqueta", fontName="Helvetica-Bold", fontSize=7, leading=9, textColor=TEXTO_3),
    "dato": _estilo("dato", fontName="Helvetica-Bold"),
    "th": _estilo("th", fontName="Helvetica-Bold", fontSize=7, leading=9, textColor=colors.white),
    "th_num": _estilo("th_num", fontName="Helvetica-Bold", fontSize=7, leading=9, textColor=colors.white, alignment=TA_RIGHT),
    "td": _estilo("td", fontSize=8.5, leading=10.5),
    "td_num": _estilo("td_num", fontSize=8.5, leading=10.5, alignment=TA_RIGHT),
    "codigo": _estilo("codigo", fontName="Courier-Bold", fontSize=8.5, leading=10.5),
    "suma": _estilo("suma", fontName="Helvetica-Bold", textColor=TEXTO_2),
    "suma_num": _estilo("suma_num", alignment=TA_RIGHT),
    "total": _estilo("total", fontName="Helvetica-BoldOblique", fontSize=15, leading=18),
    "total_num": _estilo("total_num", fontName="Helvetica-BoldOblique", fontSize=15, leading=18, alignment=TA_RIGHT),
    "aviso_rojo": _estilo("aviso_rojo", fontName="Helvetica-Bold", fontSize=7, leading=9, textColor=ROJO),
    "leyenda": _estilo("leyenda", fontSize=7.5, leading=9.5, textColor=TEXTO_3, alignment=TA_CENTER),
}


def _e(texto):
    return escape(str(texto if texto is not None else ""))


def _p(texto, estilo="texto"):
    return Paragraph(texto, E[estilo])


def _cuando(momento):
    """16 nov 2026, 19:50"""
    momento = timezone.localtime(momento)
    return f"{momento.day} {MESES[momento.month - 1][:3]} {momento.year}, {momento:%H:%M}"


def _dia(fecha, largo=True):
    """24 de noviembre de 2026 (o 24 nov 2026)"""
    mes = MESES[fecha.month - 1]
    return f"{fecha.day} de {mes} de {fecha.year}" if largo else f"{fecha.day} {mes[:3]} {fecha.year}"


def _chico(texto, color=TEXTO_3):
    return f' <font size="7.5" color="{color.hexval().replace("0x", "#")}">{_e(texto)}</font>'


def _quien(usuario):
    return (usuario.get_short_name() or usuario.get_username()) if usuario else "—"


def _cliente(cliente, nombre, folio=False):
    """Nombre, teléfono, RFC y domicilio del cliente, como en la hoja impresa."""
    partes = [_e(nombre)]
    if folio:
        partes.append(_e(cliente.folio))
    if cliente.telefono:
        partes.append("Tel. " + _e(cliente.telefono))
    if not cliente.rfc_generico:
        partes.append("RFC " + _e(cliente.rfc))
    texto = " · ".join(partes)
    for renglon in cliente.domicilio:
        texto += f'<br/><font name="Helvetica" color="#43433E">{_e(renglon)}</font>'
    return texto


def _cabeza(titulo, folio, fecha, negocio, rfc, direccion, telefono):
    sello = Table([[_p(_e((negocio or "R")[:1].upper()), "sello")]], colWidths=[11 * mm], rowHeights=[11 * mm],
                  style=[("BACKGROUND", (0, 0), (-1, -1), ROJO), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                         ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 1.5)])
    datos = [_p(_e(negocio), "negocio"), _p(GIRO, "chico")]
    for renglon in (f"RFC {rfc}" if rfc else "", direccion, f"Tel. {telefono}" if telefono else ""):
        if renglon:
            datos.append(_p(_e(renglon), "chico"))
    izquierda = Table([[sello, datos]], colWidths=[14 * mm, ANCHO * 0.6 - 14 * mm], style=SIN_ORILLA)
    derecha = [_p(_e(titulo.upper()), "titulo"), _p(_e(folio), "folio"), _p(_e(fecha), "fecha")]
    return Table([[izquierda, derecha]], colWidths=[ANCHO * 0.6, ANCHO * 0.4],
                 style=SIN_ORILLA + [("LINEBELOW", (0, 0), (-1, -1), 3.5, ROJO), ("BOTTOMPADDING", (0, 0), (-1, -1), 9)])


def _datos(pares):
    """Cuadro de dos columnas: «CLIENTE / Taller Ramírez», «VEHÍCULO / Tsuru 2003»…"""
    celdas = [[_p(_e(etiqueta.upper()), "etiqueta"), _p(valor, "dato")] for etiqueta, valor in pares]
    if len(celdas) % 2:
        celdas.append("")
    filas = [celdas[i:i + 2] for i in range(0, len(celdas), 2)]
    return Table(filas, colWidths=[ANCHO / 2] * 2,
                 style=SIN_ORILLA + [("RIGHTPADDING", (0, 0), (0, -1), 14), ("BOTTOMPADDING", (0, 0), (-1, -1), 7)])


def _partidas(renglones):
    """renglones: [(cantidad, código, descripción ya armada, precio ya armado, importe)]"""
    filas = [[_p("CANT.", "th_num"), _p("CÓDIGO", "th"), _p("DESCRIPCIÓN", "th"), _p("P. UNITARIO", "th_num"),
              _p("IMPORTE", "th_num")]]
    for cantidad, codigo, descripcion, precio, importe in renglones:
        filas.append([_p(cantidad, "td_num"), _p(_e(codigo), "codigo"), _p(descripcion, "td"), _p(precio, "td_num"),
                      _p(_e(pesos(importe)), "td_num")])
    anchos = [17 * mm, 36 * mm, 0, 27 * mm, 26 * mm]
    anchos[2] = ANCHO - sum(anchos)
    return Table(filas, colWidths=anchos, repeatRows=1, style=[
        ("BACKGROUND", (0, 0), (-1, 0), CARBON), ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LEFTPADDING", (0, 0), (-1, -1), 5), ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("LINEBELOW", (0, 1), (-1, -1), 0.6, RAYA)])


def _pie(notas, subtotal, iva, total):
    sumas = Table([[_p("Subtotal", "suma"), _p(_e(pesos(subtotal)), "suma_num")],
                   [_p("IVA 16%", "suma"), _p(_e(pesos(iva)), "suma_num")],
                   [_p("TOTAL", "total"), _p(_e(pesos(total)), "total_num")]],
                  colWidths=[24 * mm, 40 * mm],
                  style=SIN_ORILLA + [("BOTTOMPADDING", (0, 0), (-1, 1), 4), ("LINEABOVE", (0, 2), (-1, 2), 2.2, CARBON),
                                      ("TOPPADDING", (0, 2), (-1, 2), 6)])
    return Table([[notas or "", sumas]], colWidths=[ANCHO - 70 * mm, 70 * mm],
                 style=SIN_ORILLA + [("RIGHTPADDING", (0, 0), (0, 0), 18), ("ALIGN", (1, 0), (1, 0), "RIGHT")])


def _recuadro(contenido, estilo):
    return Table([[contenido]], colWidths=[ANCHO - 70 * mm - 18], style=[
        ("LEFTPADDING", (0, 0), (-1, -1), 7), ("RIGHTPADDING", (0, 0), (-1, -1), 7),
        ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 6), *estilo])


def _armar(contenido, titulo, folio, negocio, cancelada=False, comprimir=True):
    """El PDF: hoja carta con el pie «Cotización COT-000012 · Negocio · página 1» y, si se canceló, el sello."""
    salida = BytesIO()

    def en_cada_hoja(canvas, doc):
        canvas.saveState()
        canvas.setFont("Helvetica", 7)
        canvas.setFillColor(TEXTO_3)
        canvas.drawString(MARGEN, 8 * mm, f"{titulo} {folio} · {negocio}")
        canvas.drawRightString(letter[0] - MARGEN, 8 * mm, f"Página {doc.page}")
        if cancelada:
            canvas.setFillColor(ROJO, alpha=0.14)
            canvas.setFont("Helvetica-Bold", 92)
            canvas.translate(letter[0] / 2, letter[1] / 2)
            canvas.rotate(32)
            canvas.drawCentredString(0, -30, "CANCELADA")
        canvas.restoreState()

    doc = SimpleDocTemplate(salida, pagesize=letter, leftMargin=MARGEN, rightMargin=MARGEN, topMargin=MARGEN,
                            bottomMargin=MARGEN + 4 * mm, title=f"{titulo} {folio}", author=negocio,
                            subject=f"{titulo} {folio} de {negocio}", creator=negocio, pageCompression=1 if comprimir else 0)
    doc.build(contenido, onFirstPage=en_cada_hoja, onLaterPages=en_cada_hoja)
    return salida.getvalue()


def cotizacion(ctx, negocio, comprimir=True):
    """ctx: el mismo de la hoja impresa (views._cotizacion_ctx)."""
    g = ctx["g"]
    para = _e(g.para)
    if g.cliente_id:
        nombre = f"{g.para} · {g.cliente}" if g.nombre and g.cliente.nombre != "Público en general" else g.para
        para = _cliente(g.cliente, nombre)
    pares = [("Para", para)]
    if g.vehiculo:
        pares.append(("Vehículo", _e(g.vehiculo)))
    vigencia = "Precios válidos hasta el " + _e(_dia(g.vence))
    if not ctx["vigente"]:
        vigencia += ' · <font color="#E10600">vencida</font>'
    pares += [("Vigencia", vigencia), ("Atendió", _e(_quien(g.usuario)))]
    renglones = []
    for d in ctx["lineas"]:
        descripcion = _e(d["descripcion"]) + (_chico("SOBRE PEDIDO", ROJO) if d["pedido"] else "")
        renglones.append((_e(cant(d["cantidad"])), d["codigo"], descripcion, _e(pesos(d["precio"])), d["importe"]))
    notas = []
    if ctx["hay_pedido"]:
        notas.append(_p("Lo marcado <b>sobre pedido</b> no está en anaquel: se pide al proveedor al confirmar y se cobra "
                        "al entregar.", "chico"))
        notas.append(Spacer(0, 4))
    dias = ctx["dias"]
    notas.append(_p(f"Precios con IVA incluido, sujetos a existencia. Vigencia de {dias} día{'s' if dias != 1 else ''}.", "chico"))
    contenido = [
        _cabeza("Cotización", g.folio, _cuando(g.fecha), negocio, ctx["rfc"], ctx["direccion"], ctx["telefono"]),
        Spacer(0, 10), _datos(pares), Spacer(0, 4), _partidas(renglones), Spacer(0, 10),
        _pie(notas, ctx["subtotal"], ctx["iva"], g.total), Spacer(0, 16),
        _p("Cotización, no es comprobante de venta ni fiscal.", "leyenda"),
    ]
    return _armar(contenido, "Cotización", g.folio, negocio, comprimir=comprimir)


def remision(ctx, negocio, comprimir=True):
    """ctx: el mismo de la hoja impresa (views._remision_ctx)."""
    venta = ctx["venta"]
    pares = [("Cliente", _cliente(venta.cliente, str(venta.cliente), folio=venta.es_remision))]
    if venta.vehiculo:
        pares.append(("Vehículo", _e(venta.vehiculo)))
    if venta.orden_compra:
        pares.append(("Su orden de compra", f'<font name="Courier-Bold">{_e(venta.orden_compra)}</font>'))
    if venta.estado == "POR_COBRAR":
        pago = "Contado · pendiente de pago en caja"
    else:
        pago = venta.get_forma_pago_display() if venta.forma_pago else "Contado"
    if venta.estado == "CREDITO":
        pago += f" · {venta.cliente.dias_credito} días, vence {_dia(venta.vence, largo=False)}"
    pares += [("Pago", _e(pago)), ("Atendió", _e(_quien(venta.usuario)))]
    renglones = []
    for ln in ctx["lineas"]:
        precio = _e(pesos(ln.precio_unitario))
        if ln.descuento:
            precio += f"<br/>{_chico('desc. ' + cant(ln.descuento) + '%')}"
        renglones.append((_e(cant(ln.cantidad)) + _chico(ln.unidad.clave), ln.producto.numero_parte or ln.producto.sku,
                          _e(ln.producto.nombre) + _chico(ln.producto.marca), precio, ln.importe))
    notas = []
    if venta.observaciones:
        notas += [_p(f"<b>Observaciones.</b> {_e(venta.observaciones)}", "chico"), Spacer(0, 6)]
    nota_gris = [("BACKGROUND", (0, 0), (-1, -1), NOTA), ("LINEBEFORE", (0, 0), (0, -1), 3, CARBON)]
    if venta.estado == "POR_COBRAR":
        notas += [_recuadro(_p(f"<b>Pendiente de pago.</b> Se paga en caja: {_e(pesos(venta.total))}.", "chico"), nota_gris),
                  Spacer(0, 6)]
    if venta.estado == "CREDITO":
        abonado = f" (abonado {_e(pesos(ctx['abonado']))})" if ctx["abonado"] else ""
        texto = (f"<b>A crédito.</b> Saldo {_e(pesos(venta.saldo))}{abonado}. "
                 f"Vence el {_e(_dia(venta.vence))}.")
        notas += [_recuadro(_p(texto, "chico"), nota_gris), Spacer(0, 6)]
    if ctx["pedido"]:
        filas = [_p("QUEDÓ SOBRE PEDIDO · SE COBRA AL ENTREGAR", "aviso_rojo")]
        for p in ctx["pedido"]:
            filas.append(_p(f"{_e(cant(p['cantidad']))} × <font name=\"Courier-Bold\">{_e(p['codigo'])}</font> "
                            f"{_e(p['descripcion'])} · {_e(pesos(p['importe']))}", "chico"))
        filas.append(_p(f"Cotizado {_e(pesos(ctx['pedido_total']))}, IVA incluido.", "chico"))
        notas.append(_recuadro(filas, [("BOX", (0, 0), (-1, -1), 0.8, ROJO, None, (2, 2))]))
    leyenda = "Este documento no es un comprobante fiscal."
    if venta.estado == "CANCELADA":
        leyenda += " <b>Venta cancelada.</b>"
    contenido = [
        _cabeza("Remisión", venta.folio, _cuando(venta.fecha), negocio, ctx["rfc"], ctx["direccion"],
                ctx["telefono"]),
        Spacer(0, 10), _datos(pares), Spacer(0, 4), _partidas(renglones), Spacer(0, 10),
        _pie(notas, ctx["subtotal"], venta.iva, venta.total), Spacer(0, 16), _p(leyenda, "leyenda"),
    ]
    return _armar(contenido, "Remisión", venta.folio, negocio, cancelada=venta.estado == "CANCELADA", comprimir=comprimir)
