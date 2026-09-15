"""La fuente: Sentinel-2 del mes sobre el ROI, en reflectancia 0-1 (M.2.1).

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
from pipeline.periodos import Mes, rango
from pipeline.receta import Receta

# S2 SR guarda la reflectancia multiplicada por esto.
ESCALA_REFLECTANCIA: Final = 10_000
BANDA_CLASIFICACION: Final = "SCL"
BANDA_PROBABILIDAD: Final = "probability"

# La máscara de sombras compara el NIR contra `sombras_nir_oscuro`, así que el NIR
# viene siempre, lo use o no algún índice de la receta.
_NOMBRE_SOMBRAS: Final = "NIR"
# La propiedad donde el join deja la imagen de probabilidad de cada escena.
_CLAVE_UNION: Final = "probabilidad_de_nube"
_MS_POR_SEGUNDO: Final = 1000


def bandas_espectrales(receta: Receta) -> tuple[str, ...]:
    """Las bandas de S2 que la receta necesita: las de sus índices, más el NIR.

    Van en el orden de ``BANDAS``, que es el de S2 (B2, B4, B5, B8, B11). Ordenar
    el texto pondría B11 antes que B2. Pedir solo estas achica cada imagen: las
    etapas siguientes no cargan bandas que ningún índice usa.
    """
    nombres = {_NOMBRE_SOMBRAS}.union(
        *(INDICES[indice].bandas for indice in receta.indices)
    )
    return tuple(banda for nombre, banda in BANDAS.items() if nombre in nombres)


def metodo_de_remuestreo(receta: Receta) -> str | None:
    """El argumento de ``ee.Image.resample``, o ``None`` si no hay que llamarlo.

    ``nearest`` es lo que GEE hace cuando no se le pide otro, y ``resample`` no
    lo acepta como argumento: solo toma ``bilinear`` y ``bicubic``.
    """
    return None if receta.remuestreo == "nearest" else receta.remuestreo


def milisegundos(mes: Mes) -> tuple[int, int]:
    """El intervalo ``[inicio, fin)`` del mes, en milisegundos desde la época.

    Es lo que recibe ``filterDate``, con el fin excluido. Se pasan números y no
    ``datetime``: así no depende de cómo el cliente de ``ee`` convierte un
    ``datetime`` con huso.
    """
    inicio, fin = rango(mes)
    return (
        int(inicio.timestamp()) * _MS_POR_SEGUNDO,
        int(fin.timestamp()) * _MS_POR_SEGUNDO,
    )


def coleccion(roi: ee.Geometry, mes: Mes, receta: Receta) -> ee.ImageCollection:
    """Las escenas de S2 del mes que tocan el ROI, listas para la máscara.

    Cada escena se une a su probabilidad de nube por ``system:index``. **Una
    escena sin probabilidad queda afuera:** sin ella no hay máscara, y una
    escena sin máscara mete nubes en la mediana. Es lo que hace
    ``ee.Join.saveFirst`` sin ``outer``, y lo que hacía la capa vieja.

    No descarta escenas por nubosidad ni por cobertura del ROI. En un compuesto
    mensual, una escena casi toda nublada aporta los píxeles que sí están limpios
    (§8.2 y §8.3). La calidad se mide al final, con la cobertura del mes.

    Un mes sin escenas da una colección vacía, no un error.
    """
    inicio, fin = milisegundos(mes)
    espectrales = list(bandas_espectrales(receta))
    remuestreo = metodo_de_remuestreo(receta)

    escenas = (
        ee.ImageCollection(receta.coleccion).filterBounds(roi).filterDate(inicio, fin)
    )
    nubes = (
        ee.ImageCollection(receta.coleccion_nubes)
        .filterBounds(roi)
        .filterDate(inicio, fin)
    )
    unidas = ee.Join.saveFirst(matchKey=_CLAVE_UNION).apply(
        escenas,
        nubes,
        ee.Filter.equals(leftField="system:index", rightField="system:index"),
    )

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
        return (
            escena.select([BANDA_CLASIFICACION])
            .addBands(reflectancia)
            .addBands(probabilidad)
            .select([*espectrales, BANDA_CLASIFICACION, BANDA_PROBABILIDAD])
        )

    return ee.ImageCollection(unidas).map(preparar)
