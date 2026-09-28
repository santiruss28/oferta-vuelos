# oferta-vuelos

Seguimiento semanal de precios de vuelos **Buenos Aires (EZE/AEP) → Europa**, ida y vuelta,
estadía de 13 a 16 días (ideal 14), salida desde el **01/02/2027**.

- **Destinos principales:** París (PAR: CDG/ORY), Roma (ROM: FCO/CIA), Milán (MIL: MXP/LIN/BGY).
- **Alternativas:** entrar por Madrid (MAD) o Barcelona (BCN) y conectar en low-cost a Francia/Italia.
- **Referencia de mercado:** USD 900–1.500 en temporada baja. Mejor tarifa real vista: París
  USD 726 (Air France, 24/04–08/05/2027, vista el 07/08/2026).

Lo ejecuta una rutina programada de Claude Code una vez por semana (ver [ROUTINE.md](ROUTINE.md)).
El resultado de cada corrida es **un mail semanal** que llega vía n8n.

## Principio de diseño

- **El dato vive en CSV** (`data/`) y **el cálculo en Python** (`src/`).
- **El mail es el reporte**: lo arma `src/reporte.py` desde cero en cada corrida (HTML con tablas
  y estilos inline, sin gráficos, sin Excel, sin fórmulas).
- **Las alertas las decide un script** con reglas fijas y tests (`src/alertas.py` +
  `config/alertas.yaml`), nunca el modelo. El modelo sólo busca precios con evidencia literal.

## Estructura

```
config/
  rutas.yaml              aeropuertos, categoría, conexión low-cost i/v, fuente del índice, reglas del viaje
  alertas.yaml            umbrales, reglas, anti-spam, reintentos del webhook
data/
  observaciones.csv       fuente de verdad, append-only
  fx.csv                  tipo de cambio ARS/USD por corrida (fecha, valor, método, fuente)
  corridas.csv            una fila por corrida: rutas con/sin datos, fuentes caídas, filas agregadas/rechazadas
  alertas_emitidas.csv    estado anti-spam
  notificaciones.csv      log de cada envío al webhook (ok/error, status HTTP; nunca el token)
  notificaciones_pendientes.jsonl  mails que fallaron y se reenvían en la próxima corrida
  entrada/AAAA-MM-DD.json lo que la rutina encontró cada semana (auditoría)
  legado/historial-precios-consolidado.xlsx  Excel original de la carga inicial
docs/entrada-ejemplo.json formato de la entrada semanal
reports/
  alertas_AAAA-MM-DD.json alertas de cada corrida (con las suprimidas y el resultado del mail)
  rechazados_AAAA-MM-DD.csv filas rechazadas por validar.py, con el motivo
src/
  comun.py                paths, esquemas y utilidades
  importar_excel.py       carga inicial desde el Excel
  validar.py              esquema, reglas de negocio y evidencia
  agregar.py              JSON de observaciones nuevas → valida → ARS→USD → append
  control.py              estadísticas y cartas de control
  alertas.py              reglas → reports/alertas_AAAA-MM-DD.json
  contexto.py             contexto de precio de una tarifa (¿está barata?) y veredicto
  evaluar.py              CLI: evalúa una tarifa suelta contra el historial, sin guardar nada
  reporte.py              HTML + texto del mail semanal
  notificar.py            POST al webhook de n8n (reintentos, pendientes, prueba)
  corrida.py              orquesta la corrida completa en orden fijo
tests/                    pytest con datos sintéticos
```

## Uso

```bash
pip install -r requirements.txt
python -m pytest -q                                          # tests

python src/corrida.py --entrada data/entrada/2026-10-05.json # corrida semanal completa
python src/corrida.py --entrada ... --sin-notificar --vista-previa /tmp/mail.html   # ver el mail sin mandarlo
python src/evaluar.py PAR 790 2027-03-03 2027-03-17          # ¿está barata esta tarifa?
python src/notificar.py --prueba                             # probar la integración con n8n
python src/notificar.py --pendientes                         # reenviar sólo lo pendiente
python src/importar_excel.py data/legado/historial-precios-consolidado.xlsx  # carga inicial (ya hecha)
```

