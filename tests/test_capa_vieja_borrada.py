"""M.6.1 y M.6.2: lo que se borro de la capa vieja no vuelve.

Un borrado no deja rastro en la suite: nada se pone rojo si alguien reintroduce
la funcion, porque nadie la llamaba — que era justamente el motivo para
borrarla. Estos tests son el rastro. Es el mismo patron que
`test_process_kml_ya_no_existe`, que cuida el borrado de E.2.

Lo que cuidan **no** es el nombre por el nombre: cada uno fija una decision de
`ARQUITECTURA_PIPELINE` §9 que costo averiguar, y que se pierde en cuanto el
codigo vuelve.

Desde M.6.2 tambien cuida ese borrado. Lo que **todavia** no se borro, y por
que, esta al final: `test_lo_que_sigue_vivo_es_de_m62b`.
"""
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))


def _modulos_que_nombran_en_codigo(aguja: str) -> list[str]:
    """Modulos que nombran `aguja` **en codigo**, no en un comentario ni en un docstring.

    La distincion es el punto del test. Que un comentario diga por que se borro
    `sentinel2_dates` es lo que se quiere conservar; lo que no puede volver es
    un `CREATE TABLE` o un `INSERT` — y esos viven en literales de cadena
    normales, que si se revisan. Los comentarios `ast` ni los ve, y los
    docstrings se saltean por posicion.
    """
    import ast

    encontrados = []
    for ruta in RAIZ.rglob("*.py"):
        if ".venv" in ruta.parts or "__pycache__" in ruta.parts:
            continue
        if ruta.name == Path(__file__).name:
            continue
        arbol = ast.parse(ruta.read_text(encoding="utf-8"), filename=str(ruta))
        docstrings = {
            id(n.body[0].value)
            for n in ast.walk(arbol)
            if isinstance(n, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
            and n.body and isinstance(n.body[0], ast.Expr)
            and isinstance(n.body[0].value, ast.Constant)
            and isinstance(n.body[0].value.value, str)
        }
        for nodo in ast.walk(arbol):
            # En un literal se busca por subcadena, porque ahi la tabla aparece
            # metida en el medio de una sentencia SQL. En un identificador se
            # busca exacto: `get_sentinel2_dates` **contiene** `sentinel2_dates`
            # y no toca la tabla — consulta GEE.
            if isinstance(nodo, ast.Constant) and isinstance(nodo.value, str):
                acierto = id(nodo) not in docstrings and aguja in nodo.value
            elif isinstance(nodo, ast.Name):
                acierto = nodo.id == aguja
            elif isinstance(nodo, ast.Attribute):
                acierto = nodo.attr == aguja
            else:
                continue
            if acierto:
                encontrados.append(str(ruta.relative_to(RAIZ)))
                break
    return encontrados


def test_no_se_crea_ni_se_escribe_sentinel2_dates():
    """A-4: la tabla no tenia una sola consulta de lectura.

    Lo que importa no es que falten las dos funciones, sino que **nada** emita
    SQL contra esa tabla: mientras el worker la creara al arrancar, el
    `DROP TABLE` de 👥 M.6.1b la veria volver en el proximo deploy con una base
    limpia.
    """
    from repositories import db_repository

    assert not hasattr(db_repository, "init_db")
    assert not hasattr(db_repository, "insert_sentinel2_date")

    culpables = sorted(_modulos_que_nombran_en_codigo("sentinel2_dates"))
    assert culpables == [], f"todavia emiten SQL contra sentinel2_dates: {culpables}"


def test_el_arranque_ya_no_precalienta_la_base():
    """`init_db` era un precalentamiento que no podia reportar nada.

    Atrapaba su propia excepcion y la logueaba, asi que el `try` de `_startup()`
    nunca la veia: con la base caida, el arranque terminaba sin quejarse. Quien
    conecta y **reporta** es `registrar_conexiones()`, que corre igual.
    """
    import app

    assert not hasattr(app, "init_db")
    assert hasattr(app, "registrar_conexiones")


def test_ee_client_no_tiene_las_funciones_sin_llamadores():
    """`composite_embedding` y `maskS2clouds`, y la re-exportacion que ciclaba.

    `maskS2clouds` es la que mas conviene que no vuelva: se lee como "la mascara
    de nubes", y no es la que usa nadie. La de la capa vieja es
    `mask_s2cloudless_and_shadows`; la del pipeline, `pipeline/etapas/nubes.py`.
    """
    from services.ee import ee_client

    assert not hasattr(ee_client, "composite_embedding")
    assert not hasattr(ee_client, "maskS2clouds")
    # La re-exportacion existia solo para `services/ee/__init__.py` y creaba un
    # ciclo de imports con `ee_indices`.
    assert not hasattr(ee_client, "compute_sentinel2_index")


def test_el_paquete_services_ee_no_re_exporta():
    """Importar el paquete no arrastra `ee_client`, `ee` ni `ee_indices`."""
    import services.ee

    assert getattr(services.ee, "__all__", []) == []


def test_config_no_declara_una_lista_de_indices_que_nadie_consulta():
    """`SUPPORTED_INDICES` se leia como un contrato y no lo era.

    Ninguna funcion la consultaba: un indice de afuera de la lista se procesaba
    igual, y uno de adentro podia no estar implementado. El registro de
    `pipeline/indices.py` si es la fuente unica y falla con lo que no conoce.
    """
    import config

    assert not hasattr(config, "SUPPORTED_INDICES")


def test_lo_que_se_fue_con_los_handlers_a_demanda():
    """M.6.2: los cuatro handlers, y todo lo que solo ellos sostenian.

    Este test reemplaza a `test_lo_que_sigue_vivo_es_de_m62`, que cuidaba el
    limite de M.6.1 y se puso rojo al hacerse M.6.2 — que era exactamente lo que
    su docstring decia que iba a pasar.
    """
    from services import ee_service, export_service, inngest_handlers
    from services.ee import ee_client
    from utils_pkg import io

    for nombre in ("compute_timeseries", "query_available_dates", "export_data",
                   "compute_parcela_stats"):
        assert not hasattr(inngest_handlers, nombre), nombre

    # La segunda copia de las formulas de indices, con EVI y SAVI equivocados.
    for nombre in ("get_sentinel2_time_series", "una_por_dia", "get_sentinel2_dates"):
        assert not hasattr(ee_client, nombre), nombre

    assert not hasattr(ee_service, "generate_time_series_data")
    assert not hasattr(export_service, "export_time_series")
    assert not hasattr(io, "round_sig")


def test_ya_no_existe_la_escritura_por_pasada():
    """`insert_measurement` e `insert_measurements` escribian `receta IS NULL`.

    Son exactamente las filas que el equipo tuvo que borrar a mano en M.3.5
    (`DELETE FROM geodata.measurements WHERE receta IS NULL`). Mientras las
    funciones existieran, la canilla seguia abierta. Lo mensual se escribe con
    `upsert_mediciones_mensuales`, que siempre pone `receta`.
    """
    from repositories import db_repository

    assert not hasattr(db_repository, "insert_measurement")
    assert not hasattr(db_repository, "insert_measurements")
    assert hasattr(db_repository, "upsert_mediciones_mensuales")


def test_lo_que_sigue_vivo_es_de_m62b():
    """El limite que queda, escrito donde se rompe si alguien lo cruza.

    `generate_heatmap_on_demand` sigue registrado, y con el el resto de la capa
    vieja de GEE: `ARQUITECTURA` §9 dice que **el mapa a demanda pasa al
    pipeline**, y eso es M.6.2b. Hasta entonces `ee_service`, `export_service`,
    `ee_indices`, el constructor de colecciones de `ee_client` e
    `index_band_and_vis` tienen un llamador.

    Si este test se pone rojo porque ya no estan, es que M.6.2b se hizo: se borra
    el test, no se revive el codigo.
    """
    from services import ee_service, export_service, inngest_handlers
    from services.ee import ee_client, ee_indices
    from utils_pkg import visualization

    assert hasattr(inngest_handlers, "generate_heatmap_on_demand")
    assert hasattr(ee_service, "generate_heatmap_tiles")
    assert hasattr(export_service, "export_heatmap")
    assert hasattr(ee_indices, "compute_sentinel2_index")
    assert hasattr(visualization, "index_band_and_vis")
    for nombre in ("get_sentinel2_collection", "apply_scsc", "check_roi_coverage"):
        assert hasattr(ee_client, nombre), f"{nombre} se fue antes de tiempo"


def test_el_worker_registra_solo_lo_que_queda():
    """De 11 funciones a 7. El numero es el que tiene que ver Inngest."""
    from services import inngest_handlers

    assert len(inngest_handlers.all_functions) == 7


def test_cada_evento_que_geocore_publica_tiene_oyente():
    """El contrato entre repos, del lado del worker.

    **Esto salio de un casi-accidente de M.6.2.** Al borrar `compute_timeseries`
    se iba a dejar vivo `POST /api/processing/jobs/timeseries-on-the-fly`, que
    publica `terra/parcela.timeseries.requested`: el handler borrado era su unico
    oyente. Cada llamada habria creado un job que se queda en `pending` para
    siempre — el sintoma que la pestana Procesos marca como "En cola hace mas de
    10 min". Un endpoint que fabrica jobs colgados es peor que uno que no existe.

    No hay compilador que cruce los dos repos, asi que la lista se escribe a mano
    y se verifica contra Geocore cuando cambia. El test cuida los dos lados
    baratos: que no falte un oyente, y que **no sobre** uno — un handler que
    escucha un evento que ya nadie publica es codigo muerto que parece vivo.
    """
    from services import inngest_handlers

    # Lo que Geocore publica hoy: `ParcelaService`, `RanchoService`,
    # `ReconciliadorMensual`, `ReprocesoService` y `ProcessingJobsController`.
    de_geocore = {
        "terra/parcela.created",
        "terra/rancho.created",
        "terra/parcela.mes.requested",
        "terra/rancho.mes.requested",
        "terra/parcela.heatmap.requested",  # se va con M.6.2b
    }
    # Los que no vienen de Geocore y por eso no entran en la comparacion: uno lo
    # emite la propia plataforma (M.4.7) y el otro se dispara a mano (M.4.10).
    propios = {"inngest/function.cancelled", "terra/diagnostico.latencia"}

    escuchados = {
        evento
        for funcion in inngest_handlers.all_functions
        for disparador in funcion.get_config("http://sin-uso").main.triggers
        if (evento := getattr(disparador, "event", None))
    }

    assert escuchados - propios == de_geocore
