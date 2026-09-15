"""Registros: una entrada por índice o por estadística (``DECISIONS #32``).

Agregar un índice es agregar una entrada, no una rama de un ``if/elif``. El
registro es inmutable y valida los nombres al armarse, que es al importar el
módulo: un nombre repetido rompe el import, no el cálculo de un mes a mitad de
un alta.
"""

import re
from collections.abc import Mapping
from types import MappingProxyType
from typing import Protocol

# Minúsculas y dígitos, sin `_`. El nombre termina siendo tres cosas:
# - el nombre de una banda en GEE;
# - una clave del jsonb y un segmento de la key del COG (`ranchos/{id}/ndvi/...`),
#   donde un `/` o un `..` serían un problema;
# - el prefijo de la clave que devuelve GEE (`ndvi_median`). Sin `_` en el
#   nombre, esa clave no puede salir igual para dos pares distintos.
_NOMBRE = re.compile(r"[a-z][a-z0-9]*")


class Nombrado(Protocol):
    """Lo único que el registro le pide a una entrada: un nombre."""

    @property
    def nombre(self) -> str:
        """El nombre por el que se la busca en el registro."""


def registro[T: Nombrado](*entradas: T) -> Mapping[str, T]:
    """Arma un registro inmutable, indexado por nombre y en el orden dado.

    Raises:
        ValueError: si está vacío, si un nombre no es válido o si se repite.
    """
    if not entradas:
        msg = "un registro sin entradas no sirve para nada"
        raise ValueError(msg)
    por_nombre: dict[str, T] = {}
    for entrada in entradas:
        if _NOMBRE.fullmatch(entrada.nombre) is None:
            msg = f"nombre inválido (minúsculas y dígitos): {entrada.nombre!r}"
            raise ValueError(msg)
        if entrada.nombre in por_nombre:
            msg = f"nombre repetido en el registro: {entrada.nombre!r}"
            raise ValueError(msg)
        por_nombre[entrada.nombre] = entrada
    return MappingProxyType(por_nombre)
