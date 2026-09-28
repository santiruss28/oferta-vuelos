"""Evalúa las reglas de alerta y escribe reports/alertas_AAAA-MM-DD.json.

Las alertas las decide este script con reglas fijas (config/alertas.yaml), nunca el modelo.

Niveles y reglas:
  COMPRAR      R1_umbral            fechada <= umbral fijo de la ruta
  COMPRAR      R2_minimo_bajo_lcl   fechada nuevo mínimo histórico y < LCL (n >= 6)
  OPORTUNIDAD  R3_cerca_umbral      fechada <= umbral × 1,08
  OPORTUNIDAD  R3_percentil         fechada <= percentil 20 de la ruta (n >= 4)
  OPORTUNIDAD  R4_alternativa       alternativa >= USD 60 más barata que la mejor principal de la semana
  ATENCION     R5_indice_baja       índice baja 3 semanas seguidas
  ATENCION     R5_indice_sube       índice > 10% sobre su media de las 4 semanas previas
  ATENCION     R6_ventana_cierra    faltan <= 120 días para la ida más temprana y el índice sube
  INFO         R7_*                 ruta sin datos, fuente caída, rechazados, semanas faltantes

Sólo se evalúan las fechadas de la semana ISO de la corrida que no sean importadas sin
evidencia. Anti-spam: no se repite la misma alerta (ruta + regla + fechas) dentro de
14 días salvo que el precio haya bajado >= 3%.

Uso:
    python src/alertas.py --fecha 2026-10-05
"""
from __future__ import annotations

import argparse
import hashlib
import math
import sys
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

import control
from comun import (ALERTAS_EMITIDAS, ALTERNATIVAS, COLUMNAS_ALERTAS, COLUMNAS_CORRIDAS, CORRIDAS,
                   DATA, IMPORTADO_SIN_EVIDENCIA, NIVELES, OBSERVACIONES, PRINCIPALES, REPORTS,
                   RUTAS, a_fecha, cargar_alertas_cfg, cargar_rutas, escribir_json, fmt_fecha,
                   fmt_usd, leer_csv, leer_observaciones, reemplazar_filas, semana_iso)

PRIORIDAD = {n: i for i, n in enumerate(NIVELES)}


def alert_id(ruta: str, regla: str, ida: str, vuelta: str, precio) -> str:
    p = "" if precio is None or (isinstance(precio, float) and math.isnan(precio)) else f"{float(precio):.0f}"
    return hashlib.sha1(f"{ruta}|{regla}|{ida}|{vuelta}|{p}".encode()).hexdigest()


def _limpio(v):
    """NaN → None para que el JSON quede prolijo."""
    if v is None:
        return None
    if isinstance(v, (float, np.floating)):
        return None if math.isnan(v) else round(float(v), 2)
    if isinstance(v, np.integer):
        return int(v)
    return v


def _alerta(nivel, regla, ruta, mensaje, fila=None, umbral=None, minimo=None, precio=None) -> dict:
    fila = fila if fila is not None else {}
    ida, vuelta = fila.get("fecha_ida", "") or "", fila.get("fecha_vuelta", "") or ""
    total = _limpio(fila.get("precio_total_usd")) if fila else _limpio(precio)
    dias = _limpio(fila.get("dias_estadia"))
    return {
        "alert_id": alert_id(ruta, regla, ida, vuelta, total),
        "nivel": nivel,
        "regla": regla,
        "ruta": ruta,
        "aerolinea": fila.get("aerolinea", "") or "",
        "fecha_ida": ida,
        "fecha_vuelta": vuelta,
        "dias": int(dias) if dias is not None else None,
        "precio_usd": _limpio(fila.get("precio_usd")),
        "precio_con_valija_usd": _limpio(fila.get("precio_con_valija_usd")),
        "valija_estimada": fila.get("valija_estimada") == "si" if fila else None,
        "costo_conexion_usd": _limpio(fila.get("costo_conexion_usd")),
        "precio_total_usd": total,
        "umbral_usd": _limpio(umbral),
        "minimo_historico_usd": _limpio(minimo),
        "fuente": fila.get("fuente", "") or "",
        "url": fila.get("url", "") or "",
        "evidencia": fila.get("evidencia", "") or "",
        "mensaje": mensaje,
    }


# ---------------------------------------------------------------- reglas sobre fechadas

