"""Registro de índices espectrales (M.1.2, ``DECISIONS #32``).

Un índice es una entrada de :data:`INDICES`: nombre, fórmula en texto, rango
plausible y tema. El compuesto, las estadísticas y el mapa la toman de acá.
Sumar uno (M.9.3) es una entrada más y un test con su valor de referencia.

**Las fórmulas son sobre reflectancia 0-1.** S2 SR guarda la reflectancia
multiplicada por 10.000, y las constantes de EVI (el ``+ 1`` y los coeficientes)
están pensadas para 0-1. Con bandas de miles quedan despreciables y el índice
sale mal: es uno de los errores de la capa vieja (``ARQUITECTURA_PIPELINE.md``
§8.6). La fuente divide por 10.000 antes de cualquier fórmula (M.2.1).
"""

from dataclasses import dataclass
from types import MappingProxyType

from pipeline.formulas import bandas_de
from pipeline.registro import registro

# Las bandas espectrales de COPERNICUS/S2_SR_HARMONIZED. B10 (cirros) no está en
# el producto de superficie.
BANDAS_S2 = frozenset(
    {"B1", "B2", "B3", "B4", "B5", "B6", "B7", "B8", "B8A", "B9", "B11", "B12"}
)

# El nombre que usan las fórmulas y la banda de S2 que le corresponde. B5 y B11
# son de 20 m y se remuestrean a los 10 m de la receta: es lo habitual.
# En el orden de S2, que es el orden en que la fuente las pide. GREEN la usa sólo
# el color real (M.9.7e1): ningún índice la lee, así que sumarla no cambió nada.
BANDAS = MappingProxyType(
    {
        "BLUE": "B2",
        "GREEN": "B3",
        "RED": "B4",
        "RE1": "B5",
        "NIR": "B8",
        "SWIR1": "B11",
    }
)


# La escala del COG de un índice normalizado (M.9.7b, `DECISIONS #73`): cuatro
# decimales, y [-1, 1] cabe de sobra en un int16. Vivía en `productos.py` como una
# sola para todo el COG; desde M.9.3 cada índice tiene la suya.
ESCALA_COG_NORMALIZADO = 10_000

# El mayor int16 en valor absoluto que no es el centinela de "sin dato" (-32.768).
TOPE_INT16 = 32_767


@dataclass(frozen=True, slots=True)
class Indice:
    """Un índice espectral.

    Attributes:
        nombre: minúsculas y dígitos (lo valida ``registro``). Es el nombre de la
            banda en GEE y la clave en el jsonb.
        formula: texto sobre las claves de :data:`BANDAS` (``pipeline.formulas``).
        rango: los valores plausibles, ``(mínimo, máximo)``. Un valor afuera no es
            un índice raro sino un dato roto, como una nube que escapó a la
            máscara: así lo usa la compuerta de M.2. No es la escala de color.
        tema: qué mide, en palabras.
        escala_cog: por cuánto se multiplica para guardarlo como entero en el COG
            del rancho (M.9.7b). Los índices normalizados, por 10.000; uno que no
            vive en [-1, 1], por menos, para que su rango entre en un int16. La
            fila de ``layers`` lleva esta escala, y el panel la usa por capa.
    """

    nombre: str
    formula: str
    rango: tuple[float, float]
    tema: str
    escala_cog: int = ESCALA_COG_NORMALIZADO

    def __post_init__(self) -> None:
        """Valida fórmula, rango y escala al armar el registro, que es al importar."""
        desconocidas = self.bandas.difference(BANDAS)
        if desconocidas:
            msg = f"{self.nombre}: bandas sin definir en BANDAS: {sorted(desconocidas)}"
            raise ValueError(msg)
        minimo, maximo = self.rango
        if not minimo < maximo:
            msg = f"{self.nombre}: rango invertido o vacío: {self.rango}"
            raise ValueError(msg)
        # Con `acotar_indices` el índice vive en su rango; por la escala tiene que
        # caber en un int16 sin tocar el centinela de "sin dato" (-32.768). Si no,
        # el COG lo recortaría al tope sin ningún error: el LAI de 3,5 por 10.000
        # daba 35.000 (M.9.3, 2026-10-09).
        if max(abs(minimo), abs(maximo)) * self.escala_cog > TOPE_INT16:
            msg = (
                f"{self.nombre}: su rango {self.rango} por {self.escala_cog} no "
                f"entra en un int16 (±{TOPE_INT16}): bajar escala_cog"
            )
            raise ValueError(msg)

    @property
    def bandas(self) -> frozenset[str]:
        """Los nombres de banda que usa la fórmula, claves de :data:`BANDAS`."""
        return bandas_de(self.formula)


# Receta v1 (DECISIONS #31). Cada entrada cita de dónde sale su definición; los
# tests la comparan contra esa definición escrita aparte.
INDICES = registro(
    # Rouse et al. (1974).
    Indice(
        "ndvi",
        "(NIR - RED) / (NIR + RED)",
        rango=(-1.0, 1.0),
        tema="vegetación",
    ),
    # Huete et al. (2002): G = 2,5; C1 = 6; C2 = 7,5; L = 1.
    Indice(
        "evi",
        "2.5 * (NIR - RED) / (NIR + 6 * RED - 7.5 * BLUE + 1)",
        rango=(-1.0, 1.0),
        tema="vegetación densa",
    ),
    # Barnes et al. (2000): la diferencia normalizada con el borde rojo.
    Indice(
        "ndre",
        "(NIR - RE1) / (NIR + RE1)",
        rango=(-1.0, 1.0),
        tema="clorofila",
    ),
    # Wilson y Sader (2002): NIR contra el SWIR de 1610 nm.
    Indice(
        "ndmi",
        "(NIR - SWIR1) / (NIR + SWIR1)",
        rango=(-1.0, 1.0),
        tema="humedad",
    ),
    # M.9.3 (2026-10-09, pedido del equipo). Huete (1988), con L = 0,5: el NDVI
    # corregido por el suelo a la vista, para cultivos jóvenes o ralos. Con
    # reflectancia 0-1 vive en [-1, 1]: (1 + L) y el `+ L` del denominador se
    # compensan en los extremos.
    Indice(
        "savi",
        "1.5 * (NIR - RED) / (NIR + RED + 0.5)",
        rango=(-1.0, 1.0),
        tema="vegetación con suelo a la vista",
    ),
    # M.9.3 (2026-10-09). **Una estimación**, no una medición: el LAI empírico de
    # Boegh et al. (2002), LAI = 3,618·EVI - 0,118, calibrado en cultivos. Lo eligió
    # el usuario frente a dos alternativas (desde SAVI con logaritmo, y la red
    # neuronal biofísica de SNAP), `DECISIONS #81`. La fórmula repite la del EVI
    # porque el lenguaje no deja citar otro índice.
    #
    # **El rango es [0, 3,5], y no "hasta 8" como un LAI medido**: es lineal en el
    # EVI, y acotarlo a 3,5 = 3,618·1 - 0,118 es lo mismo que calcularlo desde el
    # EVI acotado a [-1, 1]. O sea, satura con canopeo denso: arriba de 3,5 no
    # distingue. Va por 1.000 en el COG, porque 3,5 por 10.000 no entra en un int16.
    Indice(
        "lai",
        "3.618 * (2.5 * (NIR - RED) / (NIR + 6 * RED - 7.5 * BLUE + 1)) - 0.118",
        rango=(0.0, 3.5),
        tema="área foliar (estimada desde el EVI)",
        escala_cog=1_000,
    ),
)
