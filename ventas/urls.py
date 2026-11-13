from django.urls import path

from . import views

urlpatterns = [
    path("", views.mostrador, name="mostrador"),
    path("vehiculo/listas/", views.vehiculo_listas, name="vehiculo_listas"),
    path("ticket/agregar/", views.agregar, name="ticket_agregar"),
    path("ticket/afinacion/", views.agregar_afinacion, name="ticket_afinacion"),
    path("ticket/<int:i>/quitar/", views.quitar, name="ticket_quitar"),
    path("ticket/<int:i>/cantidad/", views.cantidad, name="ticket_cantidad"),
    path("ticket/cliente/", views.cliente, name="ticket_cliente"),
    path("ticket/cobrar/", views.cobrar, name="ticket_cobrar"),
    path("caja/", views.caja, name="caja"),
    path("estilo/<slug:clave>/", views.estilo, name="estilo"),
]