def reglas_fechadas(obs: pd.DataFrame, sem: str, semanas: list[str], rutas_cfg: dict,
                    cfg: dict) -> list[dict]:
    r = cfg["reglas"]
    k = cfg["control"]["k_sigma"]
    fech = obs[obs["serie"] == "fechada"].dropna(subset=["precio_total_usd"])
    actuales = fech[(fech["semana_iso"] == sem) & (fech["origen_dato"] != IMPORTADO_SIN_EVIDENCIA)]
    previas = fech[fech["semana_iso"] < sem]
    semanas_previas = [s for s in semanas if s < sem]
    principales_semana = actuales[actuales["ruta"].isin(PRINCIPALES)]
    mejor_principal = principales_semana["precio_total_usd"].min() if len(principales_semana) else math.nan

    out = []
    for _, f in actuales.iterrows():
        f = f.to_dict()
        ruta, total = f["ruta"], f["precio_total_usd"]
        umbral = cfg["umbrales_usd"][ruta]
        hist = previas[previas["ruta"] == ruta]["precio_total_usd"]
        minimo = float(hist.min()) if len(hist) else math.nan
        semanal = control.serie_semanal(obs, ruta, "fechada", semanas_previas).dropna()
        n = len(semanal)
        nombre = rutas_cfg["rutas"][ruta]["nombre"]
        desc = (f"{nombre} {fmt_usd(total)} – {fmt_fecha(f['fecha_ida'])}→{fmt_fecha(f['fecha_vuelta'])}"
                f" – {f.get('aerolinea') or 's/aerolínea'}")

        if r["r1_umbral"]["activa"] and total <= umbral:
            out.append(_alerta("COMPRAR", "R1_umbral", ruta,
                               f"{desc}: bajo el umbral fijo de {fmt_usd(umbral)}", f, umbral, minimo))
        if r["r2_minimo_bajo_lcl"]["activa"] and n >= r["r2_minimo_bajo_lcl"]["min_semanas"]:
            lcl, _, _ = control.limites(semanal, k, r["r2_minimo_bajo_lcl"]["min_semanas"])
            if (math.isnan(minimo) or total < minimo) and total < lcl:
                out.append(_alerta("COMPRAR", "R2_minimo_bajo_lcl", ruta,
                                   f"{desc}: nuevo mínimo histórico y bajo el LCL ({fmt_usd(lcl)})",
                                   f, umbral, minimo))
        r3 = r["r3_oportunidad"]
        if r3["activa"]:
            if total <= umbral * r3["factor_umbral"]:
                out.append(_alerta("OPORTUNIDAD", "R3_cerca_umbral", ruta,
                                   f"{desc}: a menos de {round((r3['factor_umbral'] - 1) * 100)}% "
                                   f"del umbral {fmt_usd(umbral)}", f, umbral, minimo))
            elif n >= r3["min_semanas_percentil"]:
                p = float(np.percentile(semanal.values, r3["percentil"]))
                if total <= p:
                    out.append(_alerta("OPORTUNIDAD", "R3_percentil", ruta,
                                       f"{desc}: en el percentil {r3['percentil']} de la ruta "
                                       f"(≤ {fmt_usd(p)})", f, umbral, minimo))
        r4 = r["r4_alternativa"]
        if r4["activa"] and ruta in ALTERNATIVAS and not math.isnan(mejor_principal):
            if total <= mejor_principal - r4["ahorro_minimo_usd"]:
                out.append(_alerta("OPORTUNIDAD", "R4_alternativa", ruta,
                                   f"{desc} (con conexión low-cost): {fmt_usd(mejor_principal - total)} "
                                   f"menos que la mejor principal de la semana ({fmt_usd(mejor_principal)})",
                                   f, umbral, minimo))
    return _dedupe_por_viaje(out)


def _dedupe_por_viaje(alertas: list[dict]) -> list[dict]:
    """Por cada tarifa (ruta + fechas + precio + fuente) deja sólo la regla de mayor nivel."""
    mejores: dict[tuple, dict] = {}
    for a in alertas:
        k = (a["ruta"], a["fecha_ida"], a["fecha_vuelta"], a["precio_total_usd"], a["fuente"],
             a["aerolinea"])
        if k not in mejores or PRIORIDAD[a["nivel"]] < PRIORIDAD[mejores[k]["nivel"]]:
            mejores[k] = a
    return list(mejores.values())


# ---------------------------------------------------------------- reglas sobre el índice

