"""M.4.3: las filas mensuales de una parcela (`pipeline/filas.py`)."""
import dataclasses
import math
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))

from pipeline.etapas.reduccion import Reduccion
from pipeline.filas import filas_de
from pipeline.periodos import Mes
from pipeline.receta import RECETA_VIGENTE
from pipeline.ventanas import Ventana, del_mes

PARCELA = "a1b2c3d4-e5f6-4a7b-8c9d-0e1f2a3b4c5d"
TENANT = "7f3c2a10-5b6d-4e8f-9a01-23456789abcd"


def _estadisticas(mediana):
    return {
        "mediana": mediana, "media": mediana, "min": -0.1, "max": 0.9,
        "p10": 0.2, "p90": 0.8, "desvio": 0.05,
    }


def _reduccion(cobertura=0.95, observaciones=4.0, mediana=0.61):
    return Reduccion(
        estadisticas={indice: _estadisticas(mediana) for indice in RECETA_VIGENTE.indices},
        cobertura=cobertura,
        observaciones=observaciones,
    )


def _filas(**cambios):
    argumentos = {
        "parcela_id": PARCELA, "tenant_id": TENANT, "ventana": del_mes(Mes(2025, 9)),
        "reduccion": _reduccion(), "receta": RECETA_VIGENTE,
    }
    return filas_de(**(argumentos | cambios))


def test_una_fila_por_indice_de_la_receta():
    filas = _filas()
    assert [f.indice for f in filas] == list(RECETA_VIGENTE.indices)
    assert {f.parcela_id for f in filas} == {PARCELA}
    assert {f.tenant_id for f in filas} == {TENANT}


def test_la_fecha_es_el_primer_instante_del_mes_en_utc():
    """Es la PK: tiene que ser la misma en cada reproceso (`DECISIONS` E.6)."""
    assert {f.fecha for f in _filas()} == {datetime(2025, 9, 1, tzinfo=UTC)}


def test_valor_es_la_mediana_y_las_estadisticas_van_enteras():
    fila = _filas()[0]
    assert fila.valor == pytest.approx(0.61)
    assert fila.estadisticas == _estadisticas(0.61)
    assert fila.cobertura == pytest.approx(0.95)
    assert fila.observaciones == pytest.approx(4.0)
    assert fila.receta == "s2-mensual-v1"


def test_bajo_la_cobertura_minima_valor_es_nulo_y_la_fila_existe():
    """`ARQUITECTURA` §6: la fila se escribe igual, para dibujar el hueco (B-6)."""
    bajo = RECETA_VIGENTE.cobertura_minima - 0.01
    filas = _filas(reduccion=_reduccion(cobertura=bajo))
    assert len(filas) == len(RECETA_VIGENTE.indices)
    assert all(f.valor is None for f in filas)
    # Las estadisticas se guardan igual: se calcularon, y quien las lea filtra.
    assert filas[0].estadisticas["mediana"] == pytest.approx(0.61)
    assert filas[0].cobertura == pytest.approx(bajo)


def test_justo_en_la_cobertura_minima_valor_va():
    minima = RECETA_VIGENTE.cobertura_minima
    assert _filas(reduccion=_reduccion(cobertura=minima))[0].valor == pytest.approx(0.61)


def test_un_mes_sin_un_pixel_con_dato():
    """Cobertura 0: GEE devuelve las estadisticas y las observaciones en None."""
    sin_dato = Reduccion(
        estadisticas={
            i: dict.fromkeys(_estadisticas(0.0)) for i in RECETA_VIGENTE.indices
        },
        cobertura=0.0,
        observaciones=None,
    )
    fila = _filas(reduccion=sin_dato)[0]
    assert fila.valor is None
    assert fila.observaciones is None
    assert set(fila.estadisticas.values()) == {None}


