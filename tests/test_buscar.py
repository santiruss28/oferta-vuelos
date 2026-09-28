import subprocess
from datetime import date
from pathlib import Path

import pandas as pd

import agregar
import buscar

PLANTILLA = (Path(__file__).parent / "fixtures" / "google_flights_min.html").read_text(encoding="utf-8")
HOY = date(2026, 10, 5)


def pagina(ida="2027-03-03", vuelta="2027-03-17"):
    return PLANTILLA.replace("{ida}", ida).replace("{vuelta}", vuelta)


def fake(paginas_por_llamada=None):
    """bajar() falso: devuelve la página con las fechas pedidas en la URL."""
    llamadas = []

    def bajar(url, presupuesto):
        from urllib.parse import parse_qs, urlparse
        q = parse_qs(urlparse(url).query)["q"][0]
        ida, vuelta = q.split(" on ")[1].split(" through ")
        llamadas.append((q, presupuesto))
        if paginas_por_llamada is not None:
            return paginas_por_llamada.pop(0)
        return pagina(ida, vuelta)
    bajar.llamadas = llamadas
    return bajar


def test_parsear():
    r = buscar.parsear(pagina())
    assert r["fechas"] == ("2027-03-03", "2027-03-17")
    assert r["encabezado"] == "salida el 2027-03-03 y vuelta el 2027-03-17"
    precios = [t["precio"] for t in r["tarifas"]]
    assert precios == [1268, 1300, 1340, 1415, 1545]         # ordenadas y sin duplicados
    latam = next(t for t in r["tarifas"] if t["precio"] == 1415)
    assert latam["aerolinea"] == "LATAM" and latam["origen"] == "AEP" and latam["destino"] == "FCO"
    ita = r["tarifas"][-1]
    assert ita["escalas"] == 0 and ita["aerolinea"] == "ITA Airways"
    assert r["tarifas"][1]["destino"] == "CIA"


def test_elegir_una_por_aerolinea():
    elegidas = buscar.elegir(buscar.parsear(pagina())["tarifas"], 3)
    assert [(t["aerolinea"], t["precio"]) for t in elegidas] == [
        ("British Airways", 1268), ("Air France", 1340), ("LATAM", 1415)]


def test_buscar_arma_fechadas_e_indice(rutas_cfg):
    bajar = fake()
    e = buscar.buscar(HOY, rutas_cfg, ["ROM"], ["2027-03-03", "2027-03-17"], bajar, pausa_s=0,
                      log=lambda *_: None)
    fech = [o for o in e["observaciones"] if o["serie"] == "fechada"]
    ind = [o for o in e["observaciones"] if o["serie"] == "indice"]
    assert len(fech) == 6 and len(ind) == 1                  # 3 por búsqueda; 1 índice por mes
    assert ind[0]["precio_moneda_original"] == 1268
    assert ind[0]["fuente"] == rutas_cfg["rutas"]["ROM"]["fuente_indice"]
    assert "mínimo de 2 de 2 búsquedas" in ind[0]["notas"]
    assert fech[0]["incluye_valija"] == "desconocido" and fech[0]["fecha_vuelta"] == "2027-03-17"
    assert "Buenos Aires" not in bajar.llamadas[0][0] and "BUE to ROM" in bajar.llamadas[0][0]
    assert e["fuentes_caidas"] == []


def test_lo_que_arma_buscar_pasa_la_validacion(rutas_cfg, dirs):
    d, r = dirs
    e = buscar.buscar(HOY, rutas_cfg, ["ROM", "MAD"], ["2027-03-03"], fake(), pausa_s=0,
                      log=lambda *_: None)
    res = agregar.agregar(e, HOY, d, r)
    assert res["filas_rechazadas"] == 0, res["rechazadas"]
    obs = pd.read_csv(d / "observaciones.csv")
    mad = obs[(obs["ruta"] == "MAD") & (obs["serie"] == "fechada")].iloc[0]
    assert mad["valija_estimada"] == "si" and mad["precio_total_usd"] == 1268 + 70 + 120


def test_sin_resultados_reintenta_y_registra_caida(rutas_cfg):
    bajar = fake(["<html></html>", "<html></html>"])
    e = buscar.buscar(HOY, rutas_cfg, ["PAR"], ["2027-03-03"], bajar, pausa_s=0, log=lambda *_: None)
    assert e["observaciones"] == [] and "sin resultados" in e["fuentes_caidas"][0]["detalle"]
    assert [p for _, p in bajar.llamadas] == [20000, 40000]


def test_pagina_con_otras_fechas_no_se_usa(rutas_cfg):
    bajar = fake([pagina("2027-03-04", "2027-03-18")])
    e = buscar.buscar(HOY, rutas_cfg, ["PAR"], ["2027-03-03"], bajar, pausa_s=0, log=lambda *_: None)
    assert e["observaciones"] == [] and "otras fechas" in e["fuentes_caidas"][0]["detalle"]


def test_spki_del_bundle(tmp_path):
    key, crt = tmp_path / "k.pem", tmp_path / "c.pem"
    subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", str(key),
                    "-out", str(crt), "-days", "1", "-subj", "/CN=prueba"], check=True,
                   capture_output=True)
    huellas = buscar.spki_del_bundle(str(crt))
    assert len(huellas) == 1 and len(huellas[0]) == 44
    assert buscar.spki_del_bundle(str(tmp_path / "no-existe.pem")) == []
