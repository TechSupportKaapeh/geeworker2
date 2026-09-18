"""Las keys del COG mensual en el bucket (M.4.1, ``DECISIONS #47``).

La forma es::

    tenants/{tenantId}/ranchos/{ranchoId}/{receta}/{indice}/{AAAA-MM}.tif

Va de lo más estable a lo que más varía, porque S3 filtra por prefijo
(``PREGUNTAS_ABIERTAS`` A-7), y cada pregunta útil queda como un prefijo:

- ``tenants/{t}/``: todo lo de un tenant. Es lo que TiTiler va a comparar
  contra el ``tenant_id`` del token de mapa para cerrar A01 (M.8.1);
- ``…/ranchos/{r}/{receta}/``: lo que produjo una receta, para limpiarlo cuando
  la reemplace otra;
- ``…/{receta}/ndvi/``: la serie NDVI del rancho.

**La receta va en la key** porque el tileserver sirve los tiles con
``Cache-Control: public, max-age=31536000, immutable``. Si un reproceso con otra
receta escribiera sobre la misma key, la URL del tile no cambiaría y el navegador
seguiría mostrando el mapa viejo hasta un año. Con la receta adentro, una receta
nueva es otra URL.

**La receta no va en la natural_key.** La fila de ``layers`` es una por rancho,
índice y mes, igual que la de ``measurements``: al reprocesar, el upsert apunta
la fila a la key nueva y actualiza ``receta``. El objeto de la receta anterior
queda en el bucket, bajo su propio prefijo, hasta que se limpie.

Todo es puro: arma texto, no habla con el bucket.
"""

import uuid
from typing import NamedTuple

from pipeline.periodos import Mes
from pipeline.receta import Receta

PREFIJO_TENANTS = "tenants"
# `Guid.Empty` en Geocore: un id sin asignar, nunca uno de verdad.
_UUID_NULO = uuid.UUID(int=0)


class ClavesDeCapa(NamedTuple):
    """Las dos claves de una capa, que salen siempre juntas (E.9).

    Attributes:
        storage_key: la key del objeto en el bucket, pelada, sin
            ``s3://{bucket}/``. Es lo que va a ``layers.storage_key``.
        natural_key: la identidad de la capa. ``insert_layer`` siembra con ella
            el UUIDv5 de la fila, así que dos escrituras de la misma capa caen en
            la misma fila.
    """

    storage_key: str
    natural_key: str


def _uuid_canonico(valor: str, nombre: str) -> str:
    """El uuid en su forma canónica: minúsculas, con guiones.

    TiTiler va a comparar el prefijo como texto contra el ``tenant_id`` del token,
    que Geocore escribe con ``Guid.ToString()``: minúsculas y con guiones. Un
    ``A1B2…`` en la key y un ``a1b2…`` en el token serían dos tenants distintos.

    Pasar por ``uuid.UUID`` también garantiza que el segmento no traiga ``/`` ni
    ``..``: el id viene de un evento, y un id que armara otra ruta escribiría
    fuera del prefijo de su tenant.
    """
    if not isinstance(valor, str):
        msg = f"{nombre} tiene que ser texto: {valor!r}"
        raise TypeError(msg)
    try:
        canonico = uuid.UUID(valor)
    except ValueError as error:
        msg = f"{nombre} no es un uuid: {valor!r}"
        raise ValueError(msg) from error
    if canonico == _UUID_NULO:
        msg = f"{nombre} es el uuid nulo: es un id sin asignar"
        raise ValueError(msg)
    return str(canonico)


def prefijo_de_tenant(tenant_id: str) -> str:
    """``tenants/{tenantId}/``, con la barra final.

    La barra no es decorativa: sin ella, el prefijo de un tenant sería también el
    comienzo de cualquier otro id que empezara igual. Con uuid de largo fijo no
    pasa, pero la comparación de M.8.1 no debería depender de eso.
    """
    return f"{PREFIJO_TENANTS}/{_uuid_canonico(tenant_id, 'tenant_id')}/"


def claves_cog_mensual(
    *, tenant_id: str, rancho_id: str, receta: Receta, indice: str, mes: Mes
) -> ClavesDeCapa:
    """Las claves del COG de un rancho, un índice y un mes.

    Los argumentos van por nombre: ``tenant_id`` y ``rancho_id`` son los dos
    texto, e intercambiarlos daría una key válida en el lugar equivocado.

    Raises:
        TypeError: si un id no es texto.
        ValueError: si un id no es un uuid o es el nulo, o si la receta no
            calcula ``indice``.
    """
    if indice not in receta.indices:
        msg = f"la receta {receta.version} no calcula {indice!r}: {receta.indices}"
        raise ValueError(msg)
    rancho = _uuid_canonico(rancho_id, "rancho_id")
    return ClavesDeCapa(
        storage_key=(
            f"{prefijo_de_tenant(tenant_id)}ranchos/{rancho}/"
            f"{receta.version}/{indice}/{mes}.tif"
        ),
        # `mensual` la separa de las natural_key de la capa vieja
        # (`rancho_{indice}_{id}_{AAAA-MM-DD}`), que siguen en `layers`.
        natural_key=f"rancho_mensual_{indice}_{rancho}_{mes}",
    )
