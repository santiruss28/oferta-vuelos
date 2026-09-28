import math

import numpy as np
import pandas as pd
import pytest
from conftest import df, fila, indice

import control
from comun import NOTA_NO_HOMOGENEA, rango_semanas


def test_racha():
    assert control.racha([1, 2, 3]) == 2
    assert control.racha([3, 2, 1, 0]) == -3
    assert control.racha([1, 3, 2]) == -1
    assert control.racha([2, 2]) == 0
    assert control.racha([5]) == 0
    assert control.racha([3, np.nan, 2, np.nan, 1]) == -2  # las semanas vacías no cortan


def test_limites_solo_con_6_semanas():
    lcl, ucl, metodo = control.limites([1000, 1010, 990, 1005, 995], 2, 6)
    assert math.isnan(lcl) and math.isnan(ucl) and "banda" in metodo
    v = [1000, 1010, 990, 1005, 995, 1000]
    lcl, ucl, metodo = control.limites(v, 2, 6)
    m, s = np.mean(v), np.std(v, ddof=1)
    assert lcl == pytest.approx(m - 2 * s) and ucl == pytest.approx(m + 2 * s)


def test_serie_semanal_mejor_precio_y_semanas_vacias_sin_interpolar(rutas_cfg):
    obs = df([
        fila(rutas_cfg, fecha_busqueda="2026-09-21", precio_moneda_original=900,
             evidencia="3 mar 17 mar US$900"),
        fila(rutas_cfg, fecha_busqueda="2026-09-22", precio_moneda_original=850,
             fecha_ida="2027-03-04", fecha_vuelta="2027-03-18", evidencia="4 mar 18 mar US$850"),
        fila(rutas_cfg, fecha_busqueda="2026-10-05", precio_moneda_original=800),
    ])
    semanas = rango_semanas("2026-W39", "2026-W41")
    s = control.serie_semanal(obs, "PAR", "fechada", semanas)
    assert s["2026-W39"] == 850
    assert math.isnan(s["2026-W40"])
    assert s["2026-W41"] == 800


def test_indice_excluye_fuente_no_homogenea(rutas_cfg):
    obs = df([indice(rutas_cfg, "2026-10-05", 950),
              indice(rutas_cfg, "2026-10-05", 700, notas=f"{NOTA_NO_HOMOGENEA}. Kayak")])
    s = control.serie_semanal(obs, "PAR", "indice", ["2026-W41"])
    assert s.iloc[0] == 950


def test_estadisticas():
    semanas = [f"2026-W{n}" for n in range(30, 37)]
    s = pd.Series([1000, 980, np.nan, 960, 940, 920, 900], index=semanas)
    st = control.estadisticas(s, k=2, min_semanas=6, ventana=4)
    assert st["n_semanas"] == 6 and st["semanas_vacias"] == 1
    assert st["minimo"] == 900 and st["ultimo"] == 900 and st["semana_ultimo"] == "2026-W36"
    assert st["mediana"] == 950
    assert st["media_4s"] == pytest.approx(np.mean([940, 920, 900, 960]))
    assert st["racha"] == -5
    assert st["metodo_limites"].startswith("μ")
    assert st["tendencia"] == "baja"


def test_estadisticas_pocas_semanas_sin_limites():
    s = pd.Series([1000, 1100], index=["2026-W40", "2026-W41"])
    st = control.estadisticas(s, min_semanas=6)
    assert math.isnan(st["lcl"]) and "banda" in st["metodo_limites"]


def test_semanas_sin_datos(rutas_cfg):
    obs = df([fila(rutas_cfg, fecha_busqueda="2026-09-21", evidencia="3 mar 17 mar US$780"),
              indice(rutas_cfg, "2026-10-05", 950)])
    semanas = rango_semanas("2026-W39", "2026-W41")
    t = control.semanas_sin_datos(obs, semanas)
    assert list(t[t["sin_ningun_dato"] == "si"]["semana_iso"]) == ["2026-W40"]
    w41 = t[t["semana_iso"] == "2026-W41"].iloc[0]
    assert "PAR" not in w41["rutas_sin_indice"] and "PAR" in w41["rutas_sin_fechada"]


def test_vigentes_filtra_por_ventana_del_viaje(rutas_cfg):
    from comun import COLUMNAS_CORRIDAS
    cfg = {**rutas_cfg, "viaje": {**rutas_cfg["viaje"], "salida_desde": "2027-06-01",
                                  "salida_hasta": "2027-07-31", "meses_indice": ["2027-06"],
                                  "seguimiento_desde": "2026-09-28"}}
    obs = df([
        fila(rutas_cfg, fecha_ida="2027-03-03", fecha_vuelta="2027-03-17"),   # viaje anterior
        fila(rutas_cfg, fecha_ida="2027-06-09", fecha_vuelta="2027-06-23"),
        fila(rutas_cfg, fecha_ida="2027-08-04", fecha_vuelta="2027-08-18"),   # fuera de la ventana
        fila(rutas_cfg, serie="indice", fuente="Turismocity", fecha_ida="2027-06-09", fecha_vuelta="",
             evidencia=""),
        indice(rutas_cfg, "2026-10-05", 900),                                  # índice de feb
    ])
    v = control.vigentes(obs, cfg)
    assert sorted(v["fecha_ida"]) == ["2027-06-09", "2027-06-09"]
    corr = pd.DataFrame([{"fecha": "2026-09-27"}, {"fecha": "2026-10-05"}], columns=COLUMNAS_CORRIDAS)
    assert list(control.corridas_vigentes(corr, cfg)["fecha"]) == ["2026-10-05"]
