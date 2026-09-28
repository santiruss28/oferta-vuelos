from datetime import datetime

import pandas as pd

import importar_excel
from comun import COLUMNAS, COLUMNAS_CORRIDAS, IMPORTADO_SIN_EVIDENCIA, NOTA_NO_HOMOGENEA, leer_csv

ENCABEZADOS = ["Fecha de búsqueda", "Ruta", "Ruta original", "Aerolínea",
               "Fechas de viaje (ida y vuelta, ~14 días)", "Días de estadía", "Tipo de tarifa",
               "Precio (USD)", "Fuente", "Enlace", "Notas", "Archivo de origen", "Semana (lunes)"]


def excel(tmp_path, filas):
    p = tmp_path / "historial.xlsx"
    pd.DataFrame(filas, columns=ENCABEZADOS).to_excel(p, sheet_name="Historial", index=False)
    return p


def test_importar(tmp_path, dirs):
    d, r = dirs
    p = excel(tmp_path, [
        [datetime(2026, 8, 7), "París", "París", "Air France", "24/04/2027 - 08/05/2027 (14 días)", 14,
         "Fechada 13–16 días", 726, "Air France", "https://af", "Con equipaje: USD 795. Estimado: ~70",
         "h5.xlsx", "=A2"],
        [datetime(2026, 9, 18), "Roma", "Roma (directo)", "Varias", "10/02/2027 - 24/02/2027", 14,
         "Referencia", 935, "Turismocity (agregador)", "https://www.turismocity.com.ar/x",
         "Tarifa mínima (ARS 1,45M / TC oficial ~1550).", "h2.xlsx", "=A3"],
        [datetime(2026, 9, 18), "Alternativa – Madrid", "Madrid", "Iberia", "Marzo 2027", None,
         "Referencia", 942, "Kayak", "https://k", "Con equipaje: USD 1010.", "h2.xlsx", "=A4"],
        [datetime(2026, 9, 4), "Roma", "Multitramo BUE-ROM / MAD-BUE", "Varias", "flexible", None,
         "Referencia", 1257, "Turismocity",
         "https://www.turismocity.com.ar/vuelos-baratos-dos-tramos-BUE-ROM,MAD-BUE", "", "", "=A5"],
        [datetime(2026, 9, 27), "Milán", "Milán (EZE-MXP)", "Air Europa", "16/02/2027 – 02/03/2027", 14,
         "Fechada 13–16 días", 915, "KAYAK AR", "https://k", "ARS 1.424.454 ≈ USD 915.", "h1", "=A6"],
    ])
    res = importar_excel.importar(p, d, r, faltantes=("2026-08-10",))
    assert res["importadas"] == 5 and not res["rechazadas"]

    obs = leer_csv(d / "observaciones.csv", COLUMNAS).set_index("precio_usd")
    af = obs.loc["726.0"]
    assert af["serie"] == "fechada" and af["origen_dato"] == IMPORTADO_SIN_EVIDENCIA
    assert af["fecha_ida"] == "2027-04-24" and af["precio_con_valija_usd"] == "795.0"
    assert af["valija_estimada"] == "si" and af["precio_total_usd"] == "795.0"

    rom = obs.loc["935.0"]
    assert rom["serie"] == "indice" and NOTA_NO_HOMOGENEA not in rom["notas"]
    assert rom["moneda"] == "ARS" and float(rom["precio_moneda_original"]) == 1_450_000
    assert NOTA_NO_HOMOGENEA in obs.loc["942.0"]["notas"]            # Kayak
    assert obs.loc["942.0"]["valija_estimada"] == "no"
    assert NOTA_NO_HOMOGENEA in obs.loc["1257.0"]["notas"]           # multitramo
    mil = obs.loc["915.0"]
    assert mil["destino"] == "MXP" and mil["origen"] == "EZE"
    assert float(mil["fx_usado"]) == round(1_424_454 / 915, 2)

    corridas = leer_csv(d / "corridas.csv", COLUMNAS_CORRIDAS)
    faltante = corridas[corridas["fecha"] == "2026-08-10"].iloc[0]
    assert faltante["estado"] == "faltante" and faltante["semana_iso"] == "2026-W33"
    assert set(corridas[corridas["estado"] == "importada"]["fecha"]) == {
        "2026-08-07", "2026-09-04", "2026-09-18", "2026-09-27"}
    assert len(pd.read_csv(d / "fx.csv")) == 2

    # Re-importar no duplica.
    res = importar_excel.importar(p, d, r, faltantes=("2026-08-10",))
    assert res["importadas"] == 0 and all(m == ["duplicado"] for _, m in res["rechazadas"])
