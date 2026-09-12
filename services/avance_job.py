"""La bitacora de un job: lo que el panel ve mientras el worker procesa.

`processing_jobs` dice *en que estado* esta un job; la bitacora
(`processing_job_events`, tabla de Geocore) dice *en que va*: que etapa arranco,
sobre que ventana de fechas, cuantas imagenes encontro y que fallo en cada
intento. Sin ella el panel veia `running` durante minutos, sin forma de saber si
el historico iba por el primer trimestre o por el ultimo, ni que un step llevaba
tres reintentos.

Tres reglas, las tres por como ejecuta Inngest:

1. **Se reporta desde adentro de los `step.run`, nunca desde el cuerpo del
   handler.** Inngest vuelve a ejecutar el cuerpo entero en cada request y solo
   memoiza lo que devuelven los steps: un `reportar()` suelto en el cuerpo se
   escribiria una vez por cada step ya terminado — veinte veces el mismo
   "arranco".

2. **Los fallos de un step se registran adentro del step** (`paso()`). El SDK
   convierte la excepcion de un step en un `ResponseInterrupt`, que es
   `BaseException`: el `except Exception` del wrapper de jobs no la ve nunca.
   Para el wrapper, un step que falla tres veces y se recupera a la cuarta es
   invisible; para la bitacora son tres avisos, cada uno con su error.

3. **Nunca levanta.** Es telemetria: si la base no esta, se pierde la linea y el
   procesamiento sigue (`registrar_evento_job`).
"""
import logging
import re
import time
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass

import inngest

from repositories.db_repository import registrar_evento_job

logger = logging.getLogger("avance_job")

# Los tres niveles que entiende el panel. `warning` es "fallo, pero se va a
# reintentar"; `error` es "no se recupera". Es la misma distincion que E.4 hace
# con el estado `failed`.
INFO = "info"
AVISO = "warning"
ERROR = "error"


@dataclass(frozen=True)
class _Job:
    job_id: str
    intento: int  # 1-indexado: el numero que lee una persona
    total_intentos: int


# Los handlers corren en un pool de hilos y cada uno arranca con el contexto
# vacio (ver `utils_pkg/logging_config.py`): un job no ve el de otro.
_actual: ContextVar["_Job | None"] = ContextVar("avance_job", default=None)


@contextmanager
def seguimiento(job_id, attempt: int, retries: int):
    """Activa la bitacora de `job_id` mientras dure el bloque.

    `attempt` es el `ctx.attempt` de Inngest (0-indexado) y `retries` el
    `retries=` de la funcion. Sin `job_id` no se activa nada y `reportar()` no
    hace nada: los eventos que no vienen de un pedido de Geocore no tienen donde
    escribir.
    """
    job = _Job(str(job_id), attempt + 1, retries + 1) if job_id else None
    token = _actual.set(job)
    try:
        yield
    finally:
        _actual.reset(token)


def reportar(etapa: str, mensaje: str, *, progreso: float | None = None,
             nivel: str = INFO, **detalle) -> None:
    """Escribe una linea en la bitacora del job activo, si lo hay.

    `mensaje` es para una persona ("Serie NDVI, mes 7 de 12: …"); `detalle` son
    los datos crudos (`desde`, `hasta`, `imagenes`, `ms`), que el panel muestra
    aparte. `progreso` (0–100) mueve la barra de `processing_jobs`.

    Llamarla **solo dentro de un `step.run`** (regla 1 del modulo).
    """
    job = _actual.get()
    if job is None:
        return
    registrar_evento_job(job.job_id, job.intento, etapa, nivel, mensaje,
                         detalle or None, progreso)


