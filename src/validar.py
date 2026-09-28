"""Validación de observaciones antes de agregarlas a data/observaciones.csv.

Reglas (todas deterministas; el modelo nunca decide si un dato es válido):
- Esquema: ruta, serie, moneda, incluye_valija, fechas y precios con formato válido.
- Precio: > 0 y <= precio_max_usd (default 5000).
- Serie fechada: evidencia obligatoria y las fechas de ida/vuelta y el precio tienen
  que aparecer literalmente en la evidencia; estadía 13–16 días; ida dentro de la
  ventana salida_desde–salida_hasta de config/rutas.yaml.
- Serie índice: siempre de la fuente configurada para la ruta y de un mes seguido.
- Duplicados: misma serie, ruta, aerolínea, fechas, fuente y fecha_busqueda.
- Las filas importadas (origen_dato=importado_sin_evidencia) quedan exceptuadas de
  la regla de evidencia y de la fuente del índice, pero no del resto.

También completa los campos derivados: categoría, semana ISO, días de estadía,
valija (estimada con el recargo de config si no viene), conexión y precio total.
"""
from __future__ import annotations

import re
import unicodedata
from datetime import date
from pathlib import Path

import pandas as pd

from comun import (COLUMNAS, IMPORTADO_SIN_EVIDENCIA, MONEDAS, REPORTS, RUTAS, SERIES,
                   VALIJA, a_fecha, num, semana_iso, vacio)

MESES = {
    1: ("enero", "ene", "january", "jan"),
    2: ("febrero", "feb", "february"),
    3: ("marzo", "mar", "march"),
    4: ("abril", "abr", "april", "apr"),
    5: ("mayo", "may"),
    6: ("junio", "jun", "june"),
    7: ("julio", "jul", "july"),
    8: ("agosto", "ago", "august", "aug"),
    9: ("septiembre", "setiembre", "sept", "sep", "set", "september"),
    10: ("octubre", "oct", "october"),
    11: ("noviembre", "nov", "november"),
    12: ("diciembre", "dic", "december", "dec"),
}

CLAVE_DUPLICADO = ("serie", "ruta", "aerolinea", "fecha_ida", "fecha_vuelta", "fuente",
                   "fecha_busqueda")


# ---------------------------------------------------------------- evidencia

def _normalizar(texto: str) -> str:
    t = unicodedata.normalize("NFKD", str(texto)).encode("ascii", "ignore").decode()
    return t.lower()


def fecha_en_evidencia(evidencia: str, d: date) -> bool:
    """True si la fecha aparece en el texto en algún formato habitual.

    Acepta 24/04, 24/4/2027, 24-04, 2027-04-24, "24 abr", "24 de abril",
    "sáb. 24 abr.", "Apr 24". El año no es obligatorio.
    """
    t = _normalizar(evidencia)
    if d.isoformat() in t:
        return True
    dia, mes = d.day, d.month
    numerico = rf"(?<!\d)0?{dia}\s*[/.\-]\s*0?{mes}(?!\d)"
    if re.search(numerico, t):
        return True
    nombres = "|".join(sorted(MESES[mes], key=len, reverse=True))
    mes_re = rf"(?:{nombres})(?![a-z])\.?"
    if re.search(rf"(?<!\d)0?{dia}\s*(?:de\s+)?{mes_re}", t):
        return True
    if re.search(rf"(?<![a-z]){mes_re}\s*0?{dia}(?!\d)", t):
        return True
    return False


_NUMERO = re.compile(r"\d{1,3}(?:[.,\s  ]\d{3})+(?:[.,]\d{1,2})?|\d+(?:[.,]\d{1,2})?")


def _candidatos(token: str) -> set[float]:
    """Valores posibles de un número escrito con separadores de miles o decimales."""
    limpio = re.sub(r"[\s  ]", "", token)
    out = {float(re.sub(r"[.,]", "", limpio))}
    m = re.match(r"^(.*)[.,](\d{1,2})$", limpio)
    if m:
        entero = re.sub(r"[.,]", "", m.group(1)) or "0"
        out.add(float(f"{entero}.{m.group(2)}"))
    return out


