"""Lo que comparten las altas de parcela y de rancho (M.4.4 y M.4.5).

Las dos recorren los mismos meses con la misma receta, y se equivocan de la misma
forma si cada una lo escribe a su manera:

- :func:`planificar`, el step ``plan``: "hoy" y los meses, leídos **una vez**
  dentro de un step, porque Inngest vuelve a correr el cuerpo del handler en cada
  request;
- :func:`requerido`: un campo del evento sin el cual no hay reintento que ayude;
- :func:`errores_de_gee`: la traducción de ``ErrorDeGEE`` a lo que Inngest
  entiende, que el pipeline no conoce (``DECISIONS #42``).
"""

import contextlib
from collections.abc import Iterator
from typing import Any

import inngest
from pipeline.ejecucion import ErrorDeGEE
from pipeline.periodos import Mes, hoy_utc, meses_cerrados
from pipeline.receta import Receta
from services.avance_job import reportar

# La barra: el plan deja 2 y los meses reparten el resto hasta 99. El 100 lo pone
# el wrapper al marcar `completed`.
PROGRESO_PLAN = 2
PROGRESO_MESES = 99


def requerido(payload: dict, clave: str) -> Any:  # noqa: ANN401 - lo que traiga el evento
    """Un campo que el evento tiene que traer.

    Sin él no hay reintento que ayude: se levanta ``NonRetriableError`` para que
    el job quede ``failed`` al primer intento, en vez de fallar cuatro veces igual.
    """
    valor = payload.get(clave)
    if valor is None or valor == "":
        msg = f"el evento no trae {clave}"
        raise inngest.NonRetriableError(msg)
    return valor


def porcentaje(fraccion: float) -> str:
    """``0.936`` como ``93,6 %``, que es como lo lee el panel."""
    return f"{fraccion * 100:.1f} %".replace(".", ",")


def planificar(receta: Receta) -> dict[str, Any]:
    """El step ``plan``: los meses del alta y la receta con que se calculan.

    Devuelve texto (``AAAA-MM``), que es lo que cruza entre steps
    (``DECISIONS #26``); :func:`meses_del_plan` lo vuelve a leer.
    """
    meses = meses_cerrados(hoy_utc(), receta.meses_historico)
    reportar(
        "plan",
        f"{len(meses)} meses cerrados, de {meses[0]} a {meses[-1]}, "
        f"con la receta {receta.version}",
        progreso=PROGRESO_PLAN,
        desde=str(meses[0]),
        hasta=str(meses[-1]),
        receta=receta.version,
    )
    return {"meses": [str(mes) for mes in meses], "receta": receta.version}


def meses_del_plan(plan: dict[str, Any]) -> list[Mes]:
    """Los meses que devolvió el step ``plan``, ya como :class:`Mes`."""
    return [Mes.desde_texto(texto) for texto in plan["meses"]]


@contextlib.contextmanager
def errores_de_gee(mes: Mes, que: str) -> Iterator[None]:
    """Traduce un ``ErrorDeGEE`` definitivo a ``NonRetriableError``.

    "Sin memoria" o "demasiados píxeles" dan el mismo error en cada reintento y
    gastan cuota. Los pasajeros (concurrencia, timeout) se dejan subir tal cual, y
    Inngest los reintenta.

    Args:
        mes: el mes del step, para el mensaje.
        que: la entidad, para el mensaje ("esta parcela", "este rancho").

    Raises:
        inngest.NonRetriableError: si el pedido a GEE no se puede hacer así.
    """
    try:
        yield
    except ErrorDeGEE as error:
        if error.reintentable:
            raise
        msg = f"GEE no puede calcular {mes} de {que}: {error}"
        raise inngest.NonRetriableError(msg) from error
