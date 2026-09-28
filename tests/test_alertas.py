from datetime import date

import pandas as pd
from conftest import df, fila, indice, lunes

import alertas
from comun import COLUMNAS_ALERTAS, COLUMNAS_CORRIDAS, IMPORTADO_SIN_EVIDENCIA

HOY = date(2026, 10, 5)  # lunes, semana 2026-W41; faltan 119 días para el 01/02/2027
SIN_EMITIDAS = pd.DataFrame(columns=COLUMNAS_ALERTAS)


def corridas(*filas):
    return pd.DataFrame([{c: f.get(c, "") for c in COLUMNAS_CORRIDAS} for f in filas],
                        columns=COLUMNAS_CORRIDAS)


def evaluar(obs, rutas_cfg, cfg, fecha=HOY, emitidas=SIN_EMITIDAS, corr=None, rech=0):
    return alertas.evaluar(fecha, obs, corr if corr is not None else corridas(), emitidas,
                           rutas_cfg, cfg, rech)


def reglas(res, nivel=None):
    return [a["regla"] for a in res["alertas"] if nivel is None or a["nivel"] == nivel]


def fechada(rutas_cfg, precio, ruta="PAR", fecha_busqueda="2026-10-05", ida="2027-03-03",
            vuelta="2027-03-17", **kw):
    d_ida, d_vuelta = int(ida[-2:]), int(vuelta[-2:])
    return fila(rutas_cfg, ruta=ruta, fecha_busqueda=fecha_busqueda, fecha_ida=ida,
                fecha_vuelta=vuelta, precio_moneda_original=precio, incluye_valija="si",
                evidencia=f"{d_ida} mar – {d_vuelta} mar US${precio}", **kw)


# ---------------------------------------------------------------- COMPRAR / OPORTUNIDAD

def test_r1_comprar_bajo_umbral(rutas_cfg, cfg):
    umbral = cfg["umbrales_usd"]["PAR"]
    res = evaluar(df([fechada(rutas_cfg, umbral)]), rutas_cfg, cfg)
    comprar = [a for a in res["alertas"] if a["nivel"] == "COMPRAR"]
    assert [a["regla"] for a in comprar] == ["R1_umbral"]
    a = comprar[0]
    assert a["umbral_usd"] == umbral and a["dias"] == 14 and a["evidencia"]
    assert len(a["alert_id"]) == 40


def test_r1_no_aplica_a_indice_ni_a_importadas(rutas_cfg, cfg):
    obs = df([indice(rutas_cfg, "2026-10-05", 500),
              fechada(rutas_cfg, 500, origen_dato=IMPORTADO_SIN_EVIDENCIA)])
    res = evaluar(obs, rutas_cfg, cfg)
    assert not reglas(res, "COMPRAR") and not reglas(res, "OPORTUNIDAD")


def test_r1_no_aplica_a_semanas_anteriores(rutas_cfg, cfg):
    obs = df([fechada(rutas_cfg, 500, fecha_busqueda="2026-09-28")])
    assert not reglas(evaluar(obs, rutas_cfg, cfg), "COMPRAR")


def test_r3_cerca_del_umbral(rutas_cfg, cfg):
    umbral = cfg["umbrales_usd"]["ROM"]
    limite = int(umbral * cfg["reglas"]["r3_oportunidad"]["factor_umbral"])
    res = evaluar(df([fechada(rutas_cfg, limite, ruta="ROM")]), rutas_cfg, cfg)
    assert reglas(res, "OPORTUNIDAD") == ["R3_cerca_umbral"]
    res = evaluar(df([fechada(rutas_cfg, limite + 2, ruta="ROM")]), rutas_cfg, cfg)
    assert not reglas(res, "OPORTUNIDAD")


def test_r3_percentil_con_4_semanas(rutas_cfg, cfg):
    hist = [fechada(rutas_cfg, p, fecha_busqueda=lunes(i + 1)) for i, p in
            enumerate([1200, 1150, 1100, 1250])]
    res = evaluar(df(hist + [fechada(rutas_cfg, 1090)]), rutas_cfg, cfg)
    assert "R3_percentil" in reglas(res, "OPORTUNIDAD")
    res = evaluar(df(hist[:3] + [fechada(rutas_cfg, 1090)]), rutas_cfg, cfg)  # n=3
    assert "R3_percentil" not in reglas(res)


