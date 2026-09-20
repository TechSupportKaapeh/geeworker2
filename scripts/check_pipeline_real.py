r"""M.2.6: el pipeline mensual contra la realidad.

QUE HACE
--------
Corre el pipeline nuevo (`pipeline/`) sobre parcelas y meses reales, y lo pone
**al lado de lo que hace el codigo de hoy**. Cinco escalones:

1. **Los numeros del mes**, parcela por parcela y mes por mes: mediana, cobertura,
   observaciones y cuanto tardo.
2. **Las dos variantes pendientes**, cambiando solo la receta:
   - `nubes_erosion_px` 0 contra 2 (`DECISIONS #39`);
   - `acotar_indices` False contra True (`DECISIONS #41`).
3. **`nearest` contra `bilinear`** en NDRE y NDMI, que son los indices que usan
   las bandas de 20 m (`DECISIONS #36`). Con tolerancia de float32 (~1e-6), no la
   de los tests.
4. **Tres cosas de GEE que estaban supuestas** y hay que confirmar:
   - que dividir por cero da 0 y no un error;
   - la clave de `reduceRegion` con **una** banda y varias salidas;
   - que `minMax` sale como `min` y `max`.
5. **El COG de un rancho** (`--cog`): baja `mapa_del_mes`, lo convierte y lo
   valida con `rio-cogeo`.

EL LADO A LADO YA NO ESTA
-------------------------
Hasta M.6.2 el escalon 1 imprimia al lado lo que devolvia la capa vieja. Esa
comparacion era la compuerta de M.2.6 y **ya se corrio**: el 2026-09-17, sobre 3
parcelas reales x 3 meses (`DECISIONS #44` y `#45`). La capa vieja se borro con
sus handlers, asi que no hay contra que comparar; los numeros de entonces quedan
en esa decision y en la cronica de la sesion.

Las diferencias que se habian previsto, y que explicaban lo observado: la capa
vieja usaba la **media** y no la mediana, reducia a **60 m** con
`bestEffort=True`, **descartaba** las pasadas con menos del 50 % del ROI limpio,
cortaba en 30 imagenes ordenadas de la mas vieja, y calculaba EVI **sin dividir
por 10.000** (`ARQUITECTURA` §8).

LA COMPUERTA (SPRINTS, antes de M.4)
------------------------------------
- NDVI dentro de [-1, 1] y coherente con la estacion;
- la cobertura, coherente con la estacion;
- un mes de parcela en **menos de ~60 s**.

Si no pasa, se revisa el diseño antes de M.4.

LAS PARCELAS
------------
Por defecto corre sobre `ROI_DE_PRUEBA`, un cuadrado de 2 km en el Bajio que
**no es de un cliente**, para poder correrlo sin datos reales. Las de verdad se
dejan como GeoJSON o KML en `scratch/parcelas_m26/` —esa carpeta esta en
`.gitignore`— y se pasan con `--parcelas`:

USO
---
    .venv\Scripts\python.exe scripts\check_pipeline_real.py
    .venv\Scripts\python.exe scripts\check_pipeline_real.py --parcelas scratch\parcelas_m26 --meses 2026-03 2026-07 2026-11
    .venv\Scripts\python.exe scripts\check_pipeline_real.py --cog

No escribe en la base ni sube nada: solo lee de GEE e imprime. El COG, si se
pide, queda en una carpeta temporal.
"""

import argparse
import dataclasses
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Un cuadrado de ~2 km en el Bajio (Guanajuato). Es el mismo de los tests `gee`:
# sirve para correr el script sin datos de clientes.
ROI_DE_PRUEBA = [-100.86, 20.54, -100.84, 20.56]

# Cuantos meses corre si no se le pasan. Se calculan **cerrados** desde hoy: una
# lista fija terminaba pidiendole a GEE un mes que todavia no paso, y eso da una
# coleccion vacia que parece un error del pipeline.
#
# Para la compuerta conviene elegirlos a mano, con estaciones distintas: tres
# meses seguidos caen todos en la misma, y lo que hay que ver es si la cobertura
# y el indice se mueven con la estacion.
MESES_POR_DEFECTO = 3

# La tolerancia de float32, que es lo que GEE devuelve. La de los tests (1e-6
# absoluto sobre valores de 0 a 1) es mas fina que lo que el tipo puede sostener.
TOLERANCIA_FLOAT32 = 1e-6