def reglas_indice(obs: pd.DataFrame, sem: str, semanas: list[str], fecha: date,
                  rutas_cfg: dict, cfg: dict) -> list[dict]:
    r5, r6 = cfg["reglas"]["r5_indice"], cfg["reglas"]["r6_ventana"]
    desde = a_fecha(rutas_cfg["viaje"]["salida_desde"])
    ida_relevante = max(desde, fecha)
    dias_para_ida = (ida_relevante - fecha).days
    out = []
    for ruta in RUTAS:
        s = control.serie_semanal(obs, ruta, "indice", [x for x in semanas if x <= sem])
        if s.empty or pd.isna(s.iloc[-1]):
            continue
        ultimo = float(s.iloc[-1])
        rch = control.racha(s)
        nombre = rutas_cfg["rutas"][ruta]["nombre"]
        if r5["activa"] and rch <= -r5["semanas_baja"]:
            out.append(_alerta("ATENCION", "R5_indice_baja", ruta,
                               f"Índice {nombre} bajó {-rch} semanas seguidas (último {fmt_usd(ultimo)}): "
                               "buen momento para mirar fechas concretas", precio=ultimo))
        base = control.media_previa(s, r5["ventana_media"])
        if r5["activa"] and not math.isnan(base) and ultimo > base * (1 + r5["suba_pct"] / 100):
            out.append(_alerta("ATENCION", "R5_indice_sube", ruta,
                               f"Índice {nombre} {fmt_usd(ultimo)}: +{(ultimo / base - 1) * 100:.0f}% "
                               f"sobre su media de 4 semanas ({fmt_usd(base)})", precio=ultimo))
        if r6["activa"] and dias_para_ida <= r6["dias_para_ida"] and rch > 0:
            out.append(_alerta("ATENCION", "R6_ventana_cierra", ruta,
                               f"Faltan {dias_para_ida} días para la ida más temprana "
                               f"({fmt_fecha(ida_relevante)}) y el índice {nombre} viene subiendo "
                               f"({rch} semana/s, último {fmt_usd(ultimo)})", precio=ultimo))
    return out


# ---------------------------------------------------------------- INFO

def reglas_info(obs: pd.DataFrame, sem: str, fecha: date, corridas: pd.DataFrame,
                n_rechazadas: int) -> list[dict]:
    out = []
    de_semana = obs[obs["semana_iso"] == sem]
    for ruta in RUTAS:
        de_ruta = de_semana[de_semana["ruta"] == ruta]
        faltan = [s for s in ("indice", "fechada") if (de_ruta["serie"] == s).sum() == 0]
        if faltan:
            out.append(_alerta("INFO", "R7_sin_datos", ruta,
                               f"{ruta}: sin datos esta semana ({', '.join(faltan)})"))
    hoy = corridas[corridas["fecha"] == fecha.isoformat()]
    for fuente in ";".join(hoy["fuentes_caidas"]).split(";"):
        if fuente.strip():
            out.append(_alerta("INFO", "R7_fuente_caida", "", f"Fuente caída: {fuente.strip()}"))
    if n_rechazadas:
        out.append(_alerta("INFO", "R7_rechazados", "",
                           f"{n_rechazadas} fila(s) rechazadas por validar.py "
                           f"(ver reports/rechazados_{fecha.isoformat()}.csv)"))
    previas = corridas[(corridas["fecha"] < fecha.isoformat())
                       & corridas["estado"].isin(["ok", "importada"])]
    if len(previas):
        ultima = previas["semana_iso"].max()
        cubiertas = set(corridas[corridas["estado"].isin(["ok", "importada"])]["semana_iso"])
        huecos = [s for s in control.rango_semanas(ultima, sem)[1:-1] if s not in cubiertas]
        if huecos:
            out.append(_alerta("INFO", "R7_semanas_faltantes", "",
                               f"Semanas sin corrida desde la última ({ultima}): {', '.join(huecos)}"))
    return out


# ---------------------------------------------------------------- anti-spam

def aplicar_antispam(alertas: list[dict], emitidas: pd.DataFrame, fecha: date,
                     cfg: dict) -> tuple[list[dict], list[dict]]:
    """Separa (a_emitir, suprimidas) según las emitidas en los últimos N días."""
    a = cfg["antispam"]
    desde = (fecha - timedelta(days=a["dias"])).isoformat()
    recientes = emitidas[(emitidas["fecha_emision"] >= desde)
                         & (emitidas["fecha_emision"] < fecha.isoformat())]
    emitir, suprimidas = [], []
    for al in alertas:
        if al["nivel"] not in a["niveles"]:
            emitir.append(al)
            continue
        previas = recientes[(recientes["ruta"] == al["ruta"]) & (recientes["regla"] == al["regla"])
                            & (recientes["fecha_ida"] == al["fecha_ida"])
                            & (recientes["fecha_vuelta"] == al["fecha_vuelta"])]
        if previas.empty:
            emitir.append(al)
            continue
        precio_prev = pd.to_numeric(previas["precio_total_usd"], errors="coerce").min()
        precio = al["precio_total_usd"]
        bajo = (precio is not None and not pd.isna(precio_prev)
                and precio <= precio_prev * (1 - a["baja_minima_pct"] / 100))
        if bajo:
            al["mensaje"] += f" (bajó desde {fmt_usd(precio_prev)})"
            emitir.append(al)
        else:
            suprimidas.append(al | {"motivo_supresion":
                                    f"ya emitida el {previas['fecha_emision'].max()} "
                                    f"a {fmt_usd(precio_prev)}"})
    return emitir, suprimidas


