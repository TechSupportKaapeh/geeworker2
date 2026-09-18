"""El cierre de un job cuando su corrida termina fuera del handler (M.4.7).

El wrapper (``handlers/seguimiento.py``) marca ``failed`` cuando **ve** el error
definitivo. Hay dos casos en que no lo ve nadie, y el job quedaba en ``running``
(o en ``pending``) para siempre. Pasó con los 7 jobs colgados desde el
2026-08-10, y con las altas canceladas a mano el 2026-09-18:

- **Inngest da la corrida por fallida sin que el handler corra su último
  intento**: el contenedor muere (OOM, reinicio, deploy) o el request se corta.
  Inngest publica ``inngest/function.failed``, y el SDK lo entrega al
  ``on_failure`` de la función: :func:`cerrar_por_falla`.
- **Alguien cancela la corrida** desde el dashboard o por la API. Inngest publica
  ``inngest/function.cancelled``, que ``on_failure`` **no** recibe. Lo escucha
  una función propia (``handlers/cancelaciones.py``), que llama a
  :func:`cerrar_por_cancelacion`.

Los dos escriben con ``cerrar_job_abierto``, que solo toca un job ``pending`` o
``running``: no pisan un final que el handler ya escribió, y un cierre repetido
no hace nada.

El estado es ``failed`` y no uno nuevo: Geocore y el panel conocen ``pending``,
``running``, ``completed`` y ``failed``. El motivo va en ``error_message`` y en
la bitácora.
"""

import logging
from collections.abc import Callable, Mapping
from typing import Any

import inngest
from repositories import db_repository
from services.avance_job import ERROR, resumir_error

logger = logging.getLogger(__name__)

# La bitácora de un cierre va con intento 1: la corrida original no le dice al
# cierre en qué intento murió. El panel no separa intentos por la línea `fin`
# (ya pasaba con `ctx.attempt`, HANDOFF §4).
_INTENTO_DEL_CIERRE = 1


def _datos_de(evento: object) -> Mapping[str, Any]:
    """El ``data`` de un evento, sea un objeto del SDK o un ``dict``."""
    datos = getattr(evento, "data", None)
    if datos is None and isinstance(evento, Mapping):
        datos = evento.get("data")
    return datos if isinstance(datos, Mapping) else {}


def job_del_evento_original(datos_del_sistema: Mapping[str, Any]) -> str | None:
    """El ``jobId`` del evento que disparó la corrida que terminó.

    Los eventos de sistema de Inngest traen el evento original en ``event``. Sus
    claves vienen como las manda Geocore (``JobId``, en PascalCase), así que se
    busca con las dos formas, como hace el wrapper.
    """
    original = _datos_de(datos_del_sistema.get("event"))
    for clave in ("jobId", "JobId"):
        valor = original.get(clave)
        if valor:
            return str(valor)
    return None


def motivo_del_evento(datos_del_sistema: Mapping[str, Any], por_defecto: str) -> str:
    """El error que trae el evento de sistema, resumido y sin secretos.

    Termina en ``error_message``, que ve el usuario del tenant: pasa por el mismo
    ``resumir_error`` que el wrapper.
    """
    error = datos_del_sistema.get("error")
    if not isinstance(error, Mapping):
        return por_defecto
    if not (error.get("message") or error.get("name")):
        return por_defecto
    return resumir_error(
        inngest.StepError(
            message=str(error.get("message") or ""),
            name=str(error.get("name") or "Error"),
            stack=None,
        )
    )


def _cerrar(
    ctx: inngest.Context,
    step: inngest.StepSync,
    *,
    mensaje_de: Callable[[str], str],
    por_defecto: str,
) -> dict:
    """Cierra el job del evento original, si hay uno y sigue abierto."""
    datos = _datos_de(ctx.event)
    job_id = job_del_evento_original(datos)
    if not job_id:
        # Una corrida sin job (un evento que no vino de Geocore) no tiene qué cerrar.
        return {"job": None, "cerrado": False}
    mensaje = mensaje_de(motivo_del_evento(datos, por_defecto))

    def _escribir() -> bool:
        cerrado = db_repository.cerrar_job_abierto(job_id, mensaje)
        if cerrado:
            db_repository.registrar_evento_job(
                job_id, _INTENTO_DEL_CIERRE, "fin", ERROR, mensaje, None, None
            )
        return cerrado

    cerrado = step.run("cerrar-job", _escribir)
    estado = "escrito" if cerrado else "ya estaba cerrado"
    logger.info("Cierre del job %s (%s): %s", job_id, estado, mensaje)
    return {"job": job_id, "cerrado": cerrado}


def cerrar_por_falla(ctx: inngest.Context, step: inngest.StepSync) -> dict:
    """El ``on_failure`` de las altas: Inngest dio la corrida por fallida."""
    return _cerrar(
        ctx,
        step,
        mensaje_de=lambda motivo: f"Inngest dio la corrida por fallida: {motivo}",
        por_defecto="sin detalle del error",
    )


def cerrar_por_cancelacion(ctx: inngest.Context, step: inngest.StepSync) -> dict:
    """Una corrida de un alta se canceló en Inngest."""
    return _cerrar(
        ctx,
        step,
        mensaje_de=lambda _motivo: "Se canceló la corrida en Inngest",
        por_defecto="",
    )
