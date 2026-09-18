"""El estado del job y su bitácora alrededor de un handler (M.4.2).

Salió de ``services/inngest_handlers.py`` sin cambiar comportamiento. Lo que hace,
y por qué, está en los comentarios de :func:`_correr_con_estado`: marcar
``running`` y ``completed`` dentro de steps, y ``failed`` solo ante un error
definitivo (E.4).
"""

import logging
from collections.abc import Callable
from typing import Any

import inngest
from repositories import db_repository
from services.avance_job import (
    AVISO,
    ERROR,
    es_definitivo,
    reportar,
    resumir_error,
    seguimiento,
)
from utils_pkg.logging_config import contexto_de_ejecucion

logger = logging.getLogger(__name__)

# Reintentos de Inngest, en **un solo lugar**. El wrapper de jobs necesita saber
# cuál es el último intento para no marcar `failed` antes de tiempo (E.4), y si
# este número y el de los decoradores se separan, el estado del job vuelve a
# mentir sin que nada falle. `ctx.attempt` es 0-indexado, así que el último
# intento es `attempt == RETRIES`.
RETRIES = 3

type Handler = Callable[[inngest.Context, inngest.StepSync, dict], Any]
type ActualizarJob = Callable[..., None]


def envolver_con_estado(
    func: Handler, actualizar_job: ActualizarJob
) -> Callable[[inngest.Context, inngest.StepSync], Any]:
    """Envuelve ``func`` para que lleve el estado de su job.

    ``actualizar_job`` es lo que escribe en ``processing_jobs``, con la firma de
    ``db_repository.update_processing_job``. Se recibe en vez de importarse para
    que cada capa decida de dónde sale: los handlers nuevos usan
    :func:`con_seguimiento`, y los viejos, el suyo (``_with_job_tracking``).
    """

    def wrapper(ctx: inngest.Context, step: inngest.StepSync) -> Any:  # noqa: ANN401 - devuelve lo que devuelva el handler
        # Normalize payload keys (first letter lowercase) to match Python expectations
        payload = {
            k[:1].lower() + k[1:] if isinstance(k, str) else k: v
            for k, v in ctx.event.data.items()
        }
        job_id = payload.get("jobId")

        # F.18: todo lo que se loguee de acá para adentro lleva con qué ejecución
        # y con qué entidad pasó, incluidos los logs de `storage_service`,
        # `db_repository` y `gee_download`, que son los que fallan de verdad y
        # que no tienen forma de conocer este contexto por su cuenta.
        #
        # `run_id` es el identificador que asigna Inngest y el único que permite
        # juntar los intentos de una misma ejecución en el panel y en el log.
        #
        # `seguimiento` hace lo mismo para la bitácora del job: `reportar()` y
        # `paso()` saben a qué job y en qué intento escriben sin que cada
        # handler tenga que pasarlos (`services/avance_job.py`).
        with (
            contexto_de_ejecucion(
                run_id=getattr(ctx, "run_id", None),
                attempt=ctx.attempt,
                funcion=func.__name__,
                job_id=job_id,
                tenant_id=payload.get("tenantId"),
                parcela_id=payload.get("parcelaId"),
                rancho_id=payload.get("ranchoId"),
            ),
            seguimiento(job_id, ctx.attempt, RETRIES),
        ):
            return _correr_con_estado(
                func, ctx, step, payload, job_id=job_id, actualizar_job=actualizar_job
            )

    wrapper.__name__ = func.__name__
    return wrapper


def con_seguimiento(
    func: Handler,
) -> Callable[[inngest.Context, inngest.StepSync], Any]:
    """El decorador de los handlers nuevos: el estado va a ``processing_jobs``.

    ``update_processing_job`` se busca en ``db_repository`` al llamarla, no al
    decorar, así que un test que la reemplace en el repositorio la alcanza.
    """
    return envolver_con_estado(func, _actualizar_en_la_base)


