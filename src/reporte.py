"""Arma el mail semanal (HTML + texto plano) desde los CSV y el JSON de alertas.

El mail es el reporte: no hay Excel, gráficos ni Markdown. Todo son tablas con estilos
inline (lo que Gmail y Outlook renderizan bien). Si hay una tarifa para COMPRAR u
OPORTUNIDAD, va destacada arriba de todo; en las tablas, las filas bajo el umbral o
cerca de él se resaltan con color y con etiqueta (nunca sólo color).

Uso (vista previa local, no se commitea):
    python src/reporte.py --fecha 2026-10-05 --salida /tmp/mail.html
"""
from __future__ import annotations

import argparse
import html
import json
import math
import sys
from datetime import date
from pathlib import Path

import control
from comun import (COLUMNAS_CORRIDAS, COLUMNAS_FX, CORRIDAS, DATA, ETIQUETA, FX,
                   IMPORTADO_SIN_EVIDENCIA, OBSERVACIONES, REPORTS, a_fecha,
                   cargar_alertas_cfg, cargar_rutas, fmt_fecha, leer_csv, leer_observaciones,
                   semana_iso)

# Colores: tinta de texto neutra; rojo/naranja/amarillo reservados para estados, siempre
# acompañados de su etiqueta.
TEXTO = "#1d1d1b"
TEXTO_2 = "#5f5e5a"
BORDE = "#e4e3df"
FONDO_TABLA = "#f6f5f2"
ESTADO = {
    "COMPRAR": {"fondo": "#fdecea", "borde": "#c0392b", "texto": "#8e1f14"},
    "OPORTUNIDAD": {"fondo": "#fff3e0", "borde": "#d9730d", "texto": "#8a4700"},
    "ATENCION": {"fondo": "#fdf8e1", "borde": "#b8930a", "texto": "#6b5500"},
}
FLECHA = {"sube": "▲ sube", "baja": "▼ baja", "estable": "▬ estable",
          "sin historia": "· sin historia", "sin datos": "–"}


# ---------------------------------------------------------------- formato

def _nan(x) -> bool:
    return x is None or (isinstance(x, float) and math.isnan(x)) or x == ""


def usd(x, prefijo=True) -> str:
    if _nan(x):
        return "–"
    s = f"{float(x):,.0f}".replace(",", ".")
    return f"USD {s}" if prefijo else s


def _e(x) -> str:
    return html.escape("" if _nan(x) else str(x))


def nombre(ruta: str, rutas_cfg: dict) -> str:
    return rutas_cfg["rutas"].get(ruta, {}).get("nombre", ruta).split(" (")[0]


def fechas_txt(ida, vuelta) -> str:
    i, v = a_fecha(ida), a_fecha(vuelta)
    if i and v:
        return f"{i:%d/%m}→{v:%d/%m/%Y}"
    return fmt_fecha(ida) or "–"


def estado_precio(total, umbral, factor) -> str | None:
    """COMPRAR si <= umbral; OPORTUNIDAD si <= umbral × factor; si no, None."""
    if _nan(total) or _nan(umbral):
        return None
    if total <= umbral:
        return "COMPRAR"
    if total <= umbral * factor:
        return "OPORTUNIDAD"
    return None


# ---------------------------------------------------------------- bloques HTML

def _th(txt, align="left"):
    return (f'<th style="text-align:{align};padding:6px 8px;font-size:12px;color:{TEXTO_2};'
            f'font-weight:600;border-bottom:1px solid {BORDE};background:{FONDO_TABLA}">{txt}</th>')


def _td(txt, align="left", extra=""):
    return (f'<td style="text-align:{align};padding:6px 8px;font-size:13px;color:{TEXTO};'
            f'border-bottom:1px solid {BORDE};{extra}">{txt}</td>')


def _tabla(encabezados: list[tuple[str, str]], filas: list[str]) -> str:
    cab = "".join(_th(t, a) for t, a in encabezados)
    return ('<table role="presentation" cellpadding="0" cellspacing="0" width="100%" '
            'style="border-collapse:collapse;margin:4px 0 16px">'
            f"<tr>{cab}</tr>{''.join(filas)}</table>")


def _titulo(txt: str) -> str:
    return f'<h2 style="font-size:16px;color:{TEXTO};margin:24px 0 6px">{txt}</h2>'


