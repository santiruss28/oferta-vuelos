"""Contexto de precio de una tarifa: ¿está barata o no, y por qué?

Lo usan el mail semanal (para cada tarifa destacada) y src/evaluar.py (para una tarifa
suelta que encontraste vos). Todo sale de data/observaciones.csv con reglas fijas:

1. Posición histórica: qué % de las fechadas vistas para la ruta eran más caras.
2. Contra el mínimo histórico y el umbral fijo de la ruta.
3. Momento del mercado: tendencia del índice (Turismocity) de la ruta.
4. Días que faltan para la ida.
5. Precio real: valija (real o estimada) y conexión low-cost incluidas.
6. Contra las alternativas (o las principales) de la misma semana.

Veredicto (mismas reglas que alertas.py R1–R3, más "CARO"):
  COMPRAR      total <= umbral, o nuevo mínimo y < LCL (n >= 6)
  OPORTUNIDAD  total <= umbral × 1,08, o <= percentil 20 (n >= 4)
  CARO         total > UCL (n >= 6), o >= percentil 80 (n >= 4), o > techo de la banda 900–1.500
  NORMAL       el resto
"""
from __future__ import annotations

import math
from datetime import date

import numpy as np
import pandas as pd

import control
from comun import ALTERNATIVAS, PRINCIPALES, a_fecha, semana_iso

CONFIABILIDAD = ((6, "alta"), (4, "media"), (0, "baja"))


def _usd(x) -> str:
    return "–" if x is None or (isinstance(x, float) and math.isnan(x)) else \
        f"USD {x:,.0f}".replace(",", ".")


def _nombre(ruta, rutas_cfg):
    return rutas_cfg["rutas"][ruta]["nombre"].split(" (")[0]


def historial_fechadas(obs: pd.DataFrame, ruta: str, fecha: date, incluir_semana: bool) -> pd.DataFrame:
    """Fechadas de la ruta antes de la semana de `fecha` (o hasta esa semana inclusive)."""
    sem = semana_iso(fecha)
    d = obs[(obs["ruta"] == ruta) & (obs["serie"] == "fechada")].dropna(subset=["precio_total_usd"])
    return d[d["semana_iso"] <= sem] if incluir_semana else d[d["semana_iso"] < sem]


