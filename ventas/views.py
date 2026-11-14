from decimal import Decimal, InvalidOperation
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django.db.models import Count
from django.http import JsonResponse
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from compras.aplicaciones import buscar, clave_modelo, leer_consulta, selector

from . import mostrador as m
from .models import Cliente, FormaPago, Turno, Venta
from .servicios import abrir_turno, cerrar_turno, esperado_en_caja
from .templatetags.erp import cant, pesos


def _volver(request, defecto="mostrador"):
    destino = request.POST.get("next") or ""
    if url_has_allowed_host_and_scheme(destino, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
        return redirect(destino)
    return redirect(defecto)


CAMPO = {FormaPago.EFECTIVO: "efectivo", FormaPago.TARJETA: "tarjeta", FormaPago.TRANSFERENCIA: "transferencia"}


def _turno(user):
    return Turno.objects.filter(usuario=user, cerrado__isnull=True).first()


def _url(**params):
    from django.urls import reverse

    params = {k: v for k, v in params.items() if v not in (None, "")}
    return reverse("mostrador") + ("?" + urlencode(params) if params else "")


def _decimal(txt, defecto=None):
    try:
        return Decimal(str(txt).replace(",", "").replace("$", "").strip())
    except (InvalidOperation, ValueError):
        return defecto


def _vehiculo_de_busqueda(texto):
    """'tsuru 2003' → parámetros de las listas con el vehículo más común que coincide."""
    palabras, anio, litros = leer_consulta(texto)
    if not anio:
        return None, "Escribe también el año, por ejemplo «tsuru 2003»."
    mejor = (buscar(texto).values("armadora", "modelo_base").annotate(n=Count("partida", distinct=True))
             .order_by("-n").first())
    if not mejor:
        return None, f"No encontré «{texto}» en las listas. Prueba con las listas de marca, año y modelo."
    return {"armadora": mejor["armadora"], "anio": anio, "modelo": mejor["modelo_base"], "litros": litros}, None


def _elegido(g):
    """Lee marca › año › modelo › motor de la URL y suelta lo que ya no cuadra con lo anterior
    (por ejemplo, si cambiaron la marca y el año elegido no existe para la nueva)."""
    armadora = g.get("armadora", "")
    anio = int(g["anio"]) if g.get("anio", "").isdigit() else None
    modelo, litros = g.get("modelo", ""), g.get("litros", "")
    listas = selector(armadora, anio, modelo)
    if armadora not in {v for v, _ in listas["marcas"]}:
        armadora, anio, modelo, litros = "", None, "", ""
    if anio not in listas["anios"]:
        anio, modelo, litros = None, "", ""
    nombres = {clave_modelo(v): v for v, _ in listas["modelos"]}
    if modelo:
        modelo = nombres.get(clave_modelo(modelo), "")
        if not modelo:
            litros = ""
    sel = {"armadora": armadora, "anio": anio, "modelo": modelo, "litros": litros}
    if (armadora, anio, modelo) != (g.get("armadora", ""), int(g["anio"]) if g.get("anio", "").isdigit() else None,
                                    g.get("modelo", "")):
        listas = selector(armadora, anio, modelo)
    motores = [v for v, _ in listas["motores"]]
    if modelo and litros != "todos" and litros not in motores:
        # un solo motor (o ninguno escrito en la lista): no hay nada que preguntar
        sel["litros"] = motores[0] if len(motores) == 1 else ("todos" if not motores else "")
    return sel, listas


def _recordar(request, sel):
    """Últimos vehículos atendidos, para volver a ellos con un toque."""
    recientes = [r for r in request.session.get("recientes", []) if r != sel]
    request.session["recientes"] = [sel] + recientes[:5]


@login_required
def mostrador(request):
    g = request.GET
    if g.get("q"):
        params, error = _vehiculo_de_busqueda(g["q"])
        if error:
            messages.warning(request, error)
            return redirect("mostrador")
        return redirect(_url(**params))

    ticket = m.Ticket(request.session)
    sel, listas = _elegido(g)
    ctx = {"ticket": ticket, "turno": _turno(request.user), "sel": sel, "listas": listas,
           "clientes": Cliente.objects.filter(activo=True), "cliente": ticket.cliente,
           "marcas_comunes": listas["marcas"][:8],
           "marcas": sorted(listas["marcas"], key=lambda x: x[0].lower())}
    if sel["modelo"] and sel["litros"]:
        veh = m.Vehiculo(sel["anio"], sel["armadora"], sel["modelo"], "" if sel["litros"] == "todos" else sel["litros"])
        ctx.update(_ficha(request, ticket, veh, sel["litros"]))
        _recordar(request, sel)
    else:
        ctx["paso"] = ("motor" if sel["modelo"] else "modelo" if sel["anio"] else "anio" if sel["armadora"]
                       else "marca")
    ctx["recientes"] = [dict(r, url=_url(**r)) for r in request.session.get("recientes", []) if r != sel][:5]
    ctx["lineas"] = lineas = ticket.lineas(ctx.get("veh"))
    vender = [ln for ln in lineas if not ln.sobre_pedido]
    ctx["total"] = total = sum((ln.importe for ln in vender), Decimal(0))
    ctx["iva"] = total - total / m.IVA
    ctx["piezas"] = sum((ln.cantidad for ln in vender), Decimal(0))
    ctx["pedido_total"] = sum((ln.importe for ln in lineas if ln.sobre_pedido), Decimal(0))
    ctx["pedido_n"] = sum(1 for ln in lineas if ln.sobre_pedido)
    ctx["pagos"] = [fp for fp in FormaPago if fp != FormaPago.CREDITO or ticket.cliente.dias_credito > 0]
    ctx["aqui"] = _sin(request.get_full_path(), "codigo", "cantidad", "precio")
    if g.get("codigo"):
        texto, cuantas = m.leer_captura(g["codigo"], g.get("cantidad"))
        ctx["por_codigo"] = {"texto": texto, "cantidad": cuantas, "precio": g.get("precio", ""),
                             "resultados": m.buscar_codigo(texto, ticket.cliente.lista_precio)[0]}
    ctx["enfocar_codigo"] = request.session.pop("enfocar_codigo", False) or bool(g.get("codigo"))
    folio = request.session.pop("ultima_venta", None)
    ctx["ultima_venta"] = Venta.objects.filter(folio=folio).first() if folio else None
    return render(request, "ventas/mostrador.html", ctx)


def _sin(url, *params):
    """La misma dirección sin esos parámetros (para no repetir una búsqueda al volver)."""
    partes = urlsplit(url)
    q = [(k, v) for k, v in parse_qsl(partes.query, keep_blank_values=True) if k not in params]
    return urlunsplit(partes._replace(query=urlencode(q)))


def _con(url, **params):
    partes = urlsplit(url)
    q = [(k, v) for k, v in parse_qsl(partes.query, keep_blank_values=True) if k not in params]
    return urlunsplit(partes._replace(query=urlencode(q + list(params.items()))))


@login_required
def vehiculo_listas(request):
    """Las listas siguientes al cambiar una, para llenarlas sin recargar la página."""
    sel, listas = _elegido(request.GET)
    return JsonResponse({"sel": sel, "anios": listas["anios"],
                         "modelos": [v for v, _ in listas["modelos"]],
                         "motores": [v for v, _ in listas["motores"]]})


def _ficha(request, ticket, veh, litros_param, pos_clave=None):
    """Hoja de afinación: todas las piezas (renglones) por los tres niveles (columnas), más el total
    de la afinación completa en cada nivel, que es lo primero que pregunta el cliente."""
    elegidas = ticket.por_posicion()
    lista = ticket.cliente.lista_precio
    base = dict(anio=veh.anio, armadora=veh.armadora, modelo=veh.modelo, litros=litros_param)
    filas, totales = [], {k: {"clave": k, "nombre": n, "total": Decimal(0), "piezas": 0, "pedido": 0} for k, n in m.NIVELES}
    for p in m.POSICIONES:
        tarjetas, otras = m.opciones_de(veh, p, lista)
        en_ticket = elegidas.get(p.clave, {}).get("clave")
        celdas = []
        for k, nombre in m.NIVELES:
            op = tarjetas[k]
            celdas.append({"nivel": k, "nombre": nombre, "op": op, "en_ticket": bool(op and op.clave == en_ticket)})
            if op and op.precio is not None:
                totales[k]["total"] += op.precio * p.cantidad
                totales[k]["piezas"] += 1
                totales[k]["pedido"] += op.sobre_pedido
        otras_en_ticket = next((o for o in otras if o.clave == en_ticket), None)
        filas.append({"p": p, "celdas": celdas, "otras": otras[:8], "mas": max(len(otras) - 8, 0),
                      "hay": any(c["op"] for c in celdas) or bool(otras),
                      "elegida": bool(en_ticket), "otra_elegida": otras_en_ticket})
    return {"paso": "ficha", "veh": veh, "filas": filas, "totales": list(totales.values()), "base": base,
            "elegidas": len(elegidas), "sobreprecio": int(m.sobreprecio() * 100)}


def _precio_tecleado(request, obj, texto):
    """Precio con IVA por unidad tecleado en mostrador; vacío es el de la lista (None). Abajo del costo
    solo lo puede dejar un administrador, y el mensaje no dice el costo."""
    if not str(texto or "").strip():
        return None
    precio = _decimal(texto)
    if precio is None or precio <= 0:
        raise ValidationError("Escribe un precio válido, por ejemplo 125.50.")
    precio = precio.quantize(Decimal("0.01"))
    if m.abajo_del_costo(obj, precio):
        if not request.user.is_staff:
            raise ValidationError(f"{pesos(precio)} queda abajo del costo. Solo un administrador puede dejarlo a ese precio.")
        messages.warning(request, f"Ojo: {pesos(precio)} queda abajo del costo.")
    return precio


@login_required
@require_POST
def agregar(request):
    ticket = m.Ticket(request.session)
    try:
        clave = request.POST["clave"]
        precio = _precio_tecleado(request, m.resolver(clave), request.POST.get("precio"))
        ticket.agregar(clave, request.POST.get("pos"), cantidad=_decimal(request.POST.get("cantidad")),
                       nivel=request.POST.get("nivel", ""), precio=precio)
    except (KeyError, ObjectDoesNotExist):
        messages.error(request, "Esa opción ya no existe; vuelve a elegir.")
        return _volver(request)
    except ValidationError as e:
        messages.error(request, " ".join(e.messages))
        return _volver(request)
    if not request.POST.get("pos"):
        request.session["enfocar_codigo"] = True  # listo para el siguiente código
    return _volver(request)


@login_required
@require_POST
def agregar_codigo(request):
    """Código tecleado o escaneado. Si es de una sola pieza entra directo al ticket; si no, se muestran
    las coincidencias para elegir."""
    texto, cantidad = m.leer_captura(request.POST.get("codigo", ""), request.POST.get("cantidad"))
    ticket = m.Ticket(request.session)
    lista = ticket.cliente.lista_precio
    request.session["enfocar_codigo"] = True
    encontrados = None
    if request.POST.get("clave"):  # eligió una de las sugerencias: ya se sabe cuál es
        try:
            encontrados, directo = [m.encontrado(m.resolver(request.POST["clave"]), lista)], True
        except (ValidationError, ObjectDoesNotExist):
            encontrados = None
    if encontrados is None:
        if not texto:
            return _volver(request)
        encontrados, directo = m.buscar_codigo(texto, lista)
    precio_txt = request.POST.get("precio", "").strip()
    if directo:
        op = encontrados[0].opcion
        try:
            precio = _precio_tecleado(request, m.resolver(op.clave), precio_txt)
        except ValidationError as e:
            messages.error(request, " ".join(e.messages))
            return _volver(request)
        ticket.agregar(op.clave, None, cantidad=cantidad, precio=precio)
        messages.success(request, f"{op.codigo} {op.marca} × {cant(cantidad)} al ticket"
                         + (f" a {pesos(precio)} c/u" if precio is not None and precio != op.precio else "") + "."
                         + (" Va sobre pedido." if op.existencia is None or op.existencia < cantidad else ""))
        return _volver(request)
    if not encontrados:
        messages.warning(request, f"No encontré «{texto}». Revisa el código o busca por nombre.")
        return _volver(request)
    destino = request.POST.get("next") or ""
    if not url_has_allowed_host_and_scheme(destino, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
        destino = reverse("mostrador")
    extra = {"precio": precio_txt} if precio_txt else {}
    return redirect(_con(destino, codigo=texto, cantidad=f"{cantidad.normalize():f}", **extra))


@login_required
def sugerir(request):
    """Sugerencias para el campo de código mientras se teclea."""
    res = m.sugerir(request.GET.get("q", ""), m.Ticket(request.session).cliente.lista_precio)
    return JsonResponse({"q": request.GET.get("q", ""), "sugerencias": [{
        "clave": e.opcion.clave, "codigo": e.opcion.codigo, "marca": e.opcion.marca,
        "nombre": e.nombre[:70], "equivalente": e.por_equivalencia,
        "hay": not e.opcion.sobre_pedido, "existencia": cant(e.opcion.existencia) if e.opcion.existencia else "",
        "precio": cant(e.opcion.precio).replace(",", "") if e.opcion.precio is not None else "",
        "precio_txt": pesos(e.opcion.precio) or "sin precio",
    } for e in res]})


@login_required
@require_POST
def agregar_afinacion(request):
    """Lleva al ticket la afinación completa de un nivel: la opción de ese nivel en cada pieza que la tenga."""
    g = request.POST
    if not g.get("anio", "").isdigit() or g.get("nivel") not in dict(m.NIVELES):
        return _volver(request)
    litros = g.get("litros", "")
    veh = m.Vehiculo(int(g["anio"]), g.get("armadora", ""), g.get("modelo", ""), "" if litros == "todos" else litros)
    ticket = m.Ticket(request.session)
    n = 0
    for p in m.POSICIONES:
        op = m.opciones_de(veh, p, ticket.cliente.lista_precio)[0][g["nivel"]]
        if op and op.precio is not None:
            ticket.agregar(op.clave, p.clave, nivel=g["nivel"])
            n += 1
    if not n:
        messages.warning(request, "No hay piezas de ese nivel para este vehículo.")
    return _volver(request)


@login_required
@require_POST
def quitar(request, i):
    m.Ticket(request.session).quitar(i)
    return _volver(request)


@login_required
@require_POST
def cantidad(request, i):
    c = _decimal(request.POST.get("cantidad"))
    if c is not None:
        m.Ticket(request.session).cambiar_cantidad(i, c)
    return _volver(request)


@login_required
@require_POST
def precio(request, i):
    """Precio con IVA de un renglón del ticket; vacío lo regresa al de la lista."""
    ticket = m.Ticket(request.session)
    clave = ticket.clave_de(i)
    if clave:
        try:
            ticket.cambiar_precio(i, _precio_tecleado(request, m.resolver(clave), request.POST.get("precio")))
        except ValidationError as e:
            messages.error(request, " ".join(e.messages))
        except ObjectDoesNotExist:
            pass
    return _volver(request)


@login_required
@require_POST
def cliente(request):
    try:
        m.Ticket(request.session).elegir_cliente(int(request.POST["cliente"]))
    except (KeyError, ValueError, Cliente.DoesNotExist):
        messages.error(request, "Cliente inválido.")
    return _volver(request)


@login_required
@require_POST
def cobrar(request):
    turno = _turno(request.user)
    if not turno:
        messages.error(request, "Abre la caja antes de cobrar.")
        return redirect("caja")
    ticket = m.Ticket(request.session)
    veh = None
    if request.POST.get("anio", "").isdigit():
        litros = request.POST.get("litros", "")
        veh = m.Vehiculo(int(request.POST["anio"]), request.POST.get("armadora", ""), request.POST.get("modelo", ""),
                         "" if litros == "todos" else litros)
    try:
        venta, ordenes = m.cobrar(ticket, turno, request.POST.get("forma_pago", FormaPago.EFECTIVO), request.user, veh)
    except ValidationError as e:
        messages.error(request, " ".join(e.messages))
        return _volver(request)
    partes = []
    if venta:
        request.session["ultima_venta"] = venta.folio
        partes.append(f"Venta {venta.folio} por ${venta.total:,.2f} con {venta.get_forma_pago_display().lower()}.")
    for oc in ordenes:
        partes.append(f"Quedó pedido a {oc.proveedor} en {oc.folio} (borrador, compra de emergencia).")
    messages.success(request, " ".join(partes))
    return redirect("mostrador")  # siguiente cliente


@login_required
def caja(request):
    turno = _turno(request.user)
    if request.method == "POST":
        try:
            if request.POST.get("accion") == "abrir":
                abrir_turno(request.user, _decimal(request.POST.get("fondo"), Decimal(0)))
                messages.success(request, "Caja abierta.")
                return redirect("mostrador")
            if request.POST.get("accion") == "cerrar" and turno:
                contado = [_decimal(request.POST.get(k), Decimal(0)) for k in CAMPO.values()]
                turno, esperado = cerrar_turno(turno, *contado)
                request.session["corte"] = turno.pk
                return redirect("caja")
        except ValidationError as e:
            messages.error(request, " ".join(e.messages))
        return redirect("caja")
    ctx = {"turno": turno}
    if turno:
        esp = esperado_en_caja(turno)
        ctx["esperado"] = [(FormaPago(k).label, CAMPO[k], v) for k, v in esp.items()]
        ctx["ventas"] = turno.ventas.order_by("-pk")[:20]
    corte = request.session.pop("corte", None)
    if corte:
        t = Turno.objects.filter(pk=corte, usuario=request.user).first()
        if t:
            esp = esperado_en_caja(t)
            ctx["corte"] = t
            ctx["corte_filas"] = [(FormaPago(k).label, v, getattr(t, f"{CAMPO[k]}_contado"),
                                   getattr(t, f"{CAMPO[k]}_contado") - v) for k, v in esp.items()]
    return render(request, "ventas/caja.html", ctx)


@login_required
def remision(request, folio):
    """Remisión de una venta para imprimir, en hoja carta o en ticket de 80 mm. No es comprobante fiscal."""
    from django.conf import settings

    venta = get_object_or_404(Venta.objects.select_related("cliente", "usuario", "lista"), folio=folio)
    formato = "ticket" if request.GET.get("formato") == "ticket" else "carta"
    pedido = venta.pedido or []
    ctx = {"venta": venta, "formato": formato, "imprimir": request.GET.get("imprimir") == "1",
           "lineas": venta.lineas.select_related("producto", "producto__marca", "unidad"),
           "subtotal": venta.total - venta.iva, "pedido": pedido,
           "pedido_total": sum((Decimal(p["importe"]) for p in pedido), Decimal(0)),
           "abonado": venta.total - venta.saldo if venta.estado == Venta.Estado.CREDITO else None,
           "rfc": settings.NEGOCIO_RFC, "direccion": settings.NEGOCIO_DIRECCION, "telefono": settings.NEGOCIO_TELEFONO}
    return render(request, "ventas/remision.html", ctx)


@login_required
def remisiones(request):
    """Ventas para buscar y reimprimir su remisión: por folio o por cliente."""
    q = (request.GET.get("q") or "").strip()
    ventas = Venta.objects.select_related("cliente").order_by("-fecha")
    if q:
        ventas = ventas.filter(Q(folio__icontains=q) | Q(cliente__nombre__icontains=q))
    return render(request, "ventas/remisiones.html", {"ventas": ventas[:60], "q": q, "turno": _turno(request.user)})
