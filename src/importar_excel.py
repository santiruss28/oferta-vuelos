"""Carga inicial desde historial-precios-consolidado.xlsx (hoja Historial).

- "Fechada 13–16 días" → serie=fechada, origen_dato=importado_sin_evidencia (exceptuadas
  de la regla de evidencia; el resto de las validaciones se aplican igual).
- "Referencia" → serie=indice. Si la fuente no es Turismocity (o es un multitramo, que no
  es comparable con el precio "desde" simple), se marca "fuente no homogénea" en notas y
  queda fuera de las rachas y límites del índice.
- "Con equipaje: USD X" en notas → precio_con_valija_usd (valija_estimada=si si la nota
  dice "Estimado").
- "ARS 1.358.227" / "ARS 1,46M" en notas → precio_moneda_original en ARS y fx implícito.
- Registra en corridas.csv una fila por fecha importada y las semanas faltantes.

Uso:
    python src/importar_excel.py data/legado/historial-precios-consolidado.xlsx
"""
from __future__ import annotations

import argparse
import re
import sys
import unicodedata
from datetime import date
from pathlib import Path
from statistics import median

import pandas as pd

from comun import (COLUMNAS, COLUMNAS_CORRIDAS, COLUMNAS_FX, CORRIDAS, DATA, FX,
                   IMPORTADO_SIN_EVIDENCIA, NOTA_NO_HOMOGENEA, OBSERVACIONES, REPORTS, RUTAS,
                   a_fecha, append_csv, cargar_rutas, leer_csv, reemplazar_filas, semana_iso)
from validar import escribir_rechazados, validar_lote

SEMANAS_FALTANTES = ("2026-08-10", "2026-09-07")
NOMBRE_RUTA = {"paris": "PAR", "roma": "ROM", "milan": "MIL", "madrid": "MAD", "barcelona": "BCN"}
COLUMNAS_EXCEL = {
    "fecha de busqueda": "fecha_busqueda", "ruta": "ruta_txt", "ruta original": "ruta_original",
    "aerolinea": "aerolinea", "fechas de viaje": "fechas_txt", "dias de estadia": "dias",
    "tipo de tarifa": "tipo", "precio (usd)": "precio_usd", "fuente": "fuente", "enlace": "url",
    "notas": "notas", "archivo de origen": "archivo",
}


def _norm(t) -> str:
    return unicodedata.normalize("NFKD", str(t or "")).encode("ascii", "ignore").decode().lower().strip()


def _columna(nombre: str) -> str | None:
    """Encabezado del Excel → nombre interno (exacto primero; si no, el prefijo más largo)."""
    n = _norm(nombre)
    if n in COLUMNAS_EXCEL:
        return COLUMNAS_EXCEL[n]
    for clave in sorted(COLUMNAS_EXCEL, key=len, reverse=True):
        if n.startswith(clave + " ") or n.startswith(clave + "("):
            return COLUMNAS_EXCEL[clave]
    return None


def ruta_de(texto: str) -> str | None:
    n = _norm(texto)
    for nombre, codigo in NOMBRE_RUTA.items():
        if nombre in n:
            return codigo
    return None


def fechas_de(texto: str) -> tuple[date | None, date | None]:
    encontradas = re.findall(r"(\d{1,2})/(\d{1,2})/(\d{4})", str(texto or ""))
    if len(encontradas) < 2:
        return None, None
    (d1, m1, a1), (d2, m2, a2) = encontradas[:2]
    return date(int(a1), int(m1), int(d1)), date(int(a2), int(m2), int(d2))


def aeropuertos_de(texto: str, ruta: str, rutas_cfg: dict) -> tuple[str, str]:
    t = str(texto or "").upper()
    origen = next((c for c in rutas_cfg["origenes"] if re.search(rf"\b{c}\b", t)), "BUE")
    destino = next((c for c in rutas_cfg["rutas"][ruta]["aeropuertos"] if re.search(rf"\b{c}\b", t)),
                   ruta)
    return origen, destino


