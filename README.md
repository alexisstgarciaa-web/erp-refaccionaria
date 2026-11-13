# ERP Refaccionaria

ERP a la medida para refaccionaria de afinación, lubricantes y químicos automotrices. Una sucursal, web (Django + PostgreSQL).

## Avance

| Fase | Contenido | Estado |
|---|---|---|
| Mes 1 | Catálogo (equivalencias, aplicaciones vehiculares, kits, unidades), inventario con lote/caducidad FEFO y costo promedio, compras con recepción parcial | Modelo de datos y reglas listos |
| Mes 2 | Punto de venta, clientes, crédito a talleres, corte de caja | Pendiente |
| Mes 3 | Reportes ABC, rotación, punto de reorden, caducidades; carga de catálogo; pruebas | Pendiente |
| Fase 2 | CFDI 4.0 vía PAC | Pendiente |

## Correr en local

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver   # http://127.0.0.1:8000/admin
python manage.py test
```

## Listas de precios de proveedor

```bash
python manage.py importar_lista_proveedor MORSA "ruta/a/la/carpeta" --fecha 2026-11-13
```

Carga cada fila como partida del proveedor (código, tipo, marca, aplicación, costo sin IVA, equivalencia). No crea productos: desde el admin, en "Partidas de lista de proveedor", se buscan por código, equivalencia o aplicación (ej. `tsuru 1.6`) y la acción "Agregar a mi catálogo" las da de alta como producto con su costo de proveedor. Volver a importar actualiza costos sin duplicar.

## Reglas clave

- Todo cambio de existencia pasa por `inventario/servicios.py` y queda en el kardex (`Movimiento`), que no se edita a mano.
- El inventario se lleva en la unidad base del producto; compras y ventas en otras unidades se convierten (1 tambor = 208 L).
- Químicos con caducidad exigen lote y fecha; las salidas toman primero el lote que caduca antes (FEFO).
