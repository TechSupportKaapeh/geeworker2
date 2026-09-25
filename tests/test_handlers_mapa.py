"""M.6.2b: el mapa a demanda sobre el pipeline (`handlers/mapa.py`).

Mismo trato que los tests del cierre y de las altas: sin GEE ni base, con el
borde de verdad (`ejecucion.traer`, `reduccion.leer`) y falsos el `init_ee`, el
ROI, la subida del COG y el almacenamiento.

Lo que se prueba acá es lo **propio** del mapa a demanda, que es lo que lo
distingue del sistemático:

- que el período sea un mes y venga del evento;
- que la key caiga bajo `tenants/`, que es lo que hace posible M.8.1;
- que un polígono libre se identifique por su job y no por una parcela;
- que un mes sin un píxel limpio no suba nada;
- que el objeto se reutilice si ya está, pero la fila se escriba igual;
- que la fila lleve `source="on_demand"`, la receta y las estadísticas.

Que el mes se calcule bien ya lo prueban `test_handlers_parcela` y
`test_handlers_rancho`: acá se reusa el mismo borde.
"""
import sys
from pathlib import Path

import inngest
import pytest

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))

from handlers import mapa, raster
from pipeline import ejecucion
from pipeline.estadisticas import claves_de_salida
from pipeline.receta import RECETA_VIGENTE
from repositories import db_repository
from services import avance_job

PARCELA = "0b6f0a52-8c1e-4d4b-9f39-1d6f7a1e2c3d"
TENANT = "7d0c1b8a-3e2f-4a5b-8c6d-9e0f1a2b3c4d"
JOB = "5c4b3a29-1e0f-4d3c-8b7a-6f5e4d3c2b1a"
UUID_NULO = "00000000-0000-0000-0000-000000000000"
COORDS = [{"lat": 20.5, "lng": -101.2}]

DE_UNA_PARCELA = {"JobId": JOB, "ParcelaId": PARCELA, "TenantId": TENANT,
                  "Periodo": "2026-08", "Indice": "ndvi", "Coordinates": COORDS}
DE_UN_POLIGONO = {"JobId": JOB, "ParcelaId": UUID_NULO, "TenantId": TENANT,
                  "Periodo": "2026-08", "Indice": "ndvi", "Coordinates": COORDS}


class _Interrupcion(BaseException):
    """Lo que hace el SDK con el error de un step: un BaseException."""


class _Step:
    def __init__(self):
        self.ejecutados = []

    def run(self, nombre, funcion, *args):
        self.ejecutados.append(nombre)
        try:
            return funcion(*args)
        except (inngest.NonRetriableError, inngest.RetryAfterError):
            raise
        except Exception as e:
            raise _Interrupcion(e) from e


class _Ctx:
    def __init__(self, data, attempt=0):
        self.event = type("Evento", (), {"data": data})()
        self.attempt = attempt


class _Expresion:
    def __init__(self, contestar):
        self._contestar = contestar

    def getInfo(self):  # el nombre que usa ee
        return self._contestar()


def _respuesta_de_gee(cobertura=0.9, mediana=0.5):
    claves = claves_de_salida(RECETA_VIGENTE.indices, RECETA_VIGENTE.estadisticas)
    respuesta = {
        clave: (mediana if estadistica == "mediana" else 0.1)
        for (_, estadistica), clave in claves.items()
    }
    respuesta.update(cobertura=cobertura, observaciones=4.0)
    return respuesta


@pytest.fixture
def mundo(monkeypatch):
    """GEE, la base, el almacenamiento y la subida del COG, falsos."""
    estado = {"capas": [], "bitacora": [], "jobs": [], "subidas": [], "pedidos": [],
              "existe": False, "gee": _respuesta_de_gee}

    def _registrar(job_id, attempt, stage, level, message, detail=None, progress=None):
        estado["bitacora"].append({"etapa": stage, "nivel": level, "mensaje": message,
                                   "detalle": detail or {}, "progreso": progress})

    def _estadisticas_de(roi, ventana, receta):
        # `ventana.etiqueta` de una ventana mensual es el mismo `AAAA-MM` que antes
        # era `str(mes)`.
        estado["pedidos"].append(ventana.etiqueta)
        return _Expresion(estado["gee"])

    monkeypatch.setattr(ejecucion, "estadisticas_de", _estadisticas_de)
    monkeypatch.setattr(avance_job, "registrar_evento_job", _registrar)
    monkeypatch.setattr(db_repository, "update_processing_job",
                        lambda job_id, status, **kw: estado["jobs"].append((status, kw)))

    monkeypatch.setattr(mapa, "init_ee", lambda: None)
    monkeypatch.setattr(mapa, "coords_to_geometry", lambda c: "roi")
    monkeypatch.setattr(
        mapa, "mapa_de",
        lambda *a, **k: type("Img", (), {"unmask": lambda self, *a, **k: self})(),
    )
    monkeypatch.setattr(mapa, "url_de_descarga", lambda *a, **k: "https://gee/descarga.tif")
    monkeypatch.setattr(
        mapa, "subir_cog",
        lambda url, key: estado["subidas"].append(key) or ([0, 0, 1, 1], 1.5),
    )
    monkeypatch.setattr(mapa, "insert_layer", lambda **kw: estado["capas"].append(kw))
    monkeypatch.setattr(
        mapa, "get_storage_service",
        lambda: type("S", (), {"object_exists": lambda self, k: estado["existe"]})(),
    )
    return estado


