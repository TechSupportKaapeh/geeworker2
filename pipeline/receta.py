"""La receta versionada del pipeline (M.1.4 y M.1.7, ``ARQUITECTURA_PIPELINE.md`` §3.4).

Todo parámetro que cambia un número vive acá, con un nombre de versión. Cada fila
que se escribe guarda la versión que la produjo. Eso da tres cosas:

- se sabe con qué parámetros salió cada número (D-1);
- al cambiar de receta, se sabe qué filas quedaron viejas;
- cambiar las estadísticas es cambiar la receta, no el esquema.

La receta vive en el código, no en variables de entorno: un parámetro que cambia
los datos tiene que quedar en git y pasar por revisión. Un test fija la
:meth:`Receta.huella` de cada versión, así que cambiar un parámetro sin subir la
versión lo pone en rojo.

**Lo que la receta no fija, porque es del pedido y no del cálculo:** cada pedido a
GEE va con ``bestEffort=False`` y un ``maxPixels`` explícito. Con
``bestEffort=True``, GEE usa una escala mayor que ``escala_m`` cuando hay muchos
píxeles, y el número cambia sin avisar: la serie vieja lo hacía. Si GEE no puede a
``escala_m``, el pedido falla, y ``ejecucion.py`` (M.2.5) traduce el error
(``DECISIONS #36``).
"""

import dataclasses
import hashlib
import json
import math
import re
from dataclasses import dataclass

from pipeline.estadisticas import ESTADISTICAS, claves_de_salida
from pipeline.indices import BANDAS, INDICES
from pipeline.ventanas import AGRUPAMIENTOS, ENTERO, POR_PASADA

# Va en la columna `receta` de cada fila: minúsculas, dígitos y guiones.
_VERSION = re.compile(r"[a-z0-9]+(-[a-z0-9]+)*")
_PROBABILIDAD_MAXIMA = 100
# Los métodos de `ee.Image.resample`, más `nearest`, que es lo que GEE hace si no
# se le pide otro.
_REMUESTREOS = frozenset({"nearest", "bilinear", "bicubic"})


