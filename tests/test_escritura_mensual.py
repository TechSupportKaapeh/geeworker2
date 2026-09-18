"""M.4.3: la escritura de lo mensual en `geodata` (`repositories/db_repository.py`).

Con conexion falsa: se captura el SQL y los valores que llegarian a Postgres. La
corrida contra PostGIS real, con los CHECK de `MedicionesMensuales`, esta en
`DECISIONS #49`.
"""
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))

from pipeline.etapas.reduccion import Reduccion
from pipeline.filas import filas_del_mes
from pipeline.periodos import Mes
from pipeline.receta import RECETA_VIGENTE
from repositories import db_repository

PARCELA = "a1b2c3d4-e5f6-4a7b-8c9d-0e1f2a3b4c5d"
TENANT = "7f3c2a10-5b6d-4e8f-9a01-23456789abcd"


class _Conexion:
    def __init__(self, falla=False):
        self.falla = falla
        self.commits = self.rollbacks = 0
        self.ejecutado = []

    def cursor(self):
        conexion = self

        class _Cursor:
            def execute(self, sql, params=None):
                conexion.ejecutado.append((sql, params))
                if conexion.falla:
                    raise RuntimeError("new row violates check constraint")

        return _Cursor()

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


@pytest.fixture
def conexion(monkeypatch):
    conn = _Conexion()
    devueltas = []
    monkeypatch.setattr(db_repository, "get_connection", lambda: conn)
    monkeypatch.setattr(db_repository, "release_connection", devueltas.append)
    conn.devueltas = devueltas
    return conn


@pytest.fixture
def lotes(monkeypatch, conexion):
    """Lo que `execute_values` mandaria: el SQL y las tuplas de valores."""
    capturados = []

    def _execute_values(cur, sql, valores):
        cur.execute(sql)
        capturados.append((sql, list(valores)))

    monkeypatch.setattr(db_repository, "execute_values", _execute_values)
    return capturados


def _filas(cobertura=0.9, mediana=0.61):
    estadisticas = {"mediana": mediana, "media": 0.6, "min": -0.1, "max": 0.9,
                    "p10": 0.3, "p90": 0.8, "desvio": 0.1}
    reduccion = Reduccion(
        estadisticas=dict.fromkeys(RECETA_VIGENTE.indices, estadisticas),
        cobertura=cobertura, observaciones=4.0,
    )
    return filas_del_mes(parcela_id=PARCELA, tenant_id=TENANT, mes=Mes(2025, 9),
                         reduccion=reduccion, receta=RECETA_VIGENTE)


def _json(valor):
    """Lo que psycopg2 mandaria por un `Json`."""
    return json.loads(valor.dumps(valor.adapted))


# --- measurements -----------------------------------------------------------


def test_upsert_escribe_las_columnas_mensuales(conexion, lotes):
    assert db_repository.upsert_mediciones_mensuales(_filas()) == 4
    (sql, valores), = lotes
    for columna in ("estadisticas", "cobertura", "observaciones", "receta"):
        assert columna in sql
    parcela, indice, fecha, tenant, valor, estadisticas, cobertura, obs, receta = valores[0]
    assert (parcela, indice, tenant, receta) == (PARCELA, "ndvi", TENANT, "s2-mensual-v1")
    assert fecha == datetime(2025, 9, 1, tzinfo=UTC)
    assert valor == pytest.approx(0.61)
    assert _json(estadisticas)["p90"] == pytest.approx(0.8)
    assert (cobertura, obs) == (pytest.approx(0.9), pytest.approx(4.0))
    assert conexion.commits == 1


def test_las_filas_sin_valor_se_escriben(conexion, lotes):
    """La vieja las salteaba; lo mensual no: el mes existe aunque este nublado."""
    filas = _filas(cobertura=0.1)
    assert all(f.valor is None for f in filas)
    assert db_repository.upsert_mediciones_mensuales(filas) == 4
    assert [v[4] for v in lotes[0][1]] == [None] * 4