def _correr(step, payload=DE_UNA_PARCELA, attempt=0):
    return mapa.generate_heatmap_on_demand._handler(_Ctx(payload, attempt), step)


# --- 1. La key, que es lo que desbloquea M.8.1 ----------------------------


def test_la_key_cuelga_del_prefijo_del_tenant(mundo):
    """Hasta M.6.2b colgaba de `parcelas/{id}/`, en la raíz del bucket.

    Ese era el motivo real de la tarea: el token de mapa de M.8.1 compara el
    prefijo del objeto contra el `tenant_id`, y un objeto fuera de `tenants/` no
    se puede autorizar.
    """
    resultado = _correr(_Step())

    assert resultado["storage_key"] == (
        f"tenants/{TENANT}/parcelas/{PARCELA}/"
        f"{RECETA_VIGENTE.version}/ndvi/2026-08.tif"
    )
    assert mundo["subidas"] == [resultado["storage_key"]]


def test_un_poligono_libre_se_identifica_por_su_job(mundo):
    """`heatmap-on-the-fly` manda el uuid nulo en `parcelaId`: no hay entidad."""
    resultado = _correr(_Step(), DE_UN_POLIGONO)

    assert resultado["storage_key"] == (
        f"tenants/{TENANT}/adhoc/{JOB}/{RECETA_VIGENTE.version}/ndvi/2026-08.tif"
    )
    # Sin parcela detrás, la fila tampoco puede apuntar a una.
    assert mundo["capas"][0]["parcela_id"] is None


def test_el_mapa_de_una_parcela_no_pisa_el_del_rancho(mundo):
    """Comparten forma de key y podrían compartir mes e índice, pero no fila."""
    from pipeline.claves import claves_cog_mensual
    from pipeline.periodos import Mes
    from pipeline.ventanas import del_mes

    _correr(_Step())
    del_rancho = claves_cog_mensual(
        tenant_id=TENANT, rancho_id=PARCELA, receta=RECETA_VIGENTE,
        indice="ndvi", ventana=del_mes(Mes(2026, 8)),
    )
    assert mundo["capas"][0]["natural_key"] != del_rancho.natural_key
    assert mundo["capas"][0]["storage_key"] != del_rancho.storage_key


# --- 2. La fila de `layers` -----------------------------------------------


def test_la_fila_lleva_receta_y_estadisticas(mundo):
    """Antes iban en nulo, y el panel no podía fijar la escala sin abrir el ráster (D-2)."""
    _correr(_Step())

    capa = mundo["capas"][0]
    assert capa["source"] == "on_demand"
    assert capa["receta"] == RECETA_VIGENTE.version
    assert capa["product"] == "ndvi"
    assert capa["estadisticas"]["cobertura"] == 0.9
    assert capa["estadisticas"]["observaciones"] == 4.0
    assert capa["estadisticas"]["mediana"] == 0.5
    assert capa["tenant_id"] == TENANT
    assert capa["parcela_id"] == PARCELA


def test_las_estadisticas_son_las_del_indice_pedido(mundo):
    """Si copiara las de NDVI, la ficha de un mapa de NDMI mentiría."""
    claves = claves_de_salida(RECETA_VIGENTE.indices, RECETA_VIGENTE.estadisticas)
    respuesta = {clave: 0.1 for clave in claves.values()}
    respuesta[claves[("ndmi", "mediana")]] = -0.33
    respuesta[claves[("ndvi", "mediana")]] = 0.77
    respuesta.update(cobertura=0.8, observaciones=2.0)
    mundo["gee"] = lambda: respuesta

    _correr(_Step(), {**DE_UNA_PARCELA, "Indice": "ndmi"})

    assert mundo["capas"][0]["estadisticas"]["mediana"] == -0.33