# ---------------------------------------------------------------- orquestación

def resumen_indice(obs: pd.DataFrame, semanas: list[str], cfg: dict) -> list[dict]:
    v = cfg["control"]["ventana_media_movil"]
    out = []
    for ruta in RUTAS:
        s = control.serie_semanal(obs, ruta, "indice", semanas)
        st = control.estadisticas(s, ventana=v)
        out.append({"ruta": ruta, "ultimo": _limpio(st["ultimo"]),
                    "semana_ultimo": st["semana_ultimo"], "media_4s": _limpio(st["media_4s"]),
                    "tendencia": st["tendencia"]})
    return out


def evaluar(fecha: date, obs: pd.DataFrame, corridas: pd.DataFrame, emitidas: pd.DataFrame,
            rutas_cfg: dict, cfg: dict, n_rechazadas: int = 0) -> dict:
    sem = semana_iso(fecha)
    semanas = control.rango_de(obs, fecha, corridas)
    candidatas = (reglas_fechadas(obs, sem, semanas, rutas_cfg, cfg)
                  + reglas_indice(obs, sem, semanas, fecha, rutas_cfg, cfg))
    if cfg["reglas"]["r7_info"]["activa"]:
        candidatas += reglas_info(obs, sem, fecha, corridas, n_rechazadas)
    emitir, suprimidas = aplicar_antispam(candidatas, emitidas, fecha, cfg)
    emitir.sort(key=lambda a: (PRIORIDAD[a["nivel"]], a["precio_total_usd"] or 0))
    return {
        "fecha_corrida": fecha.isoformat(),
        "semana_iso": sem,
        "alertas": emitir,
        "suprimidas": suprimidas,
        "resumen_indice": resumen_indice(obs, semanas, cfg),
    }


def contar_rechazadas(fecha: date, reports_dir: Path = REPORTS) -> int:
    p = reports_dir / f"rechazados_{fecha.isoformat()}.csv"
    return len(pd.read_csv(p)) if p.exists() else 0


def ejecutar(fecha: date, data_dir: Path = DATA, reports_dir: Path = REPORTS,
             config_dir: Path | None = None) -> dict:
    """Evalúa, escribe el JSON de la corrida y registra las alertas emitidas (no INFO)."""
    rutas_cfg, cfg = cargar_rutas(config_dir), cargar_alertas_cfg(config_dir)
    obs = leer_observaciones(data_dir / OBSERVACIONES.name)
    corridas = leer_csv(data_dir / CORRIDAS.name, COLUMNAS_CORRIDAS)
    emitidas = leer_csv(data_dir / ALERTAS_EMITIDAS.name, COLUMNAS_ALERTAS)
    res = evaluar(fecha, obs, corridas, emitidas, rutas_cfg, cfg,
                  contar_rechazadas(fecha, reports_dir))
    escribir_json(reports_dir / f"alertas_{fecha.isoformat()}.json", res)
    registro = [{"fecha_emision": fecha.isoformat(), **{c: a.get(c, "") for c in COLUMNAS_ALERTAS[1:]}}
                for a in res["alertas"] if a["nivel"] != "INFO"]
    reemplazar_filas(data_dir / ALERTAS_EMITIDAS.name, COLUMNAS_ALERTAS, "fecha_emision",
                     fecha.isoformat(), registro)
    return res


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Evalúa las reglas de alerta de la corrida.")
    p.add_argument("--fecha", default=date.today().isoformat(), help="AAAA-MM-DD (default hoy)")
    a = p.parse_args(argv)
    res = ejecutar(a_fecha(a.fecha))
    for al in res["alertas"]:
        print(f"[{al['nivel']}] {al['regla']}: {al['mensaje']}")
    print(f"{len(res['alertas'])} alerta(s), {len(res['suprimidas'])} suprimida(s) por anti-spam")
    return 0


if __name__ == "__main__":
    sys.exit(main())
