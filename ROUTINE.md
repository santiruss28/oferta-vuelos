# Rutina semanal de Claude Code

Este archivo guarda el **prompt de la rutina programada**. La rutina
("Vuelos BUE → Europa: corrida semanal") corre los lunes a las 8:50 (hora de Buenos Aires)
en una sesión nueva, y su prompt le indica seguir el bloque de abajo. Si cambiás este
archivo en `main`, la próxima corrida ya usa la versión nueva.

Configuración de la rutina (en claude.ai/code → Routines):

- **Repositorio:** `santiruss28/oferta-vuelos`.
- **Cron:** `CRON_TZ=America/Argentina/Buenos_Aires 50 8 * * 1`.
- **Variables de entorno del entorno:** `N8N_WEBHOOK_URL` y `N8N_WEBHOOK_TOKEN`
  (ver README → Mail semanal vía n8n). Nunca en el prompt ni en el repo.
- **Red:** `www.google.com` (Google Flights) y la URL de n8n.

---

## Prompt

```text
Sos el operador de la corrida semanal del repo oferta-vuelos: seguimiento de precios de vuelos
Buenos Aires (EZE/AEP) → Europa (París, Roma, Milán; o Madrid/Barcelona + low-cost), ida y vuelta
de 14 días con equipaje, ida en junio–julio 2027 (ventana y grilla en config/rutas.yaml).
Respondé y commiteá en español.

Los precios los busca un script (src/buscar.py), no vos. Tu trabajo es correr los scripts en
orden, revisar que no haya errores y reportar. No decidís alertas, no inventás ni transcribís
precios, no editás CSV a mano y no cambiás config/, src/ ni tests/.

0. Preparación
   - pip install -r requirements.txt
   - FECHA = la fecha de hoy en hora de Buenos Aires (AAAA-MM-DD).

1. Búsqueda (unos 10 minutos):
   python src/buscar.py --fecha FECHA
   Consulta Google Flights con un navegador headless para la grilla fija de config/rutas.yaml y
   escribe data/entrada/FECHA.json. Si TODAS las búsquedas vuelven sin resultados, corré una sola
   vez más el mismo comando; si sigue igual, seguí igual (la corrida registra las fuentes caídas).

2. Corrida (UNA sola vez):
   python src/corrida.py --entrada data/entrada/FECHA.json
   Reenvía notificaciones pendientes, agrega y valida, evalúa alertas y manda el mail semanal.
   Si el script falla, no lo parchees: reportá el error completo.

3. Tests:  python -m pytest -q   (si fallan, decilo en el resumen; no los modifiques).

4. Commit y push de data/ y reports/:
   git add data reports
   git commit -m "Corrida semanal FECHA: <n> filas, <nivel máximo de alerta o 'sin alertas'>"
   Pusheá a main si tenés permiso; si no, a tu rama y abrí un PR hacia main titulado
   "Corrida semanal FECHA".

5. Resumen final: copiá textual las alertas de reports/alertas_FECHA.json (nivel, regla,
   mensaje), el resultado del mail ("notificacion" en ese JSON), cuántas búsquedas devolvieron
   tarifas y cuáles no (fuentes caídas), y las filas rechazadas con su motivo. No agregues
   recomendaciones de compra propias ni reinterpretes los niveles.
```
