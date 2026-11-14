"""Convierte el texto de aplicación de las listas de proveedor en vehículos con rango de años.

Formatos que maneja (listas MORSA):
  Gonher / Fram / LTH:  "NISSAN. TSURU L4 1.6 (90-04) - SENTRA L4 1.6 (97-00) / L4 2.0 (01-06)"
  Autoprime:            "NISS. TSURU III L4 1.6L 1995-2008,NISS. TSURU III L4 1.6L 2009-2018"
La armadora se arrastra entre segmentos hasta que aparece otra. Lo que no se entiende se descarta
(el texto original sigue disponible para búsqueda libre)."""

import re
from dataclasses import dataclass

ARMADORAS = {
    "NISSAN": "Nissan", "NISS": "Nissan", "NIS": "Nissan",
    "VW": "Volkswagen", "VOLKSWAGEN": "Volkswagen", "VOLKS": "Volkswagen",
    "CHEVROLET": "Chevrolet", "CHEV": "Chevrolet",  # CHEVY no: es modelo
    "GM": "GM", "GMC": "GMC", "BUICK": "Buick", "CADILLAC": "Cadillac", "PONTIAC": "Pontiac", "OLDSMOBILE": "Oldsmobile",
    "FORD": "Ford", "LINCOLN": "Lincoln", "MERCURY": "Mercury",
    "DODGE": "Dodge", "CHRYSLER": "Chrysler", "CHRYS": "Chrysler", "JEEP": "Jeep", "RAM": "RAM", "PLYMOUTH": "Plymouth",
    "TOYOTA": "Toyota", "TY": "Toyota", "LEXUS": "Lexus", "SCION": "Scion",
    "HONDA": "Honda", "ACURA": "Acura", "MAZDA": "Mazda", "MITSUBISHI": "Mitsubishi", "MITS": "Mitsubishi",
    "HYUNDAI": "Hyundai", "KIA": "Kia", "SUZUKI": "Suzuki", "ISUZU": "Isuzu", "SUBARU": "Subaru",
    "RENAULT": "Renault", "PEUGEOT": "Peugeot", "SEAT": "Seat", "AUDI": "Audi", "BMW": "BMW", "MINI": "Mini",
    "MERCEDES": "Mercedes-Benz", "FIAT": "Fiat", "INFINITI": "Infiniti", "JAC": "JAC", "JMC": "JMC",
    "GEO": "Geo", "SATURN": "Saturn", "MERCEDEZ": "Mercedes-Benz", "CR": "Chrysler", "HYUN": "Hyundai",
    "FD": "Ford", "SUZ": "Suzuki", "SUZIKI": "Suzuki", "MITSUB": "Mitsubishi", "TOTOYA": "Toyota",
    "INTINITI": "Infiniti", "SMART": "Smart", "CY": "Chirey", "ALFA": "Alfa Romeo",
    "MB": "Mercedes-Benz", "MITSU": "Mitsubishi",
    "DATSUN": "Nissan",  # en las listas "DATSUN SEDAN" va dentro de Nissan; si fuera armadora aparte se perdería el Tsuru
    "VOLVO": "Volvo", "SAAB": "Saab", "LAND": "Land Rover", "JAGUAR": "Jaguar", "CHIREY": "Chirey", "MG": "MG",
}
CIL = r"(?:[LV]\s?\d{1,2}|(?<![\d.])\d\s?L(?=\s)|\d\s?CIL\.?)"
CIL_RE = re.compile(rf"\b{CIL}\b")
DISP_RE = re.compile(r"\b(\d\.\d)\s?(?:L|LTS)?\b")
ANIOS_PAR = re.compile(r"\(\s*(\d{2}|\d{4})\s*(?:-\s*(\d{2}|\d{4}))?\s*\)")
ANIOS_4 = re.compile(r"\b((?:19|20)\d{2})(?:\s*-\s*((?:19|20)?\d{2}))?\b")
ANIOS_2 = re.compile(r"(?<![\w.])(\d{2})\s?[-/]\s?(\d{2})(?!\w|\.\d)")
ROMANOS = {"II", "III", "IV", "VI", "VII"}


@dataclass(frozen=True)
class Vehiculo:
    armadora: str
    modelo: str
    motor: str
    anio_desde: int
    anio_hasta: int


