"""El alta de un rancho sobre el pipeline mensual (M.4.5).

``terra/rancho.created`` trae un rancho nuevo, y este handler le deja un COG de
NDVI por mes cerrado en el bucket, con su fila en ``layers``
(``ARQUITECTURA_PIPELINE.md`` §2 y §6). Con la receta v1 son hasta 24 mapas.

Los steps son los del alta de una parcela (``handlers/altas.py``): ``plan`` y un
``mes-AAAA-MM`` por mes. Cada mes, en **un solo step** (E.3, ``DECISIONS #26``):

1. las estadísticas del rancho en el mes, con la misma reducción que la parcela
   (una llamada a GEE). Van a ``layers.estadisticas`` junto con la cobertura;
2. **si la cobertura es 0, no hay mapa**: no se descarga ni se sube nada, y queda
   un aviso en la bitácora (decisión del usuario, 2026-09-18). Con cualquier
   cobertura mayor, aunque quede bajo el mínimo de la receta, el mapa va: muestra
   lo que se vio;
3. la imagen del mes (``productos.mapa_del_mes``), con lo enmascarado relleno con
   :data:`NODATA_COG`, la URL por el borde (``ejecucion.url_de_descarga``), la
   descarga, el COG y la subida a la key de ``claves_cog_mensual``;
4. ``insert_layer`` con ``source="mensual"``, la receta y las estadísticas.

**Con la receta v3 el mes suma sus pasadas** (M.9.7e2, ``DECISIONS #77``): además
del compuesto, un COG por cada pasada con algún píxel despejado en el rancho,
bajados en paralelo, con ``source="pasada"`` y ``acquired_ts`` en el instante de
la pasada. Ver :func:`procesar_mes`.

Los temporales no cruzan steps: lo que sale del step son las ``storage_keys``.

Reemplaza al ``process_rancho`` de la capa vieja con el mismo ``fn_id``, y a su
``register_layer``, que solo escuchaba el evento que emitía el viejo.

No importa ``services/inngest_handlers.py``, que M.6.1 borra.
"""

import time
from datetime import UTC, datetime
from typing import Any, Final, NamedTuple

import inngest
from pipeline import ejecucion
from pipeline.claves import ClavesDeCapa, claves_cog_mensual, claves_cog_pasada
from pipeline.ejecucion import reducciones_de, url_de_descarga, ventanas_de
from pipeline.etapas.reduccion import Reduccion
from pipeline.periodos import Mes
from pipeline.productos import (
    bandas_de_producto,
    bandas_del_cog,
    escala_de_producto,
    mapa_multibanda_de,
    productos_del_cog,
)
from pipeline.receta import RECETA_VIGENTE, Receta
from pipeline.ventanas import Ventana, agrupamiento, del_mes
from repositories.db_repository import insert_layer
from services.avance_job import AVISO, paso, reportar
from services.ee.ee_client import init_ee
from services.inngest_client import inngest_client

from handlers.altas import (
    CONCURRENCIA_GEE,
    PROGRESO_MESES,
    PROGRESO_PLAN,
    errores_de_gee,
    meses_del_plan,
    planificar,
    porcentaje,
    requerido,
)
from handlers.cierre import cerrar_por_falla
from handlers.geometria import coords_to_geometry
from handlers.raster import NODATA_ENTERO, en_paralelo, parametros_del_mapa, subir_cog
from handlers.seguimiento import RETRIES, con_seguimiento
from handlers.utilidades import entre, ms_desde


# **Un mapa por índice de la receta** (decisión del usuario, 2026-09-20). Hasta el
# 2026-09-20 era sólo NDVI, que fue con lo que se probó el pipeline.
#
# No es un parámetro de la receta y por eso **no rompe el congelamiento de
# `s2-mensual-v1`**: no cambia ningún número, cambia qué se dibuja.
def indices_del_mapa(receta: Receta) -> tuple[str, ...]:
    """Los índices que se suben como mapa: todos los de la receta."""
    return receta.indices

