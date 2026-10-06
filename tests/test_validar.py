from datetime import date

import pandas as pd
import pytest
from conftest import fila

import agregar
from comun import COLUMNAS, IMPORTADO_SIN_EVIDENCIA, leer_csv
from validar import fecha_en_evidencia, precio_en_evidencia, validar_fila, validar_lote

VACIO = pd.DataFrame(columns=COLUMNAS)


# ---------------------------------------------------------------- evidencia

@pytest.mark.parametrize("texto", [
    "Ida 03/03/2027 vuelta 17/03", "3/3", "2027-03-03", "mié, 3 mar", "3 de marzo",
    "Mar 3, 2027", "MIÉ. 3 MAR.", "03-03",
])
def test_fecha_en_evidencia_formatos(texto):
    assert fecha_en_evidencia(texto, date(2027, 3, 3))


@pytest.mark.parametrize("texto", ["13/03", "3/10", "30 mar", "marzo 13", "23-03"])
def test_fecha_en_evidencia_no_confunde(texto):
    assert not fecha_en_evidencia(texto, date(2027, 3, 3))


@pytest.mark.parametrize("texto,precio", [
    ("US$780", 780), ("USD 1.020 i/v", 1020), ("$ 1.358.227", 1358227),
    ("total ARS 1 358 227", 1358227), ("US$ 779,60", 780), ("USD 1,020", 1020),
])
def test_precio_en_evidencia(texto, precio):
    assert precio_en_evidencia(texto, precio)


def test_precio_no_en_evidencia():
    assert not precio_en_evidencia("US$ 870 · 3 mar – 17 mar", 780)


# ---------------------------------------------------------------- reglas de fechadas

def test_fechada_valida(rutas_cfg):
    assert validar_fila(fila(rutas_cfg), rutas_cfg) == []


def test_fechada_sin_evidencia(rutas_cfg):
    assert "fechada sin evidencia" in validar_fila(fila(rutas_cfg, evidencia=""), rutas_cfg)


def test_fechas_y_precio_deben_estar_en_evidencia(rutas_cfg):
    f = fila(rutas_cfg, evidencia="Air France desde US$ 999 en marzo")
    motivos = " | ".join(validar_fila(f, rutas_cfg))
    assert "fecha de ida" in motivos and "fecha de vuelta" in motivos and "precio 780" in motivos


@pytest.mark.parametrize("vuelta,ok", [("2027-03-15", False), ("2027-03-16", True),
                                       ("2027-03-19", True), ("2027-03-20", False)])
def test_estadia_13_a_16(rutas_cfg, vuelta, ok):
    ev = f"3 mar – {int(vuelta[-2:])} mar · US$780"
    f = fila(rutas_cfg, fecha_vuelta=vuelta, evidencia=ev)
    motivos = validar_fila(f, rutas_cfg)
    assert (not any("estadía" in m for m in motivos)) == ok


def test_ida_anterior_a_febrero_2027(rutas_cfg):
    f = fila(rutas_cfg, fecha_ida="2027-01-20", fecha_vuelta="2027-02-03",
             evidencia="20 ene – 3 feb US$780")
    assert any("anterior a 2027-02-01" in m for m in validar_fila(f, rutas_cfg))


@pytest.mark.parametrize("precio,ok", [(0, False), (-5, False), (1, True), (5000, True), (5001, False)])
def test_rango_de_precio(rutas_cfg, precio, ok):
    f = fila(rutas_cfg, precio_moneda_original=precio, evidencia=f"3 mar 17 mar US${precio}")
    assert (validar_fila(f, rutas_cfg) == []) == ok


def test_importada_exceptuada_de_evidencia(rutas_cfg):
    f = fila(rutas_cfg, evidencia="", origen_dato=IMPORTADO_SIN_EVIDENCIA)
    assert validar_fila(f, rutas_cfg) == []
    f = fila(rutas_cfg, evidencia="", origen_dato=IMPORTADO_SIN_EVIDENCIA, fecha_vuelta="2027-03-30")
    assert any("estadía" in m for m in validar_fila(f, rutas_cfg))


# ---------------------------------------------------------------- índice

def test_indice_debe_venir_de_la_fuente_configurada(rutas_cfg):
    f = fila(rutas_cfg, serie="indice", fuente="Kayak", evidencia="", fecha_ida="2027-02-01")
    assert any("Turismocity" in m for m in validar_fila(f, rutas_cfg))
    f = fila(rutas_cfg, serie="indice", fuente="Turismocity", evidencia="", fecha_ida="2027-02-01",
             fecha_vuelta="")
    assert validar_fila(f, rutas_cfg) == []


def test_indice_fuera_de_los_meses_seguidos(rutas_cfg):
    f = fila(rutas_cfg, serie="indice", fuente="Turismocity", evidencia="", fecha_ida="2027-05-01",
             fecha_vuelta="")
    assert any("meses seguidos" in m for m in validar_fila(f, rutas_cfg))