## Datos

### `data/observaciones.csv`

| Campo | Descripción |
|---|---|
| `fecha_busqueda`, `semana_iso` | cuándo se vio el precio (semana ISO, p. ej. `2026-W41`) |
| `ruta`, `categoria` | `PAR`/`ROM`/`MIL` (principal) o `MAD`/`BCN` (alternativa) |
| `serie` | `indice` (precio "desde" del mes) o `fechada` (ida y vuelta concretas) |
| `origen`, `destino`, `aerolinea`, `escalas` | |
| `fecha_ida`, `fecha_vuelta`, `dias_estadia` | |
| `precio_moneda_original`, `moneda`, `precio_usd`, `fx_usado` | lo que muestra la página y su conversión |
| `incluye_valija`, `precio_con_valija_usd`, `valija_estimada` | si no viene, se estima con `recargo_valija_usd` (70) |
| `costo_conexion_usd` | sólo alternativas (config: 120 i/v) |
| `precio_total_usd` | `precio_con_valija_usd + costo_conexion_usd`: **todo se compara sobre este valor** |
| `fuente`, `url`, `evidencia`, `notas` | `evidencia` = fragmento literal de la página con fechas y precio |
| `origen_dato` | `rutina` o `importado_sin_evidencia` |

**Dos series por ruta:**

- `indice`: precio "desde" de febrero y marzo 2027, **siempre de la misma fuente** (Turismocity,
  configurable en `rutas.yaml`). Sirve para ver la tendencia del mercado. Nunca dispara compras.
- `fechada`: ida y vuelta concretas de 13–16 días. Es **la única** que puede disparar COMPRAR/OPORTUNIDAD.

### Validación (`validar.py`)

Una `fechada` se rechaza si: falta evidencia; las fechas o el precio no aparecen dentro de la
evidencia (acepta `24/04`, `24 abr`, `24 de abril`, `Apr 24`, `US$ 1.020`, `$ 1.358.227`…);
la estadía no es de 13–16 días; la ida es anterior al 01/02/2027; el precio es ≤ 0 o > 5.000.
Un `indice` se rechaza si no viene de la fuente configurada o no es de feb/mar 2027.
También se rechazan duplicados (serie, ruta, aerolínea, fechas, fuente y fecha de búsqueda).
Todo lo rechazado va a `reports/rechazados_AAAA-MM-DD.csv` con el motivo.

### Carga inicial

`historial-precios-consolidado.xlsx` (hoja Historial) → 35 filas, todas con
`origen_dato=importado_sin_evidencia` (cuentan para el historial, **no disparan alertas**):

- "Fechada 13–16 días" → `serie=fechada` (5 filas; exceptuadas sólo de la regla de evidencia).
- "Referencia" → `serie=indice`. Las que no son de Turismocity, y los multitramo de Turismocity
  (BUE-ROM / MAD-BUE, que no son comparables con el "desde" simple), llevan la nota
  "fuente no homogénea" y quedan fuera de rachas y límites del índice.
- "Con equipaje: USD X" → `precio_con_valija_usd` (estimada si la nota dice "Estimado").
- "ARS 1.358.227" / "ARS 1,46M" en las notas → precio original en ARS y tipo de cambio implícito.
- Semanas faltantes registradas en `corridas.csv`: 10/08 y 07/09/2026.

## Carta de control (`control.py`)

Por ruta y por serie, sobre el **mejor `precio_total_usd` de cada semana ISO**:
mínimo, promedio, mediana, desvío, último, n de semanas, media móvil de 4 semanas y racha de
subas/bajas. Límites **μ ± 2σ sólo con ≥ 6 semanas**; antes se muestran la banda 900–1.500 y el
umbral fijo. Las semanas sin dato quedan vacías (no se interpola) y se listan en el mail.

## Alertas (`alertas.py` + `config/alertas.yaml`)

Umbrales fijos sobre el total con valija: **PAR 800 · ROM 850 · MIL 850 · MAD/BCN 850** (con conexión).