# `NODATA_COG`, `_parametros` y `_subir_cog` se mudaron a `handlers/raster.py` en
# M.6.2b: el mapa a demanda hace lo mismo, y el nodata y la escala son un
# contrato con el tileserver que no puede vivir en dos lados.

# `layers.source` de cada familia de COG del rancho. El panel distingue por esto
# el compuesto del mes de una pasada (M.9.7f).
FUENTE_MENSUAL: Final = "mensual"
FUENTE_PASADA: Final = "pasada"


def _estadisticas_de_la_capa(reduccion: Reduccion, producto: str) -> dict[str, Any]:
    """Los números del mapa: los de su índice, con cobertura y observaciones (D-2).

    El color real no tiene estadísticas de índice: lleva sólo la cobertura y las
    observaciones, que son de la ventana y no del producto.
    """
    return {
        **reduccion.estadisticas.get(producto, {}),
        "cobertura": reduccion.cobertura,
        "observaciones": reduccion.observaciones,
    }


class _Cog(NamedTuple):
    """Un COG del mes para subir: su ventana, su familia y sus números."""

    ventana: Ventana
    fuente: str
    reduccion: Reduccion


class _Subido(NamedTuple):
    """Un COG ya subido: lo que hace falta para escribir sus filas."""

    cog: _Cog
    archivo: str
    bbox: list[float]
    megas: float


def _ventanas_del_raster(
    roi: object, mes: Mes, receta: Receta
) -> tuple[Ventana, tuple[Ventana, ...]]:
    """El compuesto del mes y, si la receta lo pide, sus pasadas (M.9.7e2).

    **El compuesto mensual va siempre** (d39, ``DECISIONS #72``): para informes y
    comparaciones. Las pasadas, sólo si ``agrupamiento_raster`` es por pasada, y
    eso cuesta una llamada —las fechas—; con ``entero`` no se le pregunta nada a
    GEE, como antes.
    """
    mensual = del_mes(mes)
    if not agrupamiento(receta.agrupamiento_raster).necesita_fechas:
        return mensual, ()
    return mensual, ventanas_de(roi, mensual, receta, para_raster=True)


