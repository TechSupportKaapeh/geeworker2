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

import re
import uuid
from typing import NamedTuple

from pipeline.receta import Receta
from pipeline.ventanas import Ventana

PREFIJO_TENANTS = "tenants"
# `Guid.Empty` en Geocore: un id sin asignar, nunca uno de verdad.
_UUID_NULO = uuid.UUID(int=0)

# Lo que se acepta como último segmento de la key. Hasta M.9.0b era siempre un
# `AAAA-MM` armado acá; desde que lo trae la etiqueta de la ventana, hay que
# mirarlo: una etiqueta con `/` escribiría en otra carpeta —el mismo riesgo que
# `_uuid_canonico` ya cubre para los ids— y una con `:` o espacios daría una key
# que S3 sirve pero que es incómoda en una URL de tile. El ráster sigue siendo
# mensual (`DECISIONS #63`), así que hoy acá sólo llegan etiquetas `AAAA-MM`.
_ETIQUETA = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")


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


def _claves_mensuales(  # noqa: PLR0913 - todo por nombre, y son datos distintos
    *,
    tenant_id: str,
    carpeta: str,
    entidad: str,
    entidad_id: str,
    familia: str,
    receta: Receta,
    indice: str,
    ventana: Ventana,
) -> ClavesDeCapa:
    """El armador único de las dos claves. Ver los tres envoltorios de abajo."""
    if indice not in receta.indices:
        msg = f"la receta {receta.version} no calcula {indice!r}: {receta.indices}"
        raise ValueError(msg)
    if _ETIQUETA.fullmatch(ventana.etiqueta) is None:
        msg = f"la etiqueta de la ventana no sirve para una key: {ventana.etiqueta!r}"
        raise ValueError(msg)
    ident = _uuid_canonico(entidad_id, f"{entidad}_id")
    return ClavesDeCapa(
        storage_key=(
            f"{prefijo_de_tenant(tenant_id)}{carpeta}/{ident}/"
            f"{receta.version}/{indice}/{ventana.etiqueta}.tif"
        ),
        # `familia` separa capas que comparten entidad, índice y ventana. Se llama
        # así desde M.9.0b: antes era `etiqueta`, y ahora ese nombre es el de la
        # ventana, que también entra en la natural_key.
        # `mensual` nació para no chocar con la capa vieja
        # (`rancho_{indice}_{id}_{AAAA-MM-DD}`); `ondemand` hace lo mismo con el
        # mapa a pedido, que vive al lado del sistemático pero no es el mismo
        # producto: uno lo produce el alta o el cierre, el otro lo pide alguien.
        natural_key=f"{entidad}_{familia}_{indice}_{ident}_{ventana.etiqueta}",
    )


def claves_cog_mensual(
    *, tenant_id: str, rancho_id: str, receta: Receta, indice: str, ventana: Ventana
) -> ClavesDeCapa:
    """Las claves del COG sistemático de un rancho, un índice y una ventana.

    Los argumentos van por nombre: ``tenant_id`` y ``rancho_id`` son los dos
    texto, e intercambiarlos daría una key válida en el lugar equivocado.

    Raises:
        TypeError: si un id no es texto.
        ValueError: si un id no es un uuid o es el nulo, o si la receta no
            calcula ``indice``.
    """
    return _claves_mensuales(
        tenant_id=tenant_id,
        carpeta="ranchos",
        entidad="rancho",
        entidad_id=rancho_id,
        familia="mensual",
        receta=receta,
        indice=indice,
        ventana=ventana,
    )


def claves_cog_parcela_a_demanda(
    *, tenant_id: str, parcela_id: str, receta: Receta, indice: str, ventana: Ventana
) -> ClavesDeCapa:
    """Las claves del mapa a demanda de una parcela (M.6.2b).

    **Misma forma que el sistemático, un nivel al lado.** Un mapa NDVI de una
    parcela y el ráster NDVI de su rancho son el mismo tipo de objeto; estaban
    separados por *por qué se pidió*, no por *qué son*. Lo que los distingue es
    ``layers.source``, no la carpeta.

    Que esté bajo ``tenants/{t}/`` no es cosmético: hasta M.6.2b el mapa a demanda
    colgaba de ``parcelas/{id}/`` en la raíz del bucket, y **eso es lo que impedía
    cerrar A01** (M.8.1), que compara el prefijo del objeto contra el
    ``tenant_id`` del token de mapa.
    """
    return _claves_mensuales(
        tenant_id=tenant_id,
        carpeta="parcelas",
        entidad="parcela",
        entidad_id=parcela_id,
        familia="ondemand",
        receta=receta,
        indice=indice,
        ventana=ventana,
    )


def claves_cog_adhoc(
    *, tenant_id: str, job_id: str, receta: Receta, indice: str, ventana: Ventana
) -> ClavesDeCapa:
    """Las claves del mapa de un polígono libre, que no es de ninguna entidad.

    ``heatmap-on-the-fly`` manda un polígono suelto y ``parcelaId`` en el uuid
    nulo. **La identidad es el job**, porque no hay otra: dos pedidos con el
    mismo polígono son dos objetos distintos, y eso es correcto — sin una entidad
    detrás no hay forma de saber que son el mismo recorte, y reutilizar por
    coincidencia de coordenadas sería adivinar.

    La consecuencia práctica, y hay que tenerla presente: **estos objetos no se
    reutilizan ni se sobrescriben**, así que se acumulan. Su retención es parte de
    la decisión de retención que quedó abierta (``PREGUNTAS_ABIERTAS`` C-5).
    """
    return _claves_mensuales(
        tenant_id=tenant_id,
        carpeta="adhoc",
        entidad="adhoc",
        entidad_id=job_id,
        familia="ondemand",
        receta=receta,
        indice=indice,
        ventana=ventana,
    )