def test_el_conflicto_actualiza_y_limpia_min_y_max(lotes, conexion):
    """Una fila vieja por pasada del dia 1 cae en la misma PK que la del mes."""
    db_repository.upsert_mediciones_mensuales(_filas())
    sql = " ".join(lotes[0][0].split())
    assert "ON CONFLICT (parcela_id, indice, fecha) DO UPDATE" in sql
    assert "min_val = NULL" in sql
    assert "max_val = NULL" in sql
    assert "receta = EXCLUDED.receta" in sql


def test_un_lote_vacio_no_abre_conexion(monkeypatch):
    monkeypatch.setattr(db_repository, "get_connection",
                        lambda: pytest.fail("no deberia pedir una conexion"))
    assert db_repository.upsert_mediciones_mensuales([]) == 0


def test_filas_repetidas_se_rechazan_antes_de_ir_a_la_base(monkeypatch):
    monkeypatch.setattr(db_repository, "get_connection",
                        lambda: pytest.fail("no deberia pedir una conexion"))
    filas = _filas()
    with pytest.raises(ValueError, match="repetidas"):
        db_repository.upsert_mediciones_mensuales([*filas, filas[0]])


def test_si_la_base_rechaza_el_lote_hace_rollback_y_devuelve_la_conexion(
    conexion, lotes
):
    conexion.falla = True
    with pytest.raises(RuntimeError, match="check constraint"):
        db_repository.upsert_mediciones_mensuales(_filas())
    assert conexion.rollbacks == 1
    assert conexion.commits == 0
    assert conexion.devueltas == [conexion]


def test_el_json_rechaza_nan():
    """Defensa en el borde: `filas_del_mes` ya los rechaza, pero no es el unico camino."""
    estricto = db_repository._json_estricto({"mediana": float("nan")})
    with pytest.raises(ValueError, match="JSON"):
        estricto.dumps(estricto.adapted)


# --- layers -----------------------------------------------------------------


def _capa(**cambios):
    argumentos = {
        "natural_key": "rancho_mensual_ndvi_r_2025-09", "product": "ndvi",
        "storage_key": "tenants/t/ranchos/r/s2-mensual-v1/ndvi/2025-09.tif",
        "acquired_ts": datetime(2025, 9, 1, tzinfo=UTC),
        "ingested_ts": datetime(2026, 9, 17, tzinfo=UTC),
        "tenant_id": TENANT, "rancho_id": PARCELA, "source": "mensual",
    }
    return db_repository.insert_layer(**(argumentos | cambios))


def test_insert_layer_escribe_receta_y_estadisticas(conexion):
    _capa(receta="s2-mensual-v1", estadisticas={"mediana": 0.5, "p90": 0.8})
    sql, params = conexion.ejecutado[0]
    assert "receta = EXCLUDED.receta" in sql
    assert "estadisticas = EXCLUDED.estadisticas" in sql
    assert params[-2] == "s2-mensual-v1"
    assert _json(params[-1]) == {"mediana": 0.5, "p90": 0.8}


def test_insert_layer_de_la_capa_vieja_deja_las_columnas_en_null(conexion):
    """Los llamadores viejos no las pasan: queda lo que habia antes de M.4.3."""
    _capa(source="systematic")
    _, params = conexion.ejecutado[0]
    assert params[-2:] == (None, None)


def test_insert_layer_rechaza_estadisticas_que_no_son_un_objeto(conexion):
    """La base tiene un CHECK para eso; aca el error dice que se paso."""
    with pytest.raises(TypeError, match="dict"):
        _capa(estadisticas=[0.5, 0.8])
    assert conexion.ejecutado == []


def test_insert_layer_hace_rollback_si_la_base_rechaza(conexion):
    """Antes volvia al pool con la transaccion abortada."""
    conexion.falla = True
    with pytest.raises(RuntimeError):
        _capa(receta="s2-mensual-v1", estadisticas={"mediana": 0.5})
    assert conexion.rollbacks == 1
    assert conexion.devueltas == [conexion]