def procesar_mes(  # noqa: PLR0913 - lo que necesita un mes, por nombre
    *,
    rancho_id: str,
    tenant_id: str,
    coordenadas: object,
    mes: Mes,
    posicion: int,
    total: int,
    receta: Receta,
) -> dict[str, Any]:
    """El step ``mes-AAAA-MM``: el mapa del mes y, con v3, el de cada pasada.

    Es idempotente: la key y la ``natural_key`` salen del rancho, el producto, la
    receta y la ventana, así que repetir el step sobrescribe los mismos objetos y
    las mismas filas.

    **Con v3** (``agrupamiento_raster: por_pasada``, M.9.7e2, ``DECISIONS #77``):

    1. **una llamada con los números del mes y de todas sus pasadas**
       (``reducciones_de``, M.9.7d): la cobertura de cada pasada sobre el rancho
       sale de ahí, sin otro pedido;
    2. se sube **toda pasada con al menos un píxel despejado** (d37), y el
       compuesto del mes si tiene alguno (``#51``);
    3. **en paralelo** (``raster.en_paralelo``): la URL, la descarga, el COG y la
       subida de cada uno. En serie, el Cauca se iba a ~46 s por mes;
    4. **las filas, al final**, cuando subió todo. Si algo falla antes, el step
       se reintenta sin haber escrito filas que apunten a archivos que no están.

    **Con v2 hace lo de antes**: una sola ventana, el mes, y la misma bitácora.

    Raises:
        inngest.NonRetriableError: si un id del evento no es un uuid, o si GEE
            dice que el pedido no se puede hacer así (sin memoria, demasiados
            píxeles, un rancho que no entra en una descarga).
    """
    t0 = time.monotonic()
    productos = productos_del_cog(receta)
    bandas = bandas_del_cog(receta)
    # Las claves del mes primero, las de todos los productos: validan los uuid
    # antes de pedirle nada a GEE. Un id que no es un uuid no se arregla
    # reintentando. Las de las pasadas usan los mismos ids.
    try:
        for producto in productos:
            claves_cog_mensual(
                tenant_id=tenant_id, rancho_id=rancho_id, receta=receta,
                indice=producto, ventana=del_mes(mes),
            )
    except (TypeError, ValueError) as error:
        msg = f"el evento no trae ids válidos para la key del mapa: {error}"
        raise inngest.NonRetriableError(msg) from error
    init_ee()
    roi = coords_to_geometry(coordenadas)
    progreso = entre(PROGRESO_PLAN, PROGRESO_MESES, posicion, total)

    def claves_de(cog: _Cog, producto: str) -> ClavesDeCapa:
        if cog.fuente == FUENTE_PASADA:
            return claves_cog_pasada(
                tenant_id=tenant_id, rancho_id=rancho_id, receta=receta,
                producto=producto, ventana=cog.ventana,
            )
        return claves_cog_mensual(
            tenant_id=tenant_id, rancho_id=rancho_id, receta=receta,
            indice=producto, ventana=cog.ventana,
        )

    def subir(cog: _Cog) -> _Subido:
        # **Una** descarga con todas las bandas (M.9.7b). La URL es un pedido a
        # GEE: se cuenta y se traduce igual, porque el hilo corre en una copia
        # del contexto del step.
        url = url_de_descarga(
            mapa_multibanda_de(roi, cog.ventana, receta, bandas).unmask(
                NODATA_ENTERO, sameFootprint=False
            ),
            parametros_del_mapa(roi, receta),
        )
        archivo = claves_de(cog, productos[0]).storage_key
        bbox, megas = subir_cog(url, archivo, nodata=NODATA_ENTERO)
        return _Subido(cog, archivo, bbox, megas)

    with errores_de_gee(mes, "este rancho"), ejecucion.contando() as conteo:
        mensual, pasadas = _ventanas_del_raster(roi, mes, receta)
        # Una llamada: el compuesto y todas las pasadas (M.9.7d).
        reduccion_del_mes, *de_pasadas = reducciones_de(
            roi, (mensual, *pasadas), receta
        )
        # d37: toda pasada con al menos un píxel despejado en el rancho.
        con_pixeles = [
            _Cog(ventana, FUENTE_PASADA, reduccion)
            for ventana, reduccion in zip(pasadas, de_pasadas, strict=True)
            if reduccion.cobertura > 0
        ]
        # `#51`: un mes sin un píxel limpio no tiene COG. Con cobertura 0 en el
        # compuesto, tampoco la tiene ninguna pasada.
        a_subir = (
            [_Cog(mensual, FUENTE_MENSUAL, reduccion_del_mes)]
            if reduccion_del_mes.cobertura > 0
            else []
        ) + con_pixeles
        if not a_subir:
            reportar(
                f"mes-{mes}",
                f"Mes {posicion} de {total} ({mes}): sin un píxel limpio, sin mapa",
                progreso=progreso,
                nivel=AVISO,
                mes=str(mes),
                cobertura=0.0,
                pasadas=len(pasadas),
                llamadas=conteo.llamadas,
                ms=ms_desde(t0),
            )
            return {"mes": str(mes), "cobertura": 0.0, "storage_keys": [], "mapas": 0}
        subidos = en_paralelo(subir, a_subir)

    # Las filas al final, cuando subió todo. Cada producto es su fila y dice qué
    # bandas del archivo es: un índice, una; el color real, tres.
    for subido in subidos:
        for producto in productos:
            insert_layer(
                natural_key=claves_de(subido.cog, producto).natural_key,
                product=producto,
                storage_key=subido.archivo,
                # El instante de la pasada, o el día 1 del mes: el mapa del panel
                # filtra por esto desde M.9.7c.
                acquired_ts=subido.cog.ventana.inicio,
                ingested_ts=datetime.now(UTC),
                tenant_id=tenant_id,
                rancho_id=rancho_id,
                bbox=subido.bbox,
                source=subido.cog.fuente,
                receta=receta.version,
                estadisticas=_estadisticas_de_la_capa(subido.cog.reduccion, producto),
                bandas=bandas_de_producto(receta, producto),
                escala=escala_de_producto(producto),
            )

    mapas = len(subidos) * len(productos)
    reportar(
        f"mes-{mes}",
        _mensaje_del_mes(
            posicion=posicion, total=total, mes=mes, productos=productos,
            cobertura=reduccion_del_mes.cobertura, pasadas=len(pasadas),
            subidas=len(con_pixeles),
        ),
        progreso=progreso,
        mes=str(mes),
        cobertura=reduccion_del_mes.cobertura,
        megas=round(sum(s.megas for s in subidos), 2),
        mapas=mapas,
        pasadas=len(pasadas),
        pasadas_con_mapa=len(con_pixeles),
        llamadas=conteo.llamadas,
        ms=ms_desde(t0),
    )
    return {
        "mes": str(mes),
        "cobertura": reduccion_del_mes.cobertura,
        "storage_keys": [s.archivo for s in subidos],
        # Las capas, no los archivos: con el COG multibanda son varias en uno.
        "mapas": mapas,
    }


