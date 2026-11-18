from django.contrib import admin
from django.contrib.auth import views as auth
from django.http import HttpResponse
from django.urls import include, path


def salud(request):
    """Para el aviso que visita el sistema cada 10 minutos y no lo deja dormir en Render gratis: no toca la base."""
    return HttpResponse("ok", content_type="text/plain")


urlpatterns = [
    path("salud/", salud, name="salud"),
    path("admin/", admin.site.urls),
    path("entrar/", auth.LoginView.as_view(), name="entrar"),
    path("salir/", auth.LogoutView.as_view(), name="salir"),
    path("", include("ventas.urls")),
]
