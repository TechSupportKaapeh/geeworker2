r"""M.2.6 y M.9.0: el pipeline mensual contra la realidad.

QUE HACE
--------
Corre el pipeline nuevo (`pipeline/`) sobre parcelas y meses reales. Seis
escalones, en dos grupos que **no se corren juntos**:

- **1 a 5, la compuerta de M.2.6** (ya corrida, el 2026-09-17): el pipeline
  contra si mismo y contra lo que GEE promete;
- **6, el informe de M.9.0** (`--pasadas`): cuantas pasadas limpias hay por mes
  y que cobertura tiene cada una SOBRE LA PARCELA.

Los escalones de la compuerta:

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
5. **El COG de un rancho** (`--cog`): baja `mapa_de`, lo convierte y lo
   valida con `rio-cogeo`.

Y aparte, con `--pasadas`:

6. **Las pasadas del mes** (M.9.0): por parcela y por mes, cuantas escenas hay,
   cuantas pasadas, cuantas llegan a la cobertura minima de la receta, y
   **cuanta parcela cubre cada una**. Al lado, la cobertura del compuesto
   mensual: la diferencia `comp - mejor` es la que decide si la fila mensual
   agrega algo que agregar las pasadas al leer no pueda dar (`DECISIONS #63`).
   Corre solo, sin los otros cinco, porque son 24 meses por parcela.

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

M.9.0, los 24 meses de la receta sobre las parcelas reales:

    .venv/Scripts/python.exe scripts/check_pipeline_real.py --pasadas --parcelas scratch/parcelas_m26 --meses 2024-10 ... 2026-08 --csv scratch/m90_pasadas.csv

No escribe en la base ni sube nada: solo lee de GEE e imprime. El COG, si se
pide, queda en una carpeta temporal.
"""

import argparse
import csv
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

# Las claves con que vuelve cada pasada del escalon 6. Van en una constante
# porque las escribe GEE (`rename`) y las lee Python: el nombre tiene que ser el
# mismo de los dos lados.
CLAVE_COBERTURA = "cobertura"
CLAVE_VALOR = "valor"
CLAVE_FECHA = "fecha"

# A partir de cuanto se considera que el compuesto **agrega** cobertura sobre la
# mejor pasada sola. Cinco puntos de la parcela: por debajo, la fila mensual del
# compuesto estaria midiendo casi lo mismo que la mejor pasada, y `DECISIONS #63`
# se cierra con «por pasada puro». No es un umbral del pipeline: es el corte con
# que se lee esta tabla, y esta aca para que se lea el numero y no la palabra.
_DELTA_QUE_IMPORTA = 0.05

# Desde cuantas pasadas limpias un mes deja de ser «una foto» y pasa a ser una
# serie: con 3 o mas, agregar al leer devuelve algo que el mensual no tiene.
_VARIAS_PASADAS = 3

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
    from pipeline.ventanas import del_mes

    reloj = time.monotonic()
    with ejecucion.contando() as conteo:
        leida = ejecucion.reduccion_de(roi, del_mes(mes), receta)
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
    from pipeline.ventanas import del_mes
    from services.cog_converter import convert_to_cog
    from services.ee.gee_download import descargar_a_archivo, parametros_de_descarga

    nombre, roi = parcelas[0]
    mes = meses[0]
    print(f"\n  5. EL COG DEL MES  ({nombre}, {mes})\n")

    mapa = productos.mapa_de(roi, del_mes(mes), receta, indice)
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


def _reducir_como_el_pipeline(imagen, roi, receta, reduce):
    """Un `reduceRegion` con la politica de pedidos de `DECISIONS #36`.

    Es una copia de `reduccion._reducir`, que es privado a proposito. Se repite
    aca en vez de exponerlo porque M.9.0 **no toca el modulo de produccion**, y
    porque los cinco argumentos tienen que coincidir —`bestEffort=False` sobre
    todo— para que la cobertura por pasada sea comparable con la del compuesto.
    """
    from pipeline.etapas import reduccion

    return imagen.reduceRegion(
        reducer=reduce,
        geometry=roi,
        scale=receta.escala_m,
        bestEffort=False,
        maxPixels=reduccion.MAX_PIXELES,
    )


