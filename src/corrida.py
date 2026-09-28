"""Corrida semanal completa, en el orden fijo que usa la rutina.

    1. Reenvía notificaciones pendientes de corridas anteriores.
    2. Agrega las observaciones nuevas (valida, convierte ARS→USD, append).
       Sin --entrada registra una corrida sin datos.
    3. Evalúa las alertas → reports/alertas_AAAA-MM-DD.json.
    4. Arma el mail semanal (HTML con alertas destacadas y carta de control) y lo manda
       a n8n. Si no está configurado, lo registra como INFO y sigue.

Uso:
    python src/corrida.py --entrada data/entrada/2026-10-05.json
    python src/corrida.py --fecha 2026-10-05            # sin datos nuevos
    python src/corrida.py --entrada ... --sin-notificar --vista-previa /tmp/mail.html
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

import agregar
import alertas
import notificar
import reporte
from comun import a_fecha


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Corrida semanal completa.")
    p.add_argument("--entrada", type=Path, help="JSON con las observaciones de la semana")
    p.add_argument("--fecha", help="AAAA-MM-DD (default: la del JSON o hoy)")
    p.add_argument("--sin-notificar", action="store_true", help="no hace ningún POST")
    p.add_argument("--vista-previa", type=Path, help="guarda el HTML del mail en este archivo")
    a = p.parse_args(argv)

    entrada = json.loads(a.entrada.read_text(encoding="utf-8")) if a.entrada else None
    fecha = (a_fecha(a.fecha) if a.fecha else None) or \
        (a_fecha(entrada.get("fecha_busqueda")) if entrada else None) or date.today()

    if not a.sin_notificar:
        r = notificar.reenviar_pendientes()
        print(f"1. Pendientes reenviados: {r['reenviados']} · siguen: {r['siguen_pendientes']}")

    if entrada is not None:
        r = agregar.agregar(entrada, fecha)
    else:
        r = agregar.registrar_corrida_vacia(fecha, notas="corrida sin entrada")
    print(f"2. Agregadas: {r['filas_agregadas']} · rechazadas: {r['filas_rechazadas']}")
    for x in r["rechazadas"]:
        print(f"   ✗ {x['ruta']} {x['serie']} ({x['fuente']}): {'; '.join(x['motivos'])}")

    res = alertas.ejecutar(fecha)
    print(f"3. Alertas: {len(res['alertas'])} · suprimidas: {len(res['suprimidas'])}")
    for al in res["alertas"]:
        print(f"   [{al['nivel']}] {al['mensaje']}")

    if not a.sin_notificar:
        print("4. Mail semanal:", notificar.notificar_corrida(fecha))
    if a.vista_previa:
        a.vista_previa.write_text(reporte.generar(fecha)["html"], encoding="utf-8")
        print(f"   Vista previa: {a.vista_previa}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