def _nota(txt: str) -> str:
    return f'<p style="font-size:12px;color:{TEXTO_2};margin:0 0 8px">{txt}</p>'


def _chip(nivel: str) -> str:
    c = ESTADO[nivel]
    return (f'<span style="display:inline-block;padding:1px 6px;border-radius:3px;font-size:11px;'
            f'font-weight:700;color:{c["texto"]};border:1px solid {c["borde"]}">'
            f'{ETIQUETA[nivel]}</span>')


def bloque_destacado(a: dict, rutas_cfg: dict) -> str:
    """Tarjeta grande para una alerta COMPRAR u OPORTUNIDAD."""
    c = ESTADO[a["nivel"]]
    total, umbral, minimo = a.get("precio_total_usd"), a.get("umbral_usd"), a.get("minimo_historico_usd")
    detalles = []
    if not _nan(umbral):
        dif = total - umbral
        detalles.append(f"{usd(abs(dif))} {'bajo' if dif <= 0 else 'sobre'} el umbral ({usd(umbral)})")
    if not _nan(minimo):
        detalles.append("nuevo mínimo histórico" if total < minimo
                        else f"mínimo histórico {usd(minimo)}")
    desglose = [f"tarifa {usd(a.get('precio_usd'))}"]
    if not _nan(a.get("precio_con_valija_usd")) and a.get("precio_con_valija_usd") != a.get("precio_usd"):
        desglose.append(f"con valija {usd(a['precio_con_valija_usd'])}"
                        + (" (estimada)" if a.get("valija_estimada") else ""))
    if not _nan(a.get("costo_conexion_usd")) and a.get("costo_conexion_usd"):
        desglose.append(f"+ conexión low-cost {usd(a['costo_conexion_usd'])}")
    link = (f' · <a href="{_e(a["url"])}" style="color:{c["texto"]};font-weight:700">Ver tarifa</a>'
            if a.get("url") else "")
    return (
        '<table role="presentation" cellpadding="0" cellspacing="0" width="100%" '
        f'style="border-collapse:collapse;margin:0 0 12px;background:{c["fondo"]};'
        f'border-left:6px solid {c["borde"]}"><tr><td style="padding:14px 16px">'
        f'<div style="font-size:12px;font-weight:700;letter-spacing:1px;color:{c["texto"]}">'
        f'{ETIQUETA[a["nivel"]]}</div>'
        f'<div style="font-size:28px;font-weight:700;color:{TEXTO};margin:2px 0">{usd(total)} '
        f'<span style="font-size:16px;font-weight:400">· {_e(nombre(a["ruta"], rutas_cfg))}</span></div>'
        f'<div style="font-size:14px;color:{TEXTO}">{fechas_txt(a["fecha_ida"], a["fecha_vuelta"])}'
        f' · {_e(a.get("dias"))} días · {_e(a.get("aerolinea") or "s/aerolínea")}</div>'
        f'<div style="font-size:13px;color:{TEXTO_2};margin-top:6px">{" · ".join(detalles)}</div>'
        f'<div style="font-size:12px;color:{TEXTO_2};margin-top:2px">{" · ".join(desglose)} · '
        f'{_e(a.get("fuente"))}{link}</div>'
        "</td></tr></table>")