def _mensaje_del_mes(  # noqa: PLR0913 - lo que dice la línea, por nombre
    *,
    posicion: int,
    total: int,
    mes: Mes,
    productos: tuple[str, ...],
    cobertura: float,
    pasadas: int,
    subidas: int,
) -> str:
    """La línea de la bitácora del mes.

    Con v2 es la de siempre. Con v3 suma las pasadas: cuántas hubo y de cuántas
    quedó mapa, que son las que tienen algún píxel despejado en el rancho.
    """
    linea = (
        f"Mes {posicion} de {total} ({mes}): {len(productos)} mapas en un archivo "
        f"({', '.join(p.upper() for p in productos)}), "
        f"cobertura {porcentaje(cobertura)}"
    )
    if not pasadas:
        return linea
    return f"{linea}; {subidas} de {pasadas} pasadas con mapa"


@inngest_client.create_function(
    fn_id="process-rancho",
    trigger=inngest.TriggerEvent(event="terra/rancho.created"),
    retries=RETRIES,
    # M.5.3: la misma cola virtual que el cierre de mes, para no pasarse de la
    # cuota de GEE ni del plan de Inngest.
    concurrency=CONCURRENCIA_GEE,
    # M.4.7: si Inngest da la corrida por fallida sin que el handler lo vea (el
    # contenedor murió, el request se cortó), el job no queda en `running`.
    on_failure=cerrar_por_falla,
)
@con_seguimiento
def process_rancho(
    ctx: inngest.Context,  # noqa: ARG001 - la firma que pide con_seguimiento
    step: inngest.StepSync,
    payload: dict,
) -> dict[str, Any]:
    """Los mapas mensuales de un rancho nuevo, un step por mes."""
    rancho_id = requerido(payload, "ranchoId")
    tenant_id = requerido(payload, "tenantId")
    coordenadas = requerido(payload, "coordinates")
    receta = RECETA_VIGENTE

    plan = paso(step, "plan", lambda: planificar(receta))
    meses = meses_del_plan(plan)

    resultados = [
        paso(
            step,
            f"mes-{mes}",
            # Los valores del bucle entran como defaults: una clausura común
            # vería los de la última vuelta.
            lambda mes=mes, posicion=posicion: procesar_mes(
                rancho_id=rancho_id,
                tenant_id=tenant_id,
                coordenadas=coordenadas,
                mes=mes,
                posicion=posicion,
                total=len(meses),
                receta=receta,
            ),
        )
        for posicion, mes in enumerate(meses, start=1)
    ]

    return {
        "status": "success",
        "receta": plan["receta"],
        "meses": len(resultados),
        "mapas": sum(r["mapas"] for r in resultados),
    }