def precio_en_evidencia(evidencia: str, precio: float, tolerancia: float = 1.0) -> bool:
    """True si el precio (en la moneda original) aparece en el texto, ±1 por redondeo."""
    for token in _NUMERO.findall(str(evidencia)):
        if any(abs(c - precio) <= tolerancia for c in _candidatos(token)):
            return True
    return False


# ---------------------------------------------------------------- campos derivados

def completar(fila: dict, rutas_cfg: dict) -> dict:
    """Completa categoría, semana, días, valija, conexión y total. No valida."""
    f = dict(fila)
    ruta = f.get("ruta")
    cfg_ruta = rutas_cfg["rutas"].get(ruta, {})
    viaje = rutas_cfg["viaje"]

    if vacio(f.get("categoria")) and cfg_ruta:
        f["categoria"] = cfg_ruta["categoria"]
    fb = a_fecha(f.get("fecha_busqueda"))
    if fb:
        f["fecha_busqueda"] = fb.isoformat()
        f["semana_iso"] = semana_iso(fb)
    ida, vuelta = a_fecha(f.get("fecha_ida")), a_fecha(f.get("fecha_vuelta"))
    if ida:
        f["fecha_ida"] = ida.isoformat()
    if vuelta:
        f["fecha_vuelta"] = vuelta.isoformat()
    if ida and vuelta and vacio(f.get("dias_estadia")):
        f["dias_estadia"] = (vuelta - ida).days

    precio = num(f.get("precio_usd"))
    if vacio(f.get("incluye_valija")):
        f["incluye_valija"] = "desconocido"
    if precio is not None:
        if f["incluye_valija"] == "si":
            f["precio_con_valija_usd"] = precio
            f["valija_estimada"] = "no"
        elif vacio(f.get("precio_con_valija_usd")):
            f["precio_con_valija_usd"] = round(precio + viaje["recargo_valija_usd"], 2)
            f["valija_estimada"] = "si"
        elif vacio(f.get("valija_estimada")):
            f["valija_estimada"] = "no"

    if vacio(f.get("costo_conexion_usd")):
        f["costo_conexion_usd"] = (cfg_ruta.get("costo_conexion_usd", 0)
                                   if f.get("categoria") == "alternativa" else 0)
    con_valija = num(f.get("precio_con_valija_usd"))
    if con_valija is not None:
        f["precio_total_usd"] = round(con_valija + (num(f["costo_conexion_usd"]) or 0), 2)
    return f


# ---------------------------------------------------------------- reglas

