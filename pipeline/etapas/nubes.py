"""Las nubes: la máscara s2cloudless con sombras, con la receta (M.2.2).

Es la máscara de la capa vieja (``mask_s2cloudless_and_shadows``), con tres
cambios:

- **los parámetros salen de la receta** (``ARQUITECTURA_PIPELINE.md`` §8.7);
- **el NIR ya viene en reflectancia 0-1** (M.2.1), así que se compara contra
  ``sombras_nir_oscuro`` (0,15) y no contra 1500;
- **la máscara se arma en una proyección fija**: la de la escena, a ``escala_m``
  (``DECISIONS #36``).

**Por qué la proyección fija.** ``directionalDistanceTransform`` mide la distancia
de sombra en píxeles, y ``focalMax`` resuelve los 50 m de dilatación en píxeles.
Si GEE calcula la máscara en la proyección del pedido, un pedido a 60 m
proyecta las sombras seis veces más lejos, y la dilatación de 50 m queda por
debajo de un píxel. El COG del rancho y las estadísticas de la parcela saldrían
con máscaras distintas. Con ``reproject``, la máscara se calcula siempre en
píxeles de ``escala_m``, y un pedido a otra escala solo la muestrea.

No descarta la escena por cobertura (§8.2): solo enmascara píxeles.
"""

from typing import Final

import ee

from pipeline.etapas.fuente import BANDA_CLASIFICACION, BANDA_PROBABILIDAD
from pipeline.indices import BANDAS
from pipeline.receta import Receta

# En SCL, 6 es agua. Un píxel oscuro de agua no es una sombra.
SCL_AGUA: Final = 6

# Las capas de la máscara: 1 donde se descarta el píxel.
BANDA_NUBE: Final = "nube"
BANDA_SOMBRA: Final = "sombra"
BANDA_DESCARTE: Final = "descarte"
COMPONENTES: Final = (BANDA_NUBE, BANDA_SOMBRA, BANDA_DESCARTE)

_AZIMUT_SOLAR: Final = "MEAN_SOLAR_AZIMUTH_ANGLE"
# La sombra cae del lado opuesto al sol.
_MEDIA_VUELTA: Final = 180


def proyeccion_fija(escena: ee.Image, receta: Receta) -> ee.Projection:
    """La proyección de la máscara: la del NIR de la escena, a ``escala_m``.

    El NIR es una banda de 10 m en la UTM de la escena (EPSG:32614 en el Bajío,
    verificado el 2026-09-15). Si la receta pide otra escala, ``atScale`` la
    lleva ahí sin cambiar el sistema de coordenadas.
    """
    return escena.select(BANDAS["NIR"]).projection().atScale(receta.escala_m)


def componentes(escena: ee.Image, receta: Receta) -> ee.Image:
    """Las tres capas de la máscara, calculadas en la proyección fija.

    - ``nube``: probabilidad de nube mayor que ``nubes_max_prob``;
    - ``sombra``: un píxel oscuro en NIR, que no es agua, y que cae dentro de la
      sombra proyectada de una nube, hasta ``sombras_distancia_px`` en la
      dirección opuesta al sol;
    - ``descarte``: nube o sombra, dilatadas ``nubes_dilatacion_m``.

    Sale aparte de :func:`enmascarar` para que los tests y M.2.6 puedan medir
    cada capa.
    """
    nir = escena.select(BANDAS["NIR"])
    nube = escena.select(BANDA_PROBABILIDAD).gt(receta.nubes_max_prob)
    oscuro = nir.lt(receta.sombras_nir_oscuro).And(
        escena.select(BANDA_CLASIFICACION).neq(SCL_AGUA)
    )
    azimut_sombra = ee.Number(escena.get(_AZIMUT_SOLAR)).add(_MEDIA_VUELTA)
    # `distance` tiene dato hasta `sombras_distancia_px` de una nube en esa
    # dirección: su máscara es la sombra proyectada.
    proyectada = (
        nube.directionalDistanceTransform(azimut_sombra, receta.sombras_distancia_px)
        .select("distance")
        .mask()
    )
    sombra = proyectada.And(oscuro)
    descarte = nube.Or(sombra).focalMax(receta.nubes_dilatacion_m, "circle", "meters")
    return ee.Image.cat(
        nube.rename(BANDA_NUBE),
        sombra.rename(BANDA_SOMBRA),
        descarte.rename(BANDA_DESCARTE),
    ).reproject(proyeccion_fija(escena, receta))


def enmascarar(escena: ee.Image, receta: Receta) -> ee.Image:
    """La escena con los píxeles de nube y sombra enmascarados.

    Conserva las bandas y las propiedades de la escena. Un pedido a otra escala
    muestrea la máscara de ``escala_m``: no la vuelve a calcular.
    """
    descarte = componentes(escena, receta).select(BANDA_DESCARTE)
    return escena.updateMask(descarte.Not())