def _pasadas_del_mes(roi, mes, receta, indice):
    """Las pasadas de un mes sobre una parcela, y el compuesto al lado (M.9.0).

    Devuelve `(pasadas, mensual, escenas)`:

    - `pasadas`: una entrada por pasada, ordenada por fecha, con `fecha`,
      `cobertura` —la fraccion de **la parcela** que esa pasada dejo sin
      enmascarar— y `valor`, la mediana del indice en lo que quedo limpio.
      **Esa cobertura es lo que M.9.0 viene a medir**: hoy no existe en ningun
      lado, porque `reduccion.cobertura` la calcula sobre el compuesto;
    - `mensual`: la `Reduccion` del compuesto, **la misma que escribe el
      pipeline**. Sale de `reduccion.valores`, no de una cuenta rehecha aca: la
      comparacion solo vale si la columna del compuesto es la de produccion;
    - `escenas`: cuantas imagenes de S2 hay antes de juntar teselas. Contra el
      largo de `pasadas` muestra el solapamiento que resuelve `por_pasada`.

    **Es una sola llamada a GEE**: las reducciones por pasada, las del compuesto
    y el conteo van en un `ee.Dictionary`, como hace `reduccion.valores`.

    Una pasada enteramente enmascarada **no trae la clave `valor`**: con todos
    los pixeles fuera, GEE omite la salida en vez de mandarla en `None`. Es el
    mismo caso que documenta `reduccion.leer`, y se lee con `dict.get`. Lo mismo
    puede pasarle a `cobertura` —`filterBounds` filtra por el rectangulo que
    envuelve al ROI, asi que una pasada puede tocar ese rectangulo y no tocar el
    poligono, y entonces la reduccion no ve un solo pixel—, y ahi la cobertura es
    cero: no hubo nada de parcela que cubrir.

    La expresion de las pasadas y la del compuesto se arman por separado a
    proposito, aunque compartan las tres primeras etapas: la del compuesto sale
    de `compuesto_de` **tal cual la usa produccion**. Reusar las pasadas de
    aca para armar el compuesto seria reimplementar `compuesto()` en un script, y
    la columna de referencia dejaria de ser la de verdad. Es armado de
    expresiones, no calculo: sigue siendo un solo pedido.
    """
    import ee

    from pipeline import ejecucion
    from pipeline.etapas import compuesto as etapa_compuesto
    from pipeline.etapas import fuente, nubes, reduccion
    from pipeline.productos import compuesto_de
    from pipeline.ventanas import del_mes

    escenas = fuente.coleccion(roi, del_mes(mes), receta)
    limpias = escenas.map(lambda escena: nubes.enmascarar(ee.Image(escena), receta))
    pasadas = etapa_compuesto.por_pasada(limpias).map(
        lambda imagen: etapa_compuesto.indices_de(ee.Image(imagen), receta)
    )

    def medir(imagen):
        """La cobertura y el indice de UNA pasada, sobre la parcela."""
        imagen = ee.Image(imagen)
        banda = imagen.select([indice])
        # `mask()` vale 1 donde la pasada dejo dato y 0 donde la enmascaro, y no
        # deja pixeles enmascarados, asi que el promedio sobre el ROI es la
        # fraccion cubierta. Es la cuenta de `reduccion.cobertura`, por pasada.
        cubierto = _reducir_como_el_pipeline(
            banda.mask().rename(CLAVE_COBERTURA), roi, receta, ee.Reducer.mean()
        )
        valor = _reducir_como_el_pipeline(
            banda.rename(CLAVE_VALOR), roi, receta, ee.Reducer.median()
        )
        return (
            ee.Dictionary(cubierto)
            .combine(valor)
            .set(CLAVE_FECHA, imagen.date().format("YYYY-MM-dd'T'HH:mm'Z'"))
        )

    respuesta = ejecucion.traer(
        ee.Dictionary({
            "escenas": escenas.size(),
            # Una `ee.FeatureCollection` metida en un `ee.Dictionary` vuelve sin
            # sus rasgos: `getInfo()` la serializa como `{type, columns}` y nada
            # mas (verificado el 2026-09-25). Con `toList` vuelve la lista entera.
            "pasadas": pasadas.toList(pasadas.size()).map(medir),
            "mensual": reduccion.valores(
                compuesto_de(roi, del_mes(mes), receta), roi, receta
            ),
        })
    )
    filas = sorted(respuesta["pasadas"], key=lambda fila: fila[CLAVE_FECHA])
    for fila in filas:
        fila.setdefault(CLAVE_COBERTURA, 0.0)
    return filas, reduccion.leer(respuesta["mensual"], receta), respuesta["escenas"]


