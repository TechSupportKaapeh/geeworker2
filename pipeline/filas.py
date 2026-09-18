"""Las filas mensuales de una parcela, desde la reducción del mes (M.4.3).

Una fila por parcela, índice y mes (``ARQUITECTURA_PIPELINE.md`` §6):

- ``fecha`` es el primer instante del mes en UTC, igual para todos los índices;
- ``valor`` es la mediana, **o nulo si la cobertura quedó bajo el mínimo de la
  receta**. La fila se escribe igual: el front dibuja el hueco (B-6), y el cierre
  de mes sabe que ese mes ya se procesó;
- ``estadisticas`` va siempre, aunque ``valor`` sea nulo. Son los números que se
  calcularon, y descartarlos es perder información que ya se pagó. Quien los
  use filtra por ``valor`` o por ``cobertura``: la métrica del rancho ya lo hace
  (Geocore ``DECISIONS #29``);
- ``receta`` es la versión que produjo la fila.

Es puro: arma las filas, no las escribe. Las escribe
``repositories/db_repository.py:upsert_mediciones_mensuales``.
"""

import math
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime

from pipeline.etapas.reduccion import Reduccion
from pipeline.periodos import Mes, rango
from pipeline.receta import Receta

# La estadística que va a `valor`, la columna que leen la serie y la métrica del
# rancho. Es la mediana desde `DECISIONS #31`.
ESTADISTICA_VALOR = "mediana"


@dataclass(frozen=True, slots=True)
class FilaMensual:
    """Una fila de ``measurements``, con los nombres de sus columnas."""

    parcela_id: str
    tenant_id: str
    indice: str
    fecha: datetime
    valor: float | None
    estadisticas: Mapping[str, float | None]
    cobertura: float
    observaciones: float | None
    receta: str


def _finito(numero: float | None, que: str) -> float | None:
    """Deja pasar ``None`` y los números finitos; levanta con un ``NaN`` o un infinito.

    ``json.dumps`` escribe ``NaN`` sin quejarse, pero no es JSON, y Postgres
    rechaza el ``jsonb`` con el insert entero. GEE devuelve ``None`` cuando no hay
    píxeles, no ``NaN``: si aparece uno, algo cambió, y es mejor saberlo acá.
    """
    if numero is not None and not math.isfinite(numero):
        msg = f"{que} no es un número finito: {numero!r}"
        raise ValueError(msg)
    return numero


def filas_del_mes(
    *, parcela_id: str, tenant_id: str, mes: Mes, reduccion: Reduccion, receta: Receta
) -> tuple[FilaMensual, ...]:
    """Las filas de una parcela en un mes, una por índice de la receta.

    Raises:
        ValueError: si la receta no calcula la mediana, si la reducción no trae
            exactamente los índices de la receta, o si algún número no es finito.
    """
    if ESTADISTICA_VALOR not in receta.estadisticas:
        msg = f"la receta {receta.version} no calcula {ESTADISTICA_VALOR}: sin valor"
        raise ValueError(msg)
    if set(reduccion.estadisticas) != set(receta.indices):
        msg = (
            f"la reducción trae {sorted(reduccion.estadisticas)} y la receta "
            f"{receta.version} calcula {sorted(receta.indices)}"
        )
        raise ValueError(msg)

    cobertura = _finito(reduccion.cobertura, "cobertura")
    observaciones = _finito(reduccion.observaciones, "observaciones")
    alcanza = cobertura is not None and cobertura >= receta.cobertura_minima
    inicio, _ = rango(mes)

    filas = []
    for indice in receta.indices:
        estadisticas = {
            nombre: _finito(valor, f"{indice}.{nombre}")
            for nombre, valor in reduccion.estadisticas[indice].items()
        }
        filas.append(
            FilaMensual(
                parcela_id=parcela_id,
                tenant_id=tenant_id,
                indice=indice,
                fecha=inicio,
                valor=estadisticas[ESTADISTICA_VALOR] if alcanza else None,
                estadisticas=estadisticas,
                cobertura=cobertura,
                observaciones=observaciones,
                receta=receta.version,
            )
        )
    return tuple(filas)