def _anio(txt, ref=None):
    n = int(txt)
    if len(txt) == 4:
        return n
    if ref is not None and len(txt) == 2:  # "2009-18" → 2018, "1999-00" → 2000
        n += (ref // 100) * 100
        return n + 100 if n < ref - 50 else n
    return 1900 + n if n >= 30 else 2000 + n


def _anios(txt):
    m = ANIOS_PAR.search(txt) or ANIOS_4.search(txt) or ANIOS_2.search(txt)
    if not m:
        return None
    desde = _anio(m.group(1))
    hasta = _anio(m.group(2), ref=desde if len(m.group(1)) == 4 else None) if m.group(2) else desde
    if hasta < desde:  # "2025-22": algunas listas ponen el rango al revés
        desde, hasta = hasta, desde
    if not 1950 <= desde <= 2035 or hasta > 2035:
        return None
    return desde, hasta, m


# Para buscar la armadora a media frase solo se usan nombres completos, no abreviaturas (CR, TY, GM).
NOMBRE_COMPLETO = re.compile(r"\b(" + "|".join(sorted((k for k, v in ARMADORAS.items()
                             if len(k) >= 4 and k.capitalize() in v), key=len, reverse=True)) + r")\b")
SEGUNDA_PALABRA = {"LAND": "ROVER", "ALFA": "ROMEO"}
OPCIONAL = {"MERCEDES": "BENZ", "MERCEDEZ": "BENZ"}
MOTOR_TRAS_ANIOS = re.compile(
    r"(?:[\s,]*(?:[LV]\s?\d{1,2}|\d\s?L|\d\.\d\s?(?:L|LTS|T|H)?|TDI|TSI|TFSI|TURBO|DIESEL)\b\.?)*")


def _armadora(seg):
    seg = re.sub(r"^\s*\([^)]*\)\.?", "", seg)  # "(CARTUCHO). MERCEDES BENZ ..."
    tok = re.match(r"\s*([A-Z]+)\.?(?:\s+|$)", seg)
    if tok and tok.group(1) in ARMADORAS:
        resto = seg[tok.end():]
        segunda = SEGUNDA_PALABRA.get(tok.group(1))
        if segunda:
            if not resto.startswith(segunda):  # "LAND CRUISER" es modelo de Toyota, no Land Rover
                return None, seg
            resto = resto[len(segunda):].lstrip(" .")
        opcional = OPCIONAL.get(tok.group(1))
        if opcional and resto.startswith(opcional):
            resto = resto[len(opcional):].lstrip(" .")
        return ARMADORAS[tok.group(1)], resto
    return None, seg


def _nombre(modelo):
    """TSURU III → Tsuru III; SX-4, CR-V, F450 se quedan en mayúsculas."""
    palabras = []
    for p in modelo.split():
        if (p in ROMANOS or (len(p) <= 2 and p not in {"UP", "DE"}) or re.search(r"[\d\-]", p)
                or not re.search(r"[AEIOU]", p)):
            palabras.append(p)
        else:
            palabras.append(p.capitalize())
    return " ".join(palabras)


def _motor(txt):
    partes = []
    c = CIL_RE.search(txt)
    if c:
        cil = re.sub(r"\s|\.", "", c.group(0))
        m = re.fullmatch(r"(\d)(?:L|CIL)", cil)
        partes.append(f"L{m.group(1)}" if m else cil)
    litros = list(dict.fromkeys(DISP_RE.findall(txt)))  # "2.2 - 2.4" → 2.2/2.4 L
    if litros:
        partes.append("/".join(litros) + " L")
    return " ".join(partes)


def interpretar(texto):
    """Devuelve una lista de Vehiculo. Texto en mayúsculas o minúsculas."""
    if not texto:
        return []
    texto = texto.upper().replace("\n", " ")
    # " - " separa modelos salvo en "2.2 - 2.4"; la coma separa salvo en "2.3, 2.5"
    segmentos = re.split(r"\s+-\s+(?!\d\.\d)|,(?!\s*\d)|;|//", texto)
    armadora = None
    resultado = []
    for i, seg in enumerate(segmentos):
        nueva, resto = _armadora(seg)
        if not nueva and not armadora and i == 0:
            # "CAJA AIRE AUX NISSAN TSURU III ...": la armadora no viene al inicio
            m = NOMBRE_COMPLETO.search(seg)
            if m:
                nueva, resto = _armadora(seg[m.start():])
        if nueva:
            armadora = nueva
        if not armadora:
            continue
        pendientes = resto.split(" / ")
        modelo = None
        while pendientes:
            var = pendientes.pop(0)
            otra, var = _armadora(var)  # "... / DODGE STRATUS L4 2.4L 1998-00"
            if otra:
                armadora, modelo = otra, None
            anios = _anios(var)
            corte = [m.start() for m in (CIL_RE.search(var), DISP_RE.search(var)) if m]
            if anios:
                corte.append(anios[2].start())
                # "STRATUS 01-06 CIRRUS 01-06": lo que sigue al rango puede ser otro modelo
                fin = anios[2].end()
                fin += MOTOR_TRAS_ANIOS.match(var, fin).end() - fin  # Motorfil: "CONTOUR 95-00 2.5L"
                sigue = var[fin:]
                if re.search(r"[A-Z]{2}", sigue) and _anios(sigue):
                    pendientes.insert(0, sigue)
                    var = var[:fin]
            cabeza = var[: min(corte)] if corte else var
            cabeza = re.sub(r"[^A-Z0-9 \-]", " ", cabeza).strip(" -")
            if cabeza:
                modelo = re.sub(r"\s+", " ", cabeza)
            if not anios or not modelo or len(modelo) > 40:
                continue
            resultado.append(Vehiculo(armadora, _nombre(modelo), _motor(var), anios[0], anios[1]))
    return list(dict.fromkeys(resultado))


# ---------------------------------------------------------------------------------------------
# Base de datos: guardar lo interpretado y buscar por vehículo
# ---------------------------------------------------------------------------------------------

DOS_PALABRAS = {"GRAND", "TOWN", "NEW", "TRANS", "CROWN", "EL", "PT", "SANTA", "PARK", "LAND", "MONTE",
                "RANGE", "PICK", "SUPER", "POLICE", "GRAN", "SEA", "LE", "KING"}


def modelo_base(modelo):
    """Tsuru III → Tsuru; Silverado 2500 → Silverado; Grand Caravan SE → Grand Caravan."""
    p = modelo.split()
    n = 2 if p and p[0].upper() in DOS_PALABRAS and len(p) > 1 else 1
    return " ".join(p[:n])[:40]


def generar(partidas):
    """Borra y vuelve a crear los vehículos de cada partida a partir de su texto de aplicación.
    Devuelve cuántos vehículos se crearon."""
    from .models import AplicacionProveedor

    partidas = list(partidas)
    AplicacionProveedor.objects.filter(partida__in=[p.pk for p in partidas]).delete()
    filas = [
        AplicacionProveedor(partida=p, armadora=v.armadora[:40], modelo=v.modelo[:60], modelo_base=modelo_base(v.modelo),
                            motor=v.motor[:40], anio_desde=v.anio_desde, anio_hasta=v.anio_hasta)
        for p in partidas if p.tipo != "OTRO"
        for v in interpretar(p.aplicacion)
    ]
    AplicacionProveedor.objects.bulk_create(filas, batch_size=1000)
    return len(filas)


AÑO_RE = re.compile(r"^(?:(19|20)\d{2}|\d{2})$")
LITROS_RE = re.compile(r"^\d\.\d$")


ALIAS_MOSTRADOR = {"VOCHO": ["VOLKSWAGEN", "SEDAN"], "BOCHO": ["VOLKSWAGEN", "SEDAN"],
                   "VOCHITO": ["VOLKSWAGEN", "SEDAN"]}


def leer_consulta(texto):
    """'tsuru 2003', 'vw jetta 04 2.0' → (palabras, año, litros). Las abreviaturas de armadora
    se cambian por el nombre completo (VW → VOLKSWAGEN) y los apodos por el modelo (VOCHO → SEDAN)."""
    palabras, anio, litros = [], None, None
    for t in (texto or "").upper().replace(",", " ").split():
        t = t.rstrip("L") if LITROS_RE.match(t.rstrip("L")) else t
        if anio is None and AÑO_RE.match(t):
            anio = _anio(t)
        elif litros is None and LITROS_RE.match(t):
            litros = t
        elif t in ALIAS_MOSTRADOR:
            palabras += ALIAS_MOSTRADOR[t]
        else:
            palabras.append(ARMADORAS.get(t, t).upper())
    return palabras, anio, litros


def buscar(texto):
    """Vehículos de las listas de proveedor que coinciden con la consulta. Cada palabra debe aparecer
    en la armadora o en el modelo; el año debe caer dentro del rango; los litros, en el motor."""
    from django.db.models import Q

    from .models import AplicacionProveedor

    palabras, anio, litros = leer_consulta(texto)
    base = AplicacionProveedor.objects.select_related("partida", "partida__proveedor")
    if anio:
        base = base.filter(anio_desde__lte=anio, anio_hasta__gte=anio)
    if litros:
        base = base.filter(motor__contains=litros)
    # Si nada coincide con todas las palabras, se sueltan las últimas ("jetta a4 2004": la lista
    # solo dice JETTA). Siempre queda al menos la primera palabra.
    for n in range(len(palabras), 0, -1):
        qs = base
        for p in palabras[:n]:
            qs = qs.filter(Q(armadora__icontains=p) | Q(modelo__icontains=p))
        if n == 1 or qs.exists():
            return qs
    return base


def piezas_para(texto):
    """Agrupa por tipo de filtro las partidas que le quedan al vehículo buscado, más baratas primero.
    Es lo que necesita el mostrador para armar Económico / Estándar / Premium."""
    grupos = {}
    for v in buscar(texto).order_by("partida__costo"):
        grupos.setdefault(v.partida.tipo, {}).setdefault(v.partida_id, v.partida)
    return {tipo: list(partidas.values()) for tipo, partidas in grupos.items()}


def clave_modelo(modelo):
    """X-TERRA, XTERRA y X TERRA son el mismo modelo para el selector."""
    return re.sub(r"[^A-Z0-9]", "", (modelo or "").upper())


def patron_modelo(modelo):
    """Expresión que reconoce el modelo con o sin guiones/espacios, como inicio del nombre."""
    return "^" + "[- ]?".join(map(re.escape, clave_modelo(modelo)))


def coincide(anio=None, armadora=None, modelo=None, litros=None, prefijo=""):
    """Filtro Q para un vehículo elegido en el selector. Sirve para AplicacionProveedor y para
    catalogo.Vehiculo (con prefijo, ej. "aplicaciones__vehiculo__"). El modelo es el modelo base
    (Tsuru incluye Tsuru II y Tsuru III). Si el texto no decía motor, la pieza se considera válida."""
    from django.db.models import Q

    f = lambda campo: prefijo + campo  # noqa: E731
    q = Q(**{f("anio_desde__lte"): anio, f("anio_hasta__gte"): anio}) if anio else Q()
    if armadora:
        q &= Q(**{f("armadora"): armadora})
    if modelo:
        q &= Q(**{f("modelo__iregex"): patron_modelo(modelo)})
    if litros:
        q &= Q(**{f("motor__contains"): litros}) | Q(**{f("motor"): ""})
    return q


def opciones(anio=None, armadora=None, modelo=None):
    """Opciones de un paso del selector de vehículo, las más comunes primero: sin armadora da las
    armadoras; con armadora, los modelos; con modelo, los litros del motor. El año es opcional.
    Devuelve [(valor, partidas que le quedan), ...]."""
    from django.db.models import Count

    from .models import AplicacionProveedor

    qs = AplicacionProveedor.objects.filter(coincide(anio, armadora, modelo))
    if armadora and modelo:  # último paso: litros del motor ("1.6"), sin contar textos sin motor
        cuenta = {}
        for motor, partida in qs.exclude(motor="").values_list("motor", "partida_id").distinct():
            for litros in DISP_RE.findall(motor):
                cuenta.setdefault(litros, set()).add(partida)
        return sorted(((k, len(v)) for k, v in cuenta.items()), key=lambda x: (-x[1], x[0]))
    if armadora:  # modelos: se juntan las variantes de escritura (X-TERRA / XTERRA) bajo la más común
        grupos = {}
        for base, partida in qs.values_list("modelo_base", "partida_id").distinct():
            g = grupos.setdefault(clave_modelo(base), {"nombres": {}, "partidas": set()})
            g["nombres"][base] = g["nombres"].get(base, 0) + 1
            g["partidas"].add(partida)
        res = [(max(g["nombres"], key=g["nombres"].get), len(g["partidas"])) for g in grupos.values()]
        return sorted(res, key=lambda x: (-x[1], x[0]))
    filas = qs.values("armadora").annotate(n=Count("partida", distinct=True)).order_by("-n", "armadora")
    return [(f["armadora"], f["n"]) for f in filas]


def anios_de(armadora, hasta=None):
    """Años en que hay piezas para la armadora, del más nuevo al más viejo (une los rangos de la lista)."""
    from datetime import date

    from .models import AplicacionProveedor

    hasta = hasta or date.today().year + 1
    anios = set()
    for desde, fin in (AplicacionProveedor.objects.filter(armadora=armadora)
                       .values_list("anio_desde", "anio_hasta").distinct()):
        anios.update(range(desde, min(fin, hasta) + 1))
    return sorted(anios, reverse=True)


def selector(armadora="", anio=None, modelo=""):
    """Las cuatro listas del mostrador, en orden marca › año › modelo › motor. Cada lista sale
    filtrada por lo elegido antes; mientras falte un paso anterior, la lista va vacía."""
    res = {"marcas": opciones(), "anios": [], "modelos": [], "motores": []}
    if armadora:
        res["anios"] = anios_de(armadora)
        if anio:
            res["modelos"] = sorted(opciones(anio, armadora), key=lambda x: x[0].lower())
            if modelo:
                res["motores"] = opciones(anio, armadora, modelo)
    return res