def test_r2_minimo_historico_bajo_lcl(rutas_cfg, cfg):
    precios = [1200, 1210, 1190, 1205, 1195, 1200]
    hist = [fechada(rutas_cfg, p, fecha_busqueda=lunes(i + 1)) for i, p in enumerate(precios)]
    res = evaluar(df(hist + [fechada(rutas_cfg, 1100)]), rutas_cfg, cfg)
    assert "R2_minimo_bajo_lcl" in reglas(res, "COMPRAR")
    res = evaluar(df(hist[:5] + [fechada(rutas_cfg, 1100)]), rutas_cfg, cfg)  # n=5
    assert "R2_minimo_bajo_lcl" not in reglas(res)


def test_r4_alternativa_mas_barata_que_principal(rutas_cfg, cfg):
    conexion = rutas_cfg["rutas"]["MAD"]["costo_conexion_usd"]
    principal = fechada(rutas_cfg, 1200)
    alt = fechada(rutas_cfg, 1200 - 60 - conexion, ruta="MAD", destino="MAD")
    res = evaluar(df([principal, alt]), rutas_cfg, cfg)
    r4 = [a for a in res["alertas"] if a["regla"] == "R4_alternativa"]
    assert len(r4) == 1 and r4[0]["ruta"] == "MAD" and r4[0]["nivel"] == "OPORTUNIDAD"
    alt = fechada(rutas_cfg, 1200 - 59 - conexion, ruta="MAD", destino="MAD")
    assert "R4_alternativa" not in reglas(evaluar(df([principal, alt]), rutas_cfg, cfg))


def test_una_alerta_por_tarifa_con_el_mayor_nivel(rutas_cfg, cfg):
    res = evaluar(df([fechada(rutas_cfg, 700)]), rutas_cfg, cfg)
    assert reglas(res, "COMPRAR") == ["R1_umbral"] and not reglas(res, "OPORTUNIDAD")


# ---------------------------------------------------------------- ATENCIÓN

def test_r5_indice_baja_3_semanas(rutas_cfg, cfg):
    obs = df([indice(rutas_cfg, lunes(3), 1000), indice(rutas_cfg, lunes(2), 990),
              indice(rutas_cfg, lunes(1), 980), indice(rutas_cfg, lunes(0), 970)])
    res = evaluar(obs, rutas_cfg, cfg)
    assert "R5_indice_baja" in reglas(res, "ATENCION")
    obs = df([indice(rutas_cfg, lunes(2), 990), indice(rutas_cfg, lunes(1), 980),
              indice(rutas_cfg, lunes(0), 970)])
    assert "R5_indice_baja" not in reglas(evaluar(obs, rutas_cfg, cfg))


def test_r5_indice_sube_mas_de_10_pct(rutas_cfg, cfg):
    base = [indice(rutas_cfg, lunes(i), 1000) for i in (4, 3, 2, 1)]
    tope = 1100  # justo +10% sobre la media de las 4 semanas previas: todavía no alerta
    assert "R5_indice_sube" not in reglas(evaluar(df(base + [indice(rutas_cfg, lunes(0), tope)]),
                                                  rutas_cfg, cfg))
    assert "R5_indice_sube" in reglas(evaluar(df(base + [indice(rutas_cfg, lunes(0), tope + 5)]),
                                              rutas_cfg, cfg), "ATENCION")


