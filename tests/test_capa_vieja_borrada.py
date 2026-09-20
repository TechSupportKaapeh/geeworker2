"""M.6.1: lo que se borro de la capa vieja no vuelve.

Un borrado no deja rastro en la suite: nada se pone rojo si alguien reintroduce
la funcion, porque nadie la llamaba — que era justamente el motivo para
borrarla. Estos tests son el rastro. Es el mismo patron que
`test_process_kml_ya_no_existe`, que cuida el borrado de E.2.

Lo que cuidan **no** es el nombre por el nombre: cada uno fija una decision de
`ARQUITECTURA_PIPELINE` §9 que costo averiguar, y que se pierde en cuanto el
codigo vuelve.

Lo que M.6.1 **no** borro, y por que, esta al final: `test_lo_que_sigue_vivo_es_de_m62`.
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


def test_lo_que_sigue_vivo_es_de_m62():
    """El limite de M.6.1, escrito donde se rompe si alguien lo cruza.

    §9 da por borradas estas funciones, pero **hoy tienen llamador**: los
    handlers a demanda (`compute_timeseries`, `query_available_dates`,
    `export_data`, `compute_parcela_stats` y `generate_heatmap_on_demand`). Se
    van con ellos en M.6.2, que espera la confirmacion 👥 de si el front de los
    tenants los usa.

    Si este test se pone rojo porque las funciones ya no estan, es que M.6.2 se
    hizo: se borra el test, no se revive el codigo.
    """
    from services.ee import ee_client, ee_indices

    for nombre in ("apply_scsc", "check_roi_coverage", "get_sentinel2_collection",
                   "get_sentinel2_time_series", "una_por_dia", "get_sentinel2_dates"):
        assert hasattr(ee_client, nombre), f"{nombre} se fue antes de tiempo"
    assert hasattr(ee_indices, "compute_sentinel2_index")
