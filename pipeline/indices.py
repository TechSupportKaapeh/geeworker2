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
BANDAS = MappingProxyType(
    {"BLUE": "B2", "RED": "B4", "RE1": "B5", "NIR": "B8", "SWIR1": "B11"}
)


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
    """

    nombre: str
    formula: str
    rango: tuple[float, float]
    tema: str

    def __post_init__(self) -> None:
        """Valida fórmula y rango al armar el registro, que es al importar."""
        desconocidas = self.bandas.difference(BANDAS)
        if desconocidas:
            msg = f"{self.nombre}: bandas sin definir en BANDAS: {sorted(desconocidas)}"
            raise ValueError(msg)
        minimo, maximo = self.rango
        if not minimo < maximo:
            msg = f"{self.nombre}: rango invertido o vacío: {self.rango}"
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
)
