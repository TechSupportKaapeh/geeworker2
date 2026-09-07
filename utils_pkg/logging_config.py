"""Logging del worker, con **contexto de ejecucion**.

El problema que resuelve (`PLAN.md` F.18). Antes de esto:

- el `JSONFormatter` existia pero estaba **apagado por defecto**
  (`LOG_FORMAT=text`), y la variable no aparecia en ningun `.env` ni doc;
- el formatter serializaba solo `timestamp/level/logger/message`, asi que
  **descartaba cualquier `extra`**: loguear con contexto no servia de nada;
- y **nada correlacionaba**. `ctx.run_id` existe en el `Context` de Inngest y no
  se usaba en ninguna parte, ni `job_id`, ni las ids de entidad.

Consecuencia concreta: cuando un handler agotaba sus 3 reintentos quedaban lineas
de ERROR sueltas en el log de Railway y **ninguna forma de saber a que ejecucion
pertenecian** ni con que parcela.

## Como funciona

`contexto_de_ejecucion()` guarda un diccionario en un `ContextVar`, y un
`logging.Filter` lo inyecta en **todos** los registros que se emitan mientras
este activo. Eso importa mas de lo que parece: la correlacion aparece tambien en
los logs de `storage_service`, `db_repository` y `gee_download` —que son los
modulos que fallan de verdad— sin que ninguno tenga que enterarse de que existe
un contexto.

Los handlers de Inngest corren en un pool de hilos, y cada hilo arranca con su
propio contexto vacio. Como el contexto se fija **dentro** del handler
(`_with_job_tracking`), cada ejecucion queda aislada de las demas sin trabajo
extra.
"""
import contextlib
import json
import logging
import os
import sys
from contextvars import ContextVar

# Atributos que `LogRecord` trae de fabrica. Todo lo que no este aca es `extra`
# que alguien puso a proposito, y hay que serializarlo.
_ATRIBUTOS_ESTANDAR = frozenset({
    "args", "asctime", "created", "exc_info", "exc_text", "filename",
    "funcName", "levelname", "levelno", "lineno", "message", "module",
    "msecs", "msg", "name", "pathname", "process", "processName",
    "relativeCreated", "stack_info", "taskName", "thread", "threadName",
})

# El default es `None` y no `{}`: un diccionario mutable compartido entre todos
# los contextos es fragil aunque hoy nadie lo mute (ruff lo marca como B039).
_contexto: ContextVar[dict | None] = ContextVar("contexto_de_ejecucion", default=None)


def _activo() -> dict:
    return _contexto.get() or {}


@contextlib.contextmanager
def contexto_de_ejecucion(**campos):
    """Agrega campos al contexto de logging mientras dure el bloque.

    Anida: un bloque interno ve tambien los campos del externo. Se restaura en
    `finally`, asi que una excepcion no deja el contexto sucio para la proxima
    ejecucion que reuse el hilo.
    """
    limpios = {k: v for k, v in campos.items() if v not in (None, "")}
    token = _contexto.set({**_activo(), **limpios})
    try:
        yield
    finally:
        _contexto.reset(token)


class _FiltroDeContexto(logging.Filter):
    """Copia el contexto activo a cada registro, como si fuera `extra`."""

    def filter(self, record):
        for clave, valor in _activo().items():
            if not hasattr(record, clave):
                setattr(record, clave, valor)
        return True


def _extras(record):
    return {
        clave: valor
        for clave, valor in record.__dict__.items()
        if clave not in _ATRIBUTOS_ESTANDAR and not clave.startswith("_")
    }


class JSONFormatter(logging.Formatter):
    """Una linea de JSON por registro, para el log estructurado del deploy."""

    def format(self, record):
        salida = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%SZ"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        salida.update(_extras(record))
        if record.exc_info:
            salida["exception"] = self.formatException(record.exc_info)
        return json.dumps(salida, default=str)


class TextFormatter(logging.Formatter):
    """Formato legible para desarrollo, **con el contexto al final**.

    Si el contexto no se viera en modo texto, en local nadie lo usaria y el
    unico lugar donde aparece seria produccion — donde no se puede iterar.
    """

    def __init__(self):
        super().__init__(
            fmt="[%(asctime)s] %(levelname)s in %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )

    def format(self, record):
        base = super().format(record)
        extras = _extras(record)
        if not extras:
            return base
        contexto = " ".join(f"{k}={v}" for k, v in sorted(extras.items()))
        return f"{base}  [{contexto}]"


def formato_por_defecto(is_production: bool) -> str:
    """`json` en produccion, `text` en desarrollo.

    Antes el default era `text` siempre, asi que un deploy salia con logs sin
    estructurar a menos que alguien se acordara de la variable —y la variable no
    estaba documentada en ningun lado—. `LOG_FORMAT` sigue pudiendo forzar
    cualquiera de los dos.
    """
    return "json" if is_production else "text"


def setup_logging():
    """Configura el logging del proceso. Idempotente."""
    from config import IS_PRODUCTION

    nivel = getattr(logging, os.getenv("LOG_LEVEL", "INFO").upper(), logging.INFO)
    formato = os.getenv("LOG_FORMAT", formato_por_defecto(IS_PRODUCTION)).lower()

    raiz = logging.getLogger()
    raiz.setLevel(nivel)
    if raiz.hasHandlers():
        raiz.handlers.clear()

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JSONFormatter() if formato == "json" else TextFormatter())
    handler.addFilter(_FiltroDeContexto())
    raiz.addHandler(handler)

    # uvicorn y fastapi traen sus propios handlers; se los reemplaza para que
    # sus lineas salgan en el mismo formato y con el mismo contexto.
    for nombre in ("uvicorn", "uvicorn.error", "uvicorn.access", "fastapi"):
        logger = logging.getLogger(nombre)
        logger.handlers = [handler]
        logger.propagate = False