_ok = "  OK        "
_falla = "  FALLA     "
_dato = "            "


def _parcelas_desde(carpeta: Path):
    """Las geometrias de una carpeta con GeoJSON o KML, una por archivo.

    Devuelve `[(nombre, coordenadas)]`. El KML se lee con el parser de `ee` si
    esta disponible; si no, se avisa y se saltea, porque este repo dejo de
    parsear KML a proposito (`DECISIONS #23`).
    """
    import ee

    parcelas = []
    for archivo in sorted(carpeta.glob("*")):
        if archivo.suffix.lower() in {".json", ".geojson"}:
            crudo = json.loads(archivo.read_text(encoding="utf-8"))
            geometria = crudo.get("geometry", crudo)
            if geometria.get("type") == "FeatureCollection":
                geometria = geometria["features"][0]["geometry"]
            parcelas.append((archivo.stem, ee.Geometry(geometria)))
        elif archivo.suffix.lower() == ".kml":
            print(f"{_dato}(salteado {archivo.name}: convertilo a GeoJSON primero)")
    return parcelas


# `_mes_viejo()` se borro en M.6.2 (`DECISIONS #60`) con
# `get_sentinel2_time_series`. La comparacion lado a lado ya cumplio su
# proposito: fue la compuerta de M.2.6, el 2026-09-17 (`DECISIONS #44` y `#45`),
# y esos numeros estan escritos. La capa vieja ya no existe para compararse.


def _mes_nuevo(roi, mes, receta):
    """Los numeros del pipeline para ese mes, y cuanto tardo."""
    from pipeline import ejecucion

    reloj = time.monotonic()
    with ejecucion.contando() as conteo:
        leida = ejecucion.reduccion_del_mes(roi, mes, receta)
    return leida, time.monotonic() - reloj, conteo.llamadas


def escalon_numeros(parcelas, meses, receta, indice):
    """1. El mes de cada parcela.

    Hasta M.6.2 esto imprimia al lado lo que devolvia la capa vieja. Esa
    comparacion fue la compuerta de M.2.6 y sus numeros estan en `DECISIONS #44`;
    el codigo viejo se borro, asi que el informe queda con las columnas del
    pipeline.
    """
    from pipeline.indices import INDICES

    minimo, maximo = INDICES[indice].rango
    problemas = []
    print(f"\n  1. LOS NUMEROS DEL MES  (indice {indice})\n")
    print(f"{_dato}{'parcela':14s} {'mes':8s} {'mediana':>9s} {'cobertura':>10s} "
          f"{'obs':>4s} {'seg':>6s}")

    for nombre, roi in parcelas:
        for mes in meses:
            leida, seg, llamadas = _mes_nuevo(roi, mes, receta)
            valor = leida.estadisticas[indice]["mediana"]

            print(f"{_dato}{nombre:14.14s} {mes!s:8s} "
                  f"{_num(valor):>9s} {_num(leida.cobertura):>10s} "
                  f"{_num(leida.observaciones, 0):>4s} {seg:6.1f}")

            if llamadas != 1:
                problemas.append(f"{nombre} {mes}: {llamadas} llamadas a GEE, se esperaba 1")
            if valor is not None and not minimo <= valor <= maximo:
                problemas.append(f"{nombre} {mes}: {indice} fuera de rango ({valor})")
            if not 0 <= leida.cobertura <= 1:
                problemas.append(f"{nombre} {mes}: cobertura fuera de [0, 1] ({leida.cobertura})")
            if seg > 60:
                problemas.append(f"{nombre} {mes}: tardo {seg:.0f} s, la compuerta pide < 60")
    return problemas


