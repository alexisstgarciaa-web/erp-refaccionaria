from django.conf import settings

# Estilos a prueba: el dueño elige uno en su página y los otros se quitan.
# Cada uno: (nombre, fuentes de Google Fonts que usa).
ESTILOS = {
    "cristal": ("Cristal", "family=Geist:wght@400..700&family=Geist+Mono:wght@400..600"),
    "expresivo": ("Expresivo", "family=Bricolage+Grotesque:opsz,wght@12..96,500..800"
                               "&family=Onest:wght@400..700&family=Geist+Mono:wght@400..600"),
    "pista": ("Pista", "family=Archivo:ital,wdth,wght@0,62..125,400..900;1,62..125,400..900"
                       "&family=Geist+Mono:wght@400..600"),
}
ESTILO_DEFECTO = "cristal"


def estilo_actual(request):
    clave = getattr(request, "session", {}).get("estilo")
    return clave if clave in ESTILOS else ESTILO_DEFECTO


def negocio(request):
    clave = estilo_actual(request)
    nombre, fuentes = ESTILOS[clave]
    return {"negocio": getattr(settings, "NEGOCIO_NOMBRE", "Refaccionaria"),
            "estilo": {"clave": clave, "nombre": nombre, "fuentes": fuentes},
            "estilos": [(k, n) for k, (n, _) in ESTILOS.items()]}