def escalon_pasadas(parcelas, meses, receta, indice, destino_csv=None):
    """6. Cuantas pasadas limpias hay por mes, y con que cobertura (M.9.0).

    Es la compuerta del bloque M.9.0 y **no es parte de la compuerta de M.2.6**:
    no compara el pipeline contra nada, mide el dato que falta para decidir si la
    ventana de observacion sigue siendo el mes (`DECISIONS #63`).

    Tres cosas salen de la tabla, y son las que deciden:

    1. **cuantas pasadas limpias** tiene un mes tipico. Si casi siempre son 1 o
       2, el mensual esta bien y el bloque se cierra;
    2. **`comp - mejor`**: cuanto agrega el compuesto sobre la mejor pasada sola.
       Si es ~0, el compuesto no esta tapando huecos que el `GROUP BY` no tape, y
       «por pasada puro» no pierde nada. Si es grande, la fila mensual es **otra
       medicion** y hace falta ademas de las pasadas;
    3. **el rango del indice entre las pasadas limpias**: cuanto se mueve el
       indice dentro de un mes, que es lo que la mediana mensual se lleva puesto.

    `destino_csv`, si se pasa, recibe el detalle pasada por pasada. Conviene
    dejarlo en `scratch/`, que esta en `.gitignore`: son datos de clientes.
    """
    from pipeline.ejecucion import ErrorDeGEE

    umbral = receta.cobertura_minima
    print(f"\n  6. LAS PASADAS DEL MES  (M.9.0, indice {indice})\n")
    print(f"{_dato}La cobertura es SOBRE LA PARCELA. 'limp' son las pasadas que llegan")
    print(f"{_dato}a la cobertura minima de la receta ({umbral:.2f}); 'mejor' es la de")
    print(f"{_dato}la mejor pasada sola y 'comp' la del compuesto mensual.\n")
    print(f"{_dato}{'parcela':14s} {'mes':8s} {'esc':>4s} {'pas':>4s} {'limp':>5s} "
          f"{'mejor':>7s} {'comp':>7s} {'comp-mejor':>11s} {'obs':>4s} "
          f"{'idx comp':>9s} {'idx entre limpias':>24s}")

    problemas = []
    resumen = []
    detalle = []
    for nombre, roi in parcelas:
        for mes in meses:
            try:
                filas, mensual, escenas = _pasadas_del_mes(roi, mes, receta, indice)
            except ErrorDeGEE as error:
                # Un mes que falla no puede cortar un informe de 24 meses por
                # parcela: se anota y se sigue. No se traga, se reporta abajo.
                problemas.append(f"{nombre} {mes}: GEE fallo ({error})")
                print(f"{_dato}{nombre:14.14s} {mes!s:8s}   -- GEE fallo: {error}")
                continue

            mejor = max((fila[CLAVE_COBERTURA] for fila in filas), default=0.0)
            limpias = [f for f in filas if f[CLAVE_COBERTURA] >= umbral]
            valores = [f[CLAVE_VALOR] for f in limpias if f.get(CLAVE_VALOR) is not None]
            amplitud = (max(valores) - min(valores)) if len(valores) > 1 else None
            rango = (f"{min(valores):.3f} a {max(valores):.3f} ({amplitud:+.3f})"
                     if amplitud is not None else "-")
            resumen.append({
                "parcela": nombre, "mes": str(mes), "escenas": escenas,
                "pasadas": len(filas), "limpias": len(limpias), "mejor": mejor,
                "comp": mensual.cobertura, "amplitud": amplitud,
                "idx_comp": mensual.estadisticas[indice]["mediana"],
                # La mediana de las medianas por pasada: lo que devolveria un
                # `GROUP BY` mensual sobre las filas por pasada. No es la mediana
                # del compuesto —«las medianas no componen»—, y la diferencia
                # entre las dos es lo que ese `GROUP BY` no puede rehacer.
                "idx_pasadas": _mediana(sorted(valores)) if valores else None,
            })
            detalle.extend(
                {"parcela": nombre, "mes": str(mes), "fecha": fila[CLAVE_FECHA],
                 "cobertura": fila[CLAVE_COBERTURA], "valor": fila.get(CLAVE_VALOR),
                 "limpia": fila[CLAVE_COBERTURA] >= umbral}
                for fila in filas
            )

            print(f"{_dato}{nombre:14.14s} {mes!s:8s} {escenas:4d} {len(filas):4d} "
                  f"{len(limpias):5d} {mejor:7.3f} {mensual.cobertura:7.3f} "
                  f"{mensual.cobertura - mejor:+11.3f} "
                  f"{_num(mensual.observaciones, 0):>4s} "
                  f"{_num(mensual.estadisticas[indice]['mediana']):>9s} {rango:>24s}")

            # El compuesto tiene dato donde lo tuvo **alguna** pasada, asi que no
            # puede cubrir menos que la mejor. Si pasara, la comparacion de esta
            # tabla no significaria lo que dice, y hay que mirar por que.
            if mensual.cobertura + TOLERANCIA_FLOAT32 < mejor:
                problemas.append(
                    f"{nombre} {mes}: el compuesto cubre menos que la mejor pasada "
                    f"({mensual.cobertura:.4f} < {mejor:.4f})")
            problemas += [
                f"{nombre} {mes} {f[CLAVE_FECHA]}: cobertura fuera de [0, 1] "
                f"({f[CLAVE_COBERTURA]})"
                for f in filas if not 0 <= f[CLAVE_COBERTURA] <= 1
            ]

    problemas += _cierre_de_pasadas(resumen, umbral)
    if destino_csv is not None and detalle:
        _escribir_csv(destino_csv, detalle)
        print(f"\n{_dato}detalle pasada por pasada: {destino_csv}  ({len(detalle)} filas)")
    return problemas


