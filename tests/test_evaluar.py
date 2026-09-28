from datetime import date

import pytest
from conftest import df, fila, indice, lunes

import alertas
from comun import COLUMNAS_ALERTAS, COLUMNAS_CORRIDAS
from contexto import contexto
from evaluar import evaluar

HOY = date(2026, 10, 5)


def fechada(rutas_cfg, precio, fecha_busqueda, ruta="PAR"):
    return fila(rutas_cfg, ruta=ruta, fecha_busqueda=fecha_busqueda, precio_moneda_original=precio,
                incluye_valija="si", evidencia=f"3 mar – 17 mar US${precio}")


@pytest.fixture
def obs(rutas_cfg):
    precios = [1000, 1050, 1100, 1150, 1200]
    return df([fechada(rutas_cfg, p, lunes(i + 1)) for i, p in enumerate(precios)]
              + [indice(rutas_cfg, lunes(2), 1000), indice(rutas_cfg, lunes(1), 990)])


def test_posicion_historica(obs, rutas_cfg, cfg):
    ctx = contexto(obs, "PAR", 1060, HOY, rutas_cfg, cfg)
    assert ctx["pct_mas_caras"] == 60 and ctx["n_tarifas"] == 5 and ctx["n_semanas"] == 5
    assert "Más barata que el 60% de las 5 tarifas" in ctx["lineas"][0]
    assert contexto(obs, "PAR", 900, HOY, rutas_cfg, cfg)["lineas"][0].startswith("Más barata que todas")
    assert ctx["confiabilidad"] == "media"
    texto = " ".join(ctx["lineas"])
    assert "USD 260 sobre el umbral" in texto and "USD 60 sobre el mínimo histórico" in texto
    assert "Índice París: baja" in texto


@pytest.mark.parametrize("total,esperado", [
    (800, "COMPRAR"), (860, "OPORTUNIDAD"), (1000, "OPORTUNIDAD"),  # <= percentil 20
    (1100, "NORMAL"), (1190, "CARO"),                                 # >= percentil 80
    (1600, "CARO"),                                                   # sobre la banda
])
def test_veredicto(obs, rutas_cfg, cfg, total, esperado):
    assert contexto(obs, "PAR", total, HOY, rutas_cfg, cfg)["veredicto"] == esperado


@pytest.mark.parametrize("precio", [700, 800, 850, 870, 1000, 1100])
def test_veredicto_coincide_con_las_alertas(obs, rutas_cfg, cfg, precio):
    """evaluar.py y alertas.py aplican las mismas reglas de COMPRAR/OPORTUNIDAD."""
    import pandas as pd
    nueva = fechada(rutas_cfg, precio, "2026-10-05")
    todo = pd.concat([obs, df([nueva])], ignore_index=True)
    res = alertas.evaluar(HOY, todo, pd.DataFrame(columns=COLUMNAS_CORRIDAS),
                          pd.DataFrame(columns=COLUMNAS_ALERTAS), rutas_cfg, cfg)
    niveles = [a["nivel"] for a in res["alertas"] if a["ruta"] == "PAR" and a["fecha_ida"]]
    nivel_alerta = niveles[0] if niveles else "NINGUNA"
    v = contexto(obs, "PAR", precio, HOY, rutas_cfg, cfg)["veredicto"]
    assert (v if v in ("COMPRAR", "OPORTUNIDAD") else "NINGUNA") == nivel_alerta


def test_evaluar_suma_valija_y_conexion_y_convierte_ars(obs, rutas_cfg, cfg):
    r = evaluar("MAD", 1_480_000, "2027-02-11", "2027-02-25", moneda="ARS", fx=1480,
                fecha=HOY, obs=obs, rutas_cfg=rutas_cfg, cfg=cfg)
    f = r["fila"]
    assert f["precio_usd"] == 1000 and f["valija_estimada"] == "si"
    assert f["precio_total_usd"] == 1000 + 70 + 120 and r["avisos"] == []
    assert "conexión low-cost" in " ".join(r["contexto"]["lineas"])


def test_evaluar_avisa_estadia_fuera_de_rango(obs, rutas_cfg, cfg):
    r = evaluar("PAR", 800, "2027-03-03", "2027-03-31", fecha=HOY, obs=obs, rutas_cfg=rutas_cfg, cfg=cfg)
    assert "28 días" in r["avisos"][0]


def test_evaluar_no_escribe_datos(obs, rutas_cfg, cfg, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    evaluar("PAR", 800, fecha=HOY, obs=obs, rutas_cfg=rutas_cfg, cfg=cfg)
    assert list(tmp_path.iterdir()) == []
