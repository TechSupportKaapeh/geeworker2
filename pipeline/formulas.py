"""Las fórmulas de los índices: texto que entienden GEE y Python (M.1.2).

Una fórmula es texto sobre bandas con nombre, en reflectancia 0-1, por ejemplo
``"(NIR - RED) / (NIR + RED)"`` (``DECISIONS #32``). GEE la evalúa con
``Image.expression`` y los tests con :func:`evaluar`. Para que los dos no puedan
leerla distinto, se acepta solo lo que significa lo mismo en los dos:

- números, nombres de banda y paréntesis;
- ``+``, ``-``, ``*`` y ``/``, y el signo delante de un término.

Nada de llamadas, atributos, potencias, comparaciones ni condicionales. Si un
índice futuro los necesita, se suman acá con su test, y antes se comprueba en GEE
que los entienda igual.

La fórmula se lee con ``ast`` y se recorre a mano: nunca pasa por ``eval``.
"""

import ast
import operator
from collections.abc import Callable, Mapping
from types import MappingProxyType

type _Operacion = Callable[[float, float], float]

_BINARIOS: Mapping[type[ast.operator], _Operacion] = MappingProxyType(
    {
        ast.Add: operator.add,
        ast.Sub: operator.sub,
        ast.Mult: operator.mul,
        ast.Div: operator.truediv,
    }
)
_UNARIOS: Mapping[type[ast.unaryop], Callable[[float], float]] = MappingProxyType(
    {ast.USub: operator.neg, ast.UAdd: operator.pos}
)


def _leer(formula: str) -> ast.expr:
    try:
        return ast.parse(formula, mode="eval").body
    except SyntaxError as error:
        msg = f"fórmula mal escrita: {formula!r}"
        raise ValueError(msg) from error


def _bandas(nodo: ast.expr, formula: str) -> set[str]:
    """Valida el nodo y devuelve los nombres de banda que usa."""
    match nodo:
        # `bool` es un `int` para Python: `True` pasaría por un número.
        case ast.Constant(value=bool()):
            pass
        case ast.Constant(value=int() | float()):
            return set()
        case ast.Name(id=nombre):
            return {nombre}
        case ast.BinOp(left=izquierda, op=op, right=derecha) if type(op) in _BINARIOS:
            return _bandas(izquierda, formula) | _bandas(derecha, formula)
        case ast.UnaryOp(op=op, operand=operando) if type(op) in _UNARIOS:
            return _bandas(operando, formula)
        case _:
            pass
    msg = f"{type(nodo).__name__} no está permitido en una fórmula: {formula!r}"
    raise ValueError(msg)


def bandas_de(formula: str) -> frozenset[str]:
    """Valida la fórmula y devuelve los nombres de banda que usa.

    Raises:
        ValueError: si la fórmula no se puede leer, o usa algo fuera de lo
            permitido (ver el docstring del módulo).
    """
    return frozenset(_bandas(_leer(formula), formula))


def _calcular(nodo: ast.expr, valores: Mapping[str, float]) -> float:
    # Solo llegan nodos que `_bandas` ya aceptó.
    match nodo:
        case ast.Constant(value=valor):
            return float(valor)
        case ast.Name(id=nombre):
            return valores[nombre]
        case ast.BinOp(left=izquierda, op=op, right=derecha):
            return _BINARIOS[type(op)](
                _calcular(izquierda, valores), _calcular(derecha, valores)
            )
        case ast.UnaryOp(op=op, operand=operando):
            return _UNARIOS[type(op)](_calcular(operando, valores))
    msg = f"nodo sin validar: {type(nodo).__name__}"
    raise AssertionError(msg)


def evaluar(formula: str, valores: Mapping[str, float]) -> float:
    """Calcula la fórmula en Python, para un solo píxel.

    Es el evaluador de referencia de los tests: prueba una fórmula sin GEE. No
    produce datos, así que una división por cero levanta ``ZeroDivisionError``.
    La documentación de ``ee.Image.divide`` dice que GEE devuelve 0 en ese caso;
    se confirma en M.2.6.

    Raises:
        ValueError: si la fórmula no es válida o falta el valor de una banda.
    """
    arbol = _leer(formula)
    faltan = _bandas(arbol, formula) - valores.keys()
    if faltan:
        msg = f"faltan valores para {sorted(faltan)} en {formula!r}"
        raise ValueError(msg)
    return _calcular(arbol, valores)
