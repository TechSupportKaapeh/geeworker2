"""La fuente: Sentinel-2 de la ventana sobre el ROI, en reflectancia 0-1 (M.2.1).

La primera etapa de ``ARQUITECTURA_PIPELINE.md`` §2. Arma una expresión y no la
calcula (§3.3).

Cada imagen de la colección trae:

- las bandas espectrales que la receta necesita, **divididas por 10.000**. Las
  fórmulas de ``pipeline.indices`` son sobre reflectancia 0-1, y las constantes
  de EVI no funcionan sobre las bandas crudas (§8.6);
- ``SCL``, la clasificación de escena, para que la máscara no tome agua por
  sombra;
- ``probability``, la probabilidad de nube de s2cloudless, de 0 a 100;
- las propiedades de la escena, entre ellas ``MEAN_SOLAR_AZIMUTH_ANGLE``, que la
  máscara usa para proyectar las sombras (M.2.2).
"""

from typing import Final

import ee

from pipeline.indices import BANDAS, INDICES
from pipeline.receta import (
    BANDA_CLOUD_SCORE,
    COLECCION_CLOUD_SCORE,
    COLOR_REAL,
    Receta,
)
from pipeline.ventanas import Ventana

# S2 SR guarda la reflectancia multiplicada por esto.
ESCALA_REFLECTANCIA: Final = 10_000
BANDA_CLASIFICACION: Final = "SCL"
BANDA_PROBABILIDAD: Final = "probability"

# La máscara de sombras compara el NIR contra `sombras_nir_oscuro`, así que el NIR
# viene siempre, lo use o no algún índice de la receta.
_NOMBRE_SOMBRAS: Final = "NIR"
# La propiedad donde el join deja la imagen de probabilidad de cada escena.
_CLAVE_UNION: Final = "probabilidad_de_nube"
# Y donde deja la de Cloud Score+, si la receta la pide (M.9.7e1).
_CLAVE_CLOUD_SCORE: Final = "cloud_score"
_MS_POR_SEGUNDO: Final = 1000

# Cuánto se ensancha el filtro de fecha de la colección de nubes, de cada lado.
# Un día: mil veces el desfase medido entre las dos colecciones (hasta 1169 s) y
# una unidad natural, en vez de un número ajustado a lo que se midió. Ver
# `coleccion`, que es donde importa.
MARGEN_DE_NUBES_MS: Final = 24 * 60 * 60 * _MS_POR_SEGUNDO

# **Los productos de prueba de ESA no entran** (`DECISIONS #79`). Su línea de base de
# procesamiento empieza con 99 (`N99.05`): son los de la puesta en marcha de un
# satélite, que ESA publica marcados como no aptos para uso operativo. El caso que lo
# trajo: en diciembre de 2024 Sentinel-2C voló en tándem 30 s detrás del 2A, y su
# toma de prueba del mismo lugar caía a menos de un minuto de la del 2A —dos
# pasadas imposibles para `ventanas.por_pasada`, que las rechaza—, así que **toda
# alta nueva fallaba en 2024-12**. Hay una por lugar (Yaqui, Zapotlan y el Cauca,
# del 11 al 13 de diciembre), y la escena es la misma que la del 2A.
PREFIJO_BASE_DE_PRUEBA: Final = "99"


def bandas_espectrales(receta: Receta) -> tuple[str, ...]:
    """Las bandas de S2 que la receta necesita: las de sus índices, más el NIR.

    Van en el orden de ``BANDAS``, que es el de S2 (B2, B3, B4, B5, B8, B11).
    Ordenar el texto pondría B11 antes que B2. Pedir solo estas achica cada
    imagen: las etapas siguientes no cargan bandas que ningún índice usa.

    Con ``color_real`` se suman el rojo, el verde y el azul (M.9.7e1). El verde
    (B3) es la única que ningún índice usa: sin esto no se bajaba, y la primera
    prueba del color real falló por eso (``ARQUITECTURA`` §3.6).
    """
    nombres = {_NOMBRE_SOMBRAS}.union(
        *(INDICES[indice].bandas for indice in receta.indices)
    )
    if receta.color_real:
        nombres |= set(COLOR_REAL.values())
    return tuple(banda for nombre, banda in BANDAS.items() if nombre in nombres)


def metodo_de_remuestreo(receta: Receta) -> str | None:
    """El argumento de ``ee.Image.resample``, o ``None`` si no hay que llamarlo.

    ``nearest`` es lo que GEE hace cuando no se le pide otro, y ``resample`` no
    lo acepta como argumento: solo toma ``bilinear`` y ``bicubic``.
    """
    return None if receta.remuestreo == "nearest" else receta.remuestreo


def milisegundos(ventana: Ventana) -> tuple[int, int]:
    """El intervalo ``[inicio, fin)`` de la ventana, en milisegundos desde la época.

    Es lo que recibe ``filterDate``, con el fin excluido. Se pasan números y no
    ``datetime``: así no depende de cómo el cliente de ``ee`` convierte un
    ``datetime`` con huso.

    Desde M.9.0b la ventana puede ser un mes, una pasada o cualquier intervalo:
    esta etapa no necesita saber cuál. Es el único lugar del pipeline que mira
    ``inicio`` y ``fin``.
    """
    inicio, fin = ventana.inicio, ventana.fin
    return (
        int(inicio.timestamp()) * _MS_POR_SEGUNDO,
        int(fin.timestamp()) * _MS_POR_SEGUNDO,
    )