def es_definitivo(error: BaseException, attempt: int, retries: int) -> bool:
    """Si un error que llego al wrapper ya no se va a recuperar.

    Tres casos, y solo el tercero depende del numero de intento:

    - `NonRetriableError`: el handler pidio explicitamente no reintentar (el
      rancho sin imagenes). Antes el wrapper solo miraba `attempt`, y como
      Inngest no reintenta, el job quedaba en `running` para siempre.
    - `StepError`: es como el SDK entrega el error **memoizado** de un step que
      ya agoto sus reintentos. Que llegue significa que ya no hay vuelta atras,
      en el intento que sea.
    - Cualquier otro: un error en el cuerpo del handler, fuera de los steps.
      Ese si lo reintenta Inngest, y es definitivo en el ultimo intento (E.4).
    """
    if isinstance(error, (inngest.NonRetriableError, inngest.StepError)):
        return True
    return attempt >= retries


def paso(step, etapa: str, funcion):
    """`step.run` que deja en la bitacora el fallo de cada intento.

    Por que hace falta: ver la regla 2 del modulo. El fallo se registra y la
    excepcion **se relanza igual** — es lo que hace que Inngest reintente.
    """
    def _con_bitacora():
        inicio = time.monotonic()
        try:
            return funcion()
        except Exception as e:
            _registrar_fallo(etapa, e, int((time.monotonic() - inicio) * 1000))
            raise
    return step.run(etapa, _con_bitacora)


def _registrar_fallo(etapa: str, error: Exception, ms: int) -> None:
    job = _actual.get()
    if job is None:
        return
    resumen = resumir_error(error)
    de_cuantos = f"intento {job.intento} de {job.total_intentos}"
    if isinstance(error, inngest.NonRetriableError) or job.intento >= job.total_intentos:
        nivel, mensaje = ERROR, f"Fallo en el {de_cuantos} y no se reintenta: {resumen}"
    else:
        nivel, mensaje = AVISO, f"Fallo en el {de_cuantos}; Inngest lo va a reintentar: {resumen}"
    registrar_evento_job(job.job_id, job.intento, etapa, nivel, mensaje,
                         {"error": resumen, "ms": ms}, None)


# --- Mensajes de error que se pueden mostrar ---------------------------------
#
# El error de un job termina en dos lugares que ve gente: la bitacora (panel,
# TerraStaff) y `processing_jobs.error_message`, que `GET /api/processing/jobs/{id}`
# le devuelve **al usuario del tenant**. El mensaje crudo de una excepcion puede
# traer una URL prefirmada de MinIO (con su firma), credenciales en una cadena de
# conexion o el host privado de un servicio. El crudo sigue yendo al log.

LARGO_MAXIMO = 500

_CREDENCIALES_EN_URL = re.compile(r"(?P<esquema>[a-z][a-z0-9+.-]*://)[^\s/@:]+:[^\s/@]+@", re.IGNORECASE)
_QUERY_DE_URL = re.compile(r"(?P<base>https?://[^\s?#]+)\?\S*", re.IGNORECASE)
_CLAVE_VALOR = re.compile(
    r"(?P<clave>password|passwd|pwd|secret|token|api[_-]?key|access[_-]?key|signature)"
    r"(?P<sep>\s*[=:]\s*)\S+",
    re.IGNORECASE,
)
_HOST_INTERNO = re.compile(r"\b[\w.-]+\.railway\.internal(?::\d+)?\b", re.IGNORECASE)


def resumir_error(error: BaseException) -> str:
    """Una linea, `Tipo: mensaje`, sin secretos ni hosts privados, de a lo sumo 500 caracteres."""
    if isinstance(error, inngest.StepError):
        # El error memoizado de un step: el tipo original viene en `name`, porque
        # la clase no se puede reconstruir del otro lado del executor.
        nombre, texto = error.name or "StepError", error.message or ""
    else:
        nombre, texto = type(error).__name__, str(error)

    linea = f"{nombre}: {texto}" if texto else nombre
    linea = _CREDENCIALES_EN_URL.sub(r"\g<esquema>***@", linea)
    linea = _QUERY_DE_URL.sub(r"\g<base>?…", linea)
    linea = _CLAVE_VALOR.sub(r"\g<clave>\g<sep>***", linea)
    linea = _HOST_INTERNO.sub("<host interno>", linea)
    linea = " ".join(linea.split())
    return linea if len(linea) <= LARGO_MAXIMO else linea[: LARGO_MAXIMO - 1] + "…"