def escalon_variantes(parcelas, meses, receta, indice):
    """2. La receta contra sus dos alternativas **apagadas**.

    Desde `DECISIONS #45` la receta ya erosiona y acota, asi que lo que hay que
    comparar es lo contrario: que pasaria sin cada una. Antes comparaba contra las
    variantes prendidas, y desde que se tomo la decision las dos columnas salian
    identicas.
    """
    sin_erosion = dataclasses.replace(receta, nubes_erosion_px=0)
    sin_acotar = dataclasses.replace(receta, acotar_indices=False)

    print("\n  2. LA RECETA CONTRA SUS ALTERNATIVAS APAGADAS  (DECISIONS #45)\n")
    print(f"{_dato}{'parcela':14s} {'mes':8s} {'cob v1':>7s} {'sin eros':>9s} "
          f"{'obs v1':>7s} {'sin eros':>9s} | {'evi min v1':>11s} {'sin acotar':>11s}")

    problemas = []
    for nombre, roi in parcelas:
        for mes in meses:
            base, _, _ = _mes_nuevo(roi, mes, receta)
            cruda, _, _ = _mes_nuevo(roi, mes, sin_erosion)
            suelta, _, _ = _mes_nuevo(roi, mes, sin_acotar)
            print(f"{_dato}{nombre:14.14s} {mes!s:8s} "
                  f"{_num(base.cobertura):>7s} {_num(cruda.cobertura):>9s} "
                  f"{_num(base.observaciones, 0):>7s} {_num(cruda.observaciones, 0):>9s} | "
                  f"{_num(base.estadisticas['evi']['min']):>11s} "
                  f"{_num(suelta.estadisticas['evi']['min']):>11s}")

            # La erosion nunca deberia empeorar la cobertura: si lo hace, la
            # decision de `#45` habria que revisarla con este dato.
            if base.cobertura < cruda.cobertura:
                problemas.append(
                    f"{nombre} {mes}: la erosion bajo la cobertura "
                    f"({cruda.cobertura:.3f} -> {base.cobertura:.3f})")
    return problemas


def escalon_remuestreo(parcelas, meses, receta):
    """3. `nearest` contra `bilinear` en los indices de 20 m."""
    bilineal = dataclasses.replace(receta, remuestreo="bilinear")
    print("\n  3. NEAREST CONTRA BILINEAR  (NDRE y NDMI, bandas de 20 m)\n")
    print(f"{_dato}{'parcela':14s} {'mes':8s} {'indice':7s} {'nearest':>9s} "
          f"{'bilinear':>9s} {'dif':>10s}")

    for nombre, roi in parcelas:
        for mes in meses:
            cerca, _, _ = _mes_nuevo(roi, mes, receta)
            lineal, _, _ = _mes_nuevo(roi, mes, bilineal)
            for indice in ("ndre", "ndmi"):
                a = cerca.estadisticas[indice]["mediana"]
                b = lineal.estadisticas[indice]["mediana"]
                dif = None if a is None or b is None else abs(a - b)
                marca = "" if dif is None or dif > TOLERANCIA_FLOAT32 else "  (igual)"
                print(f"{_dato}{nombre:14.14s} {mes!s:8s} {indice:7s} "
                      f"{_num(a):>9s} {_num(b):>9s} {_num(dif, 8):>10s}{marca}")
    return []


def escalon_confirmaciones(receta):
    """4. Lo que el pipeline da por supuesto de GEE, confirmado."""
    import ee

    from pipeline import ejecucion

    print("\n  4. LO QUE ESTABA SUPUESTO\n")
    problemas = []

    cero = ee.Image.constant(1).divide(ee.Image.constant(0))
    punto = ee.Geometry.Point([-100.85, 20.55])
    respuesta = ejecucion.traer(
        ee.Dictionary({
            "division_por_cero": cero.reduceRegion(ee.Reducer.first(), punto, 10),
            "una_banda_varias_salidas": ee.Image.constant([1, 2, 3]).select([0], ["ndvi"]).reduceRegion(
                ee.Reducer.minMax().combine(ee.Reducer.mean(), sharedInputs=True), punto, 10,
            ),
        })
    )

    valor = respuesta["division_por_cero"].get("constant")
    if valor == 0:
        print(f"{_ok}dividir por cero da 0, no un error")
    else:
        problemas.append(f"dividir por cero dio {valor!r}: las formulas tienen que protegerse")
        print(f"{_falla}dividir por cero dio {valor!r}")

    claves = sorted(respuesta["una_banda_varias_salidas"])
    esperadas = ["ndvi_max", "ndvi_mean", "ndvi_min"]
    if claves == esperadas:
        print(f"{_ok}una banda con varias salidas: {claves}")
    else:
        problemas.append(f"las claves de una banda con varias salidas son {claves}, no {esperadas}")
        print(f"{_falla}claves inesperadas: {claves}")

    # `minMax` nombra sus dos salidas `min` y `max`, que es lo que
    # `pipeline/estadisticas.py` da por supuesto en `_SALIDA`.
    if {"ndvi_min", "ndvi_max"} <= set(claves):
        print(f"{_ok}minMax sale como min y max")
    else:
        problemas.append(f"minMax no salio como min y max: {claves}")
        print(f"{_falla}minMax no salio como min y max")
    return problemas