def tabla_fechadas_semana(obs, sem, rutas_cfg, cfg) -> str:
    fact = cfg["reglas"]["r3_oportunidad"]["factor_umbral"]
    d = obs[(obs["semana_iso"] == sem) & (obs["serie"] == "fechada")].sort_values("precio_total_usd")
    if d.empty:
        return _nota("No hubo tarifas fechadas esta semana.")
    filas = []
    for r in d.itertuples():
        umbral = cfg["umbrales_usd"][r.ruta]
        est = estado_precio(r.precio_total_usd, umbral, fact)
        fondo = f"background:{ESTADO[est]['fondo']};" if est else ""
        dif = r.precio_total_usd - umbral
        vs = f"{'−' if dif < 0 else '+'}{usd(abs(dif), False)}"
        total = usd(r.precio_total_usd) + (" <small>(valija est.)</small>" if r.valija_estimada == "si" else "")
        fuente = (f'<a href="{_e(r.url)}" style="color:{TEXTO_2}">{_e(r.fuente)}</a>' if r.url
                  else _e(r.fuente))
        if r.origen_dato == IMPORTADO_SIN_EVIDENCIA:
            fuente += " <small>(sin evidencia)</small>"
        filas.append("<tr>" + "".join([
            _td(_e(nombre(r.ruta, rutas_cfg)) + (f" {_chip(est)}" if est else ""), extra=fondo),
            _td(fechas_txt(r.fecha_ida, r.fecha_vuelta), extra=fondo),
            _td(_e(int(r.dias_estadia)) if not _nan(r.dias_estadia) else "–", "right", fondo),
            _td(_e(r.aerolinea), extra=fondo),
            _td(total, "right", fondo + ("font-weight:700;" if est else "")),
            _td(vs, "right", fondo),
            _td(fuente, extra=fondo),
        ]) + "</tr>")
    return _tabla([("Ruta", "left"), ("Fechas", "left"), ("Días", "right"), ("Aerolínea", "left"),
                   ("Total", "right"), ("vs umbral", "right"), ("Fuente", "left")], filas)


def tabla_control(tabla, serie, rutas_cfg, cfg, banda) -> str:
    fact = cfg["reglas"]["r3_oportunidad"]["factor_umbral"]
    filas = []
    for r in tabla[tabla["serie"] == serie].itertuples():
        if r.n_semanas == 0:
            limites = "–"
        elif _nan(r.lcl):
            limites = f"banda {usd(banda[0], False)}–{usd(banda[1], False)}"
        else:
            limites = f"{usd(r.lcl, False)}–{usd(r.ucl, False)}"
        est = estado_precio(r.ultimo, r.umbral_usd, fact) if serie == "fechada" else None
        tend = FLECHA.get(r.tendencia, r.tendencia)
        if (r.tendencia == "sube" and r.racha > 0) or (r.tendencia == "baja" and r.racha < 0):
            tend += f" ({abs(r.racha)} sem. seguidas)"
        celdas = [_td(f"<b>{_e(r.ruta)}</b> {_e(nombre(r.ruta, rutas_cfg))}"),
                  _td(_e(r.n_semanas), "right"),
                  _td(usd(r.ultimo, False) + (f" {_chip(est)}" if est else ""), "right",
                      f"background:{ESTADO[est]['fondo']};font-weight:700;" if est else ""),
                  _td(usd(r.minimo, False), "right"), _td(usd(r.promedio, False), "right"),
                  _td(usd(r.mediana, False), "right"), _td(usd(r.desvio, False), "right"),
                  _td(usd(r.media_4s, False), "right"), _td(tend), _td(limites, "right")]
        if serie == "fechada":
            celdas.append(_td(usd(r.umbral_usd, False), "right"))
        filas.append("<tr>" + "".join(celdas) + "</tr>")
    enc = [("Ruta", "left"), ("n sem.", "right"), ("Último", "right"), ("Mín", "right"),
           ("Prom.", "right"), ("Mediana", "right"), ("Desvío", "right"), ("Media 4s", "right"),
           ("Tendencia", "left"), ("Límites", "right")]
    if serie == "fechada":
        enc.append(("Umbral", "right"))
    return _tabla(enc, filas)


def lista_alertas(alertas: list[dict], nivel: str) -> str:
    items = [a for a in alertas if a["nivel"] == nivel]
    if not items:
        return ""
    color = TEXTO if nivel == "ATENCION" else TEXTO_2
    lis = "".join(f'<li style="margin:2px 0">{_e(a["mensaje"])}</li>' for a in items)
    return f'<ul style="font-size:13px;color:{color};margin:4px 0 12px;padding-left:18px">{lis}</ul>'


# ---------------------------------------------------------------- armado

