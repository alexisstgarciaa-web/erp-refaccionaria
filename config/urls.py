from django.contrib import admin
from django.contrib.auth import views as auth
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("entrar/", auth.LoginView.as_view(), name="entrar"),
    path("salir/", auth.LogoutView.as_view(), name="salir"),
    path("", include("ventas.urls")),
]