def validar_fila(f: dict, rutas_cfg: dict) -> list[str]:
    """Devuelve la lista de motivos de rechazo (vacía si la fila es válida)."""
    motivos: list[str] = []
    viaje = rutas_cfg["viaje"]
    importada = f.get("origen_dato") == IMPORTADO_SIN_EVIDENCIA

    ruta, serie = f.get("ruta"), f.get("serie")
    if ruta not in RUTAS:
        motivos.append(f"ruta inválida: {ruta!r}")
    elif f.get("categoria") != rutas_cfg["rutas"][ruta]["categoria"]:
        motivos.append(f"categoría {f.get('categoria')!r} no corresponde a {ruta}")
    if serie not in SERIES:
        motivos.append(f"serie inválida: {serie!r}")
    if a_fecha(f.get("fecha_busqueda")) is None:
        motivos.append("fecha_busqueda falta o no es AAAA-MM-DD")
    if vacio(f.get("fuente")):
        motivos.append("falta fuente")
    if f.get("moneda") not in MONEDAS:
        motivos.append(f"moneda inválida: {f.get('moneda')!r}")
    elif f.get("moneda") == "ARS" and (num(f.get("fx_usado")) or 0) <= 0:
        motivos.append("precio en ARS sin fx_usado")
    if f.get("incluye_valija") not in VALIJA:
        motivos.append(f"incluye_valija inválido: {f.get('incluye_valija')!r}")

    precio = num(f.get("precio_usd"))
    pmin, pmax = viaje["precio_min_usd"], viaje["precio_max_usd"]
    if precio is None:
        motivos.append("falta precio_usd")
    elif precio <= pmin or precio > pmax:
        motivos.append(f"precio_usd {precio:g} fuera de rango (> {pmin} y <= {pmax})")

    ida, vuelta = a_fecha(f.get("fecha_ida")), a_fecha(f.get("fecha_vuelta"))
    if serie == "fechada":
        if ida is None or vuelta is None:
            motivos.append("fechada sin fecha_ida/fecha_vuelta AAAA-MM-DD")
        else:
            desde = a_fecha(viaje["salida_desde"])
            hasta = a_fecha(viaje.get("salida_hasta"))
            if ida < desde:
                motivos.append(f"fecha_ida {ida} anterior a {desde}")
            if hasta and ida > hasta:
                motivos.append(f"fecha_ida {ida} posterior a {hasta}")
            dias = (vuelta - ida).days
            dmin, dmax = viaje["estadia_min_dias"], viaje["estadia_max_dias"]
            if not dmin <= dias <= dmax:
                motivos.append(f"estadía de {dias} días fuera de {dmin}–{dmax}")
            declarado = num(f.get("dias_estadia"))
            if declarado is not None and int(declarado) != dias:
                motivos.append(f"dias_estadia={int(declarado)} no coincide con las fechas ({dias})")
        if not importada:
            evidencia = f.get("evidencia")
            if vacio(evidencia):
                motivos.append("fechada sin evidencia")
            else:
                if ida and not fecha_en_evidencia(evidencia, ida):
                    motivos.append(f"la fecha de ida {ida} no aparece en la evidencia")
                if vuelta and not fecha_en_evidencia(evidencia, vuelta):
                    motivos.append(f"la fecha de vuelta {vuelta} no aparece en la evidencia")
                original = num(f.get("precio_moneda_original"))
                if original is not None and not precio_en_evidencia(evidencia, original):
                    motivos.append(f"el precio {original:g} {f.get('moneda')} no aparece en la evidencia")
    elif serie == "indice" and not importada and ruta in RUTAS:
        esperada = rutas_cfg["rutas"][ruta]["fuente_indice"]
        if str(f.get("fuente", "")).strip().lower() != esperada.lower():
            motivos.append(f"índice de {ruta} debe venir de {esperada} (vino {f.get('fuente')!r})")
        meses = viaje.get("meses_indice", [])
        if ida is None or ida.strftime("%Y-%m") not in meses:
            motivos.append(f"índice sin fecha_ida dentro de los meses seguidos {meses}")
    return motivos


def clave(f: dict) -> tuple:
    partes = []
    for c in CLAVE_DUPLICADO:
        v = f.get(c)
        if c.startswith("fecha"):
            d = a_fecha(v)
            partes.append(d.isoformat() if d else "")
        else:
            partes.append("" if vacio(v) else str(v).strip().lower())
    return tuple(partes)


def validar_lote(filas: list[dict], existentes: pd.DataFrame, rutas_cfg: dict):
    """Completa y valida un lote.

    Devuelve (validas, rechazadas) con rechazadas = [(fila, [motivos])].
    Los duplicados se buscan contra el histórico y dentro del mismo lote.
    """
    vistas = {clave(r) for r in existentes.to_dict("records")} if len(existentes) else set()
    validas, rechazadas = [], []
    for original in filas:
        f = completar(original, rutas_cfg)
        motivos = validar_fila(f, rutas_cfg)
        k = clave(f)
        if k in vistas:
            motivos.append("duplicado")
        if motivos:
            rechazadas.append((f, motivos))
        else:
            vistas.add(k)
            validas.append(f)
    return validas, rechazadas


def escribir_rechazados(rechazadas, nombre: str, reports_dir: Path | None = None) -> Path | None:
    """reports/<nombre>.csv con cada fila rechazada y su motivo. None si no hubo."""
    if not rechazadas:
        return None
    destino = (reports_dir or REPORTS) / f"{nombre}.csv"
    destino.parent.mkdir(parents=True, exist_ok=True)
    filas = [{**{c: f.get(c, "") for c in COLUMNAS}, "motivo": "; ".join(m)} for f, m in rechazadas]
    pd.DataFrame(filas, columns=COLUMNAS + ["motivo"]).to_csv(destino, index=False,
                                                                lineterminator="\n")
    return destino
