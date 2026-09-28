"""Estadísticas y cartas de control por ruta y por serie.

La serie de control de cada ruta es el MEJOR precio_total_usd de cada semana ISO.
Las semanas sin dato quedan vacías (NaN): no se interpola. Sólo cuentan las tarifas
del viaje actual (ver `vigentes`). Las filas del índice
marcadas como "fuente no homogénea" se excluyen de la serie índice (rachas y límites).

Límites de control μ ± k·σ sólo con n_semanas >= min_semanas_limites (default 6);
antes se muestran la banda de referencia 900–1.500 y el umbral fijo de la ruta.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

from comun import NOTA_NO_HOMOGENEA, RUTAS, SERIES, lunes_de, rango_semanas, semana_iso

MIN_PUNTOS_MEDIA_PREVIA = 2


def filtrar_serie(obs: pd.DataFrame, ruta: str, serie: str) -> pd.DataFrame:
    d = obs[(obs["ruta"] == ruta) & (obs["serie"] == serie)]
    if serie == "indice":
        d = d[~d["notas"].fillna("").str.contains(NOTA_NO_HOMOGENEA, regex=False)]
    return d.dropna(subset=["precio_total_usd"])


def vigentes(obs: pd.DataFrame, rutas_cfg: dict) -> pd.DataFrame:
    """Sólo las tarifas del viaje actual (el CSV guarda también las de viajes anteriores).

    Fechadas: ida dentro de salida_desde–salida_hasta. Índice: ida en meses_indice.
    """
    viaje = rutas_cfg["viaje"]
    desde, hasta = viaje["salida_desde"], viaje.get("salida_hasta") or "9999-12-31"
    ida = obs["fecha_ida"].fillna("").astype(str)
    fechada = (obs["serie"] == "fechada") & (ida >= desde) & (ida <= hasta)
    indice = (obs["serie"] == "indice") & ida.str[:7].isin(viaje.get("meses_indice", []))
    return obs[fechada | indice]


def corridas_vigentes(corridas: pd.DataFrame, rutas_cfg: dict) -> pd.DataFrame:
    """Corridas desde que empezó el seguimiento del viaje actual."""
    desde = rutas_cfg["viaje"].get("seguimiento_desde") or ""
    return corridas[corridas["fecha"] >= desde]


def rango_de(obs: pd.DataFrame, hasta, corridas: pd.DataFrame | None = None) -> list[str]:
    """Semanas ISO desde la primera con datos (o corrida) hasta la de `hasta`."""
    fin = semana_iso(hasta)
    semanas = list(obs["semana_iso"].dropna())
    if corridas is not None and len(corridas):
        semanas += list(corridas["semana_iso"].dropna())
    semanas = [s for s in semanas if s and s <= fin]
    return rango_semanas(min(semanas), fin) if semanas else [fin]


def serie_semanal(obs: pd.DataFrame, ruta: str, serie: str, semanas: list[str]) -> pd.Series:
    """Mejor precio_total_usd por semana, reindexado a `semanas` (NaN donde no hay dato)."""
    d = filtrar_serie(obs, ruta, serie)
    s = d.groupby("semana_iso")["precio_total_usd"].min()
    return s.reindex(semanas).astype(float)


def racha(valores) -> int:
    """Subas (+) o bajas (−) consecutivas al final de la serie, sobre los valores observados.

    Las semanas sin dato no cortan la racha (se compara cada dato con el anterior disponible).
    Un valor igual al anterior corta la racha.
    """
    v = [x for x in valores if not pd.isna(x)]
    n = 0
    for i in range(len(v) - 1, 0, -1):
        d = v[i] - v[i - 1]
        signo = (d > 0) - (d < 0)
        if signo == 0 or (n != 0 and (n > 0) != (signo > 0)):
            break
        n += signo
    return n


def limites(valores, k: float, min_semanas: int):
    """(LCL, UCL, método). Con n >= min_semanas: μ ± k·σ; si no, (NaN, NaN, 'sin límites')."""
    v = np.array([x for x in valores if not pd.isna(x)], dtype=float)
    if len(v) >= min_semanas and len(v) >= 2:
        m, s = v.mean(), v.std(ddof=1)
        return float(m - k * s), float(m + k * s), f"μ±{k:g}σ"
    return math.nan, math.nan, f"banda de referencia (n<{min_semanas})"


def media_previa(s: pd.Series, ventana: int = 4) -> float:
    """Media de las `ventana` semanas previas a la última (sin contarla). NaN si hay < 2 datos."""
    prev = s.iloc[:-1].tail(ventana).dropna()
    return float(prev.mean()) if len(prev) >= MIN_PUNTOS_MEDIA_PREVIA else math.nan


def tendencia(s: pd.Series, ventana: int = 4, tolerancia_pct: float = 2.0) -> str:
    """'sube' | 'baja' | 'estable' comparando el último dato con la media de las semanas previas."""
    obs = s.dropna()
    if obs.empty:
        return "sin datos"
    if len(obs) < 2:
        return "sin historia"
    ultimo = obs.iloc[-1]
    base = media_previa(s.loc[:obs.index[-1]], ventana)
    if math.isnan(base):
        r = racha(s)
        return "sube" if r > 0 else "baja" if r < 0 else "estable"
    var = (ultimo / base - 1) * 100
    return "sube" if var > tolerancia_pct else "baja" if var < -tolerancia_pct else "estable"


def estadisticas(s: pd.Series, k: float = 2, min_semanas: int = 6, ventana: int = 4) -> dict:
    """Estadísticas de control sobre una serie semanal (con NaN en las semanas vacías)."""
    v = s.dropna()
    n = len(v)
    base = {"n_semanas": n, "semanas_vacias": int(s.isna().sum())}
    if n == 0:
        return base | {"minimo": math.nan, "promedio": math.nan, "mediana": math.nan,
                       "desvio": math.nan, "ultimo": math.nan, "semana_ultimo": "",
                       "media_4s": math.nan, "racha": 0, "lcl": math.nan, "ucl": math.nan,
                       "metodo_limites": "sin datos", "tendencia": "sin datos"}
    lcl, ucl, metodo = limites(v, k, min_semanas)
    mm = s.rolling(ventana, min_periods=1).mean().iloc[-1]
    return base | {
        "minimo": float(v.min()),
        "promedio": float(v.mean()),
        "mediana": float(v.median()),
        "desvio": float(v.std(ddof=1)) if n >= 2 else math.nan,
        "ultimo": float(v.iloc[-1]),
        "semana_ultimo": v.index[-1],
        "media_4s": float(mm) if not pd.isna(mm) else math.nan,
        "racha": racha(v),
        "lcl": lcl,
        "ucl": ucl,
        "metodo_limites": metodo,
        "tendencia": tendencia(s, ventana),
    }


def tabla_control(obs: pd.DataFrame, semanas: list[str], rutas_cfg: dict,
                  alertas_cfg: dict) -> pd.DataFrame:
    """Una fila por ruta y serie con las estadísticas de la carta de control."""
    c = alertas_cfg["control"]
    filas = []
    for serie in SERIES:
        for ruta in RUTAS:
            s = serie_semanal(obs, ruta, serie, semanas)
            st = estadisticas(s, c["k_sigma"], c["min_semanas_limites"], c["ventana_media_movil"])
            filas.append({"ruta": ruta, "serie": serie,
                          "categoria": rutas_cfg["rutas"][ruta]["categoria"],
                          "umbral_usd": alertas_cfg["umbrales_usd"][ruta] if serie == "fechada" else math.nan,
                          **st})
    return pd.DataFrame(filas)


def matriz_semanal(obs: pd.DataFrame, serie: str, semanas: list[str]) -> pd.DataFrame:
    """Semanas × rutas con el mejor precio de cada semana (vacío = sin dato)."""
    return pd.DataFrame({r: serie_semanal(obs, r, serie, semanas) for r in RUTAS})


def semanas_sin_datos(obs: pd.DataFrame, semanas: list[str],
                      corridas: pd.DataFrame | None = None) -> pd.DataFrame:
    """Una fila por semana a la que le falta algún dato, con qué rutas faltan en cada serie."""
    filas = []
    estados = {}
    if corridas is not None and len(corridas):
        estados = corridas.groupby("semana_iso")["estado"].agg(lambda x: ";".join(sorted(set(x))))
    for sem in semanas:
        de_la_semana = obs[obs["semana_iso"] == sem]
        falta = {serie: [r for r in RUTAS if filtrar_serie(de_la_semana, r, serie).empty]
                 for serie in SERIES}
        if falta["indice"] or falta["fechada"]:
            sin_nada = de_la_semana.empty
            filas.append({
                "semana_iso": sem,
                "lunes": lunes_de(sem).isoformat(),
                "sin_ningun_dato": "si" if sin_nada else "no",
                "rutas_sin_indice": ";".join(falta["indice"]),
                "rutas_sin_fechada": ";".join(falta["fechada"]),
                "filas_no_homogeneas": int((de_la_semana["serie"] == "indice").sum()
                                           - sum(len(filtrar_serie(de_la_semana, r, "indice")) for r in RUTAS)),
                "estado_corrida": estados.get(sem, "sin corrida"),
            })
    return pd.DataFrame(filas, columns=["semana_iso", "lunes", "sin_ningun_dato",
                                        "rutas_sin_indice", "rutas_sin_fechada", "filas_no_homogeneas",
                                        "estado_corrida"])