def con_equipaje(notas: str) -> float | None:
    m = re.search(r"con equipaje:\s*USD\s*([\d.,]+)", str(notas or ""), re.I)
    return float(re.sub(r"[.,]", "", m.group(1))) if m else None


def monto_ars(notas: str) -> float | None:
    """'ARS 1.358.227' → 1358227 · 'ARS 1,46M' / 'ARS 1,43 M' → 1460000 / 1430000."""
    m = re.search(r"ARS\s*([\d.,]+)\s*(M\b)?", str(notas or ""))
    if not m:
        return None
    num, millones = m.group(1).rstrip(".,"), bool(m.group(2))
    if millones:
        return float(num.replace(".", "").replace(",", ".")) * 1_000_000
    return float(re.sub(r"[.,]", "", num))


def es_indice_homogeneo(fuente: str, url: str, rutas_cfg: dict, ruta: str) -> bool:
    esperada = _norm(rutas_cfg["rutas"][ruta]["fuente_indice"])
    return esperada in _norm(fuente) and "dos-tramos" not in str(url or "")


def fila_desde_excel(r: dict, rutas_cfg: dict) -> dict:
    ruta = ruta_de(r.get("ruta_txt"))
    tipo = _norm(r.get("tipo"))
    serie = "fechada" if tipo.startswith("fechada") else "indice"
    ida, vuelta = fechas_de(r.get("fechas_txt"))
    notas = str(r.get("notas") or "").strip()
    archivo = str(r.get("archivo") or "").strip()
    fuente = str(r.get("fuente") or "").strip()
    url = str(r.get("url") or "").strip()
    precio = float(r["precio_usd"]) if r.get("precio_usd") not in (None, "") else None

    extras = [f"fechas originales: {r.get('fechas_txt')}"] if r.get("fechas_txt") else []
    if archivo:
        extras.append(f"origen: {archivo}")
    if serie == "indice" and ruta and not es_indice_homogeneo(fuente, url, rutas_cfg, ruta):
        notas = f"{NOTA_NO_HOMOGENEA}. {notas}"
    notas = " | ".join([notas, *extras]) if notas else " | ".join(extras)

    f = {
        "fecha_busqueda": a_fecha(r.get("fecha_busqueda")),
        "ruta": ruta or str(r.get("ruta_txt")),
        "serie": serie,
        "aerolinea": str(r.get("aerolinea") or "").strip(),
        "fecha_ida": ida, "fecha_vuelta": vuelta,
        "dias_estadia": int(r["dias"]) if r.get("dias") not in (None, "") else "",
        "moneda": "USD", "precio_moneda_original": precio, "precio_usd": precio, "fx_usado": "",
        "fuente": fuente, "url": url, "evidencia": "", "notas": notas,
        "origen_dato": IMPORTADO_SIN_EVIDENCIA,
    }
    if ruta:
        f["origen"], f["destino"] = aeropuertos_de(r.get("ruta_original"), ruta, rutas_cfg)
    ars = monto_ars(notas)
    if ars and precio:
        f.update(moneda="ARS", precio_moneda_original=ars, fx_usado=round(ars / precio, 2))
    valija = con_equipaje(notas)
    if valija:
        f.update(incluye_valija="no", precio_con_valija_usd=valija,
                 valija_estimada="si" if "estimado" in _norm(notas) else "no")
    return f


def leer_excel(path: Path) -> list[dict]:
    df = pd.read_excel(path, sheet_name="Historial")
    renombres = {c: _columna(c) for c in df.columns}
    faltan = {"fecha_busqueda", "ruta_txt", "tipo", "precio_usd"} - set(renombres.values())
    if faltan:
        raise SystemExit(f"Columnas no reconocidas en Historial: faltan {sorted(faltan)}; "
                         f"encontradas {list(df.columns)}")
    df = df.rename(columns={k: v for k, v in renombres.items() if v}).dropna(subset=["fecha_busqueda"])
    df = df.astype(object).where(df.notna(), None)
    return df.to_dict("records")