def escalon_cog(parcelas, meses, receta, indice):
    """5. El COG del rancho, bajado y validado con rio-cogeo."""
    from rio_cogeo.cogeo import cog_validate

    from pipeline import ejecucion, productos
    from services.cog_converter import convert_to_cog
    from services.ee.gee_download import descargar_a_archivo, parametros_de_descarga

    nombre, roi = parcelas[0]
    mes = meses[0]
    print(f"\n  5. EL COG DEL MES  ({nombre}, {mes})\n")

    mapa = productos.mapa_del_mes(roi, mes, receta, indice)
    url = ejecucion.url_de_descarga(mapa, parametros_de_descarga(roi))
    destino = Path(convert_to_cog.__module__ and "_diagnostico") / f"m26_{nombre}_{mes}.tif"
    destino.parent.mkdir(exist_ok=True)
    descargar_a_archivo(url, str(destino))
    cog = convert_to_cog(str(destino))

    valido, errores, avisos = cog_validate(cog)
    if valido:
        print(f"{_ok}cog valido  {Path(cog).stat().st_size:,} bytes"
              f"{f' ({len(avisos)} aviso(s))' if avisos else ''}")
        return []
    print(f"{_falla}cog valido  rio-cogeo lo rechaza:")
    for error in errores:
        print(f"{_dato}{error}")
    return [f"el COG de {nombre} {mes} no es valido"]


def _num(valor, decimales=3):
    """Un numero para la tabla, o un guion si no hay dato."""
    if valor is None:
        return "-"
    return f"{valor:.{decimales}f}"


def main():
    parser = argparse.ArgumentParser(description="M.2.6: el pipeline contra la realidad")
    parser.add_argument("--parcelas", type=Path,
                        help="carpeta con GeoJSON, uno por parcela (si no, el ROI de prueba)")
    parser.add_argument("--meses", nargs="+", default=None,
                        help="meses AAAA-MM; conviene elegirlos de estaciones distintas, "
                             f"uno de lluvias. Si no se pasan, los ultimos {MESES_POR_DEFECTO} cerrados")
    parser.add_argument("--indice", default="ndvi", help="el indice de la tabla principal")
    parser.add_argument("--cog", action="store_true", help="suma el escalon del COG")
    args = parser.parse_args()

    import ee

    from pipeline.periodos import Mes, hoy_utc, meses_cerrados
    from pipeline.receta import RECETA_VIGENTE
    from services.ee.ee_client import init_ee

    init_ee()
    if args.meses:
        meses = [Mes.desde_texto(texto) for texto in args.meses]
    else:
        meses = list(meses_cerrados(hoy_utc(), MESES_POR_DEFECTO))
    if args.parcelas:
        parcelas = _parcelas_desde(args.parcelas)
        if not parcelas:
            print(f"{_falla}no hay GeoJSON en {args.parcelas}")
            return 1
    else:
        parcelas = [("bajio-prueba", ee.Geometry.Rectangle(ROI_DE_PRUEBA))]
        print(f"\n{_dato}Sin --parcelas: corre sobre el cuadrado de prueba del Bajio,")
        print(f"{_dato}que no es una parcela de un cliente. La compuerta pide parcelas reales.")

    print(f"\n{_dato}receta {RECETA_VIGENTE.version}  huella {RECETA_VIGENTE.huella()[:12]}")
    problemas = []
    problemas += escalon_numeros(parcelas, meses, RECETA_VIGENTE, args.indice)
    problemas += escalon_variantes(parcelas, meses, RECETA_VIGENTE, args.indice)
    problemas += escalon_remuestreo(parcelas, meses, RECETA_VIGENTE)
    problemas += escalon_confirmaciones(RECETA_VIGENTE)
    if args.cog:
        problemas += escalon_cog(parcelas, meses, RECETA_VIGENTE, args.indice)

    print()
    if problemas:
        print(f"{_falla}{len(problemas)} cosa(s) para mirar:")
        for problema in problemas:
            print(f"{_dato}- {problema}")
        return 1
    print(f"{_ok}Todo lo verificable automaticamente dio bien.")
    print(f"{_dato}Los numeros de las tablas los lee una persona: la compuerta")
    print(f"{_dato}pide que sean plausibles, no que coincidan con la capa vieja.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