def _actualizar_en_la_base(*args: object, **kwargs: object) -> None:
    """``db_repository.update_processing_job``, buscada al llamarla."""
    db_repository.update_processing_job(*args, **kwargs)


def _correr_con_estado(  # noqa: PLR0913 - el cuerpo del wrapper, con lo que ya armó
    func: Handler,
    ctx: inngest.Context,
    step: inngest.StepSync,
    payload: dict,
    *,
    job_id: str | None,
    actualizar_job: ActualizarJob,
) -> Any:  # noqa: ANN401 - devuelve lo que devuelva el handler
    """El cuerpo de :func:`envolver_con_estado`, ya dentro del contexto de logging.

    Los eventos ``inicio`` y ``fin`` de la bitácora van **dentro** de los steps
    que cambian el estado: quedan memoizados con él, y se escriben una vez por
    ejecución aunque Inngest vuelva a correr este cuerpo en cada request.
    """
    if job_id:

        def _mark_running() -> None:
            actualizar_job(job_id, "running", started_at_now=True)
            reportar(
                "inicio", "El worker tomó el job", progreso=1, funcion=func.__name__
            )

        step.run("mark-job-running", _mark_running)

    try:
        res = func(ctx, step, payload)
        if job_id:

            def _mark_completed() -> None:
                actualizar_job(job_id, "completed", progress=100, finished_at_now=True)
                reportar("fin", "Terminó sin errores", progreso=100)

            step.run("mark-job-completed", _mark_completed)
    except Exception as e:
        # El mensaje se captura **ahora**, no dentro de la clausura: Python
        # borra `e` al salir del bloque `except`, así que una clausura que
        # lo referencie funciona solo mientras se llame acá adentro. Hoy es
        # el caso, pero es una trampa que espera a que alguien mueva la
        # línea. Ruff lo marcaba como F821/F841.
        #
        # Y va resumido: termina en `error_message`, que
        # `GET /api/processing/jobs/{id}` le devuelve al usuario del tenant, y
        # el crudo puede traer una URL prefirmada o un host privado. El crudo
        # va al log.
        mensaje = resumir_error(e)

        if job_id and es_definitivo(e, ctx.attempt, RETRIES):
            # E.4: **solo un error definitivo marca `failed`.**
            #
            # Antes se marcaba en cada intento, y el reintento lo volvía a
            # poner en `running`. Con `retries=3` eso significa que el
            # estado que ve el usuario miente durante toda la ventana de
            # reintentos: un job que se va a recuperar solo aparece como
            # fallido, y quien lo mire va a diagnosticar un problema que no
            # existe. `failed` tiene que significar "no se va a recuperar".
            #
            # "Definitivo" no es solo el último intento: ver `es_definitivo`.
            # Mirar solo `attempt` dejaba en `running` para siempre a un
            # `NonRetriableError`, que Inngest no reintenta.
            logger.error("Job %s fallo y no se va a reintentar: %s", job_id, e)  # noqa: TRY400 - el traceback lo loguea quien reciba el raise

            def _mark_failed() -> None:
                actualizar_job(
                    job_id, "failed", error_message=mensaje, finished_at_now=True
                )
                reportar(
                    "fin", f"Falló y no se va a reintentar: {mensaje}", nivel=ERROR
                )

            step.run("mark-job-failed", _mark_failed)
        elif job_id:
            logger.warning(
                "Job %s fallo en el intento %s de %s; Inngest va a "
                "reintentar, asi que el estado sigue en `running`: %s",
                job_id,
                ctx.attempt + 1,
                RETRIES + 1,
                e,
            )
            # Acá solo llegan errores del cuerpo del handler, fuera de los
            # steps: los de un step los registra `paso()` desde adentro
            # (`avance_job`, regla 2). Cada request que falla acá es un intento
            # real, así que la línea no se duplica en los replays.
            reportar(
                "reintento",
                f"Falló en el intento {ctx.attempt + 1} de {RETRIES + 1}; "
                f"Inngest lo va a reintentar: {mensaje}",
                nivel=AVISO,
            )
        raise
    else:
        return res
