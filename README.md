# ERP Refaccionaria

ERP a la medida para refaccionaria de afinación, lubricantes y químicos automotrices. Una sucursal, web (Django + PostgreSQL).

## Avance

| Fase | Contenido | Estado |
|---|---|---|
| Mes 1 | Catálogo (equivalencias, aplicaciones vehiculares, kits, unidades), inventario con lote/caducidad FEFO y costo promedio, compras con recepción parcial | Modelo de datos y reglas listos |
| Mes 2 | Punto de venta, clientes, crédito a talleres, corte de caja | Reglas de venta listas; falta la pantalla de mostrador |
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

### Vehículos por año

Al importar, el texto de aplicación de cada partida se interpreta en vehículos con armadora, modelo, motor y rango de años (`compras/aplicaciones.py`). "NISSAN. TSURU L4 1.6 (90-04) - SENTRA L4 1.6 (97-00)" queda como dos vehículos, cada uno con su rango. Entiende los formatos de Gonher, Fram, LTH, Autoprime y Motorfil. Con las listas MORSA del 13 nov 2026 saca vehículo del 86% de los filtros (21,691 vehículos); lo que no entiende son sobre todo textos genéricos ("FILTRO PARA AIRE") y equipo pesado, y esas partidas se siguen encontrando por texto.

En el admin, si la búsqueda trae año se busca por vehículo: `tsuru 2003`, `vw jetta 04 2.0`, `vocho 95`. El rango manda: con la lista actual a un Tsuru 2015 solo le salen los filtros cuyo texto cubre 2015. Al agregar una partida a mi catálogo, sus vehículos se copian a las aplicaciones del producto. Para volver a interpretar todo después de mejorar el intérprete:

```bash
python manage.py interpretar_aplicaciones
```

## Ventas (app `ventas`)

`ventas/servicios.py` concentra las reglas: `abrir_turno`, `registrar_venta` (precio según la lista del cliente, salida de inventario con conversión de unidades y FEFO, todo o nada), `cancelar_venta` (regresa al mismo lote y costo), `registrar_abono` y `cerrar_turno` (esperado contra contado por forma de pago). El crédito se valida contra días y límite del cliente. Cada producto lleva un nivel (Económico, Estándar, Premium) para ofrecerlo en mostrador.

## Reglas clave

- Todo cambio de existencia pasa por `inventario/servicios.py` y queda en el kardex (`Movimiento`), que no se edita a mano.
- El inventario se lleva en la unidad base del producto; compras y ventas en otras unidades se convierten (1 tambor = 208 L).
- Químicos con caducidad exigen lote y fecha; las salidas toman primero el lote que caduca antes (FEFO).
