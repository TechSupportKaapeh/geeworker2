"""M.5.3: el cierre de mes (`handlers/mes.py`).

Mismo trato que los tests de las altas: sin GEE ni base, con el borde de verdad
(`ejecucion.traer`, `reduccion.leer`, `filas_del_mes`) y falsos el `init_ee`, el
ROI, el upsert y la subida del COG.

Lo que se prueba acá es lo **propio** del cierre: que el mes salga del evento y no
de un reloj, que sea un solo step, que un periodo mal escrito no se reintente, y
que las cuatro funciones que le piden a GEE compartan la cola de concurrencia.
Que el mes se calcule bien ya lo prueban `test_handlers_parcela` y
`test_handlers_rancho`: acá se reusa la misma funcion.
"""
import sys
from datetime import UTC, datetime
from pathlib import Path

import inngest
import pytest

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))

from handlers import altas, mes as handlers_mes, parcela, rancho
from pipeline import ejecucion
from pipeline.estadisticas import claves_de_salida
from pipeline.receta import RECETA_VIGENTE
from repositories import db_repository
from services import avance_job

PARCELA = "0b6f0a52-8c1e-4d4b-9f39-1d6f7a1e2c3d"
RANCHO = "3f2a1b0c-9d8e-4f7a-8b6c-5d4e3f2a1b0c"
TENANT = "7d0c1b8a-3e2f-4a5b-8c6d-9e0f1a2b3c4d"
COORDS = [{"lat": 20.5, "lng": -101.2}]

PAYLOAD_PARCELA = {"JobId": "job-p", "ParcelaId": PARCELA, "TenantId": TENANT,
                   "RanchoId": RANCHO, "Periodo": "2026-08", "Coordinates": COORDS}
PAYLOAD_RANCHO = {"JobId": "job-r", "RanchoId": RANCHO, "TenantId": TENANT,
                  "Periodo": "2026-08", "Coordinates": COORDS}


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
    """GEE, la base, el reloj y la subida del COG, falsos."""
    estado = {"pedidos": [], "escrituras": [], "bitacora": [], "jobs": [], "capas": [],
              "gee": lambda mes: _respuesta_de_gee()}

    def _estadisticas_del_mes(roi, mes, receta):
        estado["pedidos"].append(str(mes))
        return _Expresion(lambda: estado["gee"](str(mes)))

    def _registrar(job_id, attempt, stage, level, message, detail=None, progress=None):
        estado["bitacora"].append({"etapa": stage, "nivel": level, "mensaje": message,
                                   "detalle": detail or {}, "progreso": progress})

    monkeypatch.setattr(ejecucion, "estadisticas_del_mes", _estadisticas_del_mes)
    monkeypatch.setattr(avance_job, "registrar_evento_job", _registrar)
    monkeypatch.setattr(db_repository, "update_processing_job",
                        lambda job_id, status, **kw: estado["jobs"].append((status, kw)))

    # La parcela: el upsert.
    monkeypatch.setattr(parcela, "init_ee", lambda: None)
    monkeypatch.setattr(parcela, "coords_to_geometry", lambda c: "roi")
    monkeypatch.setattr(parcela, "upsert_mediciones_mensuales",
                        lambda filas: estado["escrituras"].append(list(filas)) or len(list(filas)))

    # El rancho: el mapa, la subida y la fila de `layers`.
    monkeypatch.setattr(rancho, "init_ee", lambda: None)
    monkeypatch.setattr(rancho, "coords_to_geometry", lambda c: "roi")
    monkeypatch.setattr(rancho, "mapa_del_mes",
                        lambda *a, **k: type("Img", (), {"unmask": lambda self, *a, **k: self})())
    monkeypatch.setattr(rancho, "url_de_descarga", lambda *a, **k: "https://gee/descarga.tif")
    # `subir_cog` se mudo a `handlers/raster.py` en M.6.2b, pero `rancho` la
    # importo por nombre: se reemplaza donde la busca.
    monkeypatch.setattr(rancho, "subir_cog", lambda url, key: ([0, 0, 1, 1], 1.5))
    monkeypatch.setattr(rancho, "insert_layer",
                        lambda **kw: estado["capas"].append(kw))

    # Si algo leyera el reloj, que se note: el mes tiene que salir del evento.
    def _reloj_prohibido():
        msg = "el cierre de mes no puede leer el reloj: el mes lo manda el evento"
        raise AssertionError(msg)

    monkeypatch.setattr(altas, "hoy_utc", _reloj_prohibido)
    return estado


def _correr_parcela(step, payload=PAYLOAD_PARCELA, attempt=0):
    return handlers_mes.process_parcela_mes._handler(_Ctx(payload, attempt), step)


def _correr_rancho(step, payload=PAYLOAD_RANCHO, attempt=0):
    return handlers_mes.process_rancho_mes._handler(_Ctx(payload, attempt), step)


# --- 1. La parcela -------------------------------------------------------------


def test_la_parcela_procesa_el_mes_del_evento_en_un_solo_step(mundo):
    step = _Step()

    resultado = _correr_parcela(step)

    assert step.ejecutados == ["mark-job-running", "mes-2026-08", "mark-job-completed"]
    assert mundo["pedidos"] == ["2026-08"], "no hay step `plan`: el mes viene en el evento"
    assert resultado == {"status": "success", "receta": "s2-mensual-v1", "mes": "2026-08",
                         "con_valor": True, "filas": len(RECETA_VIGENTE.indices)}
    assert [s for s, _ in mundo["jobs"]] == ["running", "completed"]