# --- 3. Los casos que no suben nada ---------------------------------------


def test_un_mes_sin_un_pixel_limpio_no_tiene_mapa(mundo):
    """`DECISIONS #51`, ahora también acá.

    Antes se subía un ráster entero de ceros, y como el GeoTIFF de GEE no declara
    nodata, se dibujaba como suelo desnudo: un mapa verde de un mes sin datos.
    """
    mundo["gee"] = lambda: _respuesta_de_gee(cobertura=0.0)

    resultado = _correr(_Step())

    assert resultado["storage_key"] is None
    assert mundo["subidas"] == []
    assert mundo["capas"] == []
    assert mundo["bitacora"][-2]["nivel"] == avance_job.AVISO


def test_si_el_objeto_ya_esta_no_se_baja_pero_la_fila_se_escribe(mundo):
    """E.9: la key encodea el compuesto, así que "ya está" es "es el mismo".

    La fila se escribe igual: si el objeto estaba en el bucket sin su fila —o
    apuntando a otro lado— hay que registrarla.
    """
    mundo["existe"] = True

    resultado = _correr(_Step())

    assert mundo["subidas"] == []
    assert resultado["reutilizado"] is True
    assert len(mundo["capas"]) == 1
    # Sin descarga no hay bbox que leer; el resto de la fila va igual.
    assert mundo["capas"][0]["bbox"] is None
    assert mundo["capas"][0]["estadisticas"]["cobertura"] == 0.9


# --- 4. Lo que no se reintenta --------------------------------------------


@pytest.mark.parametrize("periodo", ["2026-8", "2026-13", "agosto", "2026-08-01", ""])
def test_un_periodo_que_no_es_un_mes_no_se_reintenta(mundo, periodo):
    """Cuatro reintentos darían el mismo error y gastarían cuota."""
    with pytest.raises(inngest.NonRetriableError):
        _correr(_Step(), {**DE_UNA_PARCELA, "Periodo": periodo})

    assert mundo["pedidos"] == []
    assert mundo["subidas"] == []


def test_un_indice_que_la_receta_no_calcula_no_se_reintenta(mundo):
    """Y falla **antes** de pedirle nada a GEE, que es lo que cuesta plata."""
    with pytest.raises(inngest.NonRetriableError, match="savi"):
        _correr(_Step(), {**DE_UNA_PARCELA, "Indice": "savi"})

    assert mundo["pedidos"] == []
    assert mundo["capas"] == []


@pytest.mark.parametrize("campo", ["TenantId", "Periodo", "Indice", "Coordinates"])
def test_un_evento_incompleto_falla_sin_reintentos(mundo, campo):
    payload = {k: v for k, v in DE_UNA_PARCELA.items() if k != campo}
    with pytest.raises(inngest.NonRetriableError):
        _correr(_Step(), payload)


def test_un_tenant_que_no_es_un_uuid_falla_sin_pedirle_nada_a_gee(mundo):
    """Un id que no es un uuid no se arregla reintentando, y escribiría fuera del prefijo."""
    with pytest.raises(inngest.NonRetriableError):
        _correr(_Step(), {**DE_UNA_PARCELA, "TenantId": "../otro-tenant"})

    assert mundo["pedidos"] == []
    assert mundo["subidas"] == []


# --- 5. El registro -------------------------------------------------------


def test_es_un_solo_step(mundo):
    """Con la espera de Inngest Cloud entre steps (`#55`), partirlo costaría minutos.

    Los `mark-job-*` son del wrapper de estado, no del cálculo: lo que se afirma es
    que el trabajo entero entra en **un** step.
    """
    step = _Step()
    _correr(step)
    del_calculo = [n for n in step.ejecutados if not n.startswith("mark-job-")]
    assert del_calculo == ["mapa"]


def test_comparte_la_cola_de_concurrencia_con_las_altas():
    """También le pide a GEE, y la cuota es de la cuenta entera (M.5.3)."""
    from handlers.altas import CONCURRENCIA_GEE

    assert mapa.generate_heatmap_on_demand._opts.concurrency == CONCURRENCIA_GEE


def test_el_job_se_cierra_si_la_corrida_muere():
    """M.4.7: sin `on_failure`, un job quedaría en `running` para siempre."""
    assert mapa.generate_heatmap_on_demand._opts.on_failure is not None


def test_el_nodata_es_el_mismo_que_el_del_mapa_del_rancho():
    """Son un contrato con el tileserver: si se separan, uno de los dos miente."""
    assert mapa.NODATA_COG is raster.NODATA_COG