@dataclass(frozen=True, slots=True)
class Receta:
    """Los parámetros de una versión del cálculo mensual.

    Attributes:
        version: el nombre que se guarda en cada fila (``measurements.receta``).
        coleccion: la colección de Sentinel-2 de reflectancia de superficie.
        coleccion_nubes: la probabilidad de nube de s2cloudless, que se une a la
            anterior escena por escena.
        indices: nombres de ``INDICES``.
        estadisticas: nombres de ``ESTADISTICAS``.
        cobertura_minima: la fracción de la parcela, de 0 a 1, con al menos una
            observación limpia en la ventana. Qué se hace con ella lo decide
            :attr:`umbral_al_escribir`.
        umbral_al_escribir: si una observación por debajo de
            :attr:`cobertura_minima` se guarda con ``valor`` nulo. ``True`` es lo
            que hizo el pipeline siempre (``ARQUITECTURA`` §6); ``False`` guarda
            el valor igual y deja el umbral para quien lee (``DECISIONS #63``).

            **Descartar al escribir es una reducción con pérdida que no se puede
            deshacer**: el día que 0,3 resulte mal puesto, con ``False`` se cambia
            el número y con ``True`` se reprocesa el histórico. La fila lleva su
            cobertura en las dos, así que filtrar al leer es un ``WHERE``.
        meses_historico: cuántos meses cerrados trae un alta.
        escala_m: el tamaño de píxel. Es uno solo para el compuesto, el mapa y
            las estadísticas (``ARQUITECTURA`` §8.5).
        remuestreo: cómo se llevan a ``escala_m`` las bandas de 20 m (B5 y B11):
            ``nearest``, ``bilinear`` o ``bicubic``. Cambia NDRE y NDMI. v1 usa
            ``nearest``, que es lo que GEE hace si no se le pide otro y lo que
            hacía la capa vieja; M.2.6 lo compara con ``bilinear``.
        nubes_max_prob: un píxel con probabilidad de nube mayor que esta es nube,
            de 0 a 100.
        nubes_dilatacion_m: cuánto se agranda la máscara de nubes y sombras.
        sombras_nir_oscuro: un píxel con NIR por debajo de esto, en reflectancia
            0-1, es candidato a sombra. La capa vieja comparaba contra
            ``0.15 * 10000`` porque trabajaba con las bandas crudas.
        sombras_distancia_m: hasta dónde se proyecta la sombra de una nube. La
            etapa usa :attr:`sombras_distancia_px`.
        nubes_erosion_px: cuántos píxeles se encoge la máscara de nubes **antes**
            de dilatarla, para sacar los píxeles sueltos. En píxeles de
            ``escala_m``, como la sombra. v1 usa 0, que es lo que hace la capa
            vieja; el tutorial de s2cloudless usa 2. Con 0, la dilatación de un
            píxel suelto se come un círculo de 50 m: sobre una escena con 32 % de
            nubes, el descarte pasa de 0,50 a 0,95 (``DECISIONS #39``). M.2.6 lo
            compara sobre parcelas reales.
        agrupamiento_estadisticas: cómo se parte el pedido para las **filas**:
            ``entero`` (una observación por pedido, que sobre un mes es el
            compuesto mensual de siempre) o ``por_pasada``. Ver
            :mod:`pipeline.ventanas`.
        agrupamiento_raster: lo mismo para el **COG**. Va aparte porque el ráster
            y los números tienen costos distintos —el ráster son descargas, los
            números un ``reduceRegion``— y ``DECISIONS #31`` eligió mensual con
            la cuenta del ráster. Con un solo agrupamiento global se repetiría el
            error que M.9 viene a corregir (``ARQUITECTURA`` §3.5).
        acotar_indices: si cada índice se recorta a su ``rango`` del registro. v1
            usa ``False``, que es lo que hace la capa vieja. EVI no está acotado
            por construcción —su denominador puede acercarse a cero— y se sale de
            [-1, 1] en el 0,012 % de los píxeles, con mínimos de -6,4
            (``DECISIONS #41``). Afecta al mínimo y al máximo que se guardan, no a
            la mediana. M.2.6 lo compara.
    """

    version: str
    coleccion: str
    coleccion_nubes: str
    indices: tuple[str, ...]
    estadisticas: tuple[str, ...]
    cobertura_minima: float
    umbral_al_escribir: bool
    meses_historico: int
    escala_m: int
    remuestreo: str
    nubes_max_prob: int
    nubes_dilatacion_m: int
    sombras_nir_oscuro: float
    sombras_distancia_m: int
    nubes_erosion_px: int
    acotar_indices: bool
    agrupamiento_estadisticas: str
    agrupamiento_raster: str

    def __post_init__(self) -> None:
        """Valida la receta contra los registros al armarla, que es al importar."""
        if not isinstance(self.indices, tuple) or not isinstance(
            self.estadisticas, tuple
        ):
            msg = "indices y estadisticas van en tuplas: una lista dejaría mutarla"
            raise TypeError(msg)
        # Vacíos, repetidos o fuera de su registro: levanta ValueError con el nombre.
        claves_de_salida(self.indices, self.estadisticas)
        chequeos = (
            (
                _VERSION.fullmatch(self.version) is not None,
                f"versión inválida: {self.version!r}",
            ),
            (
                0 < self.cobertura_minima <= 1,
                f"cobertura_minima fuera de (0, 1]: {self.cobertura_minima}",
            ),
            (
                self.meses_historico >= 1,
                f"meses_historico menor que 1: {self.meses_historico}",
            ),
            (self.escala_m > 0, f"escala_m no positiva: {self.escala_m}"),
            (
                self.remuestreo in _REMUESTREOS,
                (
                    f"remuestreo desconocido: {self.remuestreo!r} "
                    "(nearest, bilinear o bicubic)"
                ),
            ),
            (
                0 <= self.nubes_max_prob <= _PROBABILIDAD_MAXIMA,
                f"nubes_max_prob fuera de [0, 100]: {self.nubes_max_prob}",
            ),
            (
                self.nubes_dilatacion_m >= 0,
                f"nubes_dilatacion_m negativa: {self.nubes_dilatacion_m}",
            ),
            (
                0 < self.sombras_nir_oscuro < 1,
                f"sombras_nir_oscuro fuera de (0, 1): {self.sombras_nir_oscuro}",
            ),
            (
                self.sombras_distancia_m >= 0,
                f"sombras_distancia_m negativa: {self.sombras_distancia_m}",
            ),
            (
                self.nubes_erosion_px >= 0,
                f"nubes_erosion_px negativa: {self.nubes_erosion_px}",
            ),
            (
                self.agrupamiento_estadisticas in AGRUPAMIENTOS,
                (
                    f"agrupamiento_estadisticas desconocido: "
                    f"{self.agrupamiento_estadisticas!r} ({sorted(AGRUPAMIENTOS)})"
                ),
            ),
            (
                self.agrupamiento_raster in AGRUPAMIENTOS,
                (
                    f"agrupamiento_raster desconocido: {self.agrupamiento_raster!r} "
                    f"({sorted(AGRUPAMIENTOS)})"
                ),
            ),
        )
        problemas = [mensaje for cumple, mensaje in chequeos if not cumple]
        if problemas:
            msg = "; ".join(problemas)
            raise ValueError(msg)

    @property
    def sombras_distancia_px(self) -> int:
        """La distancia de sombra en píxeles de ``escala_m``, redondeada hacia arriba.

        ``directionalDistanceTransform`` mide en píxeles, y GEE los cuenta en la
        proyección del pedido. La capa vieja pasaba ``1000 / 10`` fijo: pedida a
        60 m, como la serie vieja, la sombra se proyectaba hasta 6 km. Además de
        usar este valor, M.2.2 arma la máscara en una proyección fija a
        ``escala_m``, para que un pedido a otra escala no la cambie.

        Sale de dos campos que ya están en la huella, así que no suma nada a ella.
        """
        return math.ceil(self.sombras_distancia_m / self.escala_m)

    def contenido(self) -> dict[str, object]:
        """Todo lo que cambia un número, en tipos de JSON. Sin la versión.

        Además de los parámetros, lo que la receta toma de los registros: la
        fórmula y las bandas de cada índice, y el tipo y el percentil de cada
        estadística (M.1.6). Así, cambiar la fórmula de NDVI o el percentil de
        ``p10`` en el registro también cambia la huella, aunque la receta no se
        toque.

        No cubre el código de las etapas (M.2), como la división por 10.000 o el
        agua de SCL en la máscara de sombras: eso se revisa en su PR, como
        cualquier código.
        """
        contenido: dict[str, object] = {
            campo.name: getattr(self, campo.name)
            for campo in dataclasses.fields(self)
            if campo.name != "version"
        }
        contenido["indices"] = {
            nombre: {
                "formula": INDICES[nombre].formula,
                "bandas": {
                    banda: BANDAS[banda] for banda in sorted(INDICES[nombre].bandas)
                },
            }
            for nombre in self.indices
        }
        contenido["estadisticas"] = {
            nombre: {
                "tipo": ESTADISTICAS[nombre].tipo,
                "percentil": ESTADISTICAS[nombre].percentil,
            }
            for nombre in self.estadisticas
        }
        return contenido

    def huella(self) -> str:
        """SHA-256 del :meth:`contenido` en JSON canónico.

        Claves ordenadas y sin espacios, así que no depende del orden en que se
        declararon los campos ni del orden de los índices, que no cambia ningún
        número. No incluye la versión: dos versiones con la misma huella serían
        la misma receta con dos nombres.
        """
        canonico = json.dumps(
            self.contenido(), sort_keys=True, separators=(",", ":"), ensure_ascii=False
        )
        return hashlib.sha256(canonico.encode()).hexdigest()


