"""¿Está barata esta tarifa? Evalúa una tarifa suelta contra el historial, sin guardar nada.

Uso:
    python src/evaluar.py PAR 790 2027-03-03 2027-03-17
    python src/evaluar.py ROM 1350000 2027-02-10 2027-02-24 --moneda ARS --fx 1480
    python src/evaluar.py MAD 690 2027-02-11 2027-02-25 --incluye-valija si
    python src/evaluar.py MIL 820 2027-02-16 2027-03-02 --con-valija 890

El precio es el que muestra la página (por persona, ida y vuelta). El script suma la valija
(real o estimada) y, en MAD/BCN, la conexión low-cost, igual que en la corrida semanal, y
aplica las mismas reglas que las alertas. No escribe en data/.
"""
from __future__ import annotations

import argparse
import sys
from datetime import date

import control
from comun import RUTAS, a_fecha, cargar_alertas_cfg, cargar_rutas, leer_observaciones
from contexto import contexto
from validar import completar

ICONO = {"COMPRAR": "🔴 COMPRAR", "OPORTUNIDAD": "🟠 OPORTUNIDAD", "NORMAL": "⚪ PRECIO NORMAL",
         "CARO": "⚫ CARO"}


def evaluar(ruta: str, precio: float, ida=None, vuelta=None, moneda="USD", fx=None,
            incluye_valija="desconocido", con_valija=None, fecha: date | None = None,
            obs=None, rutas_cfg=None, cfg=None) -> dict:
    rutas_cfg = rutas_cfg or cargar_rutas()
    cfg = cfg or cargar_alertas_cfg()
    obs = control.vigentes(leer_observaciones() if obs is None else obs, rutas_cfg)
    fecha = fecha or date.today()
    if moneda == "ARS":
        if not fx:
            raise SystemExit("Precio en ARS: indicá --fx (ARS por USD).")
        precio_usd = round(precio / fx, 2)
    else:
        precio_usd = precio
    f = completar({"ruta": ruta, "serie": "fechada", "fecha_busqueda": fecha.isoformat(),
                   "fecha_ida": ida or "", "fecha_vuelta": vuelta or "", "precio_usd": precio_usd,
                   "incluye_valija": incluye_valija,
                   "precio_con_valija_usd": con_valija if con_valija is not None else ""}, rutas_cfg)
    avisos = []
    viaje = rutas_cfg["viaje"]
    di, dv = a_fecha(ida), a_fecha(vuelta)
    if di and dv and not viaje["estadia_min_dias"] <= (dv - di).days <= viaje["estadia_max_dias"]:
        avisos.append(f"La estadía es de {(dv - di).days} días (fuera de "
                      f"{viaje['estadia_min_dias']}–{viaje['estadia_max_dias']}): no es comparable.")
    if di and di < a_fecha(viaje["salida_desde"]):
        avisos.append(f"La ida es anterior al {viaje['salida_desde']}.")
    if di and viaje.get("salida_hasta") and di > a_fecha(viaje["salida_hasta"]):
        avisos.append(f"La ida es posterior al {viaje['salida_hasta']}.")
    ctx = contexto(obs, ruta, f["precio_total_usd"], fecha, rutas_cfg, cfg, fecha_ida=ida,
                   incluir_semana=True, valija_estimada=f.get("valija_estimada") == "si",
                   costo_conexion=f.get("costo_conexion_usd") or 0)
    return {"fila": f, "contexto": ctx, "avisos": avisos}


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Evalúa si una tarifa está barata (no guarda nada).")
    p.add_argument("ruta", choices=RUTAS)
    p.add_argument("precio", type=float, help="precio que muestra la página, por persona i/v")
    p.add_argument("ida", nargs="?", help="AAAA-MM-DD")
    p.add_argument("vuelta", nargs="?", help="AAAA-MM-DD")
    p.add_argument("--moneda", choices=("USD", "ARS"), default="USD")
    p.add_argument("--fx", type=float, help="ARS por USD (si --moneda ARS)")
    p.add_argument("--incluye-valija", choices=("si", "no", "desconocido"), default="desconocido")
    p.add_argument("--con-valija", type=float, help="precio con valija en USD, si lo viste")
    p.add_argument("--fecha", help="evaluar como si fuera esta fecha (AAAA-MM-DD; default hoy)")
    a = p.parse_args(argv)

    r = evaluar(a.ruta, a.precio, a.ida, a.vuelta, a.moneda, a.fx, a.incluye_valija, a.con_valija,
                a_fecha(a.fecha) if a.fecha else None)
    f, ctx = r["fila"], r["contexto"]
    desglose = f"tarifa USD {f['precio_usd']:,.0f}"
    if f["precio_con_valija_usd"] != f["precio_usd"]:
        desglose += f" · con valija {f['precio_con_valija_usd']:,.0f}" + (
            " (estimada)" if f["valija_estimada"] == "si" else "")
    if f["costo_conexion_usd"]:
        desglose += f" · + conexión {f['costo_conexion_usd']:,.0f}"
    print(f"\n{ICONO[ctx['veredicto']]}  {a.ruta} · total USD {f['precio_total_usd']:,.0f}".replace(",", "."))
    print(f"   ({desglose.replace(',', '.')})\n")
    for linea in r["avisos"] + ctx["lineas"]:
        print(f" · {linea}")
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
