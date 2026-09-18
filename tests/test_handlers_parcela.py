"""M.4.4: `process_parcela` sobre el pipeline mensual (`handlers/parcela.py`).

Sin GEE ni base: lo falso es lo minimo. `estadisticas_del_mes` devuelve una
expresion cuyo `getInfo()` contesta con las claves que da GEE, asi que el borde
(`ejecucion.traer`, con su conteo y su traduccion de errores), `reduccion.leer` y
`filas_del_mes` corren de verdad. Se reemplazan `init_ee`, el ROI, el reloj y el
upsert.

El step imita al SDK (`StepSync.run` de inngest-py 0.4): el error de un step sale
envuelto en un `BaseException`, salvo `NonRetriableError`. Y puede memoizar por
id, que es lo que hace Inngest entre requests.
"""
import subprocess
import sys
from datetime import UTC, date, datetime
from itertools import pairwise
from pathlib import Path

import inngest
import pytest

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))

from handlers import parcela
from pipeline import ejecucion
from pipeline.estadisticas import claves_de_salida
from pipeline.receta import RECETA_VIGENTE
from repositories import db_repository
from services import avance_job, inngest_handlers

HOY = date(2026, 9, 18)
MESES_V1 = [f"{2024 + (8 + i) // 12}-{(8 + i) % 12 + 1:02d}" for i in range(24)]


class _Interrupcion(BaseException):
    """Lo que hace el SDK con el error de un step: un BaseException."""


class _Step:
    """Como `StepSync.run`; con `memo`, devuelve lo guardado sin volver a correr."""

    def __init__(self, memo=None):
        self.ejecutados = []
        self.memo = memo

    def run(self, nombre, funcion, *args):
        if self.memo is not None and nombre in self.memo:
            return self.memo[nombre]
        self.ejecutados.append(nombre)
        try:
            resultado = funcion(*args)
        except (inngest.NonRetriableError, inngest.RetryAfterError):
            raise
        except Exception as e:
            raise _Interrupcion(e) from e
        if self.memo is not None:
            self.memo[nombre] = resultado
        return resultado


class _Ctx:
    def __init__(self, data, attempt=0):
        self.event = type("Evento", (), {"data": data})()
        self.attempt = attempt


def _respuesta_de_gee(cobertura=0.9, mediana=0.5):
    """Lo que contesta GEE a `estadisticas_del_mes`: una clave por indice y estadistica."""
    claves = claves_de_salida(RECETA_VIGENTE.indices, RECETA_VIGENTE.estadisticas)
    respuesta = {
        clave: (mediana if estadistica == "mediana" else 0.1)
        for (_, estadistica), clave in claves.items()
    }
    respuesta.update(cobertura=cobertura, observaciones=4.0)
    return respuesta


class _Expresion:
    def __init__(self, contestar):
        self._contestar = contestar

    def getInfo(self):  # el nombre que usa ee
        return self._contestar()


@pytest.fixture
def mundo(monkeypatch):
    """GEE, la base y el reloj, falsos. `mundo["gee"]` decide que contesta cada mes."""
    estado = {"pedidos": [], "escrituras": [], "bitacora": [], "jobs": [],
              "gee": lambda mes: _respuesta_de_gee()}

    def _estadisticas_del_mes(roi, mes, receta):
        estado["pedidos"].append(str(mes))
        return _Expresion(lambda: estado["gee"](str(mes)))

    def _upsert(filas):
        filas = list(filas)
        estado["escrituras"].append(filas)
        return len(filas)

    def _registrar(job_id, attempt, stage, level, message, detail=None, progress=None):
        estado["bitacora"].append({"etapa": stage, "nivel": level, "mensaje": message,
                                   "detalle": detail or {}, "progreso": progress})

    monkeypatch.setattr(ejecucion, "estadisticas_del_mes", _estadisticas_del_mes)
    monkeypatch.setattr(parcela, "init_ee", lambda: None)
    monkeypatch.setattr(parcela, "coords_to_geometry", lambda c: "roi")
    monkeypatch.setattr(parcela, "hoy_utc", lambda: HOY)
    monkeypatch.setattr(parcela, "upsert_mediciones_mensuales", _upsert)
    monkeypatch.setattr(avance_job, "registrar_evento_job", _registrar)
    monkeypatch.setattr(db_repository, "update_processing_job",
                        lambda job_id, status, **kw: estado["jobs"].append((status, kw)))
    return estado


PAYLOAD = {"JobId": "job-p", "ParcelaId": "0b6f0a52-8c1e-4d4b-9f39-1d6f7a1e2c3d",
           "TenantId": "7d0c1b8a-3e2f-4a5b-8c6d-9e0f1a2b3c4d", "RanchoId": "r",
           "Coordinates": [{"lat": 20.5, "lng": -101.2}]}


