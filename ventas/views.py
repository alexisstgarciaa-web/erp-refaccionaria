from decimal import Decimal, InvalidOperation
from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django.db.models import Count
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from compras.aplicaciones import buscar, clave_modelo, leer_consulta, selector

from . import mostrador as m
from .models import Cliente, FormaPago, Turno
from .servicios import abrir_turno, cerrar_turno, esperado_en_caja


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
    ctx["aqui"] = request.get_full_path()
    return render(request, "ventas/mostrador.html", ctx)


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


def estilo(request, clave):
    """Cambia el estilo visual a prueba y regresa a la misma página."""
    from .contexto import ESTILOS

    if clave in ESTILOS:
        request.session["estilo"] = clave
    destino = request.GET.get("next") or ""
    if url_has_allowed_host_and_scheme(destino, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
        return redirect(destino)
    return redirect("mostrador")


@login_required
@require_POST
def agregar(request):
    ticket = m.Ticket(request.session)
    try:
        ticket.agregar(request.POST["clave"], request.POST.get("pos"), nivel=request.POST.get("nivel", ""))
    except (KeyError, ValidationError, ObjectDoesNotExist):
        messages.error(request, "Esa opción ya no existe; vuelve a elegir.")
        return _volver(request)
    return _volver(request)


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
