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

### Correo con el PDF (opcional)

Sin estas variables el botón de correo usa el menú de compartir de la compu. Render gratis no deja salir correo por SMTP, así que hay dos caminos:

- **Render de pago y Gmail:** `EMAIL_HOST=smtp.gmail.com`, `EMAIL_HOST_USER` (la cuenta de Gmail) y `EMAIL_HOST_PASSWORD` (una contraseña de aplicación de Google, con verificación en dos pasos). Sale de esa cuenta y queda en Enviados.
- **Brevo por su API (sirve en Render gratis):** `BREVO_API_KEY` y `CORREO_REMITENTE` («Refaccionaria López <ventas@tudominio.mx>»). Brevo pide un dominio propio verificado; con un remitente @gmail o @outlook cambia la dirección y el correo suele caer en spam.

`NEGOCIO_CORREO` (opcional) es a dónde contesta el cliente.

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
8. Cada folio en Remisiones abre la venta: partidas, cobranza (total, abonado, saldo y vencimiento), reimpresión, abonos, devoluciones, garantías y cancelación. El abono necesita caja abierta y entra al corte de ese turno. Cancelar pide motivo y el NIP de un administrador, regresa el inventario y guarda quién, cuándo y por qué. Una venta de contado de un turno ya cerrado no se cancela (ese dinero ya se contó en su corte), ni una que ya tenga abonos o devoluciones: para esas está la devolución o la garantía. La pestaña «Por cobrar» deja solo lo que se debe, con las vencidas primero.
9. «+ Cliente nuevo» en el ticket da de alta al cliente (nombre, teléfono, RFC opcional y lista de precios) y lo deja elegido. Si ya existe uno con ese nombre se elige ese. Darle días y límite de crédito pide el NIP de un administrador. Al cobrar o poner en espera, el ticket vuelve a «Público en general».
10. «Poner en espera» aparta el ticket (cliente, vehículo y piezas) para atender a otro; se retoma con un toque desde el ticket, y si había otro ticket a medias ese se aparta en su lugar. «Cotizar» guarda una cotización con folio (COT-…) y abre la hoja para imprimir en carta o ticket; el ticket se queda como está. En Remisiones › Cotizaciones se busca y se pasa al ticket, respetando los precios cotizados mientras esté vigente (`COTIZACION_VIGENCIA_DIAS`, 7 por omisión).
11. Lo que queda sobre pedido se registra en «Pedidos» con un dato para avisarle al cliente (se captura en el ticket antes de cobrar). Ahí se ve en qué va cada uno (orden sin enviar, enviada y cuándo llega, o ya en anaquel), con lo que ya llegó primero y su número en el menú. «Cobrar y entregar» lo pasa al ticket con el precio que se le dijo y al cobrarlo queda entregado, sin volver a pedirlo. «Ya no lo quiere» lo cancela y, si la orden sigue en borrador, le quita esa pieza.
12. **NIP.** Cada persona tiene un NIP de 4 a 6 números que le asigna el administrador en Administración › NIPs (no se repite, no se muestra y se guarda cifrado; números repetidos o seguidos no se aceptan). Al cobrar, abonar, abrir o cerrar caja y cancelar un pedido sale un cuadro que pide el NIP de quien lo hace, y ese nombre queda en la venta («Atendió»), el abono o el turno; así varias personas pueden usar la misma sesión de caja. Cancelar una venta, registrar una devolución o garantía y dar crédito piden el NIP de un administrador. Cinco NIPs equivocados seguidos bloquean dos minutos. Hasta que el administrador asigne los NIPs no se puede cobrar.
13. **Devolución o garantía**, desde la venta y con NIP de administrador. Devolución: se eligen piezas y cantidades (no más de lo vendido menos lo ya devuelto); si la venta es a crédito primero baja el saldo y el resto se regresa en dinero desde la caja abierta, que sale en ese corte; la pieza vuelve al anaquel si está en buen estado. Garantía: sale otra pieza igual sin cobrar (movimiento «Salida por garantía») y la mala queda «por reclamar al proveedor»; en Remisiones › Devoluciones se marca si se reclamó, si el proveedor la repuso o la rechazó.
14. **Cifras con comas** en todo: pantallas, campos que se teclean (al salir del campo, 1500 → 1,500), mensajes y la Administración (columnas, consultas y campos). Se pueden teclear con o sin comas.
15. **Cliente: RFC y dirección.** Si el cliente no da RFC queda el genérico `XAXX010101000` (también los que ya estaban sin RFC). La dirección lleva código postal, colonia, municipio, estado, calle y números; al teclear el CP se llenan municipio y estado y la colonia se elige de la lista de ese CP («Otra» si no viene). Funciona en el alta rápida del ticket y en Administración › Clientes, y la dirección sale en la remisión y en la cotización.
16. **Enter pasa al siguiente campo** en los formularios que guardan (alta de cliente, abono, devolución, corte de caja, entrar y la Administración); en el último campo, Enter guarda. Shift+Enter regresa y Ctrl+Enter guarda desde cualquier campo. Las búsquedas y los campos sueltos siguen buscando o guardando con Enter, y la captura por código y el cobro conservan su propio flujo. Al pasar con Tab o Enter la página se mueve para que el campo no quede debajo de la barra de cobro, y el ticket deja de ir fijo cuando crece más que la pantalla.
17. **Remisiones › Nueva**, aparte del mostrador. El cliente se busca por su folio (`CTE-0012`, `cte12` o solo `12`), por su celular aunque esté guardado con espacios o guiones, o por nombre o RFC; se ve su RFC, dirección, teléfono y crédito disponible, y si no existe se da de alta ahí mismo. Lleva condiciones (contado o crédito, con su vencimiento), orden de compra del cliente, vehículo y observaciones. Las partidas se capturan por código con Enter (Código › Cantidad › Precio › Descuento › otra partida; Enter en un código vacío lleva a Emitir), con precio de la lista del cliente o tecleado, descuento por partida y existencia a la vista; si cambia el cliente, los precios de lista se recalculan. Se puede guardar como borrador (Remisiones › Borradores) o emitir con el NIP de quien la hace: toma folio `REM-000001` en su propia serie, sale del inventario y se imprime en carta con firma de recibido. A crédito pasa a Por cobrar; de contado queda «por cobrar en caja»: aparece en Caja (con su número en el menú) y en la venta, y quien cobra elige forma de pago, «Paga con» y cambio, con su NIP; entra al corte de ese turno. Solo lleva lo que está en catálogo; lo sobre pedido sigue por el mostrador. El folio de cliente se ve también en el ticket y en Administración › Clientes, donde se puede buscar.
18. **Clientes cuando son miles.** En el ticket ya no hay lista desplegable: el cliente se busca por folio, celular o nombre (F4 lleva ahí desde cualquier parte; con el campo vacío salen los últimos atendidos) y se elige con flechas y Enter; si hay uno solo, Enter lo elige de una vez. La búsqueda no distingue acentos, mayúsculas ni signos («ramirez» encuentra «Ramírez, S.A.», «442-123-4567» y «4421234567» son el mismo celular) y pone primero a los que más han comprado en seis meses. Al elegirlo se ve su folio, crédito disponible y si debe algo vencido (con algo vencido, venderle a crédito en el mostrador o en una remisión pide el NIP de un administrador, y la venta dice quién lo autorizó); «Público en general» regresa al cliente de mostrador de un toque. El mismo buscador está en Remisiones › Nueva, y las listas de remisiones, cotizaciones, pedidos, devoluciones y Administración › Clientes buscan igual. El alta rápida no duplica: si el nombre (escrito como sea), el celular o el RFC ya existen, elige al que ya estaba; en Administración un RFC (no genérico) no se puede repetir.
19. **Cada venta empieza por el cliente.** El cliente va arriba de todo en el mostrador, antes del vehículo y del código: en una venta nueva el cursor ya está en «¿Quién es el cliente?»; al elegirlo (o con Enter sin escribir, que deja Público en general) pasa al código. Ahí mismo están «Público en general» y el alta rápida, que ahora también pide el correo. El ticket solo dice para quién es, con «Cambiar». Después de cobrar o de poner en espera, el cursor regresa al cliente para la siguiente venta.
20. **Cotizaciones y tickets en PDF por WhatsApp o correo, sin salir de la página.** «Mandar por WhatsApp o correo» está en Cotizar (guarda la cotización), en «Venta registrada» después de cobrar, en Remisiones (cotizaciones y ventas), en la venta y en las hojas impresas. Abre una ventana encima con el PDF ya hecho en el servidor (`ventas/pdf.py`, ReportLab), el celular y el correo del cliente. **WhatsApp:** abre el menú de compartir del aparato (Windows, Mac, Android, iPhone) con el PDF adjunto y un mensaje corto; se elige WhatsApp y el contacto. WhatsApp no deja que un sistema mande un archivo directo a un número sin su API de Business. Donde el navegador no tiene ese menú (Firefox en la compu), descarga el PDF y abre WhatsApp en el chat del cliente para adjuntarlo. **Correo:** con cuenta configurada sale desde el servidor con el PDF adjunto, sin abrir nada, y puede guardar el correo en el cliente; sin cuenta, va por el mismo menú de compartir. Los enlaces de cotización que ya se mandaron antes siguen abriendo 60 días.
21. **La hoja de piezas no brinca.** Junto a la hoja, el ticket se queda fijo y nunca mide más que la pantalla; lo que no cabe se desplaza dentro de sus renglones. Antes, en pantallas de laptop, al elegir la segunda o tercera pieza el ticket se soltaba y la página subía al principio de la hoja.

Bujías y aceite de motor no vienen en las listas de filtros de MORSA: aparecen cuando se dan de alta en el catálogo (familias "Bujías" y "Aceites de motor") con su nivel; las bujías además con su aplicación.

## Códigos postales

`ventas/datos/codigos_postales.tsv.gz` es el Catálogo Nacional de Códigos Postales de Correos de México (SEPOMEX): 31,928 CP y 157,232 colonias, tomado del paquete [catalogmx](https://github.com/openbancor/catalogmx) (licencia BSD-2-Clause). `build.sh` lo carga con `python manage.py cargar_codigos_postales`, que no hace nada si ya está cargado. Para actualizarlo, descarga el catálogo en formato TXT desde la página de códigos postales de Correos de México y corre `python manage.py cargar_codigos_postales --archivo CPdescarga.txt`. Correos de México lo da para uso propio y no permite venderlo.

## Reglas clave

- Todo cambio de existencia pasa por `inventario/servicios.py` y queda en el kardex (`Movimiento`), que no se edita a mano.
- El inventario se lleva en la unidad base del producto; compras y ventas en otras unidades se convierten (1 tambor = 208 L).
- Químicos con caducidad exigen lote y fecha; las salidas toman primero el lote que caduca antes (FEFO).
