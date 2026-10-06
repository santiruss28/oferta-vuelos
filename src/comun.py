"""Rutas de archivos, esquemas CSV y utilidades compartidas por todos los scripts."""
from __future__ import annotations

import json
import os
from datetime import date, datetime
from pathlib import Path

import pandas as pd
import yaml

RAIZ = Path(__file__).resolve().parent.parent
DATA = RAIZ / "data"
# OFERTA_VUELOS_CONFIG permite usar otra carpeta de config (los tests usan tests/config).
CONFIG = Path(os.environ.get("OFERTA_VUELOS_CONFIG", RAIZ / "config"))
REPORTS = RAIZ / "reports"

OBSERVACIONES = DATA / "observaciones.csv"
FX = DATA / "fx.csv"
CORRIDAS = DATA / "corridas.csv"
ALERTAS_EMITIDAS = DATA / "alertas_emitidas.csv"
NOTIFICACIONES = DATA / "notificaciones.csv"
PENDIENTES = DATA / "notificaciones_pendientes.jsonl"

COLUMNAS = [
    "fecha_busqueda", "semana_iso", "ruta", "categoria", "serie", "origen", "destino",
    "aerolinea", "escalas", "fecha_ida", "fecha_vuelta", "dias_estadia",
    "precio_moneda_original", "moneda", "precio_usd", "fx_usado",
    "incluye_valija", "precio_con_valija_usd", "valija_estimada",
    "costo_conexion_usd", "precio_total_usd",
    "fuente", "url", "evidencia", "notas", "origen_dato",
]
COLUMNAS_FX = ["fecha", "valor", "metodo", "fuente"]
COLUMNAS_CORRIDAS = [
    "fecha", "semana_iso", "estado", "rutas_con_datos", "rutas_sin_datos",
    "fuentes_caidas", "filas_agregadas", "filas_rechazadas", "notas",
]
COLUMNAS_ALERTAS = [
    "fecha_emision", "alert_id", "nivel", "regla", "ruta", "fecha_ida", "fecha_vuelta",
    "precio_total_usd",
]
COLUMNAS_NOTIFICACIONES = [
    "fecha_hora", "tipo", "fecha_corrida", "resultado", "status_http", "intentos", "detalle",
]

RUTAS = ("PAR", "ROM", "MIL", "MAD", "BCN")
PRINCIPALES = ("PAR", "ROM", "MIL")
ALTERNATIVAS = ("MAD", "BCN")
SERIES = ("indice", "fechada")
MONEDAS = ("USD", "ARS")
VALIJA = ("si", "no", "desconocido")

# Filas de la carga inicial: cuentan para el historial, pero no tienen evidencia
# y por eso nunca disparan alertas.
IMPORTADO_SIN_EVIDENCIA = "importado_sin_evidencia"
NOTA_NO_HOMOGENEA = "fuente no homogénea"

NIVELES = ("COMPRAR", "OPORTUNIDAD", "ATENCION", "INFO")
ETIQUETA = {"COMPRAR": "COMPRAR", "OPORTUNIDAD": "OPORTUNIDAD", "ATENCION": "ATENCIÓN", "INFO": "INFO"}


# ---------------------------------------------------------------- configuración

def cargar_yaml(nombre: str, config_dir: Path | None = None) -> dict:
    with open((config_dir or CONFIG) / nombre, encoding="utf-8") as f:
        return yaml.safe_load(f)


def cargar_rutas(config_dir: Path | None = None) -> dict:
    return cargar_yaml("rutas.yaml", config_dir)


def cargar_alertas_cfg(config_dir: Path | None = None) -> dict:
    return cargar_yaml("alertas.yaml", config_dir)


# ---------------------------------------------------------------- fechas

def a_fecha(v) -> date | None:
    """Convierte str/datetime/Timestamp a date. Vacío o inválido → None."""
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    s = str(v).strip()
    if not s:
        return None
    try:
        return date.fromisoformat(s[:10])
    except ValueError:
        return None


def semana_iso(d: date) -> str:
    """'2026-W39' para la semana ISO de la fecha."""
    a, s, _ = d.isocalendar()
    return f"{a}-W{s:02d}"


def lunes_de(semana: str) -> date:
    a, s = semana.split("-W")
    return date.fromisocalendar(int(a), int(s), 1)


def rango_semanas(desde: str, hasta: str) -> list[str]:
    """Todas las semanas ISO entre dos semanas (inclusive)."""
    d, h = lunes_de(desde), lunes_de(hasta)
    out = []
    while d <= h:
        out.append(semana_iso(d))
        d = date.fromordinal(d.toordinal() + 7)
    return out


def fmt_fecha(v) -> str:
    """AAAA-MM-DD → DD/MM/AAAA (para textos)."""
    d = a_fecha(v)
    return d.strftime("%d/%m/%Y") if d else ""


# ---------------------------------------------------------------- CSV

def vacio(v) -> bool:
    return v is None or (isinstance(v, float) and pd.isna(v)) or str(v).strip() == ""


def num(v) -> float | None:
    if vacio(v):
        return None
    try:
        return float(v)
    except (ValueError, TypeError):
        return None


def leer_csv(path: Path, columnas: list[str]) -> pd.DataFrame:
    """Lee un CSV como texto (sin NaN). Si no existe, devuelve un DataFrame vacío."""
    if not path.exists() or path.stat().st_size == 0:
        return pd.DataFrame(columns=columnas)
    df = pd.read_csv(path, dtype=str, keep_default_na=False)
    for c in columnas:
        if c not in df.columns:
            df[c] = ""
    return df[columnas]


NUMERICAS = ("escalas", "dias_estadia", "precio_moneda_original", "precio_usd", "fx_usado",
             "precio_con_valija_usd", "costo_conexion_usd", "precio_total_usd")


def tipar(df: pd.DataFrame) -> pd.DataFrame:
    """Columnas numéricas a float; fechas quedan como texto AAAA-MM-DD."""
    df = df.copy()
    for c in NUMERICAS:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c].replace("", None), errors="coerce")
    return df


def leer_observaciones(path: Path | None = None) -> pd.DataFrame:
    return tipar(leer_csv(path or OBSERVACIONES, COLUMNAS))


def append_csv(path: Path, filas: list[dict], columnas: list[str]) -> None:
    """Agrega filas al final de un CSV (crea el encabezado si no existe)."""
    if not filas:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    nuevo = pd.DataFrame(filas, columns=columnas)
    existe = path.exists() and path.stat().st_size > 0
    nuevo.to_csv(path, mode="a", header=not existe, index=False, lineterminator="\n")


def reemplazar_filas(path: Path, columnas: list[str], clave: str, valor: str,
                     filas: list[dict]) -> None:
    """Reescribe un CSV de estado quitando las filas con clave==valor y agregando las nuevas.

    Se usa en archivos de estado (corridas, alertas emitidas) para que re-ejecutar
    la corrida de un mismo día no duplique filas. observaciones.csv nunca se reescribe.
    """
    df = leer_csv(path, columnas)
    df = df[df[clave] != valor]
    df = pd.concat([df, pd.DataFrame(filas, columns=columnas)], ignore_index=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False, lineterminator="\n")


def escribir_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2, default=str)
        f.write("\n")


def fmt_usd(v) -> str:
    n = num(v)
    if n is None:
        return "–"
    return f"USD {n:,.0f}".replace(",", ".")