def test_escribe_una_fila_por_indice_de_ese_mes(mundo):
    _correr_parcela(_Step())

    (filas,) = mundo["escrituras"]
    assert [f.indice for f in filas] == list(RECETA_VIGENTE.indices)
    assert {f.fecha for f in filas} == {datetime(2026, 8, 1, tzinfo=UTC)}
    assert {f.parcela_id for f in filas} == {PARCELA}
    assert {f.tenant_id for f in filas} == {TENANT}
    assert {f.receta for f in filas} == {"s2-mensual-v1"}


def test_un_mes_sin_cobertura_escribe_las_filas_sin_valor(mundo):
    """Igual que en el alta: la fila existe, para que el front dibuje el hueco."""
    mundo["gee"] = lambda mes: _respuesta_de_gee(cobertura=0.05)

    resultado = _correr_parcela(_Step())

    (filas,) = mundo["escrituras"]
    assert all(f.valor is None for f in filas)
    assert resultado["con_valor"] is False


def test_la_barra_llega_a_100_con_un_solo_mes(mundo):
    _correr_parcela(_Step())

    progresos = [l["progreso"] for l in mundo["bitacora"] if l["progreso"] is not None]
    assert progresos == sorted(progresos)
    assert progresos[-1] == 100
    assert any("Mes 1 de 1 (2026-08)" in l["mensaje"] for l in mundo["bitacora"])


# --- 2. El rancho --------------------------------------------------------------


def test_el_rancho_sube_el_mapa_de_ese_mes(mundo):
    step = _Step()

    resultado = _correr_rancho(step)

    assert step.ejecutados == ["mark-job-running", "mes-2026-08", "mark-job-completed"]
    indices = list(RECETA_VIGENTE.indices)
    assert resultado == {"status": "success", "receta": "s2-mensual-v1",
                         "mes": "2026-08", "mapas": len(indices)}
    assert [c["product"] for c in mundo["capas"]] == indices, "un mapa por indice"
    capa = mundo["capas"][indices.index("ndvi")]
    assert capa["source"] == "mensual"
    assert capa["acquired_ts"] == datetime(2026, 8, 1, tzinfo=UTC)
    assert capa["storage_key"].endswith("/s2-mensual-v1/ndvi/2026-08.tif")
    assert capa["storage_key"].startswith(f"tenants/{TENANT}/ranchos/{RANCHO}/")


def test_un_mes_del_rancho_sin_un_pixel_limpio_no_tiene_mapa(mundo):
    """DECISIONS #51: sin cobertura no hay COG ni fila en `layers`."""
    mundo["gee"] = lambda mes: _respuesta_de_gee(cobertura=0.0)

    resultado = _correr_rancho(_Step())

    assert resultado["mapas"] == 0
    assert mundo["capas"] == []


# --- 3. El periodo del evento --------------------------------------------------


@pytest.mark.parametrize("periodo", ["2026-8", "2026-13", "agosto", "2026-08-01", ""])
def test_un_periodo_que_no_es_un_mes_no_se_reintenta(mundo, periodo):
    """Reintentar no lo arregla, y el job queda `failed` al primer intento."""
    with pytest.raises(inngest.NonRetriableError):
        _correr_parcela(_Step(), {**PAYLOAD_PARCELA, "Periodo": periodo})

    assert mundo["pedidos"] == [], "no se le pide nada a GEE con un periodo invalido"


def test_sin_periodo_tampoco_se_reintenta(mundo):
    payload = {k: v for k, v in PAYLOAD_PARCELA.items() if k != "Periodo"}

    with pytest.raises(inngest.NonRetriableError):
        _correr_parcela(_Step(), payload)


@pytest.mark.parametrize("falta", ["ParcelaId", "TenantId", "Coordinates"])
def test_sin_los_campos_de_la_entidad_tampoco(mundo, falta):
    payload = {k: v for k, v in PAYLOAD_PARCELA.items() if k != falta}

    with pytest.raises(inngest.NonRetriableError):
        _correr_parcela(_Step(), payload)


# --- 4. La concurrencia --------------------------------------------------------


def test_las_cuatro_funciones_de_gee_comparten_una_cola_de_5():
    """El plan Hobby da 5 steps a la vez en toda la cuenta (M.4.10, `DECISIONS #55`)."""
    from handlers.mes import process_parcela_mes, process_rancho_mes
    from handlers.parcela import process_parcela
    from handlers.rancho import process_rancho

    for funcion in (process_parcela, process_rancho, process_parcela_mes, process_rancho_mes):
        (concurrencia,) = funcion._opts.concurrency
        assert concurrencia.limit == altas.LIMITE_GEE == 5
        assert concurrencia.scope == "account"
        # La misma key en las cuatro: una sola cola virtual, no cuatro de 5.
        assert concurrencia.key == "'gee'"


def test_las_dos_funciones_del_mes_estan_registradas_y_escuchan_su_evento():
    from handlers.registro import all_functions

    ids = [f.id for f in all_functions]
    assert "geeworker-process-parcela-mes" in ids
    assert "geeworker-process-rancho-mes" in ids

    for funcion, evento in ((handlers_mes.process_parcela_mes, "terra/parcela.mes.requested"),
                            (handlers_mes.process_rancho_mes, "terra/rancho.mes.requested")):
        assert funcion._triggers[0].event == evento