def _cierre_de_pasadas(resumen, umbral):
    """Los agregados que contestan la pregunta, debajo de la tabla."""
    if not resumen:
        return ["el escalon de pasadas no midio ningun mes"]

    print(f"\n{_dato}POR MES DEL ANIO  (promedio entre parcelas y anios)\n")
    print(f"{_dato}{'mes':>4s} {'meses':>6s} {'pasadas':>8s} {'limpias':>8s} "
          f"{'mejor':>7s} {'comp':>7s} {'comp-mejor':>11s}")
    for numero in range(1, 13):
        delmes = [f for f in resumen if f["mes"].endswith(f"-{numero:02d}")]
        if not delmes:
            continue
        print(f"{_dato}{numero:4d} {len(delmes):6d} {_media(delmes, 'pasadas'):8.1f} "
              f"{_media(delmes, 'limpias'):8.1f} {_media(delmes, 'mejor'):7.3f} "
              f"{_media(delmes, 'comp'):7.3f} "
              f"{_media(delmes, 'comp') - _media(delmes, 'mejor'):+11.3f}")

    limpias = sorted(fila["limpias"] for fila in resumen)
    deltas = sorted(fila["comp"] - fila["mejor"] for fila in resumen)
    amplitudes = sorted(f["amplitud"] for f in resumen if f["amplitud"] is not None)
    total = len(resumen)

    print(f"\n{_dato}LO QUE DECIDE  ({total} meses de parcela medidos)\n")
    for cuantas in (0, 1, 2):
        cuenta = sum(1 for valor in limpias if valor == cuantas)
        print(f"{_dato}meses con {cuantas} pasada(s) limpia(s): "
              f"{cuenta:4d}  ({cuenta / total:6.1%})")
    cuenta = sum(1 for valor in limpias if valor >= _VARIAS_PASADAS)
    print(f"{_dato}meses con {_VARIAS_PASADAS} o mas:              "
          f"{cuenta:4d}  ({cuenta / total:6.1%})")
    print(f"{_dato}pasadas limpias por mes: mediana {_mediana(limpias):.1f}, "
          f"media {sum(limpias) / total:.2f}, maximo {limpias[-1]}")
    print(f"\n{_dato}comp - mejor: mediana {_mediana(deltas):+.4f}, "
          f"p90 {_percentil(deltas, 90):+.4f}, maximo {deltas[-1]:+.4f}")
    grandes = sum(1 for valor in deltas if valor > _DELTA_QUE_IMPORTA)
    print(f"{_dato}meses en que el compuesto agrega mas de {_DELTA_QUE_IMPORTA:.2f} "
          f"sobre la mejor pasada: {grandes} de {total}  ({grandes / total:.1%})")
    if amplitudes:
        print(f"\n{_dato}rango del indice entre las pasadas limpias de un mes "
              f"({len(amplitudes)} meses con 2 o mas):")
        print(f"{_dato}  mediana {_mediana(amplitudes):.3f}, "
              f"p90 {_percentil(amplitudes, 90):.3f}, maximo {amplitudes[-1]:.3f}")
    sin_dato = sum(1 for fila in resumen if fila["comp"] < umbral)
    print(f"\n{_dato}meses que hoy se guardan con valor nulo por cobertura < "
          f"{umbral:.2f}: {sin_dato} de {total}")
    # El mes que se perderia al pasar a por pasada: el compuesto llega al umbral
    # y ninguna pasada sola llega. Es el costo concreto de «por pasada puro».
    perdidos = sum(1 for f in resumen if f["mejor"] < umbral <= f["comp"])
    print(f"{_dato}meses en que el compuesto llega al umbral y ninguna pasada "
          f"sola: {perdidos} de {total}")

    # «Las medianas no componen»: cuanto se aparta la mediana de las medianas
    # por pasada de la mediana del compuesto. Es el limite menor que nombra
    # SPRINTS_FASE_M §M.9, y aca pasa de afirmacion a numero.
    diferencias = sorted(
        abs(f["idx_comp"] - f["idx_pasadas"])
        for f in resumen
        if f["idx_comp"] is not None and f["idx_pasadas"] is not None
    )
    if diferencias:
        print()
        print(f"{_dato}|mediana del compuesto - mediana de las medianas "
              f"por pasada|  ({len(diferencias)} meses):")
        print(f"{_dato}  mediana {_mediana(diferencias):.4f}, "
              f"p90 {_percentil(diferencias, 90):.4f}, maximo {diferencias[-1]:.4f}")
    return []


