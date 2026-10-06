"""Busca tarifas en Google Flights con un navegador headless y arma la entrada semanal.

Sin IA: consulta una grilla FIJA de fechas (config/rutas.yaml → busqueda) para cada
ruta, lee las tarjetas de resultados y escribe data/entrada/AAAA-MM-DD.json con:

- serie "fechada": hasta N tarifas por búsqueda (la más barata de cada aerolínea).
- serie "indice": por ruta y mes, el mínimo de todas las búsquedas de la grilla de ese
  mes. Misma fuente y mismas búsquedas cada semana, así la serie es comparable.

La evidencia de cada tarifa es texto literal de la página: el encabezado de la búsqueda
("… salida el 2027-06-09 y vuelta el 2027-06-23 …") más la descripción accesible de la
tarjeta ("A partir de 1318 dólares estadounidenses (precio total de ida y vuelta). Vuelo
directo con Air France. Sale de …"). Las búsquedas sin resultados se registran como
fuentes caídas.

Google Flights muestra la tarifa más básica, que normalmente no incluye valija: las
tarifas se cargan con incluye_valija="desconocido" y el recargo se estima al agregarlas.

Uso:
    python src/buscar.py                          # grilla completa, fecha de hoy
    python src/buscar.py --rutas PAR,ROM --idas 2027-06-09
    python src/buscar.py --salida /tmp/prueba.json
"""
from __future__ import annotations

import argparse
import base64
import glob
import hashlib
import html
import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import urlencode

from comun import DATA, RUTAS, a_fecha, cargar_rutas

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/140.0 Safari/537.36")
CA_BUNDLE_DEFAULT = "/root/.ccr/ca-bundle.crt"
MIN_TARIFAS = 3  # con menos, la página probablemente no terminó de cargar

ORIGENES = {"ezeiza": "EZE", "aeroparque": "AEP", "jorge newbery": "AEP"}
DESTINOS = {"charles de gaulle": "CDG", "orly": "ORY", "fiumicino": "FCO", "ciampino": "CIA",
            "malpensa": "MXP", "linate": "LIN", "bérgamo": "BGY", "orio al serio": "BGY",
            "barajas": "MAD", "prat": "BCN"}

_TARJETA = re.compile(
    r"A partir de (?P<precio>\d+) dólares estadounidenses \(precio total de ida y vuelta\)\. "
    r"Vuelo (?:directo con|con (?P<escalas>\d+) escalas? de) (?P<aerolinea>.+?)\. "
    r"Sale de (?P<sale>.+?) el .+? Llega a (?P<llega>.+?) el ")
_ENCABEZADO = re.compile(r"salida el (\d{4}-\d{2}-\d{2}) y vuelta el (\d{4}-\d{2}-\d{2})")


# ---------------------------------------------------------------- navegador

def chrome() -> str:
    """Ruta del Chromium: CHROME_PATH, el de Playwright preinstalado o uno del sistema."""
    if os.environ.get("CHROME_PATH"):
        return os.environ["CHROME_PATH"]
    candidatos = sorted(glob.glob("/opt/pw-browsers/chromium-*/chrome-linux/chrome"))
    if candidatos:
        return candidatos[-1]
    for nombre in ("chromium", "chromium-browser", "google-chrome"):
        if shutil.which(nombre):
            return shutil.which(nombre)
    raise SystemExit("No encontré Chromium: definí CHROME_PATH.")


def spki_del_bundle(ruta: str) -> list[str]:
    """Huellas SPKI (sha256, base64) de los certificados de un bundle PEM.

    Chromium no lee el bundle de CA del proxy del entorno; con
    --ignore-certificate-errors-spki-list confía exactamente en esas claves (y en nada
    más), sin desactivar la verificación TLS.
    """
    if not os.path.exists(ruta):
        return []
    pem = Path(ruta).read_text()
    huellas = []
    for cert in re.findall(r"-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----", pem, re.S):
        pub = subprocess.run(["openssl", "x509", "-pubkey", "-noout"], input=cert,
                             capture_output=True, text=True).stdout
        der = subprocess.run(["openssl", "pkey", "-pubin", "-outform", "der"], input=pub.encode(),
                             capture_output=True).stdout
        if der:
            huellas.append(base64.b64encode(hashlib.sha256(der).digest()).decode())
    return sorted(set(huellas))