@pytest.mark.parametrize("malo", [math.nan, math.inf, -math.inf])
def test_rechaza_numeros_no_finitos(malo):
    """`json.dumps` escribe NaN, pero no es JSON: el jsonb lo rechaza en la base."""
    with pytest.raises(ValueError, match="finito"):
        _filas(reduccion=_reduccion(mediana=malo))
    with pytest.raises(ValueError, match="observaciones"):
        _filas(reduccion=_reduccion(observaciones=malo))


def test_rechaza_una_reduccion_con_otros_indices():
    otra = Reduccion(estadisticas={"ndvi": _estadisticas(0.5)}, cobertura=1.0,
                     observaciones=3.0)
    with pytest.raises(ValueError, match="calcula"):
        _filas(reduccion=otra)


def test_rechaza_una_receta_sin_mediana():
    """Sin mediana no hay `valor`: mejor saberlo aca que escribir filas sin serie."""
    sin_mediana = dataclasses.replace(
        RECETA_VIGENTE, version="sin-mediana", estadisticas=("media", "p10")
    )
    with pytest.raises(ValueError, match="mediana"):
        _filas(receta=sin_mediana)


def test_los_argumentos_van_por_nombre():
    with pytest.raises(TypeError):
        filas_de(PARCELA, TENANT, del_mes(Mes(2025, 9)), _reduccion(), RECETA_VIGENTE)


def test_la_fecha_es_el_inicio_de_la_ventana_y_no_el_del_mes():
    """M.9.0b: la fila ya no calcula el mes, usa la ventana que le dan.

    Con una ventana que no empieza donde empieza su mes —lo que va a producir
    `por_pasada`— la fecha tiene que ser la de la ventana. Antes de M.9.0b esto
    era imposible de expresar: `filas_del_mes` derivaba la fecha del mes.
    """
    instante = datetime(2025, 9, 17, 15, 42, tzinfo=UTC)
    pasada = Ventana(
        etiqueta="2025-09-17T15:42Z",
        inicio=instante,
        fin=datetime(2025, 9, 17, 15, 43, tzinfo=UTC),
    )
    assert {f.fecha for f in _filas(ventana=pasada)} == {instante}


# --- El umbral al leer (M.9.0c, `DECISIONS #63`) ---------------------------


def test_sin_umbral_al_escribir_el_valor_va_aunque_la_cobertura_sea_baja():
    """`s2-pasada-v2` no descarta al escribir: el umbral es de quien lee.

    Descartar al escribir es una reduccion con perdida antes de guardar, y es la
    que no se puede deshacer: el dia que 0,3 resulte mal puesto, con el umbral al
    leer se cambia el numero y con el umbral al escribir se reprocesa.
    """
    v2 = dataclasses.replace(RECETA_VIGENTE, umbral_al_escribir=False)
    bajo = RECETA_VIGENTE.cobertura_minima - 0.01

    filas = _filas(reduccion=_reduccion(cobertura=bajo), receta=v2)

    assert all(f.valor is not None for f in filas)
    assert all(f.cobertura == bajo for f in filas), "la cobertura viaja en la fila"


def test_con_umbral_al_escribir_la_misma_cobertura_da_valor_nulo():
    """El control del de arriba: lo unico que cambia es el campo de la receta."""
    bajo = RECETA_VIGENTE.cobertura_minima - 0.01

    filas = _filas(reduccion=_reduccion(cobertura=bajo), receta=RECETA_VIGENTE)

    assert all(f.valor is None for f in filas)


def test_sin_umbral_al_escribir_un_mes_sin_un_pixel_sigue_sin_valor():
    """No hay nada que guardar: la mediana vino en None desde GEE.

    Es la diferencia entre "no llego al umbral" —que v2 deja pasar— y "no hay
    dato", que no depende de ningun umbral.
    """
    v2 = dataclasses.replace(RECETA_VIGENTE, umbral_al_escribir=False)
    vacia = Reduccion(
        estadisticas={
            indice: dict.fromkeys(RECETA_VIGENTE.estadisticas)
            for indice in RECETA_VIGENTE.indices
        },
        cobertura=0.0,
        observaciones=None,
    )

    filas = _filas(reduccion=vacia, receta=v2)

    assert all(f.valor is None for f in filas)