def datos(fecha: date, data_dir: Path = DATA, reports_dir: Path = REPORTS,
          config_dir: Path | None = None) -> dict:
    """Carga todo lo necesario para el mail."""
    rutas_cfg, cfg = cargar_rutas(config_dir), cargar_alertas_cfg(config_dir)
    obs = leer_observaciones(data_dir / OBSERVACIONES.name)
    corridas = leer_csv(data_dir / CORRIDAS.name, COLUMNAS_CORRIDAS)
    fx = leer_csv(data_dir / FX.name, COLUMNAS_FX).sort_values("fecha")
    semanas = control.rango_de(obs, fecha, corridas)
    res_path = reports_dir / f"alertas_{fecha.isoformat()}.json"
    return {
        "fecha": fecha, "rutas_cfg": rutas_cfg, "cfg": cfg, "obs": obs, "corridas": corridas,
        "fx": fx[fx["fecha"] <= fecha.isoformat()],
        "tabla": control.tabla_control(obs, semanas, rutas_cfg, cfg),
        "sin_datos": control.semanas_sin_datos(obs, semanas, corridas),
        "res": json.loads(res_path.read_text(encoding="utf-8")) if res_path.exists() else {},
    }


def armar_html(d: dict, prueba: bool = False) -> str:
    fecha, rutas_cfg, cfg, obs = d["fecha"], d["rutas_cfg"], d["cfg"], d["obs"]
    sem = semana_iso(fecha)
    alertas = d["res"].get("alertas", [])
    banda = rutas_cfg["viaje"]["banda_referencia_usd"]
    partes = [
        f'<div style="font-family:Arial,Helvetica,sans-serif;color:{TEXTO};max-width:760px">',
        f'<h1 style="font-size:20px;margin:0 0 2px">Vuelos BUE → Europa · {fmt_fecha(fecha)}</h1>',
        _nota(f"Semana {sem}. Ida y vuelta EZE/AEP → París, Roma, Milán (o Madrid/Barcelona + "
              "low-cost), 13–16 días, salida desde el 01/02/2027. Precios por persona; el "
              "<b>total</b> incluye valija (real o estimada) y, en alternativas, la conexión."),
    ]
    if prueba:
        partes.append(_nota("<b>Mensaje de prueba de la integración con n8n.</b> Si te llegó, el "
                            "webhook y el envío de mails funcionan."))

    destacadas = [a for a in alertas if a["nivel"] in ("COMPRAR", "OPORTUNIDAD")]
    if destacadas:
        partes += [bloque_destacado(a, rutas_cfg) for a in destacadas]
    else:
        partes.append(_nota("<b>Esta semana no hay tarifas para COMPRAR ni OPORTUNIDADES.</b>"))
    vigentes = [a for a in d["res"].get("suprimidas", []) if a["nivel"] in ("COMPRAR", "OPORTUNIDAD")]
    if vigentes:
        items = "".join(
            f'<li style="margin:2px 0">{_chip(a["nivel"])} {_e(a["mensaje"])} '
            f'<span style="color:{TEXTO_2}">({_e(a.get("motivo_supresion"))})</span></li>'
            for a in vigentes)
        partes += [f'<p style="font-size:13px;font-weight:700;margin:8px 0 2px">Siguen vigentes '
                   f'(ya avisadas)</p><ul style="font-size:13px;margin:0 0 12px;padding-left:18px">'
                   f'{items}</ul>']
    mv = cfg["mejor_tarifa_vista"]
    partes.append(_nota(
        f"Referencia a superar: {usd(mv['precio_usd'])} {_e(nombre(mv['ruta'], rutas_cfg))}, "
        f"{_e(mv['aerolinea'])}, {fechas_txt(mv['fecha_ida'], mv['fecha_vuelta'])} "
        f"(vista el {fmt_fecha(mv['fecha_busqueda'])}, sin valija). Umbrales COMPRAR: "
        + ", ".join(f"{r} {usd(v, False)}" for r, v in cfg["umbrales_usd"].items()) + "."))

    partes += [_titulo("Tarifas fechadas de esta semana"),
               tabla_fechadas_semana(obs, sem, rutas_cfg, cfg)]
    if any(a["nivel"] == "ATENCION" for a in alertas):
        partes += [_titulo("Atención"), lista_alertas(alertas, "ATENCION")]
    partes += [_titulo("Carta de control · fechadas 13–16 días"),
               _nota("Mejor total de cada semana. Límites μ ± 2σ con 6 semanas o más; antes se "
                     "muestra la banda de mercado. Las semanas sin dato no se interpolan."),
               tabla_control(d["tabla"], "fechada", rutas_cfg, cfg, banda),
               _titulo("Índice de mercado · Turismocity, “desde” feb–mar 2027"),
               _nota("Sólo tendencia: nunca dispara compras. Excluye fuentes no homogéneas."),
               tabla_control(d["tabla"], "indice", rutas_cfg, cfg, banda)]

    pie = []
    vacias = d["sin_datos"][d["sin_datos"]["sin_ningun_dato"] == "si"]
    if len(vacias):
        pie.append("Semanas sin ningún dato: " + ", ".join(
            f"{r.semana_iso} ({fmt_fecha(r.lunes)})" for r in vacias.itertuples()) + ".")
    hoy = d["corridas"][d["corridas"]["fecha"] == fecha.isoformat()]
    if len(hoy):
        c = hoy.iloc[-1]
        pie.append(f"Corrida: {c['filas_agregadas']} filas agregadas, {c['filas_rechazadas']} "
                   f"rechazadas; fuentes caídas: {_e(c['fuentes_caidas']) or 'ninguna'}.")
    if len(d["fx"]):
        u = d["fx"].iloc[-1]
        tc = f"{float(u['valor']):,.2f}".replace(",", "_").replace(".", ",").replace("_", ".")
        pie.append(f"Tipo de cambio: ARS {tc} por USD ({_e(u['metodo'])}, {fmt_fecha(u['fecha'])}).")
    partes += [_titulo("Info"), lista_alertas(alertas, "INFO"), *[_nota(p) for p in pie]]
    partes.append(_nota("Generado automáticamente por la rutina semanal desde los CSV del repo "
                        "oferta-vuelos. Las alertas las deciden reglas fijas (config/alertas.yaml)."))
    partes.append("</div>")
    return "".join(partes)


