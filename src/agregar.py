"""Agrega observaciones nuevas: JSON → valida → convierte ARS→USD → append al CSV.

Uso:
    python src/agregar.py data/entrada/2026-10-05.json

Formato del JSON (ver ROUTINE.md para el detalle de cada campo):
{
  "fecha_busqueda": "2026-10-05",
  "fx": {"valor": 1480.5, "metodo": "oficial BNA vendedor", "fuente": "https://www.bna.com.ar/Personas"},
  "fuentes_caidas": [{"fuente": "Google Flights", "detalle": "captcha"}],
  "observaciones": [
    {"ruta": "PAR", "serie": "fechada", "origen": "EZE", "destino": "CDG",
     "aerolinea": "Air France", "escalas": 0,
     "fecha_ida": "2027-03-03", "fecha_vuelta": "2027-03-17",
     "precio_moneda_original": 780, "moneda": "USD",
     "incluye_valija": "no", "fuente": "Google Flights", "url": "https://...",
     "evidencia": "mié, 3 mar – mié, 17 mar ... Air France ... US$780", "notas": ""}
  ]
}
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

from comun import (COLUMNAS, COLUMNAS_CORRIDAS, COLUMNAS_FX, CORRIDAS, DATA, FX, OBSERVACIONES,
                   REPORTS, RUTAS, a_fecha, append_csv, cargar_rutas, leer_csv, num,
                   reemplazar_filas, semana_iso, vacio)
from validar import escribir_rechazados, validar_lote


def convertir(obs: dict, fx: float | None) -> dict:
    """Completa precio_usd y fx_usado a partir de la moneda original."""
    f = dict(obs)
    moneda = str(f.get("moneda") or "USD").upper()
    f["moneda"] = moneda
    original = num(f.get("precio_moneda_original"))
    if original is None and moneda == "USD" and num(f.get("precio_usd")) is not None:
        original = num(f["precio_usd"])
        f["precio_moneda_original"] = original
    if moneda == "USD":
        f["precio_usd"] = original
        f["fx_usado"] = ""
    elif moneda == "ARS":
        tc = num(f.get("fx_usado")) or fx
        if tc and original is not None:
            f["fx_usado"] = tc
            f["precio_usd"] = round(original / tc, 2)
    return f


def _fuentes_caidas(entrada: dict) -> list[str]:
    out = []
    for x in entrada.get("fuentes_caidas", []) or []:
        if isinstance(x, dict):
            det = f" ({x['detalle']})" if x.get("detalle") else ""
            out.append(f"{x.get('fuente', '?')}{det}")
        else:
            out.append(str(x))
    return out


def agregar(entrada: dict, fecha: date | None = None, data_dir: Path = DATA,
            reports_dir: Path = REPORTS, config_dir: Path | None = None) -> dict:
    """Procesa una entrada y devuelve un resumen de la corrida."""
    rutas_cfg = cargar_rutas(config_dir)
    fecha = fecha or a_fecha(entrada.get("fecha_busqueda")) or date.today()
    fx_cfg = entrada.get("fx") or {}
    fx = num(fx_cfg.get("valor"))

    filas = []
    for obs in entrada.get("observaciones", []) or []:
        f = convertir(obs, fx)
        if vacio(f.get("fecha_busqueda")):
            f["fecha_busqueda"] = fecha.isoformat()
        if vacio(f.get("origen_dato")):
            f["origen_dato"] = "rutina"
        filas.append(f)

    obs_path = data_dir / OBSERVACIONES.name
    existentes = leer_csv(obs_path, COLUMNAS)
    validas, rechazadas = validar_lote(filas, existentes, rutas_cfg)
    append_csv(obs_path, [{c: f.get(c, "") for c in COLUMNAS} for f in validas], COLUMNAS)
    archivo_rech = escribir_rechazados(rechazadas, f"rechazados_{fecha.isoformat()}", reports_dir)

    if fx:
        reemplazar_filas(data_dir / FX.name, COLUMNAS_FX, "fecha", fecha.isoformat(), [{
            "fecha": fecha.isoformat(), "valor": fx,
            "metodo": fx_cfg.get("metodo") or rutas_cfg["tipo_cambio"]["metodo"],
            "fuente": fx_cfg.get("fuente", ""),
        }])

    con_datos = sorted({f["ruta"] for f in validas}, key=RUTAS.index)
    sin_datos = [r for r in RUTAS if r not in con_datos]
    caidas = _fuentes_caidas(entrada)
    resumen = {
        "fecha": fecha.isoformat(),
        "semana_iso": semana_iso(fecha),
        "estado": "ok",
        "rutas_con_datos": ";".join(con_datos),
        "rutas_sin_datos": ";".join(sin_datos),
        "fuentes_caidas": ";".join(caidas),
        "filas_agregadas": len(validas),
        "filas_rechazadas": len(rechazadas),
        "notas": entrada.get("notas", ""),
    }
    reemplazar_filas(data_dir / CORRIDAS.name, COLUMNAS_CORRIDAS, "fecha", fecha.isoformat(),
                     [resumen])
    resumen["archivo_rechazados"] = str(archivo_rech) if archivo_rech else ""
    resumen["rechazadas"] = [{"ruta": f.get("ruta"), "serie": f.get("serie"),
                              "fuente": f.get("fuente"), "motivos": m} for f, m in rechazadas]
    return resumen


def registrar_corrida_vacia(fecha: date, data_dir: Path = DATA, notas: str = "") -> dict:
    """Corrida sin entrada (p. ej. no se pudo buscar): todas las rutas sin datos."""
    return agregar({"observaciones": [], "notas": notas}, fecha, data_dir)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("entrada", type=Path, help="JSON con las observaciones nuevas")
    p.add_argument("--fecha", help="fecha de la corrida AAAA-MM-DD (default: la del JSON o hoy)")
    a = p.parse_args(argv)
    with open(a.entrada, encoding="utf-8") as fh:
        entrada = json.load(fh)
    r = agregar(entrada, a_fecha(a.fecha) if a.fecha else None)
    print(f"Agregadas: {r['filas_agregadas']} · Rechazadas: {r['filas_rechazadas']}")
    for x in r["rechazadas"]:
        print(f"  ✗ {x['ruta']} {x['serie']} ({x['fuente']}): {'; '.join(x['motivos'])}")
    if r["archivo_rechazados"]:
        print(f"Detalle en {r['archivo_rechazados']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