def _correr(step, payload=PAYLOAD, attempt=0):
    return parcela.process_parcela._handler(_Ctx(payload, attempt), step)


# --- 1. Los steps --------------------------------------------------------------


def test_un_plan_y_un_step_por_mes_del_mas_viejo_al_mas_nuevo(mundo):
    step = _Step()
    resultado = _correr(step)

    assert step.ejecutados == [
        "mark-job-running", "plan", *[f"mes-{m}" for m in MESES_V1], "mark-job-completed",
    ]
    # El alta del 2026-09-18 con 24 meses: 2024-09 a 2026-08. El mes en curso no.
    assert MESES_V1[0] == "2024-09" and MESES_V1[-1] == "2026-08"
    assert mundo["pedidos"] == MESES_V1
    assert resultado == {"status": "success", "receta": "s2-mensual-v1", "meses": 24,
                         "con_valor": 24, "filas": 24 * len(RECETA_VIGENTE.indices)}
    assert [s for s, _ in mundo["jobs"]] == ["running", "completed"]


def test_cada_mes_escribe_una_fila_por_indice_con_la_receta(mundo):
    _correr(_Step())

    assert len(mundo["escrituras"]) == 24
    for texto, filas in zip(MESES_V1, mundo["escrituras"], strict=True):
        anio, mes = map(int, texto.split("-"))
        assert [f.indice for f in filas] == list(RECETA_VIGENTE.indices)
        assert {f.fecha for f in filas} == {datetime(anio, mes, 1, tzinfo=UTC)}
        assert {f.receta for f in filas} == {"s2-mensual-v1"}
        assert {f.parcela_id for f in filas} == {PAYLOAD["ParcelaId"]}
        assert {f.tenant_id for f in filas} == {PAYLOAD["TenantId"]}
        assert all(f.valor == 0.5 and f.cobertura == 0.9 for f in filas)


def test_la_barra_solo_avanza_y_la_bitacora_dice_por_que_mes_va(mundo):
    _correr(_Step())

    progresos = [l["progreso"] for l in mundo["bitacora"] if l["progreso"] is not None]
    assert progresos == sorted(progresos)
    assert progresos[-1] == 100
    assert any("Mes 7 de 24 (2025-03)" in l["mensaje"] for l in mundo["bitacora"])
    (plan,) = [l for l in mundo["bitacora"] if l["etapa"] == "plan"]
    assert plan["detalle"] == {"desde": "2024-09", "hasta": "2026-08", "receta": "s2-mensual-v1"}


def test_cada_mes_cuesta_una_llamada_a_gee(mundo):
    """El conteo sale del borde de verdad (`ejecucion.traer`), no de un numero fijo."""
    _correr(_Step())

    meses = [l for l in mundo["bitacora"] if l["etapa"].startswith("mes-")]
    assert len(meses) == 24
    assert {l["detalle"]["llamadas"] for l in meses} == {1}


# --- 2. Los meses sin dato --------------------------------------------------


def test_un_mes_bajo_la_cobertura_minima_se_escribe_sin_valor(mundo):
    """La fila existe igual: el front dibuja el hueco y el cierre de mes sabe que se hizo."""
    mundo["gee"] = lambda mes: _respuesta_de_gee(cobertura=0.12) if mes == "2025-07" else _respuesta_de_gee()

    resultado = _correr(_Step())

    (filas,) = [f for f in mundo["escrituras"] if f[0].fecha.month == 7 and f[0].fecha.year == 2025]
    assert all(f.valor is None for f in filas)
    assert all(f.estadisticas["mediana"] == 0.5 for f in filas)  # las estadisticas van igual
    (linea,) = [l for l in mundo["bitacora"] if l["etapa"] == "mes-2025-07"]
    assert linea["nivel"] == "warning"
    assert "bajo el mínimo de 30,0 %" in linea["mensaje"]
    assert resultado["con_valor"] == 23
    assert resultado["filas"] == 24 * len(RECETA_VIGENTE.indices)


def test_un_mes_sin_un_pixel_limpio_tambien_se_escribe(mundo):
    """Sin pixeles, GEE contesta **solo** la cobertura: omite las demas claves.

    Es lo que dio la parcela 1 en 2026-05, con sus 10 escenas tapadas por la
    mascara (`DECISIONS #50`). Antes del arreglo de `leer`, ese step fallaba en
    cada reintento y el alta no terminaba nunca.
    """
    mundo["gee"] = lambda mes: {"cobertura": 0}

    resultado = _correr(_Step())

    assert resultado["con_valor"] == 0
    assert all(f.valor is None and f.observaciones is None
               for filas in mundo["escrituras"] for f in filas)