| Nivel | Regla | Condición |
|---|---|---|
| COMPRAR | `R1_umbral` | fechada ≤ umbral fijo de la ruta |
| COMPRAR | `R2_minimo_bajo_lcl` | fechada que es nuevo mínimo histórico y está bajo el LCL (n ≥ 6) |
| OPORTUNIDAD | `R3_cerca_umbral` / `R3_percentil` | fechada ≤ umbral × 1,08, o ≤ percentil 20 de la ruta (n ≥ 4) |
| OPORTUNIDAD | `R4_alternativa` | alternativa ≥ USD 60 más barata que la mejor principal de la semana |
| ATENCIÓN | `R5_indice_baja` / `R5_indice_sube` | índice baja 3 semanas seguidas, o sube > 10% vs su media de 4 semanas |
| ATENCIÓN | `R6_ventana_cierra` | faltan ≤ 120 días para la ida más temprana y el índice sube |
| INFO | `R7_*` | ruta sin datos, fuente caída, filas rechazadas, semanas faltantes, mail no enviado |

Sólo se evalúan las fechadas de la semana de la corrida. **Anti-spam:** la misma alerta
(ruta + regla + fechas) no se repite dentro de 14 días salvo que el precio baje ≥ 3%.
Una tarifa ya avisada que sigue vigente aparece en el mail como "Siguen vigentes (ya avisadas)" y
su fila sigue resaltada.

## ¿Está barata? Contexto de precio

Cada tarifa destacada en el mail trae un bloque **"¿Está barata? Contexto"**, y el mismo análisis
se puede pedir para cualquier tarifa que encuentres por tu cuenta con `src/evaluar.py`:

1. **Posición histórica**: qué % de las fechadas vistas para la ruta eran más caras.
2. **Mínimo histórico y umbral**: a cuánto está de cada uno.
3. **Carta de control**: debajo/dentro/encima de los límites (o de la banda 900–1.500 si todavía
   no hay 6 semanas).
4. **Momento del mercado**: tendencia del índice de Turismocity (bajando → esperar puede convenir;
   subiendo → conviene decidir).
5. **Días para la ida**.
6. **Precio real**: valija (real o estimada) y conexión low-cost incluidas.
7. **Alternativas**: la mejor MAD/BCN de la semana (o la mejor principal, si evaluás una alternativa).
8. **Confiabilidad**: baja (< 4 semanas), media (4–5) o alta (≥ 6). Con confiabilidad baja,
   manda el umbral fijo.

```text
$ python src/evaluar.py MAD 1300000 2027-02-11 2027-02-25 --moneda ARS --fx 1480
⚪ PRECIO NORMAL  MAD · total USD 1.068
   (tarifa USD 878 · con valija 948 (estimada) · + conexión 120)
 · No es más barata que la única tarifa fechada vista para Madrid (1 semana con datos).
 · USD 218 sobre el umbral de compra (USD 850); USD 6 sobre el mínimo histórico (USD 1.062).
 · ...
```

Veredictos: **COMPRAR** y **OPORTUNIDAD** usan exactamente las reglas R1–R3 de las alertas
(hay un test que lo verifica); **CARO** = sobre el UCL, en el percentil 80 o más, o sobre
USD 1.500; **PRECIO NORMAL** = el resto. Opciones: `--moneda ARS --fx 1480`,
`--incluye-valija si|no`, `--con-valija 890`. No escribe nada en `data/`.

## Mail semanal vía n8n

Cada corrida hace **un POST** al webhook de n8n con el mail ya armado; n8n sólo lo envía.

1. **Arriba:** si hay COMPRAR (rojo) u OPORTUNIDAD (naranja), un bloque grande por tarifa con
   precio, ruta, fechas, aerolínea, desglose, link y su contexto de precio (ver arriba).
2. Tarifas fechadas de la semana, con las filas baratas resaltadas y etiquetadas.
3. ATENCIÓN.
4. Carta de control de fechadas y del índice (tablas con ▲▼ y límites o banda).
5. INFO, semanas sin datos, tipo de cambio.

