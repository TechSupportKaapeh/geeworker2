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

import pytest

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


def test_la_capa_vieja_de_gee_ya_no_existe():
    """M.6.2b: se fueron los cuatro modulos, y `ee_client` quedo en las credenciales.

    Este test reemplaza a `test_lo_que_sigue_vivo_es_de_m62b`, que cuidaba el
    limite de M.6.2 y se puso rojo al hacerse M.6.2b — que era lo que su docstring
    decia que iba a pasar. **Ya no queda nada de la capa vieja**, asi que no hay
    otro limite que escribir.
    """
    import importlib

    for modulo in ("services.ee_service", "services.export_service",
                   "services.ee.ee_indices", "utils_pkg.visualization",
                   "services.inngest_handlers"):
        with pytest.raises(ModuleNotFoundError):
            importlib.import_module(modulo)


def test_ee_client_es_solo_las_credenciales():
    """Lo unico que quedo del modulo es `init_ee`.

    Lo que se fue y no puede volver, porque el pipeline lo hace distinto a
    proposito (`ARQUITECTURA` §8):

    - `get_sentinel2_collection` no dividia las bandas por 10.000, que es por lo
      que su EVI y su SAVI estaban mal;
    - `apply_scsc` no era SCS+C: le faltaba `cos(pendiente)` y usaba un `C` fijo;
    - `check_roi_coverage` descartaba pasadas con menos del 50 % del ROI limpio,
      que en un compuesto mensual **agrega** nulos en vez de sacarlos.
    """
    from services.ee import ee_client

    assert hasattr(ee_client, "init_ee")
    for nombre in ("get_sentinel2_collection", "apply_scsc", "check_roi_coverage",
                   "mask_s2cloudless_and_shadows", "add_cloud_probability"):
        assert not hasattr(ee_client, nombre), nombre


def test_el_mapa_a_demanda_corre_sobre_el_pipeline():
    """El ultimo handler que quedaba de la capa vieja, rehecho (M.6.2b)."""
    from handlers import mapa, registro

    assert mapa.generate_heatmap_on_demand in registro.all_functions
    # El `fn_id` es el mismo a proposito: Inngest identifica las funciones por
    # ahi, y cambiarlo dejaria la vieja archivada y los eventos en vuelo sin
    # quien los atienda.
    assert mapa.generate_heatmap_on_demand.id == "geeworker-generate-heatmap-on-demand"


def test_el_worker_registra_siete_funciones():
    """El numero es el que tiene que ver Inngest. Eran 11 antes de M.6.2."""
    from handlers import registro

    assert len(registro.all_functions) == 7


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
    from handlers import registro as inngest_handlers

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


def test_no_queda_ningun_except_exception_sin_justificar():
    """M.6.4: `ruff --select BLE` limpio en todo el repo, no sólo en `pipeline/`.

    **Qué cuenta como "sin justificar".** Ruff no marca un `except Exception` que
    relanza: ahí atrapar ancho es el patrón correcto —anotar el fallo y dejar que
    suba— y angostarlo sería peor, porque dejaría pasar sin registrar lo que no
    estuviera en la lista. Marca los que **absorben**, que son los que tapan
    bugs.

    De los 35 que quedaban al empezar M.6.4, ruff marcaba 8: tres en
    `utils_pkg/cache.py` e `io.py`, que no tenían un solo llamador y se borraron,
    y cinco en `db_repository.py`, que son deliberados y ahora lo dicen con un
    `noqa` y su motivo. El estado de un job es telemetría: perder una
    actualización no puede abortar un procesamiento que ya corrió, y angostarlos
    a `psycopg2.Error` haría que un `TypeError` serializando el detalle tumbara
    la corrida.

    El test existe porque **el CI sólo corre ruff sobre `pipeline/`** (M.0.1, y
    el CI no se toca). Sin esto, el invariante dependería de que alguien se
    acuerde de correr el comando.
    """
    import subprocess

    resultado = subprocess.run(
        [sys.executable, "-m", "ruff", "check", ".", "--select", "BLE",
         "--output-format", "concise"],
        cwd=str(RAIZ), capture_output=True, text=True, timeout=120, check=False,
    )
    assert resultado.returncode == 0, resultado.stdout


def test_utils_pkg_no_exporta_nada():
    """Lo que tenía se fue con los módulos que lo usaban (M.6.2b y M.6.4)."""
    import utils_pkg

    assert getattr(utils_pkg, "__all__", []) == []
    for modulo in ("utils_pkg.cache", "utils_pkg.io", "utils_pkg.visualization"):
        with pytest.raises(ModuleNotFoundError):
            __import__(modulo)
