"""Fixtures compartidas: datos sintéticos y directorios temporales."""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from comun import COLUMNAS, cargar_alertas_cfg, cargar_rutas, semana_iso, tipar  # noqa: E402
from validar import completar  # noqa: E402


@pytest.fixture
def rutas_cfg():
    return cargar_rutas()


@pytest.fixture
def cfg():
    return cargar_alertas_cfg()


@pytest.fixture
def dirs(tmp_path):
    d, r = tmp_path / "data", tmp_path / "reports"
    d.mkdir()
    r.mkdir()
    return d, r


def fila(rutas_cfg, **kw) -> dict:
    """Observación completa con valores por defecto razonables (fechada PAR con evidencia)."""
    base = {
        "fecha_busqueda": "2026-10-05", "ruta": "PAR", "serie": "fechada", "origen": "EZE",
        "destino": "CDG", "aerolinea": "Air France", "escalas": 0,
        "fecha_ida": "2027-03-03", "fecha_vuelta": "2027-03-17",
        "precio_moneda_original": 780, "moneda": "USD", "precio_usd": 780, "fx_usado": "",
        "incluye_valija": "si", "fuente": "Google Flights", "url": "https://example.com/vuelo",
        "evidencia": "mié, 3 mar – mié, 17 mar · Air France · 1 escala · US$780",
        "notas": "", "origen_dato": "rutina",
    }
    base.update(kw)
    if "precio_usd" not in kw and "precio_moneda_original" in kw and base["moneda"] == "USD":
        base["precio_usd"] = kw["precio_moneda_original"]
    if base["serie"] == "indice":
        base.setdefault("fuente", "Turismocity")
    return completar(base, rutas_cfg)


def indice(rutas_cfg, fecha_busqueda, precio, ruta="PAR", **kw) -> dict:
    return fila(rutas_cfg, fecha_busqueda=fecha_busqueda, ruta=ruta, serie="indice",
                fuente="Turismocity", aerolinea="", fecha_ida="2027-02-01", fecha_vuelta="",
                precio_moneda_original=precio, precio_usd=precio, evidencia="", **kw)


def df(filas: list[dict]) -> pd.DataFrame:
    d = pd.DataFrame([{c: f.get(c, "") for c in COLUMNAS} for f in filas], columns=COLUMNAS)
    d = d.astype(str).replace({"None": "", "nan": ""})
    return tipar(d)


def lunes(semanas_atras: int, desde="2026-10-05") -> str:
    """Fecha (lunes) N semanas antes de `desde`."""
    return (pd.Timestamp(desde) - pd.Timedelta(weeks=semanas_atras)).date().isoformat()


__all__ = ["fila", "indice", "df", "lunes", "semana_iso"]