### Payload

```json
{
  "tipo": "semanal | prueba",
  "fecha_corrida": "AAAA-MM-DD",
  "nivel_max": "COMPRAR | OPORTUNIDAD | ATENCION | INFO",
  "asunto": "[COMPRAR] París USD 780 – 03/03→17/03/2027 – Air France",
  "alertas": [{"alert_id": "sha1(ruta+regla+ida+vuelta+precio)", "nivel": "...", "regla": "...",
               "ruta": "PAR", "aerolinea": "...", "fecha_ida": "...", "fecha_vuelta": "...", "dias": 14,
               "precio_usd": 0, "precio_con_valija_usd": 0, "valija_estimada": true,
               "costo_conexion_usd": 0, "precio_total_usd": 0, "umbral_usd": 800,
               "minimo_historico_usd": 0, "fuente": "...", "url": "...", "evidencia": "...",
               "mensaje": "..."}],
  "resumen_indice": [{"ruta": "PAR", "ultimo": 0, "media_4s": 0, "tendencia": "baja|sube|estable"}],
  "html": "<div>… cuerpo del mail listo …</div>",
  "texto": "versión en texto plano"
}
```

Sin tarifas para comprar, el asunto es
`[Semanal] Vuelos BUE → Europa – 05/10/2026 – sin tarifas para comprar`, con `[ATENCIÓN]` delante
si hubo alertas de ese nivel.

### Comportamiento

- Auth: header `X-Webhook-Token`. `Content-Type: application/json`. Timeout 15 s.
- 3 reintentos (2 s, 5 s, 10 s) ante error de red o 5xx. **No** reintenta ante 4xx.
- Si falla, el payload queda en `data/notificaciones_pendientes.jsonl` y se reenvía **al inicio de
  la próxima corrida**, antes del nuevo. Cada intento se registra en `data/notificaciones.csv`.
- Re-ejecutar la corrida del mismo día sin cambios no manda otro mail.
- Si faltan las variables de entorno, no falla: registra INFO "notificación no configurada".
- El token nunca se escribe en logs ni archivos.

### Variables de entorno

| Variable | Valor |
|---|---|
| `N8N_WEBHOOK_URL` | URL de producción del nodo Webhook de n8n |
| `N8N_WEBHOOK_TOKEN` | token largo y aleatorio (p. ej. `python -c "import secrets; print(secrets.token_urlsafe(32))"`) |

Se cargan en el **entorno de la rutina** de Claude Code (configuración del environment →
variables de entorno), nunca en el repo ni en el prompt. Para pruebas locales podés usar un
`.env` (está en `.gitignore`; ver `.env.example`).

### Workflow de n8n sugerido

1. **Webhook** — método `POST`, path `vuelos-bue-europa`, *Authentication: Header Auth* con
   nombre `X-Webhook-Token` y el mismo valor que `N8N_WEBHOOK_TOKEN`. *Respond: Immediately*
   (así el script recibe 200 aunque el mail tarde).
2. **Send Email** (Gmail o SMTP) — *To*: tu mail · *Subject*: `{{ $json.body.asunto }}` ·
   *Email Format*: HTML · *HTML*: `{{ $json.body.html }}` · *Text*: `{{ $json.body.texto }}`.
3. *(Opcional)* **IF** `{{ $json.body.nivel_max }}` = `COMPRAR` → un segundo canal (Telegram,
   push) con el asunto, para enterarte en el momento.

Activá el workflow (URL de producción, no la de test) y probalo con
`python src/notificar.py --prueba`: debería llegar un mail con asunto `[PRUEBA] …`.

## Pendientes para Santiago

1. Armar el workflow de n8n (arriba) y activarlo.
2. Cargar `N8N_WEBHOOK_URL` y `N8N_WEBHOOK_TOKEN` en el entorno de la rutina.
3. Probar: `python src/notificar.py --prueba`.
4. Crear la rutina semanal con el prompt de [ROUTINE.md](ROUTINE.md).
