import json
from datetime import date

import pandas as pd
import pytest
import requests

import notificar
from comun import COLUMNAS_CORRIDAS, escribir_json

URL = "https://n8n.example.com/webhook/vuelos-secreto"
TOKEN = "token-super-secreto"
HOY = date(2026, 10, 12)


class Resp:
    def __init__(self, status, text=""):
        self.status_code, self.text = status, text


class Post:
    """Mock de requests.post: devuelve (o lanza) las respuestas en orden y guarda las llamadas."""

    def __init__(self, *respuestas):
        self.respuestas, self.llamadas = list(respuestas), []

    def __call__(self, url, data=None, headers=None, timeout=None):
        self.llamadas.append({"url": url, "payload": json.loads(data), "headers": headers,
                              "timeout": timeout})
        r = self.respuestas.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


class Sleep:
    def __init__(self):
        self.esperas = []

    def __call__(self, s):
        self.esperas.append(s)


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("N8N_WEBHOOK_URL", URL)
    monkeypatch.setenv("N8N_WEBHOOK_TOKEN", TOKEN)


@pytest.fixture
def sin_env(monkeypatch):
    monkeypatch.delenv("N8N_WEBHOOK_URL", raising=False)
    monkeypatch.delenv("N8N_WEBHOOK_TOKEN", raising=False)


def alerta(nivel="COMPRAR", precio=780):
    return {"alert_id": "abc", "nivel": nivel, "regla": "R1_umbral", "ruta": "PAR",
            "aerolinea": "Air France", "fecha_ida": "2027-03-03", "fecha_vuelta": "2027-03-17",
            "dias": 14, "precio_usd": precio, "precio_con_valija_usd": precio,
            "valija_estimada": False, "costo_conexion_usd": 0, "precio_total_usd": precio,
            "umbral_usd": 800, "minimo_historico_usd": 795, "fuente": "Google Flights",
            "url": "https://g", "evidencia": "3 mar – 17 mar US$780", "mensaje": "París USD 780"}


def preparar(dirs, alertas_=()):
    d, r = dirs
    escribir_json(r / f"alertas_{HOY.isoformat()}.json", {
        "fecha_corrida": HOY.isoformat(), "alertas": list(alertas_),
        "resumen_indice": [{"ruta": "PAR", "ultimo": 988, "media_4s": 1000, "tendencia": "baja"}]})
    pd.DataFrame([{"fecha": HOY.isoformat(), "semana_iso": "2026-W42", "estado": "ok"}],
                 columns=COLUMNAS_CORRIDAS).to_csv(d / "corridas.csv", index=False)
    return d, r


def log(d):
    return pd.read_csv(d / "notificaciones.csv", keep_default_na=False)


# ---------------------------------------------------------------- envío

def test_exito_un_post_con_token_y_payload(env, dirs):
    d, r = preparar(dirs, [alerta(), alerta("OPORTUNIDAD", 850), alerta("INFO")])
    post = Post(Resp(200))
    res = notificar.notificar_corrida(HOY, d, r, post=post, sleep=Sleep())
    assert res["resultado"] == "ok" and len(post.llamadas) == 1
    ll = post.llamadas[0]
    assert ll["url"] == URL and ll["timeout"] == 15
    assert ll["headers"] == {"X-Webhook-Token": TOKEN, "Content-Type": "application/json"}
    p = ll["payload"]
    assert p["tipo"] == "semanal" and p["nivel_max"] == "COMPRAR" and p["fecha_corrida"] == "2026-10-12"
    assert p["asunto"] == "[COMPRAR] París USD 780 – 03/03→17/03/2027 – Air France (+1 más)"
    assert [a["nivel"] for a in p["alertas"]] == ["COMPRAR", "OPORTUNIDAD"]  # INFO no viaja
    assert p["resumen_indice"][0]["tendencia"] == "baja"
    assert "USD 780" in p["html"] and "COMPRAR" in p["html"] and "Carta de control" in p["html"]
    assert "[COMPRAR]" in p["texto"]
    assert log(d).loc[0, "resultado"] == "ok"
    assert not (d / "notificaciones_pendientes.jsonl").exists()


def test_reejecutar_el_mismo_dia_no_reenvia(env, dirs):
    d, r = preparar(dirs, [alerta()])
    post = Post(Resp(200), Resp(200))
    notificar.notificar_corrida(HOY, d, r, post=post, sleep=Sleep())
    assert notificar.notificar_corrida(HOY, d, r, post=post)["resultado"] == "ya_enviada"
    assert len(post.llamadas) == 1
    preparar(dirs, [alerta(), alerta("OPORTUNIDAD", 850)])  # cambió el set de alertas
    assert notificar.notificar_corrida(HOY, d, r, post=post, sleep=Sleep())["resultado"] == "ok"


def test_5xx_reintenta_con_backoff(env, dirs):
    d, r = preparar(dirs, [alerta()])
    post, sleep = Post(Resp(502), Resp(503), Resp(200)), Sleep()
    res = notificar.notificar_corrida(HOY, d, r, post=post, sleep=sleep)
    assert res["resultado"] == "ok" and res["intentos"] == 3
    assert sleep.esperas == [2, 5]