def descargar(url: str, presupuesto_ms: int = 20000, timeout: int = 120) -> str:
    """HTML ya renderizado (con JavaScript) de la página."""
    args = [chrome(), "--headless=new", "--no-sandbox", "--disable-gpu",
            f"--virtual-time-budget={presupuesto_ms}", f"--user-agent={UA}", "--dump-dom", url]
    proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
    if proxy:
        args.insert(1, f"--proxy-server={proxy}")
    spki = spki_del_bundle(os.environ.get("PROXY_CA_BUNDLE", CA_BUNDLE_DEFAULT))
    if spki:
        args.insert(1, f"--ignore-certificate-errors-spki-list={','.join(spki)}")
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        return r.stdout
    except subprocess.TimeoutExpired:
        return ""


# ---------------------------------------------------------------- lectura de la página

def _texto(pagina: str) -> str:
    t = re.sub(r"<script.*?</script>|<style.*?</style>", " ", pagina, flags=re.S)
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", t)))


def _codigo(nombre: str, tabla: dict) -> str:
    n = nombre.lower()
    return next((c for k, c in tabla.items() if k in n), "")


def parsear(pagina: str) -> dict:
    """{'encabezado': texto literal o None, 'fechas': (ida, vuelta) o None, 'tarifas': [...]}."""
    m = _ENCABEZADO.search(_texto(pagina))
    tarifas, vistas = [], set()
    for crudo in re.findall(r'aria-label="(A partir de [^"]*)"', pagina):
        label = re.sub(r"\s+", " ", html.unescape(crudo)).strip()
        t = _TARJETA.match(label)
        if not t or label in vistas:
            continue
        vistas.add(label)
        tarifas.append({
            "precio": int(t["precio"]),
            "escalas": int(t["escalas"] or 0),
            "aerolinea": t["aerolinea"].split(". Operado por")[0].strip(),
            "origen": _codigo(t["sale"], ORIGENES),
            "destino": _codigo(t["llega"], DESTINOS),
            "label": label,
        })
    return {"encabezado": m.group(0) if m else None,
            "fechas": (m.group(1), m.group(2)) if m else None,
            "tarifas": sorted(tarifas, key=lambda x: x["precio"])}


# ---------------------------------------------------------------- búsqueda

def url_busqueda(consulta: str, ida: str, vuelta: str) -> str:
    q = f"Flights from {consulta} on {ida} through {vuelta}"
    return "https://www.google.com/travel/flights?" + urlencode({"q": q, "hl": "es", "curr": "USD"})


def elegir(tarifas: list[dict], n: int) -> list[dict]:
    """La más barata de cada aerolínea, hasta n (evita duplicados en observaciones.csv)."""
    out, aerolineas = [], set()
    for t in tarifas:
        if t["aerolinea"] not in aerolineas:
            aerolineas.add(t["aerolinea"])
            out.append(t)
        if len(out) == n:
            break
    return out


