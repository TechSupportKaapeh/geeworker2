"""El compuesto del mes: índices por pasada y después la mediana por píxel (M.2.3).

La tercera etapa de ``ARQUITECTURA_PIPELINE.md`` §2. Arma una expresión y no la
calcula (§3.3).

Hace tres cosas, en este orden:

1. **Junta las teselas de cada pasada.** Sentinel-2 entrega cada toma partida en
   teselas de 110 km que se solapan unos 10 km. Un ROI en esa franja recibe la
   misma pasada dos veces: sobre el cuadrado de prueba del Bajío, julio de 2026
   trae 16 imágenes que son 8 pasadas (sondeo del 2026-09-15). La mediana casi no
   lo nota, pero :data:`BANDA_OBSERVACIONES` saldría el doble, y esa es la
   columna con la que se decide si un mes es confiable.
2. **Calcula los índices en cada pasada**, con las fórmulas del registro.
3. **Reduce por píxel con la mediana**, y suma cuántas observaciones limpias
   tuvo cada píxel.

**El orden importa** (§8.1): el índice se calcula en cada pasada y después se
toma la mediana. El mapa viejo hacía la mediana de las bandas y después el
índice, que no es lo mismo, porque un cociente de medianas no es la mediana de
los cocientes.
"""

from typing import Final

import ee

from pipeline.indices import BANDAS, INDICES
from pipeline.receta import Receta

# Cuántas pasadas limpias tuvo cada píxel en el mes. Su mediana sobre la parcela
# va a `measurements.observaciones`.
BANDA_OBSERVACIONES: Final = "n_obs"

# La propiedad que identifica una toma de S2. Es la misma en las dos teselas de
# esa toma, y distinta entre pasadas: verificado el 2026-09-15 sobre julio de 2026,
# donde 16 imágenes salieron de 8 datatakes, cada uno con las teselas 14QKH y 14QLH.
PROPIEDAD_PASADA: Final = "DATATAKE_IDENTIFIER"

# Lo que se conserva al juntar las teselas: `mosaic()` no lleva propiedades, y la
# fecha hace falta para ordenar y para depurar.
_PROPIEDADES_DE_LA_PASADA: Final = ("system:time_start", PROPIEDAD_PASADA)


def bandas_de_salida(receta: Receta) -> tuple[str, ...]:
    """Las bandas del compuesto: un índice cada una, y las observaciones al final."""
    return (*receta.indices, BANDA_OBSERVACIONES)


def por_pasada(coleccion: ee.ImageCollection) -> ee.ImageCollection:
    """Una imagen por pasada: junta las teselas que comparten :data:`PROPIEDAD_PASADA`.

    Donde las teselas se solapan traen los mismos píxeles, así que ``mosaic()``
    elige cualquiera de los dos. Donde una tesela no llega, o quedó enmascarada,
    aporta la otra.
    """
    pasadas = coleccion.aggregate_array(PROPIEDAD_PASADA).distinct()

    def juntar(identificador: ee.String) -> ee.Image:
        tesela = coleccion.filter(ee.Filter.eq(PROPIEDAD_PASADA, identificador))
        return ee.Image(
            tesela.mosaic().copyProperties(tesela.first(), _PROPIEDADES_DE_LA_PASADA)
        )

    return ee.ImageCollection(pasadas.map(juntar))


def indices_de(imagen: ee.Image, receta: Receta) -> ee.Image:
    """Una banda por índice de la receta, calculada sobre las bandas de la imagen.

    La fórmula es la del registro, tal cual, y GEE la evalúa con
    ``Image.expression``. El mismo texto lo evalúa ``pipeline.formulas`` en los
    tests, así que no hay dos versiones de la fórmula (``DECISIONS #35``).
    """
    bandas = []
    for nombre in receta.indices:
        indice = INDICES[nombre]
        entradas = {
            banda: imagen.select(BANDAS[banda]) for banda in sorted(indice.bandas)
        }
        bandas.append(imagen.expression(indice.formula, entradas).rename(nombre))
    # `copyProperties` devuelve un `Element`, y `map` sobre una colección exige una
    # `Image`: sin este `ee.Image(...)`, el mapeo falla al armarse.
    return ee.Image(
        ee.Image.cat(bandas).copyProperties(imagen, _PROPIEDADES_DE_LA_PASADA)
    )


def compuesto(coleccion: ee.ImageCollection, receta: Receta) -> ee.Image:
    """El compuesto mensual: la mediana de cada índice y las observaciones por píxel.

    ``coleccion`` ya viene enmascarada (M.2.2). Las observaciones se cuentan sobre
    el primer índice de la receta: todos los índices comparten la máscara de su
    pasada, porque la máscara se aplica a la imagen entera.

    Un mes sin pasadas da una imagen sin bandas, no un error. Lo que decide qué
    hacer con eso es la cobertura (M.2.4).
    """
    pasadas = por_pasada(coleccion).map(lambda imagen: indices_de(imagen, receta))
    observaciones = (
        pasadas.select([receta.indices[0]]).count().rename(BANDA_OBSERVACIONES)
    )
    return pasadas.median().addBands(observaciones)
