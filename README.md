# ERP Refaccionaria

ERP a la medida para refaccionaria de afinación, lubricantes y químicos automotrices. Una sucursal, web (Django + PostgreSQL).

## Avance

| Fase | Contenido | Estado |
|---|---|---|
| Mes 1 | Catálogo (equivalencias, aplicaciones vehiculares, kits, unidades), inventario con lote/caducidad FEFO y costo promedio, compras con recepción parcial | Modelo de datos y reglas listos |
| Mes 2 | Punto de venta, clientes, crédito a talleres, corte de caja | Reglas de venta y pantalla de mostrador listas |
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

## Probarlo en la nube (Render, plan gratis)

1. En render.com, entrar con la cuenta de GitHub.
2. New › Blueprint › repositorio `erp-refaccionaria` (rama a probar). Render lee `render.yaml`: crea la base PostgreSQL y el servicio web.
3. Llenar `DJANGO_SUPERUSER_USERNAME`, `DJANGO_SUPERUSER_PASSWORD` y `NEGOCIO_NOMBRE`, y aplicar. `build.sh` instala, migra y crea el administrador la primera vez.
4. Abrir la dirección que da Render, entrar, y en Administración › Partidas de lista de proveedor › "Importar lista (Excel)" subir los archivos de MORSA.

En el plan gratis el servicio se duerme tras 15 minutos sin uso (tarda cerca de un minuto en despertar) y la base gratuita vence a los 30 días; para operar se pasa a los planes de pago (~20-25 USD/mes).

## Listas de precios de proveedor

```bash
python manage.py importar_lista_proveedor MORSA "ruta/a/la/carpeta" --fecha 2026-11-13
```

También desde el admin con "Importar lista (Excel)", subiendo uno o varios archivos. Carga cada fila como partida del proveedor (código, tipo, marca, aplicación, costo sin IVA, equivalencia). No crea productos: desde el admin, en "Partidas de lista de proveedor", se buscan por código, equivalencia o aplicación (ej. `tsuru 1.6`) y la acción "Agregar a mi catálogo" las da de alta como producto con su costo de proveedor. Volver a importar actualiza costos sin duplicar.

### Vehículos por año

Al importar, el texto de aplicación de cada partida se interpreta en vehículos con armadora, modelo, motor y rango de años (`compras/aplicaciones.py`). "NISSAN. TSURU L4 1.6 (90-04) - SENTRA L4 1.6 (97-00)" queda como dos vehículos, cada uno con su rango. Entiende los formatos de Gonher, Fram, LTH, Autoprime y Motorfil. Con las listas MORSA del 13 nov 2026 saca vehículo del 86% de los filtros (21,691 vehículos); lo que no entiende son sobre todo textos genéricos ("FILTRO PARA AIRE") y equipo pesado, y esas partidas se siguen encontrando por texto.

En el admin, si la búsqueda trae año se busca por vehículo: `tsuru 2003`, `vw jetta 04 2.0`, `vocho 95`. El rango manda: con la lista actual a un Tsuru 2015 solo le salen los filtros cuyo texto cubre 2015. Al agregar una partida a mi catálogo, sus vehículos se copian a las aplicaciones del producto. Para volver a interpretar todo después de mejorar el intérprete:

```bash
python manage.py interpretar_aplicaciones
```

## Ventas (app `ventas`)

`ventas/servicios.py` concentra las reglas: `abrir_turno`, `registrar_venta` (precio según la lista del cliente, salida de inventario con conversión de unidades y FEFO, todo o nada), `cancelar_venta` (regresa al mismo lote y costo), `registrar_abono` y `cerrar_turno` (esperado contra contado por forma de pago). El crédito se valida contra días y límite del cliente. Cada producto lleva un nivel (Económico, Estándar, Premium) para ofrecerlo en mostrador.

## Mostrador

`python manage.py runserver` y entrar en http://127.0.0.1:8000/ con un usuario del sistema. Diseño «Pista» (`ventas/static/ventas/erp.css`):

1. El vehículo se elige con cuatro listas desplegables: marca › año › modelo › motor. Cada una sale filtrada por la anterior y se llena sin recargar la página; si el modelo trae un solo motor, pasa directo a las piezas. Aparte hay un buscador libre ("tsuru 2003", "vw jetta 04 2.0", "vocho 95") y la lista de vehículos recientes.
2. Arriba sale la afinación completa de cada nivel (Económico, Estándar, Premium), que se lleva entera al ticket con un toque. Abajo, una tarjeta por pieza (filtros de aceite, aire y gasolina, bujías, aceite de motor) con sus tres opciones. Primero van mis productos por su nivel; si un nivel queda vacío se llena con la lista del proveedor, repartida por tercios de costo. Cada opción dice de dónde sale que le queda ("Le queda: Tsuru 1990–2004") y si hay en anaquel.
3. Lo que no hay va "sobre pedido" con precio sugerido de costo + 37% + IVA (`SOBREPRECIO_PEDIDO`).
4. El total, la forma de pago y Cobrar van en una barra fija abajo. Al cobrar se registra la venta de lo que hay (necesita caja abierta) y lo sobre pedido queda en una orden de compra de emergencia en borrador, con el cliente y el vehículo en las notas; no se cobra ese día. La pieza se da de alta en mi catálogo con el nivel y el precio cotizados.
5. Arriba de las piezas hay un campo de código que funciona como buscador: mientras se teclea sugiere códigos (empiezan con, contienen, equivalencias y por descripción o aplicación) con marca, existencia y precio. Todo va con Enter: en Código elige (el código exacto, la única sugerencia o la marcada con ↑ ↓) y pasa a Cantidad; Enter en Cantidad pasa a Precio, que ya trae el de lista seleccionado para teclear encima; Enter en Precio agrega. Lo agregado se confirma en la misma franja y sale arriba y resaltado en el ticket. Sin vehículo elegido, el ticket ocupa todo el ancho como lista (código, descripción, cantidad y precio, importe). Cambiar cantidad, precio, cliente o quitar, y tocar piezas en la hoja del vehículo, se guarda sin recargar la página: la pantalla se queda donde estaba. Junto al código van Cantidad y Precio c/u con IVA (vacío = el de la lista); el precio también se cambia en cada renglón del ticket. Abajo del costo solo un administrador puede dejar el precio, y el aviso no muestra el costo. F2 regresa al campo desde cualquier parte.
6. Al cobrar aparece «Imprimir remisión» en hoja carta o ticket de 80 mm: folio, cliente, vehículo, partidas, IVA, crédito y vencimiento, lo que quedó sobre pedido y firma de recibido. No es comprobante fiscal. En «Remisiones» se busca por folio o cliente para reimprimir. RFC, dirección y teléfono del negocio salen de `NEGOCIO_RFC`, `NEGOCIO_DIRECCION` y `NEGOCIO_TELEFONO` si están definidos.
7. En Caja se abre el turno con el fondo y se hace el corte contando a ciegas por forma de pago; el sistema muestra la diferencia contra lo esperado.

Bujías y aceite de motor no vienen en las listas de filtros de MORSA: aparecen cuando se dan de alta en el catálogo (familias "Bujías" y "Aceites de motor") con su nivel; las bujías además con su aplicación.

## Reglas clave

- Todo cambio de existencia pasa por `inventario/servicios.py` y queda en el kardex (`Movimiento`), que no se edita a mano.
- El inventario se lleva en la unidad base del producto; compras y ventas en otras unidades se convierten (1 tambor = 208 L).
- Químicos con caducidad exigen lote y fecha; las salidas toman primero el lote que caduca antes (FEFO).
