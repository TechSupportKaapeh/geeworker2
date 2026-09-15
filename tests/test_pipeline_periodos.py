"""M.1.1: los meses del pipeline (`pipeline/periodos.py`).

Los casos que pide el tablero (docs/SPRINTS_FASE_M.md): diciembre a enero, los
bisiestos, "hoy" el dia 1, y los 24 meses de un alta el 2026-09-12. Y el que no
pide pero muerde: un datetime con huso que en UTC ya es otro mes.
"""
import sys
from datetime import UTC, date, datetime, timedelta, timezone
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))

from pipeline.periodos import Mes, hoy_utc, meses_cerrados, rango

MEXICO = timezone(timedelta(hours=-6))


# --- Mes: texto, validacion y orden ---------------------------------------


def test_se_escribe_aaaa_mm_y_vuelve():
    mes = Mes(2026, 9)
    assert str(mes) == "2026-09"
    assert Mes.desde_texto("2026-09") == mes
    assert f"mes-{mes}" == "mes-2026-09"  # el id del step (ARQUITECTURA §4)


@pytest.mark.parametrize("texto", [
    "2026-9", "26-09", "2026-13", "2026-00", "2026/09", " 2026-09", "2026-09 ",
    "2026-09-01", "", "٢٠٢٦-٠٩",
])
def test_rechaza_texto_que_no_es_un_mes(texto):
    with pytest.raises(ValueError):
        Mes.desde_texto(texto)


@pytest.mark.parametrize("anio,mes", [(2026, 0), (2026, 13), (0, 1), (10000, 1)])
def test_rechaza_meses_que_no_existen(anio, mes):
    with pytest.raises(ValueError):
        Mes(anio, mes)


def test_ordena_por_anio_y_despues_por_mes():
    desordenados = [Mes(2026, 2), Mes(2025, 12), Mes(2026, 1), Mes(2024, 11)]
    assert sorted(desordenados) == [
        Mes(2024, 11), Mes(2025, 12), Mes(2026, 1), Mes(2026, 2),
    ]
    assert Mes(2025, 12) < Mes(2026, 1)


def test_es_hashable_e_inmutable():
    assert len({Mes(2026, 9), Mes(2026, 9), Mes(2026, 8)}) == 2
    with pytest.raises(AttributeError):
        Mes(2026, 9).mes = 10


# --- Anterior, siguiente y desplazar --------------------------------------


def test_diciembre_a_enero_y_vuelta():
    assert Mes(2025, 12).siguiente() == Mes(2026, 1)
    assert Mes(2026, 1).anterior() == Mes(2025, 12)


@pytest.mark.parametrize("meses,esperado", [
    (0, Mes(2026, 8)), (4, Mes(2026, 12)), (5, Mes(2027, 1)),
    (-8, Mes(2025, 12)), (-24, Mes(2024, 8)), (-25, Mes(2024, 7)),
])
def test_desplazar(meses, esperado):
    assert Mes(2026, 8).desplazar(meses) == esperado


# --- rango: semiabierto, en UTC -------------------------------------------


def test_rango_de_diciembre_termina_en_enero():
    inicio, fin = rango(Mes(2025, 12))
    assert inicio == datetime(2025, 12, 1, tzinfo=UTC)
    assert fin == datetime(2026, 1, 1, tzinfo=UTC)
    assert inicio.utcoffset() == fin.utcoffset() == timedelta(0)


@pytest.mark.parametrize("anio,dias", [(2024, 29), (2023, 28), (2000, 29), (1900, 28)])
def test_rango_de_febrero_respeta_los_bisiestos(anio, dias):
    inicio, fin = rango(Mes(anio, 2))
    assert fin - inicio == timedelta(days=dias)
    assert fin == datetime(anio, 3, 1, tzinfo=UTC)


def test_rango_es_semiabierto():
    inicio, fin = rango(Mes(2024, 2))
    ultimo_segundo = datetime(2024, 2, 29, 23, 59, 59, tzinfo=UTC)
    assert inicio <= ultimo_segundo < fin
    # El primer instante del mes siguiente no es de este mes.
    assert not fin < rango(Mes(2024, 3))[0]
    assert rango(Mes(2024, 3))[0] == fin


# --- desde_fecha y el huso ------------------------------------------------


def test_desde_fecha_con_una_date():
    assert Mes.desde_fecha(date(2024, 2, 29)) == Mes(2024, 2)


def test_un_datetime_con_huso_se_pasa_a_utc_antes_de_mirar_el_mes():
    # 20:00 del 31 de agosto en Mexico = 02:00 del 1 de septiembre en UTC.
    noche_en_mexico = datetime(2026, 8, 31, 20, 0, tzinfo=MEXICO)
    assert Mes.desde_fecha(noche_en_mexico) == Mes(2026, 9)
    assert meses_cerrados(noche_en_mexico, 1) == (Mes(2026, 8),)


def test_un_datetime_sin_huso_se_rechaza():
    with pytest.raises(ValueError, match="sin huso"):
        Mes.desde_fecha(datetime(2026, 8, 31, 20, 0))  # noqa: DTZ001 - es el caso


# --- meses_cerrados -------------------------------------------------------


def test_los_24_meses_de_un_alta_el_2026_09_12():
    meses = meses_cerrados(date(2026, 9, 12), 24)
    assert len(meses) == 24
    assert meses[0] == Mes(2024, 9)
    assert meses[-1] == Mes(2026, 8)
    # Consecutivos y del mas viejo al mas nuevo: es el orden de los steps.
    assert list(meses) == [Mes(2024, 9).desplazar(k) for k in range(24)]


def test_el_dia_1_el_mes_en_curso_todavia_no_cerro():
    assert meses_cerrados(date(2026, 9, 1), 1) == (Mes(2026, 8),)


def test_el_ultimo_dia_del_mes_tampoco_esta_cerrado():
    assert meses_cerrados(date(2026, 8, 31), 1) == (Mes(2026, 7),)


def test_en_enero_los_cerrados_son_del_anio_anterior():
    assert meses_cerrados(date(2026, 1, 15), 2) == (Mes(2025, 11), Mes(2025, 12))


def test_cero_meses_es_una_lista_vacia_y_negativo_es_un_error():
    assert meses_cerrados(date(2026, 9, 12), 0) == ()
    with pytest.raises(ValueError):
        meses_cerrados(date(2026, 9, 12), -1)


def test_hoy_utc_es_una_date_y_no_un_datetime():
    hoy = hoy_utc()
    assert isinstance(hoy, date)
    assert not isinstance(hoy, datetime)