def importar(path: Path, data_dir: Path = DATA, reports_dir: Path = REPORTS,
             config_dir: Path | None = None, faltantes=SEMANAS_FALTANTES) -> dict:
    rutas_cfg = cargar_rutas(config_dir)
    filas = [fila_desde_excel(r, rutas_cfg) for r in leer_excel(path)]
    for f in filas:
        f["fecha_busqueda"] = f["fecha_busqueda"].isoformat() if f["fecha_busqueda"] else ""
        for c in ("fecha_ida", "fecha_vuelta"):
            f[c] = f[c].isoformat() if f[c] else ""

    obs_path = data_dir / OBSERVACIONES.name
    validas, rechazadas = validar_lote(filas, leer_csv(obs_path, COLUMNAS), rutas_cfg)
    append_csv(obs_path, [{c: f.get(c, "") for c in COLUMNAS} for f in validas], COLUMNAS)
    archivo_rech = escribir_rechazados(rechazadas, f"rechazados_importacion_{date.today().isoformat()}",
                                       reports_dir)

    # Tipo de cambio implícito por fecha (sólo donde las notas traían el monto en ARS).
    por_fecha: dict[str, list[float]] = {}
    for f in validas:
        if f.get("moneda") == "ARS":
            por_fecha.setdefault(f["fecha_busqueda"], []).append(float(f["fx_usado"]))
    for fecha, valores in por_fecha.items():
        reemplazar_filas(data_dir / FX.name, COLUMNAS_FX, "fecha", fecha, [{
            "fecha": fecha, "valor": round(median(valores), 2),
            "metodo": "implícito ARS/USD de las notas del historial",
            "fuente": path.name}])

    # Una corrida por fecha importada + semanas faltantes.
    for fecha in sorted({f["fecha_busqueda"] for f in validas}):
        de_fecha = [f for f in validas if f["fecha_busqueda"] == fecha]
        con = sorted({f["ruta"] for f in de_fecha}, key=RUTAS.index)
        reemplazar_filas(data_dir / CORRIDAS.name, COLUMNAS_CORRIDAS, "fecha", fecha, [{
            "fecha": fecha, "semana_iso": semana_iso(a_fecha(fecha)), "estado": "importada",
            "rutas_con_datos": ";".join(con), "rutas_sin_datos": ";".join(r for r in RUTAS if r not in con),
            "fuentes_caidas": "", "filas_agregadas": len(de_fecha),
            "filas_rechazadas": sum(1 for f, _ in rechazadas if f["fecha_busqueda"] == fecha),
            "notas": f"carga inicial desde {path.name}"}])
    for fecha in faltantes:
        reemplazar_filas(data_dir / CORRIDAS.name, COLUMNAS_CORRIDAS, "fecha", fecha, [{
            "fecha": fecha, "semana_iso": semana_iso(a_fecha(fecha)), "estado": "faltante",
            "rutas_con_datos": "", "rutas_sin_datos": ";".join(RUTAS), "fuentes_caidas": "",
            "filas_agregadas": 0, "filas_rechazadas": 0,
            "notas": "semana sin búsqueda en el historial"}])
    corridas = leer_csv(data_dir / CORRIDAS.name, COLUMNAS_CORRIDAS).sort_values("fecha")
    corridas.to_csv(data_dir / CORRIDAS.name, index=False, lineterminator="\n")
    return {"importadas": len(validas), "rechazadas": rechazadas,
            "archivo_rechazados": str(archivo_rech) if archivo_rech else ""}


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Carga inicial desde el Excel consolidado.")
    p.add_argument("excel", type=Path, nargs="?",
                   default=DATA / "legado" / "historial-precios-consolidado.xlsx")
    a = p.parse_args(argv)
    r = importar(a.excel)
    print(f"Importadas: {r['importadas']} · Rechazadas: {len(r['rechazadas'])}")
    for f, motivos in r["rechazadas"]:
        print(f"  ✗ {f['fecha_busqueda']} {f['ruta']} {f['serie']} USD {f['precio_usd']}: "
              f"{'; '.join(motivos)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
