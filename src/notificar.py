"""Manda el mail semanal a un webhook de n8n (n8n sólo lo reenvía por mail).

- URL y token por variables de entorno N8N_WEBHOOK_URL y N8N_WEBHOOK_TOKEN.
  Si faltan, no falla: registra INFO "notificación no configurada" y sigue.
- Un POST por corrida, siempre (tipo "semanal"): el mail ES el reporte. El HTML lo arma
  src/reporte.py; si hay COMPRAR u OPORTUNIDAD, va destacado arriba y en el asunto.
- Timeout 15 s; 3 reintentos (2 s, 5 s, 10 s) ante error de red o 5xx; 4xx no se reintenta.
- Re-ejecutar la corrida del mismo día sin cambios no manda otro mail.
- Si falla, el payload queda en data/notificaciones_pendientes.jsonl y se reenvía al
  inicio de la próxima corrida. Cada intento se registra en data/notificaciones.csv.
- El token nunca se escribe en logs ni archivos.

Uso:
    python src/notificar.py --fecha 2026-10-05     # manda el mail de la corrida de esa fecha
    python src/notificar.py --pendientes           # sólo reenvía pendientes
    python src/notificar.py --prueba               # manda un payload tipo "prueba"
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path

import requests

import reporte
from comun import (COLUMNAS_NOTIFICACIONES, DATA, ETIQUETA, NIVELES, NOTIFICACIONES, PENDIENTES,
                   REPORTS, a_fecha, append_csv, cargar_alertas_cfg, cargar_rutas, escribir_json,
                   fmt_fecha, fmt_usd, leer_csv)

ENV_URL = "N8N_WEBHOOK_URL"
ENV_TOKEN = "N8N_WEBHOOK_TOKEN"
TIPO = "semanal"
NIVELES_DESTACADOS = ("COMPRAR", "OPORTUNIDAD")   # van al asunto y arriba del mail
NIVELES_MAIL = ("COMPRAR", "OPORTUNIDAD", "ATENCION")
NO_CONFIGURADA = "notificación no configurada"


# ---------------------------------------------------------------- configuración

def credenciales() -> tuple[str, str] | None:
    url, token = os.environ.get(ENV_URL, "").strip(), os.environ.get(ENV_TOKEN, "").strip()
    return (url, token) if url and token else None


def _sanear(texto: str, *secretos: str) -> str:
    """Quita URL y token de un mensaje de error antes de guardarlo."""
    t = str(texto)
    for s in secretos:
        if s:
            t = t.replace(s, "***")
    return t[:300]


def registrar(data_dir: Path, tipo: str, fecha_corrida: str, resultado: str,
              status=None, intentos: int = 0, detalle: str = "") -> None:
    append_csv(data_dir / NOTIFICACIONES.name, [{
        "fecha_hora": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "tipo": tipo, "fecha_corrida": fecha_corrida, "resultado": resultado,
        "status_http": "" if status is None else status, "intentos": intentos, "detalle": detalle,
    }], COLUMNAS_NOTIFICACIONES)


# ---------------------------------------------------------------- envío

def enviar(payload: dict, url: str, token: str, timeout: float = 15,
           esperas=(2, 5, 10), sleep=time.sleep, post=None) -> dict:
    """POST con reintentos. Devuelve {ok, status, intentos, detalle}."""
    post = post or requests.post
    headers = {"X-Webhook-Token": token, "Content-Type": "application/json"}
    cuerpo = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    intentos, status, detalle = 0, None, ""
    for espera in [0, *esperas]:
        if espera:
            sleep(espera)
        intentos += 1
        try:
            resp = post(url, data=cuerpo, headers=headers, timeout=timeout)
        except requests.RequestException as e:
            status, detalle = None, _sanear(f"{type(e).__name__}: {e}", url, token)
            continue
        status = resp.status_code
        if 200 <= status < 300:
            return {"ok": True, "status": status, "intentos": intentos, "detalle": ""}
        detalle = _sanear(f"HTTP {status}: {getattr(resp, 'text', '')}", url, token)
        if 400 <= status < 500:
            break  # error del cliente: reintentar no lo arregla
    return {"ok": False, "status": status, "intentos": intentos, "detalle": detalle}


def _leer_pendientes(data_dir: Path) -> list[dict]:
    p = data_dir / PENDIENTES.name
    if not p.exists():
        return []
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]


def _escribir_pendientes(data_dir: Path, payloads: list[dict]) -> None:
    p = data_dir / PENDIENTES.name
    if not payloads:
        if p.exists():
            p.unlink()
        return
    p.write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in payloads),
                 encoding="utf-8")


def guardar_pendiente(data_dir: Path, payload: dict) -> None:
    """Agrega el payload a pendientes (reemplaza uno previo del mismo tipo y fecha)."""
    clave = (payload.get("tipo"), payload.get("fecha_corrida"))
    resto = [x for x in _leer_pendientes(data_dir) if (x.get("tipo"), x.get("fecha_corrida")) != clave]
    _escribir_pendientes(data_dir, resto + [payload])


def firma(payload: dict) -> str:
    """Identifica el contenido notificado: tipo + fecha + asunto + cuerpo."""
    base = "|".join(str(payload.get(k, "")) for k in ("tipo", "fecha_corrida", "asunto", "html"))
    return hashlib.sha1(base.encode()).hexdigest()[:12]


def ya_enviada(data_dir: Path, payload: dict) -> bool:
    log = leer_csv(data_dir / NOTIFICACIONES.name, COLUMNAS_NOTIFICACIONES)
    ok = log[(log["resultado"] == "ok") & (log["fecha_corrida"] == payload.get("fecha_corrida"))]
    return ok["detalle"].str.contains(f"firma={firma(payload)}", regex=False).any()


def _enviar_y_registrar(payload: dict, cred, data_dir: Path, cfg: dict, **kw) -> dict:
    n = cfg.get("notificaciones", {})
    r = enviar(payload, *cred, timeout=n.get("timeout_s", 15),
               esperas=tuple(n.get("reintentos_s", (2, 5, 10))), **kw)
    detalle = f"firma={firma(payload)}" + (f"; {r['detalle']}" if r["detalle"] else "")
    registrar(data_dir, payload["tipo"], payload["fecha_corrida"], "ok" if r["ok"] else "error",
              r["status"], r["intentos"], detalle)
    return r


def reenviar_pendientes(data_dir: Path = DATA, config_dir: Path | None = None, **kw) -> dict:
    """Reenvía los payloads pendientes (antes del de la corrida actual)."""
    pendientes = _leer_pendientes(data_dir)
    if not pendientes:
        return {"reenviados": 0, "siguen_pendientes": 0}
    cred = credenciales()
    if not cred:
        registrar(data_dir, "pendientes", "", "no_configurada",
                  detalle=f"{NO_CONFIGURADA}; {len(pendientes)} pendiente(s) sin reenviar")
        return {"reenviados": 0, "siguen_pendientes": len(pendientes)}
    cfg = cargar_alertas_cfg(config_dir)
    quedan = [p for p in pendientes if not _enviar_y_registrar(p, cred, data_dir, cfg, **kw)["ok"]]
    _escribir_pendientes(data_dir, quedan)
    return {"reenviados": len(pendientes) - len(quedan), "siguen_pendientes": len(quedan)}


# ---------------------------------------------------------------- payload

def _nombre_corto(ruta: str, rutas_cfg: dict) -> str:
    return rutas_cfg["rutas"].get(ruta, {}).get("nombre", ruta).split(" (")[0]


def nivel_max(alertas: list[dict]) -> str:
    niveles = [a["nivel"] for a in alertas if a["nivel"] in NIVELES_MAIL]
    return min(niveles, key=NIVELES.index) if niveles else "INFO"


def armar_asunto(tipo: str, alertas: list[dict], fecha: date, rutas_cfg: dict) -> str:
    """Asunto listo para el mail: la mejor tarifa destacada o el aviso semanal."""
    if tipo == "prueba":
        return "[PRUEBA] Seguimiento de vuelos BUE → Europa: integración n8n"
    top = [a for a in alertas if a["nivel"] in NIVELES_DESTACADOS]
    if not top:
        prefijo = "[ATENCIÓN] " if any(a["nivel"] == "ATENCION" for a in alertas) else ""
        return f"{prefijo}[Semanal] Vuelos BUE → Europa – {fmt_fecha(fecha)} – sin tarifas para comprar"
    a = top[0]
    ida, vuelta = a_fecha(a["fecha_ida"]), a_fecha(a["fecha_vuelta"])
    fechas = f"{ida:%d/%m}→{vuelta:%d/%m/%Y}" if ida and vuelta else "sin fechas"
    asunto = (f"[{ETIQUETA[a['nivel']]}] {_nombre_corto(a['ruta'], rutas_cfg)} "
              f"{fmt_usd(a['precio_total_usd'])} – {fechas} – {a['aerolinea'] or 's/aerolínea'}")
    if len(top) > 1:
        asunto += f" (+{len(top) - 1} más)"
    return asunto


def construir_payload(tipo: str, res: dict, fecha: date, rutas_cfg: dict, mail: dict) -> dict:
    campos = ("alert_id", "nivel", "regla", "ruta", "aerolinea", "fecha_ida", "fecha_vuelta",
              "dias", "precio_usd", "precio_con_valija_usd", "valija_estimada", "costo_conexion_usd",
              "precio_total_usd", "umbral_usd", "minimo_historico_usd", "fuente", "url",
              "evidencia", "mensaje")
    alertas = [{c: a.get(c) for c in campos} for a in res.get("alertas", [])
               if a["nivel"] in NIVELES_MAIL]
    return {
        "tipo": tipo,
        "fecha_corrida": fecha.isoformat(),
        "nivel_max": nivel_max(alertas),
        "asunto": armar_asunto(tipo, alertas, fecha, rutas_cfg),
        "alertas": alertas,
        "resumen_indice": [{k: r.get(k) for k in ("ruta", "ultimo", "media_4s", "tendencia")}
                           for r in res.get("resumen_indice", [])],
        "html": mail["html"],
        "texto": mail["texto"],
    }


# ---------------------------------------------------------------- corrida

def _agregar_info(res_path: Path, res: dict, mensaje: str) -> None:
    """Deja constancia de la notificación como alerta INFO en el JSON de la corrida."""
    from alertas import _alerta  # import local: alertas importa pandas/control
    res.setdefault("alertas", []).append(_alerta("INFO", "R7_notificacion", "", mensaje))
    escribir_json(res_path, res)


def notificar_corrida(fecha: date, data_dir: Path = DATA, reports_dir: Path = REPORTS,
                      config_dir: Path | None = None, **kw) -> dict:
    """Arma el mail semanal de la corrida y hace el POST (uno por corrida, siempre)."""
    res_path = reports_dir / f"alertas_{fecha.isoformat()}.json"
    res = json.loads(res_path.read_text(encoding="utf-8"))
    rutas_cfg, cfg = cargar_rutas(config_dir), cargar_alertas_cfg(config_dir)
    mail = reporte.generar(fecha, data_dir, reports_dir, config_dir)
    payload = construir_payload(TIPO, res, fecha, rutas_cfg, mail)
    if ya_enviada(data_dir, payload):
        # Re-ejecución del mismo día sin cambios: no se manda otro mail.
        res["notificacion"] = {"tipo": TIPO, "resultado": "ya_enviada"}
        escribir_json(res_path, res)
        return res["notificacion"]
    cred = credenciales()
    if not cred:
        registrar(data_dir, TIPO, fecha.isoformat(), "no_configurada", detalle=NO_CONFIGURADA)
        res["notificacion"] = {"tipo": TIPO, "resultado": "no_configurada"}
        _agregar_info(res_path, res, f"{NO_CONFIGURADA} (faltan {ENV_URL} / {ENV_TOKEN}); "
                                     "no se envió el mail semanal")
        return res["notificacion"]
    r = _enviar_y_registrar(payload, cred, data_dir, cfg, **kw)
    if not r["ok"]:
        guardar_pendiente(data_dir, payload)
        _agregar_info(res_path, res, f"falló el envío del mail semanal (HTTP {r['status']}); "
                                     "queda pendiente para la próxima corrida")
    res["notificacion"] = {"tipo": TIPO, "resultado": "ok" if r["ok"] else "pendiente",
                           "status_http": r["status"], "intentos": r["intentos"]}
    escribir_json(res_path, res)
    return res["notificacion"]


def prueba(data_dir: Path = DATA, reports_dir: Path = REPORTS, config_dir: Path | None = None,
           **kw) -> dict:
    """Manda un payload tipo 'prueba' con el mail de la última corrida (sin alertas)."""
    cred = credenciales()
    if not cred:
        registrar(data_dir, "prueba", date.today().isoformat(), "no_configurada", detalle=NO_CONFIGURADA)
        return {"ok": False, "detalle": f"{NO_CONFIGURADA}: definí {ENV_URL} y {ENV_TOKEN}"}
    ultimos = sorted(reports_dir.glob("alertas_*.json"))
    fecha = date.fromisoformat(ultimos[-1].stem.split("_")[1]) if ultimos else date.today()
    res = json.loads(ultimos[-1].read_text(encoding="utf-8")) if ultimos else {}
    res = {"alertas": [], "resumen_indice": res.get("resumen_indice", [])}
    mail = reporte.generar(fecha, data_dir, reports_dir, config_dir, prueba=True)
    payload = construir_payload("prueba", res, date.today(), cargar_rutas(config_dir), mail)
    return _enviar_y_registrar(payload, cred, data_dir, cargar_alertas_cfg(config_dir), **kw)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Notifica alertas al webhook de n8n.")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--prueba", action="store_true", help="manda un payload de prueba")
    g.add_argument("--pendientes", action="store_true", help="sólo reenvía pendientes")
    p.add_argument("--fecha", default=date.today().isoformat(), help="AAAA-MM-DD (default hoy)")
    a = p.parse_args(argv)
    if a.prueba:
        r = prueba()
        print("Prueba enviada (HTTP %s)" % r.get("status") if r.get("ok")
              else f"Prueba NO enviada: {r.get('detalle')}")
        return 0 if r.get("ok") else 1
    r = reenviar_pendientes()
    print(f"Pendientes reenviados: {r['reenviados']} · siguen pendientes: {r['siguen_pendientes']}")
    if not a.pendientes:
        print("Notificación de la corrida:", notificar_corrida(a_fecha(a.fecha)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