# --- 3. Los errores ----------------------------------------------------------


def test_un_error_de_gee_definitivo_corta_el_alta_y_marca_failed(mundo):
    """"Sin memoria" no se arregla reintentando: se traduce a `NonRetriableError`."""
    def _sin_memoria(mes):
        if mes == "2025-01":
            raise RuntimeError("User memory limit exceeded.")
        return _respuesta_de_gee()
    mundo["gee"] = _sin_memoria
    step = _Step()

    with pytest.raises(inngest.NonRetriableError, match="2025-01"):
        _correr(step, attempt=0)

    assert step.ejecutados[-1] == "mark-job-failed"
    assert "mes-2025-02" not in step.ejecutados
    assert [s for s, _ in mundo["jobs"]] == ["running", "failed"]
    errores = [(l["etapa"], l["nivel"]) for l in mundo["bitacora"] if l["nivel"] == "error"]
    assert errores == [("mes-2025-01", "error"), ("fin", "error")]


def test_un_error_de_gee_pasajero_se_reintenta_y_no_marca_failed(mundo):
    """Concurrencia o timeout: Inngest reintenta el step; el job sigue `running`."""
    def _ocupado(mes):
        raise RuntimeError("Too many concurrent aggregations.")
    mundo["gee"] = _ocupado

    with pytest.raises(_Interrupcion):
        _correr(_Step(), attempt=0)

    assert [s for s, _ in mundo["jobs"]] == ["running"]
    (aviso,) = [l for l in mundo["bitacora"] if l["etapa"] == "mes-2024-09"]
    assert aviso["nivel"] == "warning"
    assert "ErrorDeGEE" in aviso["detalle"]["error"]


@pytest.mark.parametrize("falta", ["ParcelaId", "TenantId", "Coordinates"])
def test_un_evento_incompleto_falla_sin_reintentos(mundo, falta):
    payload = {k: v for k, v in PAYLOAD.items() if k != falta}

    with pytest.raises(inngest.NonRetriableError, match=falta[:1].lower() + falta[1:]):
        _correr(_Step(), payload=payload, attempt=0)

    assert mundo["pedidos"] == []
    assert [s for s, _ in mundo["jobs"]] == ["running", "failed"]


# --- 4. Inngest vuelve a correr el cuerpo --------------------------------------


def test_el_plan_memoizado_manda_aunque_el_run_cruce_el_fin_de_mes(mundo, monkeypatch):
    """Si el reloj se leyera afuera del step, el replay pediria 2024-10 a 2026-09."""
    memo = {}
    _correr(_Step(memo))
    pedidos = list(mundo["pedidos"])

    monkeypatch.setattr(parcela, "hoy_utc", lambda: date(2026, 10, 2))
    replay = _Step(memo)
    resultado = _correr(replay)

    assert replay.ejecutados == []          # todo memoizado: ni GEE ni la base
    assert mundo["pedidos"] == pedidos
    assert resultado["meses"] == 24


def test_los_ids_de_los_steps_no_se_repiten():
    """Inngest identifica cada step por su id: dos iguales se pisan el resultado."""
    ids = ["plan", *[f"mes-{m}" for m in MESES_V1]]
    assert len(ids) == len(set(ids))
    assert all(a < b for a, b in pairwise(MESES_V1))


# --- 5. El registro -----------------------------------------------------------


def test_el_alta_de_parcela_registrada_es_la_del_pipeline():
    """Un solo `process-parcela`, y es el de `handlers/`: el viejo no se sirve mas."""
    ids = [f.id for f in inngest_handlers.all_functions]
    assert ids.count("geeworker-process-parcela") == 1
    assert parcela.process_parcela in inngest_handlers.all_functions
    assert parcela.process_parcela._opts.retries == parcela.RETRIES


def test_el_handler_no_importa_la_capa_vieja_ni_abre_conexiones():
    """`handlers/` no depende de `inngest_handlers.py` (lo borra M.6.1), y no toca la red."""
    codigo = (
        "import socket, sys\n"
        "def prohibido(*a, **k):\n"
        "    raise AssertionError('se intento abrir una conexion')\n"
        "socket.socket.connect = prohibido\n"
        "socket.create_connection = prohibido\n"
        "socket.getaddrinfo = prohibido\n"
        "import handlers.parcela\n"
        "assert 'services.inngest_handlers' not in sys.modules, 'importo la capa vieja'\n"
    )
    resultado = subprocess.run(
        [sys.executable, "-c", codigo], cwd=str(RAIZ),
        capture_output=True, text=True, timeout=120, check=False,
    )
    assert resultado.returncode == 0, resultado.stderr