# ---------------------------------------------------------------- valija y total

def test_valija_estimada_si_no_viene(rutas_cfg):
    recargo = rutas_cfg["viaje"]["recargo_valija_usd"]
    for incluye in ("no", "desconocido", ""):
        f = fila(rutas_cfg, incluye_valija=incluye)
        assert f["precio_con_valija_usd"] == 780 + recargo
        assert f["valija_estimada"] == "si"


def test_valija_real(rutas_cfg):
    f = fila(rutas_cfg, incluye_valija="no", precio_con_valija_usd=845)
    assert f["precio_con_valija_usd"] == 845 and f["valija_estimada"] == "no"
    f = fila(rutas_cfg, incluye_valija="si")
    assert f["precio_con_valija_usd"] == 780 and f["valija_estimada"] == "no"


def test_total_de_alternativa_suma_conexion(rutas_cfg):
    f = fila(rutas_cfg, ruta="MAD", destino="MAD", incluye_valija="si")
    assert f["categoria"] == "alternativa"
    assert f["precio_total_usd"] == 780 + rutas_cfg["rutas"]["MAD"]["costo_conexion_usd"]


# ---------------------------------------------------------------- duplicados y lote

def test_duplicados_en_historico_y_en_lote(rutas_cfg):
    a = fila(rutas_cfg)
    validas, rech = validar_lote([a, dict(a)], VACIO, rutas_cfg)
    assert len(validas) == 1 and rech[0][1] == ["duplicado"]
    existentes = pd.DataFrame([{c: a.get(c, "") for c in COLUMNAS}])
    validas, rech = validar_lote([a], existentes, rutas_cfg)
    assert not validas and "duplicado" in rech[0][1]
    otra_fecha = dict(a, fecha_busqueda="2026-10-12")
    validas, _ = validar_lote([otra_fecha], existentes, rutas_cfg)
    assert len(validas) == 1


# ---------------------------------------------------------------- agregar.py

def test_agregar_convierte_ars_y_escribe_rechazados(rutas_cfg, dirs):
    d, r = dirs
    entrada = {
        "fecha_busqueda": "2026-10-05",
        "fx": {"valor": 1500, "metodo": "oficial BNA vendedor", "fuente": "bna"},
        "fuentes_caidas": [{"fuente": "Google Flights", "detalle": "captcha"}],
        "observaciones": [
            {"ruta": "ROM", "serie": "fechada", "aerolinea": "ITA", "fecha_ida": "2027-02-10",
             "fecha_vuelta": "2027-02-24", "precio_moneda_original": 1350000, "moneda": "ARS",
             "incluye_valija": "si", "fuente": "Kayak", "url": "https://k",
             "evidencia": "10 feb – 24 feb · ITA · $ 1.350.000"},
            {"ruta": "ROM", "serie": "fechada", "aerolinea": "ITA", "fecha_ida": "2027-02-10",
             "fecha_vuelta": "2027-02-28", "precio_moneda_original": 900, "moneda": "USD",
             "fuente": "Kayak", "evidencia": "10 feb – 28 feb US$900"},
        ],
    }
    res = agregar.agregar(entrada, date(2026, 10, 5), d, r)
    assert res["filas_agregadas"] == 1 and res["filas_rechazadas"] == 1
    obs = leer_csv(d / "observaciones.csv", COLUMNAS)
    assert float(obs.loc[0, "precio_usd"]) == 900.0 and obs.loc[0, "fx_usado"] == "1500.0"
    assert obs.loc[0, "semana_iso"] == "2026-W41"
    rech = pd.read_csv(r / "rechazados_2026-10-05.csv")
    assert "estadía de 18 días" in rech.loc[0, "motivo"]
    corridas = pd.read_csv(d / "corridas.csv", keep_default_na=False)
    assert corridas.loc[0, "rutas_con_datos"] == "ROM"
    assert "Google Flights (captcha)" in corridas.loc[0, "fuentes_caidas"]
    assert pd.read_csv(d / "fx.csv").loc[0, "valor"] == 1500


def test_el_ejemplo_de_entrada_de_la_documentacion_es_valido(dirs):
    import json
    from pathlib import Path
    d, r = dirs
    ejemplo = Path(__file__).resolve().parent.parent / "docs" / "entrada-ejemplo.json"
    res = agregar.agregar(json.loads(ejemplo.read_text(encoding="utf-8")), None, d, r)
    assert res["filas_rechazadas"] == 0 and res["filas_agregadas"] == 3


def test_ida_posterior_a_la_ventana(rutas_cfg):
    cfg = {**rutas_cfg, "viaje": {**rutas_cfg["viaje"], "salida_hasta": "2027-03-01"}}
    f = fila(cfg)
    assert any("posterior a 2027-03-01" in m for m in validar_fila(f, cfg))