def _media(filas, clave):
    """El promedio de una columna del resumen."""
    return sum(fila[clave] for fila in filas) / len(filas)


def _mediana(ordenados):
    """La mediana de una lista ya ordenada."""
    mitad = len(ordenados) // 2
    if len(ordenados) % 2:
        return float(ordenados[mitad])
    return (ordenados[mitad - 1] + ordenados[mitad]) / 2


def _percentil(ordenados, porcentaje):
    """El percentil de una lista ya ordenada, por el vecino mas cercano.

    Alcanza para un informe: no hace falta interpolar para decidir si el
    compuesto agrega algo.
    """
    ultimo = len(ordenados) - 1
    return ordenados[min(ultimo, round(porcentaje / 100 * ultimo))]


def _escribir_csv(destino, detalle):
    """El detalle pasada por pasada, para mirarlo fuera de la consola."""
    destino.parent.mkdir(parents=True, exist_ok=True)
    with destino.open("w", encoding="utf-8", newline="") as archivo:
        escritor = csv.DictWriter(archivo, fieldnames=list(detalle[0]))
        escritor.writeheader()
        escritor.writerows(detalle)


def _num(valor, decimales=3):
    """Un numero para la tabla, o un guion si no hay dato."""
    if valor is None:
        return "-"
    return f"{valor:.{decimales}f}"


def main():
    parser = argparse.ArgumentParser(description="M.2.6 y M.9.0: el pipeline contra la realidad")
    parser.add_argument("--parcelas", type=Path,
                        help="carpeta con GeoJSON, uno por parcela (si no, el ROI de prueba)")
    parser.add_argument("--meses", nargs="+", default=None,
                        help="meses AAAA-MM; conviene elegirlos de estaciones distintas, "
                             f"uno de lluvias. Si no se pasan, los ultimos {MESES_POR_DEFECTO} cerrados")
    parser.add_argument("--indice", default="ndvi", help="el indice de la tabla principal")
    parser.add_argument("--cog", action="store_true", help="suma el escalon del COG")
    parser.add_argument("--pasadas", action="store_true",
                        help="corre SOLO el escalon 6 (M.9.0): cuantas pasadas "
                             "limpias hay por mes y que cobertura tiene cada una "
                             "sobre la parcela")
    parser.add_argument("--csv", type=Path,
                        help="con --pasadas, donde dejar el detalle pasada por "
                             "pasada. Conviene scratch/, que esta en .gitignore")
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

    # El escalon 6 corre solo, y no por capricho: es otro informe. Los escalones
    # 1 a 4 son la compuerta de M.2.6 sobre 3 meses y cuestan 6 llamadas a GEE
    # por parcela y mes; M.9.0 mide 24 meses por parcela y le alcanza con una.
    # Correr los cinco sobre 24 meses serian ~430 llamadas para leer una tabla.
    if args.pasadas:
        problemas += escalon_pasadas(
            parcelas, meses, RECETA_VIGENTE, args.indice, args.csv
        )
        return _cerrar(problemas)

    problemas += escalon_numeros(parcelas, meses, RECETA_VIGENTE, args.indice)
    problemas += escalon_variantes(parcelas, meses, RECETA_VIGENTE, args.indice)
    problemas += escalon_remuestreo(parcelas, meses, RECETA_VIGENTE)
    problemas += escalon_confirmaciones(RECETA_VIGENTE)
    if args.cog:
        problemas += escalon_cog(parcelas, meses, RECETA_VIGENTE, args.indice)
    return _cerrar(problemas)


def _cerrar(problemas):
    """El cierre comun: lista lo que hay que mirar y da el codigo de salida."""
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