def contexto(obs: pd.DataFrame, ruta: str, total: float, fecha: date, rutas_cfg: dict, cfg: dict,
             fecha_ida=None, incluir_semana: bool = False, valija_estimada: bool = False,
             costo_conexion: float = 0) -> dict:
    """Calcula el contexto de precio de una tarifa (dict con números y textos listos)."""
    r3 = cfg["reglas"]["r3_oportunidad"]
    k, min_lim = cfg["control"]["k_sigma"], cfg["control"]["min_semanas_limites"]
    banda = rutas_cfg["viaje"]["banda_referencia_usd"]
    umbral = cfg["umbrales_usd"][ruta]
    sem = semana_iso(fecha)
    nombre = _nombre(ruta, rutas_cfg)

    hist = historial_fechadas(obs, ruta, fecha, incluir_semana)
    precios = hist["precio_total_usd"].to_numpy(dtype=float)
    semanas = control.rango_de(obs, fecha)
    semanas = [s for s in semanas if s < sem] + ([sem] if incluir_semana else [])
    semanal = control.serie_semanal(obs, ruta, "fechada", semanas).dropna() if semanas else pd.Series(dtype=float)
    n_sem = len(semanal)
    conf = next(c for minimo, c in CONFIABILIDAD if n_sem >= minimo)

    ctx = {"ruta": ruta, "total": float(total), "umbral": umbral, "n_tarifas": len(precios),
           "n_semanas": n_sem, "confiabilidad": conf, "lineas": []}
    L = ctx["lineas"]

    # 1. Posición histórica
    if len(precios):
        mas_caras = float((precios > total).mean() * 100)
        ctx["pct_mas_caras"] = mas_caras
        cola = (f"{'vista' if len(precios) == 1 else 'vistas'} para {nombre} "
                f"({n_sem} {'semana' if n_sem == 1 else 'semanas'} con datos)")
        if len(precios) == 1:
            base = f"la única tarifa fechada {cola}"
            L.append(f"{'Más barata' if mas_caras == 100 else 'No es más barata'} que {base}.")
        elif mas_caras == 100:
            L.append(f"Más barata que todas las {len(precios)} tarifas fechadas {cola}.")
        elif mas_caras == 0:
            L.append(f"No es más barata que ninguna de las {len(precios)} tarifas fechadas {cola}.")
        else:
            base = f"las {len(precios)} tarifas fechadas {cola}"
            L.append(f"Más barata que el {mas_caras:.0f}% de {base}.")
    else:
        ctx["pct_mas_caras"] = None
        L.append(f"Sin historial de tarifas fechadas para {nombre}: sólo cuentan el umbral y la banda.")

    # 2. Mínimo histórico y umbral
    minimo = float(precios.min()) if len(precios) else math.nan
    ctx["minimo"] = minimo
    dif_u = total - umbral
    txt = f"{_usd(abs(dif_u))} {'bajo' if dif_u <= 0 else 'sobre'} el umbral de compra ({_usd(umbral)})"
    if not math.isnan(minimo):
        dif_m = total - minimo
        txt += ("; nuevo mínimo histórico" if dif_m < 0 else
                "; igual al mínimo histórico" if dif_m == 0 else
                f"; {_usd(dif_m)} sobre el mínimo histórico ({_usd(minimo)})")
    L.append(txt[0].upper() + txt[1:] + ".")

    lcl, ucl, _ = control.limites(semanal, k, min_lim)
    ctx["lcl"], ctx["ucl"] = lcl, ucl
    if math.isnan(lcl):
        pos = ("debajo de" if total < banda[0] else "encima de" if total > banda[1] else "dentro de")
        L.append(f"Límites de control todavía no disponibles (n={n_sem} < {min_lim} semanas); "
                 f"está {pos} la banda de mercado {banda[0]}–{banda[1]:,}.".replace(",", "."))
    else:
        pos = "debajo del LCL" if total < lcl else "encima del UCL" if total > ucl else "dentro de los límites"
        L.append(f"Carta de control: {pos} ({_usd(lcl)}–{_usd(ucl)}).")

    # 3. Índice de mercado
    si = control.serie_semanal(obs, ruta, "indice", [s for s in control.rango_de(obs, fecha) if s <= sem])
    tend, rch = control.tendencia(si), control.racha(si)
    ctx["tendencia_indice"] = tend
    if si.dropna().empty:
        L.append("Índice de mercado: sin datos para esta ruta.")
    else:
        lectura = {"baja": "el mercado viene bajando: esperar puede convenir",
                   "sube": "el mercado viene subiendo: conviene decidir pronto",
                   "estable": "el mercado está estable"}.get(tend, "todavía sin tendencia")
        racha_txt = f", {abs(rch)} semana/s seguidas" if rch and tend in ("sube", "baja") else ""
        L.append(f"Índice {nombre}: {tend}{racha_txt} (último {_usd(si.dropna().iloc[-1])}); {lectura}.")

    # 4. Días para la ida
    ida = a_fecha(fecha_ida)
    if ida:
        ctx["dias_para_ida"] = (ida - fecha).days
        L.append(f"Faltan {ctx['dias_para_ida']} días para la ida.")

    # 5. Precio real
    extras = []
    if valija_estimada:
        extras.append(f"valija estimada (+{_usd(rutas_cfg['viaje']['recargo_valija_usd'])})")
    if costo_conexion:
        extras.append(f"conexión low-cost {_usd(costo_conexion)}")
    if extras:
        L.append("El total incluye " + " y ".join(extras) + ": confirmá esos costos antes de comprar.")

    # 6. Alternativas / principales de la semana
    otras = ALTERNATIVAS if ruta in PRINCIPALES else PRINCIPALES
    semana_obs = obs[(obs["semana_iso"] == sem) & (obs["serie"] == "fechada") & obs["ruta"].isin(otras)]
    semana_obs = semana_obs.dropna(subset=["precio_total_usd"])
    if len(semana_obs):
        mejor = semana_obs.loc[semana_obs["precio_total_usd"].idxmin()]
        dif = mejor["precio_total_usd"] - total
        tipo = "alternativa (MAD/BCN)" if ruta in PRINCIPALES else "principal"
        L.append(f"Mejor {tipo} de la semana: {mejor['ruta']} {_usd(mejor['precio_total_usd'])} "
                 f"({'+' if dif >= 0 else '−'}{_usd(abs(dif))[4:]} vs esta).")

    # Veredicto
    ctx["veredicto"] = veredicto(total, umbral, r3, precios, semanal, lcl, ucl, minimo, banda,
                                 r3["min_semanas_percentil"], min_lim)
    if conf != "alta":
        L.append(f"Confiabilidad {conf}: con {n_sem} semana/s de historial, el percentil y los "
                 "límites son orientativos; manda el umbral fijo.")
    return ctx


def veredicto(total, umbral, r3, precios, semanal, lcl, ucl, minimo, banda, min_pct, min_lim) -> str:
    n = len(semanal)
    if total <= umbral:
        return "COMPRAR"
    if n >= min_lim and not math.isnan(lcl) and (math.isnan(minimo) or total < minimo) and total < lcl:
        return "COMPRAR"
    if total <= umbral * r3["factor_umbral"]:
        return "OPORTUNIDAD"
    if n >= min_pct and total <= float(np.percentile(semanal.values, r3["percentil"])):
        return "OPORTUNIDAD"
    if (not math.isnan(ucl) and total > ucl) or total > banda[1]:
        return "CARO"
    if n >= min_pct and len(precios) and total >= float(np.percentile(precios, 80)):
        return "CARO"
    return "NORMAL"
