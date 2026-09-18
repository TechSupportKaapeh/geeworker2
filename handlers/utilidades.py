"""Utilidades chicas de los handlers: temporales, avance y milisegundos (M.4.2)."""

import logging
import os
import time

logger = logging.getLogger(__name__)


def borrar_temporales(*rutas: str | None) -> None:
    """Borra archivos temporales sin dejar que un fallo al borrar tape el real."""
    for ruta in rutas:
        if not ruta:
            continue
        try:
            os.remove(ruta)  # noqa: PTH107 - las rutas llegan como str de tempfile
        except OSError as e:
            # Un temporal que no se pudo borrar es basura en disco, no un fallo
            # del procesamiento. Se registra para que no sea invisible.
            logger.warning("No se pudo borrar el temporal %s: %s", ruta, e)


def entre(inicio: float, fin: float, hechas: int, total: int) -> float:
    """Avance de la barra cuando terminaron ``hechas`` de ``total`` partes."""
    return inicio + (fin - inicio) * hechas / total


def ms_desde(t0: float) -> int:
    """Milisegundos desde ``t0``, tomado con ``time.monotonic()``."""
    return int((time.monotonic() - t0) * 1000)