def coleccion(roi: ee.Geometry, ventana: Ventana, receta: Receta) -> ee.ImageCollection:
    """Las escenas de S2 de la ventana que tocan el ROI, listas para la máscara.

    Cada escena se une a su probabilidad de nube por ``system:index``. **Una
    escena sin probabilidad queda afuera:** sin ella no hay máscara, y una
    escena sin máscara mete nubes en la mediana. Es lo que hace
    ``ee.Join.saveFirst`` sin ``outer``, y lo que hacía la capa vieja.

    Descarta los productos de prueba de ESA (``PREFIJO_BASE_DE_PRUEBA``), y nada
    más: no descarta escenas por nubosidad ni por cobertura del ROI. En un compuesto,
    una escena casi toda nublada aporta los píxeles que sí están limpios
    (§8.2 y §8.3). La calidad se mide al final, con la cobertura de la ventana.

    Una ventana sin escenas da una colección vacía, no un error.
    """
    inicio, fin = milisegundos(ventana)
    espectrales = list(bandas_espectrales(receta))
    remuestreo = metodo_de_remuestreo(receta)

    escenas = (
        ee.ImageCollection(receta.coleccion)
        .filterBounds(roi)
        .filterDate(inicio, fin)
        # Sin los productos de prueba de ESA (ver `PREFIJO_BASE_DE_PRUEBA`). Con
        # `Not`, una escena sin la propiedad entra: el filtro saca, no exige.
        .filter(
            ee.Filter.stringStartsWith(
                "PROCESSING_BASELINE", PREFIJO_BASE_DE_PRUEBA
            ).Not()
        )
    )
    # **El filtro de fecha de las nubes es un superconjunto, no un criterio.** Quien
    # decide qué escena entra es el join por `system:index`, que es exacto; la
    # fecha está sólo para no traer la colección entera. Y tiene que ser un
    # superconjunto porque **las dos colecciones fechan la misma escena distinto**:
    # el `system:time_start` de S2_SR va de 129 a 1169 segundos después que el de
    # la probabilidad de nubes, y cuánto depende de dónde caiga el ROI en la
    # pasada (medido el 2026-09-25, `DECISIONS #67`).
    #
    # Con el mismo filtro que las escenas pasaban dos cosas: una escena de los
    # primeros minutos de un mes perdía su imagen de nubes —que había quedado en
    # el mes anterior— y se descartaba entera; y una ventana de una pasada no
    # traía ninguna. Un día de margen es mil veces el desfase medido.
    nubes = (
        ee.ImageCollection(receta.coleccion_nubes)
        .filterBounds(roi)
        .filterDate(inicio - MARGEN_DE_NUBES_MS, fin + MARGEN_DE_NUBES_MS)
    )
    por_indice = ee.Filter.equals(leftField="system:index", rightField="system:index")
    unidas = ee.Join.saveFirst(matchKey=_CLAVE_UNION).apply(escenas, nubes, por_indice)
    con_cloud_score = receta.cloud_score_minimo is not None
    if con_cloud_score:
        # **Cloud Score+ se une igual que la probabilidad**: por `system:index`,
        # que comparte con S2, con la fecha como superconjunto, y sin `outer`.
        # Una escena sin su Cloud Score+ queda afuera, por lo mismo que una sin
        # probabilidad: sin él la máscara de v3 no se puede armar, y dejarla
        # pasar con la de la receta sola metería en la pasada justo lo que d36
        # vino a sacar. Para un mes cerrado no pasa: la colección cubre todo S2.
        puntajes = (
            ee.ImageCollection(COLECCION_CLOUD_SCORE)
            .filterBounds(roi)
            .filterDate(inicio - MARGEN_DE_NUBES_MS, fin + MARGEN_DE_NUBES_MS)
        )
        unidas = ee.Join.saveFirst(matchKey=_CLAVE_CLOUD_SCORE).apply(
            ee.ImageCollection(unidas), puntajes, por_indice
        )
    salida = [*espectrales, BANDA_CLASIFICACION, BANDA_PROBABILIDAD]
    if con_cloud_score:
        salida.append(BANDA_CLOUD_SCORE)

    def preparar(escena: ee.Image) -> ee.Image:
        escena = ee.Image(escena)
        reflectancia = escena.select(espectrales).divide(ESCALA_REFLECTANCIA)
        # Solo las espectrales: SCL es una clasificación, y promediar clases con
        # `bilinear` da clases que no existen.
        if remuestreo is not None:
            reflectancia = reflectancia.resample(remuestreo)
        probabilidad = ee.Image(escena.get(_CLAVE_UNION)).select(BANDA_PROBABILIDAD)
        # Se arma sobre la escena, con `addBands`, y no a partir de la cuenta: la
        # aritmética de GEE no conserva las propiedades, y la máscara necesita el
        # azimut solar de la escena.
        preparada = (
            escena.select([BANDA_CLASIFICACION])
            .addBands(reflectancia)
            .addBands(probabilidad)
        )
        if con_cloud_score:
            preparada = preparada.addBands(
                ee.Image(escena.get(_CLAVE_CLOUD_SCORE)).select(BANDA_CLOUD_SCORE)
            )
        return preparada.select(salida)

    return ee.ImageCollection(unidas).map(preparar)
