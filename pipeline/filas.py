"""Las filas de una parcela, desde la reducción de una ventana (M.4.3).

Una fila por parcela, índice y **ventana** (``ARQUITECTURA_PIPELINE.md`` §6).
Hasta M.9.0b la ventana era siempre el mes y estaba cableada acá; desde
``DECISIONS #63`` la trae quien llama, y este módulo dejó de saber qué es un mes.
Con la receta de hoy —``agrupamiento_estadisticas: entero``— la ventana es el mes
y la fila es exactamente la misma de antes.

- ``fecha`` es el **primer instante de la ventana** en UTC, igual para todos los
  índices. Sobre un pedido mensual eso es el día 1 a las 00:00, que es lo que se
  venía escribiendo; con ``por_pasada`` sería el instante de la adquisición, y
  por eso la clave ``(parcela, índice, fecha)`` sirve sin migrar (``#63``);
- ``valor`` es la mediana. Con ``receta.umbral_al_escribir``, queda **nulo si la
  cobertura no llegó al mínimo** y la fila se escribe igual: el front dibuja el
  hueco (B-6), y el cierre de mes sabe que ese mes ya se procesó. Sin él —que es
  lo que hace ``s2-pasada-v2``— el valor va siempre y **el umbral es de quien
  lee** (``DECISIONS #63``). La fila lleva su ``cobertura`` en las dos, así que
  filtrar al leer es un ``WHERE``;
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
from pipeline.receta import Receta
from pipeline.ventanas import Ventana

# La estadística que va a `valor`, la columna que leen la serie y la métrica del
# rancho. Es la mediana desde `DECISIONS #31`.
ESTADISTICA_VALOR = "mediana"


@dataclass(frozen=True, slots=True)
class Fila:
    """Una fila de ``measurements``, con los nombres de sus columnas.

    Se llamaba ``FilaMensual`` hasta M.9.0c, y dejó de ser cierto: con
    ``s2-pasada-v2`` una fila es una pasada. Lo que no cambia es que
    :attr:`fecha` es el primer instante de su ventana, así que la clave
    ``(parcela, índice, fecha)`` sirve para las dos sin migrar (``DECISIONS
    #63``).
    """

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


def filas_de(
    *,
    parcela_id: str,
    tenant_id: str,
    ventana: Ventana,
    reduccion: Reduccion,
    receta: Receta,
) -> tuple[Fila, ...]:
    """Las filas de una parcela en una ventana, una por índice de la receta.

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
    # Sin `umbral_al_escribir` el valor va siempre: descartar acá es una reducción
    # con pérdida antes de guardar, y es la que no se puede deshacer.
    alcanza = not receta.umbral_al_escribir or (
        cobertura is not None and cobertura >= receta.cobertura_minima
    )

    filas = []
    for indice in receta.indices:
        estadisticas = {
            nombre: _finito(valor, f"{indice}.{nombre}")
            for nombre, valor in reduccion.estadisticas[indice].items()
        }
        filas.append(
            Fila(
                parcela_id=parcela_id,
                tenant_id=tenant_id,
                indice=indice,
                fecha=ventana.inicio,
                valor=estadisticas[ESTADISTICA_VALOR] if alcanza else None,
                estadisticas=estadisticas,
                cobertura=cobertura,
                observaciones=observaciones,
                receta=receta.version,
            )
        )
    return tuple(filas)