def test_r6_ventana_que_se_cierra(rutas_cfg, cfg):
    obs = df([indice(rutas_cfg, lunes(1), 1000), indice(rutas_cfg, lunes(0), 1010)])
    assert "R6_ventana_cierra" in reglas(evaluar(obs, rutas_cfg, cfg), "ATENCION")
    # 10 semanas antes faltaban > 120 días: no aplica.
    antes = df([indice(rutas_cfg, lunes(11), 1000), indice(rutas_cfg, lunes(10), 1010)])
    assert "R6_ventana_cierra" not in reglas(evaluar(antes, rutas_cfg, cfg,
                                                     fecha=date(2026, 7, 27)))
    # Índice bajando: no aplica.
    obs = df([indice(rutas_cfg, lunes(1), 1010), indice(rutas_cfg, lunes(0), 1000)])
    assert "R6_ventana_cierra" not in reglas(evaluar(obs, rutas_cfg, cfg))


# ---------------------------------------------------------------- INFO

def test_r7_info(rutas_cfg, cfg):
    obs = df([fechada(rutas_cfg, 1200), indice(rutas_cfg, "2026-10-05", 1000)])
    corr = corridas(
        {"fecha": "2026-09-14", "semana_iso": "2026-W38", "estado": "ok"},
        {"fecha": "2026-10-05", "semana_iso": "2026-W41", "estado": "ok",
         "fuentes_caidas": "Google Flights (captcha);Kayak"},
    )
    res = evaluar(obs, rutas_cfg, cfg, corr=corr, rech=2)
    info = {a["regla"]: a for a in res["alertas"] if a["nivel"] == "INFO"}
    sin_datos = [a["ruta"] for a in res["alertas"] if a["regla"] == "R7_sin_datos"]
    assert sin_datos == ["ROM", "MIL", "MAD", "BCN"]
    assert sum(a["regla"] == "R7_fuente_caida" for a in res["alertas"]) == 2
    assert "2 fila(s)" in info["R7_rechazados"]["mensaje"]
    assert "2026-W39, 2026-W40" in info["R7_semanas_faltantes"]["mensaje"]


# ---------------------------------------------------------------- anti-spam

def emitida(fecha, precio, regla="R1_umbral"):
    return pd.DataFrame([{"fecha_emision": fecha, "alert_id": "x", "nivel": "COMPRAR", "regla": regla,
                          "ruta": "PAR", "fecha_ida": "2027-03-03", "fecha_vuelta": "2027-03-17",
                          "precio_total_usd": str(precio)}], columns=COLUMNAS_ALERTAS)


def test_antispam_no_repite_dentro_de_14_dias(rutas_cfg, cfg):
    obs = df([fechada(rutas_cfg, 780)])
    res = evaluar(obs, rutas_cfg, cfg, emitidas=emitida("2026-09-28", 780))
    assert "R1_umbral" not in reglas(res)
    assert res["suprimidas"][0]["regla"] == "R1_umbral"


def test_antispam_repite_si_baja_3_pct(rutas_cfg, cfg):
    res = evaluar(df([fechada(rutas_cfg, 756)]), rutas_cfg, cfg, emitidas=emitida("2026-09-28", 780))
    assert "R1_umbral" in reglas(res)  # 756 <= 780 × 0,97
    res = evaluar(df([fechada(rutas_cfg, 760)]), rutas_cfg, cfg, emitidas=emitida("2026-09-28", 780))
    assert "R1_umbral" not in reglas(res)


def test_antispam_vence_a_los_14_dias(rutas_cfg, cfg):
    res = evaluar(df([fechada(rutas_cfg, 780)]), rutas_cfg, cfg, emitidas=emitida("2026-09-20", 780))
    assert "R1_umbral" in reglas(res)


def test_ejecutar_escribe_json_y_es_idempotente(rutas_cfg, dirs):
    from comun import COLUMNAS, leer_csv
    d, r = dirs
    obs = df([fechada(rutas_cfg, 780)])
    obs.to_csv(d / "observaciones.csv", index=False, columns=COLUMNAS)
    alertas.ejecutar(HOY, d, r)
    res = alertas.ejecutar(HOY, d, r)  # re-ejecutar el mismo día no se auto-suprime
    assert "R1_umbral" in reglas(res)
    assert (r / "alertas_2026-10-05.json").exists()
    emit = leer_csv(d / "alertas_emitidas.csv", COLUMNAS_ALERTAS)
    assert list(emit["regla"]) == ["R1_umbral"]