def test_5xx_agota_reintentos_y_queda_pendiente(env, dirs):
    d, r = preparar(dirs, [alerta()])
    post, sleep = Post(*[Resp(500)] * 4), Sleep()
    res = notificar.notificar_corrida(HOY, d, r, post=post, sleep=sleep)
    assert res["resultado"] == "pendiente" and len(post.llamadas) == 4
    assert sleep.esperas == [2, 5, 10]
    pend = (d / "notificaciones_pendientes.jsonl").read_text().splitlines()
    assert len(pend) == 1 and json.loads(pend[0])["tipo"] == "semanal"
    assert log(d).loc[0, "resultado"] == "error" and log(d).loc[0, "status_http"] == 500


def test_error_de_red_se_reintenta(env, dirs):
    d, r = preparar(dirs, [alerta()])
    post = Post(requests.ConnectionError(f"no conecta a {URL}"), Resp(200))
    res = notificar.notificar_corrida(HOY, d, r, post=post, sleep=Sleep())
    assert res["resultado"] == "ok" and res["intentos"] == 2


def test_4xx_no_reintenta(env, dirs):
    d, r = preparar(dirs, [alerta()])
    post, sleep = Post(Resp(401, f"token inválido {TOKEN}")), Sleep()
    res = notificar.notificar_corrida(HOY, d, r, post=post, sleep=sleep)
    assert len(post.llamadas) == 1 and sleep.esperas == []
    assert res["resultado"] == "pendiente" and res["status_http"] == 401


def test_nunca_loguea_token_ni_url(env, dirs):
    d, r = preparar(dirs, [alerta()])
    post = Post(requests.ConnectionError(f"{URL}?t={TOKEN}"), *[Resp(403, f"bad {TOKEN}")])
    notificar.notificar_corrida(HOY, d, r, post=post, sleep=Sleep())
    texto = (d / "notificaciones.csv").read_text()
    assert TOKEN not in texto and URL not in texto
    assert TOKEN not in (r / f"alertas_{HOY.isoformat()}.json").read_text()


# ---------------------------------------------------------------- pendientes

def test_pendientes_se_reenvian_antes_y_se_borran(env, dirs):
    d, r = dirs
    notificar.guardar_pendiente(d, {"tipo": "semanal", "fecha_corrida": "2026-10-05", "asunto": "x"})
    notificar.guardar_pendiente(d, {"tipo": "semanal", "fecha_corrida": "2026-10-05", "asunto": "y"})
    post = Post(Resp(200))
    res = notificar.reenviar_pendientes(d, post=post, sleep=Sleep())
    assert res == {"reenviados": 1, "siguen_pendientes": 0}  # el segundo reemplazó al primero
    assert post.llamadas[0]["payload"]["asunto"] == "y"
    assert not (d / "notificaciones_pendientes.jsonl").exists()


def test_pendiente_que_vuelve_a_fallar_se_conserva(env, dirs):
    d, r = dirs
    notificar.guardar_pendiente(d, {"tipo": "semanal", "fecha_corrida": "2026-10-05"})
    res = notificar.reenviar_pendientes(d, post=Post(Resp(400)), sleep=Sleep())
    assert res["siguen_pendientes"] == 1


# ---------------------------------------------------------------- variables faltantes y tipos

def test_variables_faltantes_no_falla_y_registra_info(sin_env, dirs):
    d, r = preparar(dirs, [alerta()])
    post = Post()
    res = notificar.notificar_corrida(HOY, d, r, post=post)
    assert res["resultado"] == "no_configurada" and post.llamadas == []
    js = json.loads((r / f"alertas_{HOY.isoformat()}.json").read_text())
    info = [a for a in js["alertas"] if a["regla"] == "R7_notificacion"]
    assert info and "notificación no configurada" in info[0]["mensaje"]
    assert log(d).loc[0, "resultado"] == "no_configurada"
    assert notificar.reenviar_pendientes(d)["reenviados"] == 0


def test_sin_alertas_igual_manda_el_mail_semanal(env, dirs):
    d, r = preparar(dirs, [alerta("INFO")])
    post = Post(Resp(200))
    assert notificar.notificar_corrida(HOY, d, r, post=post, sleep=Sleep())["resultado"] == "ok"
    p = post.llamadas[0]["payload"]
    assert p["tipo"] == "semanal" and p["nivel_max"] == "INFO" and p["alertas"] == []
    assert p["asunto"] == "[Semanal] Vuelos BUE → Europa – 12/10/2026 – sin tarifas para comprar"
    assert "no hay tarifas para COMPRAR" in p["html"]


def test_solo_atencion_se_marca_en_el_asunto(env, dirs):
    d, r = preparar(dirs, [alerta("ATENCION")])
    post = Post(Resp(200))
    notificar.notificar_corrida(HOY, d, r, post=post, sleep=Sleep())
    p = post.llamadas[0]["payload"]
    assert p["nivel_max"] == "ATENCION" and p["asunto"].startswith("[ATENCIÓN] [Semanal]")


def test_prueba(env, dirs):
    d, r = preparar(dirs)
    post = Post(Resp(200))
    res = notificar.prueba(d, r, post=post, sleep=Sleep())
    assert res["ok"] and post.llamadas[0]["payload"]["tipo"] == "prueba"
    p = post.llamadas[0]["payload"]
    assert p["asunto"].startswith("[PRUEBA]") and "Mensaje de prueba" in p["html"]


def test_prueba_sin_variables(sin_env, dirs):
    d, r = dirs
    assert not notificar.prueba(d, r)["ok"]
