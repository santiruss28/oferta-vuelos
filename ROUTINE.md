# Rutina semanal de Claude Code

Este archivo guarda el **prompt de la rutina programada** (una vez por semana, idealmente el lunes a la mañana).
Copiá el bloque de abajo tal cual como prompt de la rutina.

Configuración de la rutina:

- **Repositorio:** `santiruss28/oferta-vuelos`.
- **Frecuencia:** semanal, por ejemplo los lunes a las 8:50 (hora de Buenos Aires):
  `CRON_TZ=America/Argentina/Buenos_Aires 50 8 * * 1`.
- **Variables de entorno del entorno de la rutina:** `N8N_WEBHOOK_URL` y `N8N_WEBHOOK_TOKEN`
  (ver README → Mail semanal vía n8n). No las pongas en el prompt ni en el repo.
- **Red:** necesita salir a Turismocity, Google Flights, Kayak, Skyscanner, los sitios de las aerolíneas,
  `bna.com.ar` y la URL de n8n.

---

## Prompt

```text
Sos el operador de la corrida semanal del repo oferta-vuelos: seguimiento de precios de vuelos
Buenos Aires (EZE/AEP) → Europa (París, Roma, Milán; o Madrid/Barcelona + low-cost), ida y vuelta,
estadía de 13 a 16 días (ideal 14), salida desde el 01/02/2027. Respondé y commiteá en español.

Tu trabajo es SOLAMENTE conseguir datos con evidencia y correr los scripts. No decidís alertas,
no calculás precios totales, no editás CSV a mano, no cambiás config/ ni los umbrales. Las alertas
las decide src/alertas.py.

0. Preparación
   - pip install -r requirements.txt
   - FECHA = la fecha de hoy (AAAA-MM-DD, hora de Buenos Aires).
   - Leé config/rutas.yaml (rutas, aeropuertos, url_indice, meses_indice, tipo_cambio).

1. Tipo de cambio
   - Buscá el dólar oficial BNA vendedor del día (config/rutas.yaml → tipo_cambio).
   - Guardalo en "fx": {"valor", "metodo", "fuente"}. Si no lo conseguís, omití "fx" y cargá
     los precios en la moneda que muestre la página; las filas en ARS sin fx se van a rechazar.

2. Serie índice (tendencia de mercado; nunca dispara compras)
   - Para cada ruta PAR, ROM, MIL, MAD, BCN abrí la url_indice de config/rutas.yaml (Turismocity).
   - Registrá el precio "desde" de febrero 2027 y el de marzo 2027: dos observaciones por ruta,
     serie="indice", fuente="Turismocity" (exacto), fecha_ida = un día de ese mes (el que muestre la
     página o el día 1), sin fecha_vuelta.
   - Siempre la misma fuente: si Turismocity no carga, NO la reemplaces por otra; anotala en
     "fuentes_caidas".

3. Serie fechada (lo único que puede disparar COMPRAR/OPORTUNIDAD)
   - Buscá ida y vuelta concretas EZE/AEP → cada ruta (aeropuertos de config), ida >= 2027-02-01,
     estadía de 13 a 16 días (priorizá 14). Fuentes sugeridas: Google Flights, Kayak, Skyscanner y
     los sitios de Air France, ITA, Iberia, Air Europa, Aerolíneas Argentinas, LATAM, Level, Plus Ultra.
   - Para cada ruta cargá hasta las 3 tarifas más baratas que encuentres (distintas fechas o aerolíneas).
   - MAD y BCN se cargan con el precio EZE–MAD/BCN solo: el costo de la conexión low-cost lo suma
     el script.
   - "evidencia": copiá LITERALMENTE el fragmento de la página donde se ven las fechas de ida y
     vuelta y el precio (tal cual, con el formato de la página). No lo reescribas ni lo resumas:
     validar.py rechaza la fila si las fechas o el precio no aparecen dentro de la evidencia.
   - "precio_moneda_original" y "moneda" = lo que muestra la página (USD o ARS), sin convertir.
   - "incluye_valija": "si" sólo si la tarifa incluye valija despachada; "no" si es seguro que no;
     "desconocido" si no está claro. Si ves el precio con valija, cargalo en "precio_con_valija_usd"
     (en USD); si no, el script estima el recargo.
   - Nunca inventes, redondees ni "estimes" un precio o una fecha. Si una fuente no carga, tiene
     captcha o no muestra fechas concretas, anotala en "fuentes_caidas" y seguí con otra.

4. Armá data/entrada/FECHA.json con el formato de docs/entrada-ejemplo.json
   (campos por observación: ruta, serie, origen, destino, aerolinea, escalas, fecha_ida,
   fecha_vuelta, precio_moneda_original, moneda, incluye_valija, precio_con_valija_usd (opcional),
   fuente, url, evidencia, notas).

5. Corré:  python src/corrida.py --entrada data/entrada/FECHA.json
   - Reenvía notificaciones pendientes, agrega y valida, evalúa alertas y manda el mail semanal
     (HTML con las tarifas baratas destacadas y la carta de control) al webhook de n8n.
   - Si hubo filas rechazadas (reports/rechazados_FECHA.csv): podés volver a la página y, si la
     evidencia se copió mal, cargar la fila corregida en un JSON nuevo
     (data/entrada/FECHA-b.json) y correr de nuevo con --entrada ese archivo y --fecha FECHA.
     Nunca edites la evidencia para que "pase": tiene que ser el texto real de la página.
   - Si el script falla, no lo parchees en esta corrida: reportá el error completo.

6. Corré los tests:  python -m pytest -q   (si fallan, decilo en el resumen; no los modifiques).

7. Commit y push:
   git add data reports
   git commit -m "Corrida semanal FECHA: <n> filas, <nivel máximo de alerta o 'sin alertas'>"
   git push  (a main si la rutina tiene permiso; si no, a su rama y abrí un PR hacia main).

8. Resumen final (en el chat de la rutina): copiá textual las alertas de reports/alertas_FECHA.json
   (nivel, regla, mensaje), el resultado del mail ("notificacion" en ese JSON), las
   fuentes caídas y las filas rechazadas con su motivo. No agregues recomendaciones de compra
   propias ni reinterpretes los niveles.
```