# Receta v1 (DECISIONS #31). La máscara de nubes y sombras es la de la capa
# vieja (`mask_s2cloudless_and_shadows`), con sus parámetros traídos acá
# (ARQUITECTURA §8.7).
RECETA_VIGENTE = Receta(
    version="s2-mensual-v1",
    coleccion="COPERNICUS/S2_SR_HARMONIZED",
    coleccion_nubes="COPERNICUS/S2_CLOUD_PROBABILITY",
    indices=("ndvi", "evi", "ndre", "ndmi"),
    estadisticas=("mediana", "media", "min", "max", "p10", "p90", "desvio"),
    cobertura_minima=0.3,
    meses_historico=24,
    escala_m=10,
    remuestreo="nearest",
    nubes_max_prob=45,
    nubes_dilatacion_m=50,
    sombras_nir_oscuro=0.15,
    sombras_distancia_m=1000,
    # Los dos los eligió el usuario el 2026-09-16, con los números de M.2.6
    # (`DECISIONS #45`). Antes valían 0 y False, que era lo de la capa vieja.
    nubes_erosion_px=2,
    acotar_indices=True,
    # M.9.0b: el mes dejó de estar cableado, y acá queda escrito lo que antes
    # estaba implícito. `entero` sobre un pedido mensual es **exactamente** el
    # compuesto de siempre, así que ningún número se movió — lo prueba el control
    # negativo de M.9.0b, que compara las filas de antes contra las de después.
    # La huella sí cambia, porque un supuesto pasó a ser un parámetro; el porqué
    # de re-fijarla sin subir la versión está en `tests/test_pipeline_receta.py`.
    agrupamiento_estadisticas=ENTERO,
    agrupamiento_raster=ENTERO,
    # Lo que v1 hizo siempre. M.9.0c lo saca en v2, no acá.
    umbral_al_escribir=True,
)


# Receta v2 (M.9.0c, `DECISIONS #63` y `#66`). **No es la vigente**: existe,
# tiene tests y se verificó contra GEE, pero las altas y el cierre de mes siguen
# escribiendo con v1. El cambio de vigente es su propia decisión, y va **después**
# de que `/api/measurements` sepa agregar y el panel sepa dibujarlo — es la regla
# de despliegue de M.8.1, con el que lee en el lugar del que exige: si el worker
# empezara antes, habría filas que nadie sabe leer.
#
# Contra v1 cambian exactamente dos cosas, y las dos salieron de M.9.0:
#
# - **las estadísticas se agrupan por pasada** en vez de por mes. La mediana de
#   3 pasadas limpias por mes que midió M.9.0 es lo que hace que valga la pena, y
#   el `0 de 72` —ningún mes llega al umbral con todas sus pasadas por debajo— es
#   lo que dice que no se pierde ninguno (`#66`);
# - **el umbral deja de descartar al escribir**.
#
# **El ráster sigue mensual** (`#63`): los motivos de `#31` no cambiaron.
RECETA_POR_PASADA = dataclasses.replace(
    RECETA_VIGENTE,
    version="s2-pasada-v2",
    agrupamiento_estadisticas=POR_PASADA,
    umbral_al_escribir=False,
)