def armar_texto(d: dict, prueba: bool = False) -> str:
    """Versión en texto plano (clientes sin HTML y log de n8n)."""
    fecha, rutas_cfg = d["fecha"], d["rutas_cfg"]
    alertas = d["res"].get("alertas", [])
    L = [f"Vuelos BUE → Europa · {fmt_fecha(fecha)} ({semana_iso(fecha)})", ""]
    if prueba:
        L += ["Mensaje de prueba de la integración con n8n.", ""]
    for nivel in ("COMPRAR", "OPORTUNIDAD", "ATENCION"):
        for a in (x for x in alertas if x["nivel"] == nivel):
            L.append(f"[{ETIQUETA[nivel]}] {a['mensaje']}" + (f" {a['url']}" if a.get("url") else ""))
    if not any(a["nivel"] in ("COMPRAR", "OPORTUNIDAD") for a in alertas):
        L.append("Sin tarifas para COMPRAR ni OPORTUNIDADES esta semana.")
    L += ["", "Carta de control (fechadas): ruta · último · mín · media 4s · tendencia"]
    for r in d["tabla"][d["tabla"]["serie"] == "fechada"].itertuples():
        L.append(f"  {r.ruta} ({nombre(r.ruta, rutas_cfg)}): {usd(r.ultimo)} · {usd(r.minimo)} · "
                 f"{usd(r.media_4s)} · {r.tendencia}")
    L += ["", "Índice (Turismocity): ruta · último · media 4s · tendencia"]
    for r in d["tabla"][d["tabla"]["serie"] == "indice"].itertuples():
        L.append(f"  {r.ruta}: {usd(r.ultimo)} · {usd(r.media_4s)} · {r.tendencia}")
    return "\n".join(L) + "\n"


def generar(fecha: date, data_dir: Path = DATA, reports_dir: Path = REPORTS,
            config_dir: Path | None = None, prueba: bool = False) -> dict:
    """Devuelve {'html', 'texto'} del mail de la corrida."""
    d = datos(fecha, data_dir, reports_dir, config_dir)
    return {"html": armar_html(d, prueba), "texto": armar_texto(d, prueba)}


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Vista previa local del mail semanal.")
    p.add_argument("--fecha", default=date.today().isoformat(), help="AAAA-MM-DD (default hoy)")
    p.add_argument("--salida", type=Path, help="archivo .html donde guardar la vista previa")
    a = p.parse_args(argv)
    m = generar(a_fecha(a.fecha))
    if a.salida:
        a.salida.write_text(m["html"], encoding="utf-8")
        print("Vista previa:", a.salida)
    else:
        print(m["texto"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
