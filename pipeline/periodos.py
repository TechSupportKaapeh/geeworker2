"""Meses calendario en UTC: la unidad de tiempo del pipeline (M.1.1).

El histórico es mensual (``DECISIONS #31``). El mes es el calendario en UTC:
Sentinel-2 pasa sobre México cerca de las 17:00 UTC, así que el mes UTC y el
local coinciden siempre (``ARQUITECTURA_PIPELINE.md`` §1).

Todo es puro. El único lugar que lee el reloj es :func:`hoy_utc`, para que el
resto se pruebe con cualquier "hoy".
"""

import re
from dataclasses import dataclass
from datetime import MAXYEAR, MINYEAR, UTC, date, datetime
from typing import Self

_MESES_POR_ANIO = 12

# `[0-9]` y no `\d`: en un patrón de texto, `\d` también acepta dígitos de otros
# alfabetos ("٢٠٢٦-٠٩"), e `int()` los convierte sin quejarse.
_FORMATO = re.compile(r"([0-9]{4})-([0-9]{2})")


@dataclass(frozen=True, order=True, slots=True)
class Mes:
    """Un mes calendario. Se ordena por año y después por mes.

    Se escribe ``AAAA-MM`` con ``str(mes)``. Esa es la forma que viaja: el id del
    step (``mes-2025-09``), la key del COG y lo que devuelve el step del plan.
    Entre steps de Inngest solo cruzan referencias durables (``DECISIONS #26``),
    así que cruza el texto y se reconstruye con :meth:`desde_texto`.
    """

    anio: int
    mes: int

    def __post_init__(self) -> None:
        """Rechaza meses que no existen, en vez de arrastrarlos hasta GEE."""
        if not MINYEAR <= self.anio <= MAXYEAR:
            msg = f"año fuera de rango: {self.anio}"
            raise ValueError(msg)
        if not 1 <= self.mes <= _MESES_POR_ANIO:
            msg = f"mes fuera de rango: {self.mes}"
            raise ValueError(msg)

    def __str__(self) -> str:
        """``AAAA-MM``, con ceros a la izquierda."""
        return f"{self.anio:04d}-{self.mes:02d}"

    @classmethod
    def desde_texto(cls, texto: str) -> Self:
        """Lee ``AAAA-MM`` exacto: sin espacios, sin ``2026-9``, sin ``2026/09``."""
        coincidencia = _FORMATO.fullmatch(texto)
        if coincidencia is None:
            msg = f"se esperaba AAAA-MM: {texto!r}"
            raise ValueError(msg)
        return cls(int(coincidencia[1]), int(coincidencia[2]))

    @classmethod
    def desde_fecha(cls, fecha: date) -> Self:
        """El mes UTC de una fecha.

        Una ``date`` se toma como ya expresada en UTC. Un ``datetime`` con huso se
        pasa a UTC antes de mirar el mes: las 20:00 del 31 de agosto en México
        son el 1 de septiembre en UTC. Un ``datetime`` sin huso es ambiguo y se
        rechaza, porque es justo el error que corre un mes sin avisar.
        """
        if isinstance(fecha, datetime):
            if fecha.utcoffset() is None:
                msg = f"datetime sin huso, no se sabe qué mes UTC es: {fecha!r}"
                raise ValueError(msg)
            fecha = fecha.astimezone(UTC)
        return cls(fecha.year, fecha.month)

    def desplazar(self, meses: int) -> Self:
        """El mes que queda ``meses`` más adelante (o más atrás, si es negativo)."""
        anio, resto = divmod(
            self.anio * _MESES_POR_ANIO + self.mes - 1 + meses, _MESES_POR_ANIO
        )
        return type(self)(anio, resto + 1)

    def anterior(self) -> Self:
        """El mes de antes: el de enero es diciembre del año anterior."""
        return self.desplazar(-1)

    def siguiente(self) -> Self:
        """El mes de después: el de diciembre es enero del año siguiente."""
        return self.desplazar(1)


def rango(mes: Mes) -> tuple[datetime, datetime]:
    """El intervalo semiabierto ``[inicio, fin)`` del mes, en UTC.

    ``inicio`` es el primer instante del mes y ``fin`` el primero del siguiente,
    excluido. Es la convención de ``filterDate`` de GEE, y evita los dos errores
    de un rango cerrado: perder el último día o contarlo dos veces. Los
    bisiestos salen solos, sin calcular cuántos días tiene febrero.

    ``inicio`` es también la ``fecha`` de la fila mensual en ``measurements``
    (``ARQUITECTURA_PIPELINE.md`` §6).
    """
    siguiente = mes.siguiente()
    return (
        datetime(mes.anio, mes.mes, 1, tzinfo=UTC),
        datetime(siguiente.anio, siguiente.mes, 1, tzinfo=UTC),
    )


def meses_cerrados(hoy: date, n: int) -> tuple[Mes, ...]:
    """Los últimos ``n`` meses cerrados antes de ``hoy``, del más viejo al más nuevo.

    El mes de ``hoy`` nunca está cerrado, ni siquiera el día 1: todavía le faltan
    las pasadas del resto del mes. El alta de una parcela el 2026-09-12, con 24
    meses, va de 2024-09 a 2026-08.

    Del más viejo al más nuevo porque es el orden de los steps: si un alta se
    corta, lo que quedó escrito es el principio de la serie, sin huecos.
    """
    if n < 0:
        msg = f"n no puede ser negativo: {n}"
        raise ValueError(msg)
    ultimo = Mes.desde_fecha(hoy).anterior()
    return tuple(ultimo.desplazar(-atras) for atras in range(n - 1, -1, -1))


def hoy_utc() -> date:
    """La fecha de hoy en UTC. Es el único lugar del módulo que lee el reloj."""
    return datetime.now(UTC).date()