def buscar(fecha: date, rutas_cfg: dict, rutas=None, idas=None, bajar=descargar,
           pausa_s: float = 2.0, log=print) -> dict:
    """Recorre la grilla y devuelve la entrada semanal (formato de agregar.py)."""
    b = rutas_cfg["busqueda"]
    fuente, n = b["fuente"], b["tarifas_por_busqueda"]
    idas = idas or b["idas"]
    meses = rutas_cfg["viaje"].get("meses_indice", [])
    observaciones, caidas = [], []
    for ruta in rutas or RUTAS:
        cfg_ruta = rutas_cfg["rutas"][ruta]
        por_mes: dict[str, list] = {}
        for ida in idas:
            vuelta = (a_fecha(ida) + timedelta(days=b["estadia_dias"])).isoformat()
            url = url_busqueda(cfg_ruta["consulta"], ida, vuelta)
            res = parsear(bajar(url, 20000))
            if len(res["tarifas"]) < MIN_TARIFAS:  # carga parcial: un reintento con más tiempo
                otro = parsear(bajar(url, 40000))
                if len(otro["tarifas"]) > len(res["tarifas"]):
                    res = otro
            if pausa_s:
                time.sleep(pausa_s)
            if not res["tarifas"]:
                caidas.append({"fuente": fuente, "detalle": f"{ruta} {ida}→{vuelta}: sin resultados"})
                log(f"  ✗ {ruta} {ida}: sin resultados")
                continue
            if res["fechas"] != (ida, vuelta):
                caidas.append({"fuente": fuente, "detalle": f"{ruta} {ida}→{vuelta}: la página "
                                                            f"mostró otras fechas {res['fechas']}"})
                log(f"  ✗ {ruta} {ida}: fechas distintas {res['fechas']}")
                continue
            log(f"  ✓ {ruta} {ida}: {len(res['tarifas'])} tarifas, mínimo USD {res['tarifas'][0]['precio']}")
            for t in elegir(res["tarifas"], n):
                observaciones.append(_observacion(ruta, "fechada", t, res, ida, vuelta, fuente, url,
                                                  "grilla fija de Google Flights"))
            if ida[:7] in meses:
                por_mes.setdefault(ida[:7], []).append((res["tarifas"][0], res, ida, vuelta, url))
        for mes in meses:
            candidatos = por_mes.get(mes, [])
            total = sum(1 for i in idas if i[:7] == mes)
            if not candidatos:
                continue
            t, res, ida, vuelta, url = min(candidatos, key=lambda c: c[0]["precio"])
            nota = f"mínimo de {len(candidatos)} de {total} búsquedas de la grilla de {mes}"
            if len(candidatos) < total:
                nota += " (incompleto)"
            observaciones.append(_observacion(ruta, "indice", t, res, ida, vuelta,
                                              cfg_ruta["fuente_indice"], url, nota))
    return {"fecha_busqueda": fecha.isoformat(), "fuentes_caidas": caidas,
            "notas": f"generado por src/buscar.py ({len(idas)} idas × {len(rutas or RUTAS)} rutas)",
            "observaciones": observaciones}


def _observacion(ruta, serie, t, res, ida, vuelta, fuente, url, notas) -> dict:
    return {"ruta": ruta, "serie": serie, "origen": t["origen"], "destino": t["destino"] or ruta,
            "aerolinea": t["aerolinea"].split(". Operado por")[0].strip(), "escalas": t["escalas"],
            "fecha_ida": ida, "fecha_vuelta": vuelta,
            "precio_moneda_original": t["precio"], "moneda": "USD",
            "incluye_valija": "desconocido", "fuente": fuente, "url": url,
            "evidencia": f"{res['encabezado']} … {t['label']}", "notas": notas}


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Busca tarifas en Google Flights (grilla fija).")
    p.add_argument("--fecha", default=date.today().isoformat(), help="AAAA-MM-DD (default hoy)")
    p.add_argument("--salida", type=Path, help="JSON de salida (default data/entrada/FECHA.json)")
    p.add_argument("--rutas", help="subconjunto, p. ej. PAR,ROM")
    p.add_argument("--idas", help="subconjunto de idas, p. ej. 2027-06-09,2027-07-07")
    a = p.parse_args(argv)
    fecha = a_fecha(a.fecha)
    rutas = a.rutas.split(",") if a.rutas else None
    idas = a.idas.split(",") if a.idas else None
    entrada = buscar(fecha, cargar_rutas(), rutas, idas)
    salida = a.salida or DATA / "entrada" / f"{fecha.isoformat()}.json"
    salida.parent.mkdir(parents=True, exist_ok=True)
    salida.write_text(json.dumps(entrada, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    fech = sum(o["serie"] == "fechada" for o in entrada["observaciones"])
    ind = sum(o["serie"] == "indice" for o in entrada["observaciones"])
    print(f"{fech} tarifas fechadas, {ind} valores de índice, "
          f"{len(entrada['fuentes_caidas'])} búsquedas sin resultados → {salida}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
