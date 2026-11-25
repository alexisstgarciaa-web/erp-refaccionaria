from django.conf import settings
from django.contrib import admin
from django.contrib.auth import views as auth
from django.http import HttpResponse
from django.urls import include, path
from django.utils.html import escape
from django.views.decorators.cache import cache_control
from django.views.generic import RedirectView


def salud(request):
    """Para el aviso que visita el sistema cada 10 minutos y no lo deja dormir en Render gratis: no toca la base."""
    return HttpResponse("ok", content_type="text/plain")


@cache_control(public=True, max_age=86400)
def icono(request):
    """El ícono de la pestaña del navegador: el sello rojo con la inicial del negocio, como en la barra."""
    inicial = escape((settings.NEGOCIO_NOMBRE.strip() or "R")[:1].upper())
    svg = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">'
           '<rect width="64" height="64" rx="12" fill="#E10600"/>'
           '<text x="32" y="47" text-anchor="middle" font-family="Archivo, Arial Black, Arial, Helvetica, sans-serif" '
           f'font-size="42" font-weight="800" font-style="italic" fill="#fff">{inicial}</text></svg>')
    return HttpResponse(svg, content_type="image/svg+xml")


urlpatterns = [
    path("salud/", salud, name="salud"),
    path("favicon.svg", icono, name="icono"),
    path("favicon.ico", RedirectView.as_view(url="/favicon.svg", permanent=True)),
    path("admin/", admin.site.urls),
    path("entrar/", auth.LoginView.as_view(), name="entrar"),
    path("salir/", auth.LogoutView.as_view(), name="salir"),
    path("", include("ventas.urls")),
]
