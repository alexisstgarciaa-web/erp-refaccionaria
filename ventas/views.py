import unicodedata
from decimal import Decimal, InvalidOperation
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django.core.validators import validate_email
from django.db.models import Count, Sum
from django.http import JsonResponse
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from catalogo.models import ListaPrecio
from compras.aplicaciones import buscar, clave_modelo, leer_consulta, selector
from compras.models import PartidaListaProveedor

from . import clientes as cli
from . import compartir
from . import mostrador as m
from . import nip as nips
from . import remisiones as rem
from .models import (RFC_GENERICO, Cliente, CodigoPostal, Devolucion, FormaPago, PedidoCliente, RemisionBorrador,
                     TicketGuardado, Turno, Venta, rfc_valido)
from .servicios import (abrir_turno, cancelar_venta, cerrar_turno, cobrar_remision, disponible_para_devolver,
                        emitir_remision, esperado_en_caja, por_que_no_cancelar, precio_de, registrar_abono,
                        registrar_devolucion)
from .templatetags.erp import cant, miles, pesos


def _volver(request, defecto="mostrador"):
    destino = request.POST.get("next") or ""
    if url_has_allowed_host_and_scheme(destino, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
        return redirect(destino)
    return redirect(defecto)


CAMPO = {FormaPago.EFECTIVO: "efectivo", FormaPago.TARJETA: "tarjeta", FormaPago.TRANSFERENCIA: "transferencia"}


def _autoriza(request, admin=False):
    """Quién tecleó el NIP del formulario (campo «nip»). Si falta o no alcanza, None y el aviso."""
    try:
        return nips.quien(request.session, request.POST.get("nip"), admin=admin)
    except ValidationError as e:
        messages.error(request, " ".join(e.messages))
        return None


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


def _vehiculo_post(p):
    """El vehículo que se estaba atendiendo, de los campos ocultos del formulario: (Vehiculo, parámetros)."""
    if not (p.get("anio") or "").isdigit():
        return None, None
    litros = p.get("litros", "")
    veh = m.Vehiculo(int(p["anio"]), p.get("armadora", ""), p.get("modelo", ""), "" if litros == "todos" else litros)
    return veh, {"armadora": veh.armadora, "anio": veh.anio, "modelo": veh.modelo, "litros": litros}


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
           "cliente": ticket.cliente, "publico": m.cliente_mostrador(),
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
    if ticket.cliente.dias_credito:
        ctx["cliente_saldo"], ctx["cliente_disponible"], ctx["cliente_vencido"] = cli.credito(ticket.cliente)
    ctx["aqui"] = _sin(request.get_full_path(), "codigo", "cantidad", "precio")
    if g.get("codigo"):
        texto, cuantas = m.leer_captura(g["codigo"], g.get("cantidad"))
        ctx["por_codigo"] = {"texto": texto, "cantidad": cuantas, "precio": g.get("precio", ""),
                             "precio_txt": pesos(_decimal(g.get("precio", ""))),
                             "resultados": m.buscar_codigo(texto, ticket.cliente.lista_precio)[0]}
    ctx["enfocar_codigo"] = request.session.pop("enfocar_codigo", False) or bool(g.get("codigo"))
    # venta nueva (ticket vacío, sin cliente ni vehículo): se empieza por el cliente
    ctx["enfocar_cliente"] = (not ctx["enfocar_codigo"] and not lineas and ctx["paso"] == "marca"
                              and ticket.cliente.pk == ctx["publico"].pk and "cliente_nuevo" not in request.session)
    ctx["recien"] = request.session.pop("recien", [])
    ctx["recien_tipo"] = request.session.pop("recien_tipo", "Agregado")
    ctx["agregado"] = request.session.pop("agregado", None)
    folio = request.session.pop("ultima_venta", None)
    ctx["ultima_venta"] = Venta.objects.filter(folio=folio).first() if folio else None
    ctx["ultimo_pago"] = request.session.pop("ultimo_pago", None)
    ctx["puede_deshacer"] = bool(request.session.get("ticket_deshacer")) and not lineas
    ctx["listas_precio"] = ListaPrecio.objects.order_by("nombre")
    ctx["en_espera"] = TicketGuardado.objects.filter(tipo=TicketGuardado.Tipo.ESPERA, retomado__isnull=True
                                                     ).select_related("cliente")[:8]
    ctx["cliente_nuevo"] = request.session.pop("cliente_nuevo", None)
    ctx["rfc_generico"] = RFC_GENERICO
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


def _marcar(request, ticket, indices, cantidad=None):
    """Lo que se acaba de agregar o cambiar: se resalta en el ticket y, si es una sola pieza, se
    confirma en la franja de captura, que es donde está la vista de quien atiende."""
    request.session["recien"] = [i for i in indices if i is not None]
    request.session["recien_tipo"] = "Agregado"
    if cantidad is None or len(indices) != 1:
        return
    ln = next((x for x in ticket.lineas() if x.i == indices[0]), None)
    if ln is None:
        return
    nombre = ln.pos.nombre if ln.pos else (getattr(ln.objeto, "nombre", "") or getattr(ln.objeto, "descripcion", ""))
    request.session["agregado"] = {
        "codigo": ln.opcion.codigo, "marca": ln.opcion.marca, "nombre": nombre[:60],
        "agregada": cant(cantidad), "cantidad": cant(ln.cantidad), "sumado": ln.cantidad != cantidad,
        "precio": pesos(ln.precio) or "sin precio", "importe": pesos(ln.importe) or "—",
        "especial": ln.precio_especial is not None, "pedido": ln.sobre_pedido,
    }


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
        cuantas = _decimal(request.POST.get("cantidad"))
        i = ticket.agregar(clave, request.POST.get("pos"), cantidad=cuantas, nivel=request.POST.get("nivel", ""),
                           precio=precio)
    except (KeyError, ObjectDoesNotExist):
        messages.error(request, "Esa opción ya no existe; vuelve a elegir.")
        return _volver(request)
    except ValidationError as e:
        messages.error(request, " ".join(e.messages))
        return _volver(request)
    suelta = not request.POST.get("pos")
    _marcar(request, ticket, [i], (cuantas or 1) if suelta else None)
    if suelta:
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
        _marcar(request, ticket, [ticket.agregar(op.clave, None, cantidad=cantidad, precio=precio)], cantidad)
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
    cliente = None
    if (request.GET.get("cliente") or "").isdigit():  # Remisiones › Nueva: con la lista de ese cliente
        cliente = Cliente.objects.filter(pk=request.GET["cliente"]).select_related("lista_precio").first()
    lista = (cliente or m.Ticket(request.session).cliente).lista_precio
    res = m.sugerir(request.GET.get("q", ""), lista)
    if request.GET.get("para") == "remision":  # la remisión solo lleva lo que hay en el catálogo
        res = [e for e in res if e.opcion.clave.startswith("producto:")]
    return JsonResponse({"q": request.GET.get("q", ""), "sugerencias": [{
        "clave": e.opcion.clave, "codigo": e.opcion.codigo, "marca": e.opcion.marca,
        "nombre": e.nombre[:70], "equivalente": e.por_equivalencia,
        "hay": not e.opcion.sobre_pedido, "existencia": cant(e.opcion.existencia) if e.opcion.existencia else "",
        "precio": miles(e.opcion.precio) if e.opcion.precio is not None else "",
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
    n, agregadas = 0, []
    for p in m.POSICIONES:
        op = m.opciones_de(veh, p, ticket.cliente.lista_precio)[0][g["nivel"]]
        if op and op.precio is not None:
            ticket.agregar(op.clave, p.clave, nivel=g["nivel"])
            n += 1
            agregadas.append((op.clave, p.clave))
    if not n:
        messages.warning(request, "No hay piezas de ese nivel para este vehículo.")
    _marcar(request, ticket, [ticket.indice(c, p) for c, p in agregadas])
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
        if c > 0:  # en 0 se quita el renglón
            request.session["recien"], request.session["recien_tipo"] = [i], "Cambió"
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
            request.session["recien"], request.session["recien_tipo"] = [i], "Cambió"
        except ValidationError as e:
            messages.error(request, " ".join(e.messages))
        except ObjectDoesNotExist:
            pass
    return _volver(request)


@login_required
@require_POST
def vaciar(request):
    m.Ticket(request.session).vaciar(deshacer=True)
    return _volver(request)


@login_required
@require_POST
def deshacer_vaciar(request):
    if not m.Ticket(request.session).deshacer_vaciar():
        messages.warning(request, "Ya no se puede deshacer: el ticket tiene otras piezas.")
    return _volver(request)


@login_required
@require_POST
def cliente(request):
    try:
        m.Ticket(request.session).elegir_cliente(int(request.POST["cliente"]))
    except (KeyError, ValueError, Cliente.DoesNotExist):
        messages.error(request, "Cliente inválido.")
    else:
        request.session["enfocar_codigo"] = True  # ya con cliente, a capturar piezas
    return _volver(request)


DIRECCION = ("calle", "numero_exterior", "numero_interior", "codigo_postal", "colonia", "municipio", "estado")


def _sin_acentos(texto):
    return "".join(c for c in unicodedata.normalize("NFKD", texto.casefold()) if not unicodedata.combining(c))


@login_required
def codigo_postal(request, cp):
    """Estado, municipio y colonias de un código postal, para llenar la dirección al teclearlo."""
    filas = list(CodigoPostal.objects.filter(cp=cp).values("colonia", "municipio", "estado", "ciudad"))
    if not filas:
        return JsonResponse({"cp": cp, "encontrado": False, "colonias": []})
    colonias = sorted({f["colonia"] for f in filas}, key=_sin_acentos)
    return JsonResponse({"cp": cp, "encontrado": True, "estado": filas[0]["estado"], "municipio": filas[0]["municipio"],
                         "ciudad": filas[0]["ciudad"], "colonias": colonias})


@login_required
@require_POST
def cliente_nuevo(request):
    """Alta rápida desde el ticket: nombre, teléfono, RFC (el genérico si no da), dirección y lista. El crédito
    solo lo da el administrador. Si ya existe uno con ese nombre se elige ese en vez de duplicarlo."""
    p = request.POST
    datos = {k: " ".join((p.get(k) or "").split()) for k in ("nombre", "telefono", "correo", "rfc", "lista", "dias", "limite")
             + DIRECCION}
    datos["correo"] = datos["correo"].replace(" ", "").lower()
    datos["rfc"] = datos["rfc"].upper().replace(" ", "") or RFC_GENERICO
    ticket = m.Ticket(request.session)

    def error(texto):
        messages.error(request, texto)
        request.session["cliente_nuevo"] = datos
        return _volver(request)

    if not datos["nombre"]:
        return error("Escribe el nombre del cliente.")
    try:
        rfc_valido(datos["rfc"])
    except ValidationError as e:
        return error(f"{e.messages[0]} Si no lo da, deja el genérico.")
    if datos["codigo_postal"] and not (len(datos["codigo_postal"]) == 5 and datos["codigo_postal"].isdigit()):
        return error("El código postal lleva 5 números, o déjalo vacío.")
    if datos["correo"]:
        try:
            validate_email(datos["correo"])
        except ValidationError:
            return error("Revisa el correo (algo como nombre@gmail.com), o déjalo vacío.")
    existente, por = cli.duplicado(datos["nombre"], datos["telefono"], datos["rfc"])
    if existente:  # no dar de alta dos veces al mismo cliente
        otro = "" if por == "ese nombre" else " Si es otro cliente, dalo de alta con otro dato."
        messages.info(request, f"{existente} ({existente.folio}) ya estaba dado de alta con {por}; lo dejé elegido.{otro}")
        return _elegido_en(request, ticket, existente)
    lista = ListaPrecio.objects.filter(pk=datos["lista"]).first() if datos["lista"].isdigit() else None
    nuevo = Cliente(nombre=datos["nombre"][:150], telefono=datos["telefono"][:20], correo=datos["correo"][:254], rfc=datos["rfc"],
                    lista_precio=lista or m.cliente_mostrador().lista_precio,
                    **{k: datos[k][:Cliente._meta.get_field(k).max_length] for k in DIRECCION})
    dias, limite = _decimal(datos["dias"] or 0), _decimal(datos["limite"] or 0)
    if dias is None or limite is None or dias < 0 or limite < 0 or dias != int(dias) or dias > 365:
        return error("Días de crédito y límite van en números, por ejemplo 15 días y $3,000.")
    if dias and not limite:
        return error("Si le das días de crédito, ponle también un límite.")
    if dias:  # el crédito lo autoriza un administrador con su NIP
        if not _autoriza(request, admin=True):
            request.session["cliente_nuevo"] = datos
            return _volver(request)
        nuevo.dias_credito, nuevo.limite_credito = int(dias), limite
    nuevo.save()
    extra = f" con {nuevo.dias_credito} días de crédito hasta {pesos(nuevo.limite_credito)}" if nuevo.dias_credito else ""
    donde = "la remisión" if request.POST.get("para") == "remision" else "el ticket"
    messages.success(request, f"Cliente {nuevo} ({nuevo.folio}) dado de alta{extra} y elegido en {donde}.")
    return _elegido_en(request, ticket, nuevo)


def _elegido_en(request, ticket, cliente):
    """Después del alta: en Remisiones › Nueva regresa con el cliente elegido; en el mostrador lo elige en el ticket."""
    if request.POST.get("para") == "remision":
        destino = request.POST.get("next") or ""
        if not url_has_allowed_host_and_scheme(destino, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
            destino = reverse("remision_nueva")
        return redirect(_con(_sin(destino, "cliente"), cliente=cliente.pk))
    ticket.elegir_cliente(cliente.pk)
    request.session["enfocar_codigo"] = True
    return _volver(request)


@login_required
@require_POST
def cobrar(request):
    turno = _turno(request.user)
    if not turno:
        messages.error(request, "Abre la caja antes de cobrar.")
        return redirect("caja")
    ticket = m.Ticket(request.session)
    forma = request.POST.get("forma_pago", FormaPago.EFECTIVO)
    # a crédito, a quien debe algo vencido, lo autoriza un administrador
    vencido = cli.vencido(ticket.cliente) if forma == FormaPago.CREDITO else 0
    vendedor = _autoriza(request, admin=bool(vencido))
    if not vendedor:
        return _volver(request)
    veh, _ = _vehiculo_post(request.POST)
    # efectivo: con cuánto paga, para dar el cambio (opcional)
    paga_con = _decimal(request.POST.get("paga_con")) if forma == FormaPago.EFECTIVO else None
    if paga_con is not None:
        por_cobrar = sum((ln.importe for ln in ticket.lineas(veh) if not ln.sobre_pedido), Decimal(0))
        if paga_con < por_cobrar:
            messages.error(request, f"Paga con {pesos(paga_con)} y son {pesos(por_cobrar)}: faltan {pesos(por_cobrar - paga_con)}.")
            return _volver(request)
    try:
        venta, ordenes = m.cobrar(ticket, turno, forma, vendedor, veh, contacto=request.POST.get("contacto", ""))
    except ValidationError as e:
        messages.error(request, " ".join(e.messages))
        return _volver(request)
    partes = []
    if venta:
        request.session["ultima_venta"] = venta.folio
        if paga_con is not None:
            request.session["ultimo_pago"] = {"recibio": pesos(paga_con), "cambio": pesos(paga_con - venta.total)}
        partes.append(f"Venta {venta.folio} por {pesos(venta.total)} con {venta.get_forma_pago_display().lower()}.")
        if vencido:
            partes.append(f"Crédito autorizado por {vendedor.get_short_name() or vendedor.get_username()} aunque debía {pesos(vencido)} vencido.")
    for oc in ordenes:
        partes.append(f"Quedó pedido a {oc.proveedor} en {oc.folio} (borrador, compra de emergencia).")
    if ordenes:
        partes.append("Lo ves en Pedidos para avisarle al cliente cuando llegue.")
    messages.success(request, " ".join(partes))
    return redirect("mostrador")  # siguiente cliente


@login_required
@require_POST
def poner_en_espera(request):
    """Aparta el ticket para atender a otro cliente; se retoma desde el ticket."""
    veh, base = _vehiculo_post(request.POST)
    try:
        g = m.guardar_ticket(m.Ticket(request.session), TicketGuardado.Tipo.ESPERA, request.user, veh, base)
    except ValidationError as e:
        messages.error(request, " ".join(e.messages))
        return _volver(request)
    messages.success(request, f"El ticket de {g.para} ({pesos(g.total)}) quedó en espera. Lo retomas desde el ticket.")
    return redirect("mostrador")


@login_required
@require_POST
def cotizar(request):
    """Guarda la cotización con folio y abre la hoja para imprimirla. El ticket se queda como está."""
    veh, base = _vehiculo_post(request.POST)
    try:
        g = m.guardar_ticket(m.Ticket(request.session), TicketGuardado.Tipo.COTIZACION, request.user, veh, base,
                             nombre=request.POST.get("nombre", ""))
    except ValidationError as e:
        messages.error(request, " ".join(e.messages))
        return _volver(request)
    luego = "?enviar=1#enviar" if request.POST.get("luego") == "enviar" else "?imprimir=1"
    return redirect(reverse("cotizacion", args=[g.folio]) + luego)


@login_required
@require_POST
def retomar(request, folio):
    g = get_object_or_404(TicketGuardado, folio=folio)
    veh, base = _vehiculo_post(request.POST)
    try:
        apartado = m.retomar(m.Ticket(request.session), g, request.user, veh, base)
    except ValidationError as e:
        messages.error(request, " ".join(e.messages))
        return _volver(request)
    partes = [f"Retomaste {g.folio} de {g.para}."]
    if g.tipo == TicketGuardado.Tipo.COTIZACION:
        partes.append("Van los precios cotizados." if timezone.localdate() <= g.vence
                      else f"La cotización venció el {g.vence:%d/%m}; van los precios de hoy.")
    if apartado:
        partes.append(f"Lo que tenías en el ticket quedó en espera ({apartado.para}).")
    messages.success(request, " ".join(partes))
    return redirect(_url(**g.vehiculo_params))


@login_required
@require_POST
def descartar_espera(request, folio):
    g = get_object_or_404(TicketGuardado, folio=folio, tipo=TicketGuardado.Tipo.ESPERA)
    if not g.retomado:
        g.retomado = timezone.now()
        g.save(update_fields=["retomado"])
        messages.info(request, f"Descartaste el ticket en espera de {g.para}.")
    return _volver(request)


def _detalle(g):
    return [dict(d, cantidad=Decimal(d["cantidad"]), precio=Decimal(d["precio"]), importe=Decimal(d["importe"]))
            for d in g.detalle]


def _cotizacion_ctx(request, g, formato=None):
    from django.conf import settings

    lineas = _detalle(g)
    return {"g": g, "formato": formato or ("ticket" if request.GET.get("formato") == "ticket" else "carta"),
            "imprimir": request.GET.get("imprimir") == "1", "lineas": lineas,
            "hay_pedido": any(d["pedido"] for d in lineas), "subtotal": g.total / m.IVA, "iva": g.total - g.total / m.IVA,
            "vigente": timezone.localdate() <= g.vence, "dias": settings.COTIZACION_VIGENCIA_DIAS,
            "rfc": settings.NEGOCIO_RFC, "direccion": settings.NEGOCIO_DIRECCION, "telefono": settings.NEGOCIO_TELEFONO}


@login_required
def cotizacion(request, folio):
    """Cotización para imprimir, en hoja carta o ticket de 80 mm, con su vigencia, o para mandarla al cliente por
    WhatsApp o correo con un enlace que abre sin entrar al sistema."""
    from django.conf import settings

    g = get_object_or_404(TicketGuardado.objects.select_related("cliente", "usuario"), folio=folio,
                          tipo=TicketGuardado.Tipo.COTIZACION)
    ctx = _cotizacion_ctx(request, g)
    enlace = request.build_absolute_uri(reverse("cotizacion_publica", args=[compartir.token(g)]))
    negocio = settings.NEGOCIO_NOMBRE
    texto_wa = compartir.mensaje(g, ctx["lineas"], enlace, negocio)
    texto_correo = compartir.mensaje(g, ctx["lineas"], enlace, negocio, negritas=False)
    asunto = f"Cotización {g.folio} · {negocio}"
    telefono = g.cliente.telefono if g.cliente_id else ""
    correo = g.cliente.correo if g.cliente_id else ""
    ctx["envio"] = {
        "enlace": enlace, "texto_wa": texto_wa, "texto_correo": texto_correo, "asunto": asunto,
        "telefono": telefono, "correo": correo, "abrir": request.GET.get("enviar") == "1", "dias": compartir.ENLACE_DIAS,
        "whatsapp": compartir.enlace_whatsapp(telefono, texto_wa) if compartir.whatsapp(telefono) or not telefono else "",
        "mailto": compartir.enlace_correo(correo, asunto, texto_correo),
    }
    return render(request, "ventas/cotizacion.html", ctx)


def cotizacion_publica(request, token):
    """Lo que ve el cliente al abrir el enlace que le llegó por WhatsApp o correo: la cotización, sin entrar al
    sistema y sin nada más del negocio. Un enlace alterado o de hace más de ENLACE_DIAS no abre nada."""
    g = compartir.cotizacion_de(token)
    if g is None:
        return render(request, "ventas/cotizacion_enlace_vencido.html", {"dias": compartir.ENLACE_DIAS}, status=404)
    respuesta = render(request, "ventas/cotizacion.html", {**_cotizacion_ctx(request, g, formato="carta"), "publica": True})
    respuesta["X-Robots-Tag"] = "noindex, nofollow"
    respuesta["Referrer-Policy"] = "no-referrer"
    return respuesta


@login_required
def cotizaciones(request):
    q = (request.GET.get("q") or "").strip()
    cots = TicketGuardado.objects.filter(tipo=TicketGuardado.Tipo.COTIZACION).select_related("cliente")
    if q:
        cots = cots.filter(Q(folio__icontains=q) | cli.coincide(q) | Q(nombre__icontains=q))
    return render(request, "ventas/cotizaciones.html", {
        "cotizaciones": cots[:60], "q": q, "turno": _turno(request.user), "hoy": timezone.localdate(), **_pestanas()})


def _situacion(p):
    """En qué va un pedido de cliente, en palabras del mostrador: (clase, texto)."""
    if p.estado != PedidoCliente.Estado.PEDIDO:
        return "cerrado", p.get_estado_display()
    if p.hay:
        return "listo", "Ya está en anaquel: avísale"
    orden = p.linea_oc.orden if p.linea_oc_id else None
    if orden is None:
        return "alerta", "Sin orden de compra: hay que pedirlo"
    if orden.estado == orden.Estado.BORRADOR:
        return "alerta", f"{orden.folio} sin enviar a {orden.proveedor}"
    if orden.estado == orden.Estado.CANCELADA:
        return "alerta", f"{orden.folio} se canceló: hay que volver a pedirlo"
    if orden.estado == orden.Estado.ENVIADA:
        llega = f", llega el {orden.fecha_esperada:%d/%m}" if orden.fecha_esperada else ""
        return "camino", f"Pedido a {orden.proveedor} en {orden.folio}{llega}"
    return "alerta", f"{orden.folio} ya se recibió pero no alcanza en anaquel"


@login_required
def pedidos(request):
    """Lo que los clientes dejaron sobre pedido: primero lo que ya llegó, para avisarles."""
    q = (request.GET.get("q") or "").strip()
    cerrados = request.GET.get("ver") == "cerrados"
    qs = PedidoCliente.objects.select_related("cliente", "producto", "linea_oc__orden__proveedor", "venta", "venta_entrega")
    qs = qs.exclude(estado=PedidoCliente.Estado.PEDIDO).order_by("-cerrado") if cerrados else qs.filter(estado=PedidoCliente.Estado.PEDIDO)
    if q:
        qs = qs.filter(cli.coincide(q) | Q(codigo__icontains=q) | Q(contacto__icontains=q)
                       | Q(descripcion__icontains=q))
    hoy = timezone.localdate()
    filas = [{"p": p, "situacion": _situacion(p), "dias": (hoy - timezone.localtime(p.fecha).date()).days} for p in qs[:100]]
    if not cerrados:
        filas.sort(key=lambda f: (f["situacion"][0] != "listo", f["p"].fecha))
    pendientes = PedidoCliente.objects.filter(estado=PedidoCliente.Estado.PEDIDO)
    return render(request, "ventas/pedidos.html", {
        "filas": filas, "q": q, "cerrados": cerrados, "turno": _turno(request.user), "n_pendientes": pendientes.count()})


@login_required
@require_POST
def pedido_entregar(request, pk):
    p = get_object_or_404(PedidoCliente.objects.select_related("producto", "cliente"), pk=pk)
    if p.estado != PedidoCliente.Estado.PEDIDO:
        messages.error(request, "Ese pedido ya no está pendiente.")
        return redirect("pedidos")
    if not p.hay:
        messages.error(request, f"Todavía no llega: hay {cant(p.producto.existencia)} en anaquel y son {cant(p.cantidad)}.")
        return redirect("pedidos")
    ticket = m.Ticket(request.session)
    apartado = None
    if ticket.datos["lineas"] and ticket.datos.get("cliente") not in (None, p.cliente_id):
        apartado = m.guardar_ticket(ticket, TicketGuardado.Tipo.ESPERA, request.user)
    ticket.agregar_pedido(p)
    aviso = f"{p.codigo} de {p.cliente} está en el ticket con el precio que se le dijo. Al cobrarlo queda entregado."
    if apartado:
        aviso += f" Lo que tenías quedó en espera ({apartado.para})."
    messages.success(request, aviso)
    return redirect("mostrador")


@login_required
@require_POST
def pedido_cancelar(request, pk):
    p = get_object_or_404(PedidoCliente, pk=pk)
    if not _autoriza(request):
        return redirect("pedidos")
    try:
        enviada = m.cancelar_pedido(p)
    except ValidationError as e:
        messages.error(request, " ".join(e.messages))
        return redirect("pedidos")
    if enviada:
        messages.warning(request, f"Pedido cancelado. {enviada.folio} ya se envió a {enviada.proveedor}: avísale o quédatelo para anaquel.")
    else:
        messages.success(request, f"Pedido de {p.codigo} para {p.cliente} cancelado; se quitó de la orden en borrador.")
    return redirect("pedidos")


@login_required
def caja(request):
    turno = _turno(request.user)
    if request.method == "POST":
        try:
            quien = _autoriza(request)
            if not quien:
                return redirect("caja")
            if request.POST.get("accion") == "abrir":
                abrir_turno(request.user, _decimal(request.POST.get("fondo"), Decimal(0)), por=quien)
                messages.success(request, "Caja abierta.")
                return redirect("mostrador")
            if request.POST.get("accion") == "cerrar" and turno:
                contado = [_decimal(request.POST.get(k), Decimal(0)) for k in CAMPO.values()]
                turno, esperado = cerrar_turno(turno, *contado, por=quien)
                request.session["corte"] = turno.pk
                return redirect("caja")
        except ValidationError as e:
            messages.error(request, " ".join(e.messages))
        return redirect("caja")
    ctx = {"turno": turno, "formas": [c for c in FormaPago.choices if c[0] != FormaPago.CREDITO],
           "por_cobrar": Venta.objects.filter(estado=Venta.Estado.POR_COBRAR).select_related("cliente", "usuario").order_by("fecha")}
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


def _pestanas():
    """Las cuentas que se ven en las pestañas de Remisiones."""
    pendiente = Venta.objects.filter(estado__in=[Venta.Estado.CREDITO, Venta.Estado.POR_COBRAR])
    return {"saldo_total": pendiente.aggregate(s=Sum("saldo"))["s"] or 0, "n_por_cobrar": pendiente.count(),
            "n_borradores": RemisionBorrador.objects.count()}


@login_required
def remisiones(request):
    """Ventas para buscar, reimprimir, cobrar abonos o cancelar: por folio o por cliente.
    «Por cobrar» deja las de crédito con saldo (las más vencidas primero) y las remisiones de contado que
    falta cobrar en caja."""
    q = (request.GET.get("q") or "").strip()
    por_cobrar = request.GET.get("ver") == "por-cobrar"
    ventas = Venta.objects.select_related("cliente").order_by("-fecha")
    if por_cobrar:
        ventas = ventas.filter(estado__in=[Venta.Estado.CREDITO, Venta.Estado.POR_COBRAR]).order_by("vence", "fecha")
    if q:
        ventas = ventas.filter(Q(folio__icontains=q) | cli.coincide(q))
    ctx = {"ventas": ventas[:60], "q": q, "por_cobrar": por_cobrar, "turno": _turno(request.user),
           "hoy": timezone.localdate(), **_pestanas()}
    return render(request, "ventas/remisiones.html", ctx)


def _venta(folio):
    return get_object_or_404(Venta.objects.select_related("cliente", "usuario", "turno", "cancelada_por"), folio=folio)


@login_required
def venta(request, folio):
    """Una venta: sus partidas, lo que se ha abonado, imprimir, abonar y cancelar."""
    venta = _venta(folio)
    turno = _turno(request.user)
    hoy = timezone.localdate()
    lineas = list(venta.lineas.select_related("producto", "producto__marca", "unidad"))
    for ln in lineas:
        ln.por_devolver = disponible_para_devolver(ln)
    ctx = {"venta": venta, "turno": turno, "hoy": hoy, "lineas": lineas,
           "devoluciones": venta.devoluciones.select_related("autorizo").prefetch_related("lineas__venta_linea__producto"),
           "abonos": venta.abonos.select_related("turno__usuario").order_by("fecha"),
           "abonado": venta.total - venta.saldo if venta.estado != Venta.Estado.CANCELADA else 0,
           "formas_abono": [c for c in FormaPago.choices if c[0] != FormaPago.CREDITO],
           "no_cancelar": por_que_no_cancelar(venta),
           "vencida": venta.estado == Venta.Estado.CREDITO and venta.vence and venta.vence < hoy,
           "devolver_dinero": venta.forma_pago not in (FormaPago.CREDITO, "")}
    return render(request, "ventas/venta.html", ctx)


@login_required
@require_POST
def venta_abonar(request, folio):
    venta = _venta(folio)
    turno = _turno(request.user)
    if not turno:
        messages.error(request, "Abre la caja para registrar el abono: el dinero entra al corte de tu turno.")
        return redirect("venta", folio)
    monto = _decimal(request.POST.get("monto"))
    forma = request.POST.get("forma_pago", FormaPago.EFECTIVO)
    if forma not in (FormaPago.EFECTIVO, FormaPago.TARJETA, FormaPago.TRANSFERENCIA):
        forma = FormaPago.EFECTIVO
    if monto is None:
        messages.error(request, "Escribe cuánto abona.")
        return redirect("venta", folio)
    quien = _autoriza(request)
    if not quien:
        return redirect("venta", folio)
    try:
        registrar_abono(venta, monto, forma, turno, usuario=quien)
    except ValidationError as e:
        messages.error(request, " ".join(e.messages))
        return redirect("venta", folio)
    venta.refresh_from_db()
    if venta.estado == Venta.Estado.PAGADA:
        messages.success(request, f"Abono de {pesos(monto)} registrado. {venta.folio} quedó liquidada.")
    else:
        messages.success(request, f"Abono de {pesos(monto)} registrado. Resta {pesos(venta.saldo)}.")
    return redirect("venta", folio)


@login_required
@require_POST
def venta_devolver(request, folio):
    """Devolución (regresa pieza y dinero) o garantía (otra pieza igual sin cobrar), con NIP de administrador."""
    venta = _venta(folio)
    p = request.POST
    tipo = p.get("tipo")
    cantidades = {}
    for ln in venta.lineas.all():
        cant = _decimal(p.get(f"cant_{ln.pk}") or 0)
        if cant is None:
            messages.error(request, "Las cantidades van en números.")
            return redirect("venta", folio)
        cantidades[ln.pk] = cant
    admin = _autoriza(request, admin=True)
    if not admin:
        return redirect("venta", folio)
    try:
        d = registrar_devolucion(venta, tipo, cantidades, admin, p.get("motivo"), turno=_turno(request.user),
                                 forma_reembolso=p.get("forma_reembolso"), regresa_anaquel=p.get("regresa") == "1")
    except ValidationError as e:
        messages.error(request, " ".join(e.messages))
        return redirect("venta", folio)
    if d.tipo == Devolucion.Tipo.GARANTIA:
        messages.success(request, f"Garantía {d.folio}: entrega la pieza nueva sin cobrar. La mala queda por reclamar al proveedor.")
    else:
        partes = [f"Devolución {d.folio} por {pesos(d.importe)}."]
        if d.a_saldo:
            partes.append(f"Bajó {pesos(d.a_saldo)} del saldo.")
        if d.reembolso:
            partes.append(f"Regrésale {pesos(d.reembolso)} en {d.get_forma_reembolso_display().lower()}; sale del corte de este turno.")
        partes.append("La pieza regresó al anaquel." if d.regresa_anaquel else "La pieza no regresa al anaquel.")
        messages.success(request, " ".join(partes))
    return redirect("venta", folio)


@login_required
def devoluciones(request):
    q = (request.GET.get("q") or "").strip()
    por_reclamar = request.GET.get("ver") == "por-reclamar"
    qs = Devolucion.objects.select_related("venta__cliente", "autorizo").prefetch_related("lineas__venta_linea__producto")
    if por_reclamar:
        qs = qs.filter(tipo=Devolucion.Tipo.GARANTIA, reclamo__in=[Devolucion.Reclamo.PENDIENTE, Devolucion.Reclamo.RECLAMADA])
    if q:
        qs = qs.filter(Q(folio__icontains=q) | Q(venta__folio__icontains=q) | cli.coincide(q, "venta__cliente")
                       | Q(lineas__venta_linea__producto__sku__icontains=q)).distinct()
    return render(request, "ventas/devoluciones.html", {
        "devoluciones": qs[:60], "q": q, "por_reclamar": por_reclamar, "turno": _turno(request.user),
        "reclamos": Devolucion.Reclamo.choices,
        "n_reclamar": Devolucion.objects.filter(tipo=Devolucion.Tipo.GARANTIA, reclamo=Devolucion.Reclamo.PENDIENTE).count(),
        **_pestanas()})


@login_required
@require_POST
def devolucion_reclamo(request, folio):
    d = get_object_or_404(Devolucion, folio=folio, tipo=Devolucion.Tipo.GARANTIA)
    estado = request.POST.get("reclamo")
    if estado in Devolucion.Reclamo.values:
        d.reclamo = estado
        d.save(update_fields=["reclamo"])
        messages.success(request, f"{d.folio}: {d.get_reclamo_display().lower()}.")
    return _volver(request, "devoluciones")


@login_required
@require_POST
def venta_cancelar(request, folio):
    venta = _venta(folio)
    razon = por_que_no_cancelar(venta)
    motivo = (request.POST.get("motivo") or "").strip()
    if razon:
        messages.error(request, razon)
    elif not motivo:
        messages.error(request, "Escribe el motivo de la cancelación.")
    elif admin := _autoriza(request, admin=True):
        try:
            cancelar_venta(venta, admin, motivo)
        except ValidationError as e:
            messages.error(request, " ".join(e.messages))
        else:
            aviso = f"{venta.folio} cancelada. El inventario regresó al anaquel."
            if venta.forma_pago not in (FormaPago.CREDITO, ""):  # «por cobrar en caja» no se había pagado
                aviso += f" Devuelve {pesos(venta.total)} al cliente; ya no cuenta en el corte."
            messages.success(request, aviso)
    return redirect("venta", folio)


# ---------------------------------------------------------------------------------------------
# Remisiones › Nueva: la remisión hecha con calma, aparte del mostrador
# ---------------------------------------------------------------------------------------------

@login_required
def clientes_buscar(request):
    """Clientes por folio (CTE-0012), celular o nombre, para elegirlo en la remisión."""
    q = request.GET.get("q", "").strip()
    if not q:  # campo vacío: los últimos clientes atendidos
        return JsonResponse({"clientes": [], "recientes": [cli.cliente_json(c) for c in cli.recientes(sin=m.cliente_mostrador().pk)]})
    return JsonResponse({"clientes": [cli.cliente_json(c) for c in cli.buscar_clientes(q)]})


@login_required
def remision_precios(request):
    """Precio de lista y existencia de las partidas para el cliente elegido (al cambiar de cliente)."""
    cliente = Cliente.objects.filter(pk=request.GET.get("cliente") or 0).select_related("lista_precio").first()
    res = {}
    for clave in request.GET.getlist("clave")[:100]:
        try:
            obj = m.resolver(clave)
        except (ValidationError, ObjectDoesNotExist):
            continue
        if cliente and not isinstance(obj, PartidaListaProveedor):
            res[clave] = rem.fila_de(obj, cliente)
    return JsonResponse({"filas": res})


def _datos_remision(request, borrador=None):
    """Lo que trae el formulario (o el borrador guardado) para volver a pintarlo."""
    if request.method == "POST":
        p = request.POST
        cliente = (Cliente.objects.filter(pk=p["cliente"], activo=True).select_related("lista_precio").first()
                   if (p.get("cliente") or "").isdigit() else None)
        return {"cliente": cliente, "condiciones": "CREDITO" if p.get("condiciones") == "CREDITO" else "CONTADO",
                "orden_compra": p.get("orden_compra", "").strip()[:40], "vehiculo": p.get("vehiculo", "").strip()[:120],
                "observaciones": p.get("observaciones", "").strip()[:300], "lineas": rem.leer_lineas(p)}
    if borrador:
        nuevo = (Cliente.objects.filter(pk=request.GET["cliente"], activo=True).first()
                 if (request.GET.get("cliente") or "").isdigit() else None)  # recién dado de alta desde el borrador
        return {"cliente": nuevo or borrador.cliente, "condiciones": borrador.condiciones, "orden_compra": borrador.orden_compra,
                "vehiculo": borrador.vehiculo, "observaciones": borrador.observaciones, "lineas": borrador.lineas}
    cliente = None
    if (request.GET.get("cliente") or "").isdigit():  # recién dado de alta desde esta pantalla
        cliente = Cliente.objects.filter(pk=request.GET["cliente"], activo=True).first()
    return {"cliente": cliente, "condiciones": "CONTADO", "orden_compra": "", "vehiculo": "", "observaciones": "",
            "lineas": []}


def _precio_remision(request, cliente):
    """Regla de precio de la remisión: vacío es el de la lista del cliente; con el descuento aplicado no puede
    quedar abajo del costo, salvo que lo deje un administrador (sin decir el costo)."""
    def precio(prod, texto, descuento):
        tecleado = None
        if str(texto or "").strip():
            tecleado = _decimal(texto)
            if tecleado is None or tecleado <= 0:
                raise ValidationError(f"Escribe un precio válido para {prod.sku}, por ejemplo 125.50.")
            tecleado = tecleado.quantize(Decimal("0.01"))
        base = tecleado if tecleado is not None else precio_de(prod, cliente.lista_precio)
        neto = (base * (100 - descuento) / 100).quantize(Decimal("0.01"))
        if m.abajo_del_costo(prod, neto):
            if not request.user.is_staff:
                raise ValidationError(f"{prod.sku} a {pesos(neto)} queda abajo del costo. Solo un administrador puede dejarlo así.")
            messages.warning(request, f"Ojo: {prod.sku} a {pesos(neto)} queda abajo del costo.")
        return tecleado
    return precio


@login_required
def remision_nueva(request, pk=None):
    """Remisión con cliente (por folio, celular o nombre), condiciones de pago, orden de compra, observaciones
    y partidas. Se guarda como borrador o se emite con el NIP de quien la hace."""
    borrador = get_object_or_404(RemisionBorrador, pk=pk) if pk else None
    datos = _datos_remision(request, borrador)
    if request.method == "POST":
        if request.POST.get("accion") == "emitir":
            try:
                cliente = datos["cliente"]
                if not cliente:
                    raise ValidationError("Elige al cliente: teclea su folio, su celular o su nombre.")
                if datos["condiciones"] == "CREDITO" and cliente.dias_credito <= 0:
                    raise ValidationError(f"{cliente} no tiene crédito autorizado: va de contado.")
                partidas = rem.partidas(datos["lineas"], cliente, _precio_remision(request, cliente))
                # a crédito, a quien debe algo vencido, lo autoriza un administrador
                vencido = cli.vencido(cliente) if datos["condiciones"] == "CREDITO" else 0
                quien = _autoriza(request, admin=bool(vencido))
                if quien:
                    venta = emitir_remision(cliente, partidas, datos["condiciones"], quien, datos["orden_compra"],
                                            datos["observaciones"], datos["vehiculo"])
                    if borrador:
                        borrador.delete()
                    siguiente = ("Ya puedes cobrarla en Caja." if venta.estado == Venta.Estado.POR_COBRAR
                                 else f"Quedó en Por cobrar; vence el {venta.vence:%d/%m/%Y}.")
                    autorizo = (f" Crédito autorizado por {quien.get_short_name() or quien.get_username()} aunque debía "
                                f"{pesos(vencido)} vencido.") if vencido else ""
                    messages.success(request, f"Remisión {venta.folio} por {pesos(venta.total)} para {cliente}. {siguiente}{autorizo}")
                    return redirect("venta", venta.folio)
            except (ValidationError, ObjectDoesNotExist) as e:
                messages.error(request, " ".join(getattr(e, "messages", [str(e)])))
        else:  # guardar borrador
            borrador = borrador or RemisionBorrador(usuario=request.user)
            for campo in ("cliente", "condiciones", "orden_compra", "vehiculo", "observaciones", "lineas"):
                setattr(borrador, campo, datos[campo])
            borrador.total = rem.total_tecleado(datos["lineas"])
            borrador.save()
            messages.success(request, f"Borrador {borrador.pk} guardado. Lo sigues desde Remisiones › Borradores.")
            return redirect("remision_borrador", borrador.pk)
    cliente = datos["cliente"]
    ctx = {"datos": datos, "borrador": borrador, "cliente_actual": cli.cliente_json(cliente) if cliente else None,
           "lineas": datos["lineas"] + [dict.fromkeys(rem.CAMPOS_LINEA, "")],  # siempre una fila vacía al final
           "turno": _turno(request.user), "listas_precio": ListaPrecio.objects.order_by("nombre"),
           "cliente_nuevo": request.session.pop("cliente_nuevo", None), "rfc_generico": RFC_GENERICO,
           "aqui": request.get_full_path()}
    return render(request, "ventas/remision_nueva.html", ctx, status=400 if request.method == "POST" else 200)


@login_required
def remision_borradores(request):
    borradores = RemisionBorrador.objects.select_related("cliente", "usuario")
    return render(request, "ventas/borradores.html", {"borradores": borradores, "turno": _turno(request.user), **_pestanas()})


@login_required
@require_POST
def remision_borrador_borrar(request, pk):
    b = get_object_or_404(RemisionBorrador, pk=pk)
    b.delete()
    messages.success(request, f"Borrador {pk} borrado.")
    return redirect("remision_borradores")


@login_required
@require_POST
def remision_cobrar(request, folio):
    """Caja cobra una remisión de contado (con el NIP de quien cobra)."""
    venta = _venta(folio)
    turno = _turno(request.user)
    destino = "caja" if request.POST.get("desde") == "caja" else None
    if not turno:
        messages.error(request, "Abre la caja para cobrar: el dinero entra al corte de tu turno.")
        return redirect("caja")
    quien = _autoriza(request)
    if quien:
        forma = request.POST.get("forma_pago", "")
        paga_con = _decimal(request.POST.get("paga_con")) if forma == FormaPago.EFECTIVO else None
        try:
            if paga_con is not None and paga_con < venta.total:
                raise ValidationError(f"Paga con {pesos(paga_con)} y son {pesos(venta.total)}: faltan {pesos(venta.total - paga_con)}.")
            cobrar_remision(venta, turno, forma, quien)
            cambio = f" Cambio: {pesos(paga_con - venta.total)}." if paga_con is not None else ""
            messages.success(request, f"{venta.folio} cobrada: {pesos(venta.total)} en {FormaPago(forma).label.lower()}.{cambio}")
        except ValidationError as e:
            messages.error(request, " ".join(e.messages))
    return redirect(destino or "venta", *([] if destino else [venta.folio]))
