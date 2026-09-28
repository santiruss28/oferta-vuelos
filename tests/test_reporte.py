from datetime import date

from conftest import df, fila, indice, lunes

import alertas
import reporte
from comun import COLUMNAS

HOY = date(2026, 10, 5)


def guardar(d, filas):
    df(filas).to_csv(d / "observaciones.csv", index=False, columns=COLUMNAS)


def fechada(rutas_cfg, precio, fecha_busqueda="2026-10-05", ruta="PAR", **kw):
    return fila(rutas_cfg, ruta=ruta, fecha_busqueda=fecha_busqueda, precio_moneda_original=precio,
                incluye_valija="si", evidencia=f"3 mar – 17 mar US${precio}", **kw)


def mail(d, r, fecha=HOY):
    alertas.ejecutar(fecha, d, r)
    return reporte.generar(fecha, d, r)


def test_tarifa_barata_destacada_arriba_y_en_la_tabla(rutas_cfg, dirs):
    d, r = dirs
    guardar(d, [fechada(rutas_cfg, 780), fechada(rutas_cfg, 1300, ruta="ROM")])
    m = mail(d, r)
    h = m["html"]
    # Bloque destacado antes de la tabla, con el precio grande y el link.
    bloque = h.index("font-size:28px")
    assert bloque < h.index("Tarifas fechadas de esta semana")
    assert "USD 780" in h[bloque:bloque + 200] and "Ver tarifa" in h
    assert "USD 20 bajo el umbral (USD 800)" in h
    # Fila resaltada con fondo y etiqueta; la de Roma (cara) no.
    tabla = h[h.index("Tarifas fechadas de esta semana"):h.index("Carta de control")]
    assert tabla.count("#fdecea") >= 1 and ">COMPRAR</span>" in tabla
    fila_roma = tabla[tabla.index("Roma"):]
    assert "#fdecea" not in fila_roma[:fila_roma.index("</tr>")]
    assert "[COMPRAR]" in m["texto"]


def test_oportunidad_cerca_del_umbral(rutas_cfg, dirs, cfg):
    d, r = dirs
    guardar(d, [fechada(rutas_cfg, 850)])  # 800 < 850 <= 864
    h = mail(d, r)["html"]
    assert "#fff3e0" in h and ">OPORTUNIDAD</span>" in h


def test_sin_alertas(rutas_cfg, dirs):
    d, r = dirs
    guardar(d, [fechada(rutas_cfg, 1300)])
    h = mail(d, r)["html"]
    assert "no hay tarifas para COMPRAR" in h and "font-size:28px" not in h


def test_carta_de_control_banda_y_limites(rutas_cfg, dirs):
    d, r = dirs
    pocas = [fechada(rutas_cfg, 1200, fecha_busqueda=lunes(i)) for i in range(3)]
    guardar(d, pocas)
    assert "banda 900–1.500" in mail(d, r)["html"]
    muchas = [fechada(rutas_cfg, p, fecha_busqueda=lunes(i))
              for i, p in enumerate([1200, 1210, 1190, 1205, 1195, 1200])]
    guardar(d, muchas)
    h = mail(d, r)["html"]
    fila_par = h[h.index("<b>PAR</b>"):]
    fila_par = fila_par[:fila_par.index("</tr>")]
    assert "banda" not in fila_par and "1.186–1.214" in fila_par  # μ ± 2σ


def test_indice_con_tendencia(rutas_cfg, dirs):
    d, r = dirs
    guardar(d, [indice(rutas_cfg, lunes(1), 1000), indice(rutas_cfg, lunes(0), 900)])
    h = mail(d, r)["html"]
    assert "▼ baja" in h


def test_alerta_ya_avisada_sigue_visible(rutas_cfg, dirs):
    d, r = dirs
    guardar(d, [fechada(rutas_cfg, 780, fecha_busqueda="2026-09-28")])
    alertas.ejecutar(date(2026, 9, 28), d, r)
    guardar(d, [fechada(rutas_cfg, 780, fecha_busqueda="2026-09-28"), fechada(rutas_cfg, 780)])
    h = mail(d, r)["html"]
    assert "Siguen vigentes" in h and "ya emitida el 2026-09-28" in h
    assert "font-size:28px" not in h            # no se re-destaca...
    assert ">COMPRAR</span>" in h                # ...pero la fila sigue marcada


def test_html_escapa_texto_externo(rutas_cfg, dirs):
    d, r = dirs
    guardar(d, [fechada(rutas_cfg, 1300, aerolinea="<script>x</script>")])
    assert "<script>" not in mail(d, r)["html"]
