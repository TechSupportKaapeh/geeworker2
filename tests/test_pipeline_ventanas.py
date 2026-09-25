"""M.9.0b: el agrupamiento como dato (`pipeline/ventanas.py`).

Partir es puro, asi que todo esto corre sin GEE y sin credenciales. Lo que si
necesita GEE —saber que pasadas existen— vive en `pipeline/ejecucion.py` y se
prueba con la marca `gee` en `test_pipeline_ejecucion.py`.

La aceptacion de M.9.0b es "s2-mensual-v1 produce EXACTAMENTE las mismas filas
que antes", y eso no se prueba aca: se probo comparando las filas de antes contra
las de despues, con el codigo de `main` al lado (DECISIONS #67). Lo que hay aca
es lo que queda como red para el futuro.
"""
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))

from pipeline.periodos import Mes, rango
from pipeline.receta import RECETA_VIGENTE
from pipeline.ventanas import (
    AGRUPAMIENTOS,
    ENTERO,
    POR_PASADA,
    Ventana,
    agrupamiento,
    del_mes,
    etiqueta_de_instante,
)

AGOSTO = Mes(2026, 8)


def _instante(dia, hora=15, minuto=11):
    return datetime(2026, 8, dia, hora, minuto, tzinfo=UTC)


# --- La ventana de un mes -------------------------------------------------


def test_la_ventana_de_un_mes_es_el_mes():
    ventana = del_mes(AGOSTO)
    inicio, fin = rango(AGOSTO)
    assert ventana == Ventana(etiqueta="2026-08", inicio=inicio, fin=fin)


def test_la_etiqueta_del_mes_es_la_que_ya_iba_en_la_key():
    """Lo que hace que M.9.0b no cambie ninguna key ni ningun `periodo`."""
    for mes in (Mes(2024, 1), Mes(2025, 12), Mes(2026, 8)):
        assert del_mes(mes).etiqueta == str(mes)


def test_la_ventana_del_mes_es_semiabierta():
    ventana = del_mes(Mes(2026, 12))
    assert ventana.inicio == datetime(2026, 12, 1, tzinfo=UTC)
    assert ventana.fin == datetime(2027, 1, 1, tzinfo=UTC)


# --- entero ---------------------------------------------------------------


def test_entero_devuelve_el_pedido_y_nada_mas():
    pedido = del_mes(AGOSTO)
    assert agrupamiento(ENTERO).partir(pedido, ()) == (pedido,)


def test_entero_no_mira_las_fechas():
    """Por eso `necesita_fechas` es falso y no cuesta una llamada a GEE."""
    pedido = del_mes(AGOSTO)
    con_fechas = agrupamiento(ENTERO).partir(pedido, [_instante(3), _instante(18)])
    assert con_fechas == agrupamiento(ENTERO).partir(pedido, ())


def test_entero_no_necesita_fechas_y_por_pasada_si():
    assert agrupamiento(ENTERO).necesita_fechas is False
    assert agrupamiento(POR_PASADA).necesita_fechas is True


def test_entero_sirve_para_cualquier_pedido_no_solo_un_mes():
    """`mensual` y `rango_libre` del diseño resultaron la misma función."""
    rango_raro = Ventana(
        etiqueta="2026-08-d1",
        inicio=datetime(2026, 8, 1, tzinfo=UTC),
        fin=datetime(2026, 8, 11, tzinfo=UTC),
    )
    assert agrupamiento(ENTERO).partir(rango_raro, ()) == (rango_raro,)


# --- por_pasada -----------------------------------------------------------


def test_por_pasada_da_una_ventana_por_fecha():
    ventanas = agrupamiento(POR_PASADA).partir(
        del_mes(AGOSTO), [_instante(18), _instante(3)]
    )
    assert [v.inicio for v in ventanas] == [_instante(3), _instante(18)]
    assert [v.etiqueta for v in ventanas] == ["2026-08-03T15:11Z", "2026-08-18T15:11Z"]


def test_la_ventana_de_una_pasada_contiene_su_instante_y_no_el_de_la_siguiente():
    primera, segunda = agrupamiento(POR_PASADA).partir(
        del_mes(AGOSTO), [_instante(3), _instante(8)]
    )
    assert primera.inicio <= _instante(3) < primera.fin
    assert not primera.inicio <= _instante(8) < primera.fin
    assert segunda.inicio <= _instante(8) < segunda.fin


def test_dos_teselas_de_la_misma_pasada_no_dan_dos_ventanas():
    """Si pasara, la misma observación escribiría dos filas iguales."""
    ventanas = agrupamiento(POR_PASADA).partir(
        del_mes(AGOSTO), [_instante(3), _instante(3)]
    )
    assert len(ventanas) == 1


def test_sin_pasadas_no_hay_ventanas():
    assert agrupamiento(POR_PASADA).partir(del_mes(AGOSTO), ()) == ()


@pytest.mark.parametrize(
    "fuera",
    [
        datetime(2026, 7, 31, 23, 59, tzinfo=UTC),
        datetime(2026, 9, 1, 0, 0, tzinfo=UTC),
    ],
)
def test_una_pasada_fuera_del_pedido_se_rechaza(fuera):
    """Escribiría una fila con la fecha de otro período, y nadie lo notaría."""
    with pytest.raises(ValueError, match="fuera del pedido"):
        agrupamiento(POR_PASADA).partir(del_mes(AGOSTO), [_instante(3), fuera])


def test_el_ultimo_instante_del_mes_si_entra():
    """Control del control: el rechazo de arriba no puede rechazar todo."""
    ultimo = datetime(2026, 8, 31, 23, 59, 59, tzinfo=UTC)
    (ventana,) = agrupamiento(POR_PASADA).partir(del_mes(AGOSTO), [ultimo])
    assert ventana.inicio == ultimo


# --- La etiqueta ----------------------------------------------------------


def test_la_etiqueta_de_una_pasada_lleva_el_minuto_en_utc():
    assert etiqueta_de_instante(_instante(3)) == "2026-08-03T15:11Z"


def test_la_etiqueta_se_pasa_a_utc_antes_de_escribirla():
    """Un instante con otro huso no puede cambiar de día al etiquetarse."""
    from datetime import timedelta, timezone

    mexico = timezone(timedelta(hours=-6))
    assert etiqueta_de_instante(datetime(2026, 8, 3, 9, 11, tzinfo=mexico)) == (
        "2026-08-03T15:11Z"
    )


# --- El registro ----------------------------------------------------------


def test_un_agrupamiento_que_no_existe_se_rechaza_por_su_nombre():
    with pytest.raises(ValueError, match="mensual"):
        agrupamiento("mensual")


def test_cada_agrupamiento_se_llama_como_su_clave():
    assert all(nombre == modo.nombre for nombre, modo in AGRUPAMIENTOS.items())


def test_el_registro_no_se_puede_mutar():
    with pytest.raises(TypeError):
        AGRUPAMIENTOS["nuevo"] = None  # type: ignore[index]


# --- La receta ------------------------------------------------------------


def test_la_receta_vigente_agrupa_entero_en_los_dos_productos():
    """Es lo que hace que M.9.0b no cambie ningún número."""
    assert RECETA_VIGENTE.agrupamiento_estadisticas == ENTERO
    assert RECETA_VIGENTE.agrupamiento_raster == ENTERO


@pytest.mark.parametrize(
    "campo", ["agrupamiento_estadisticas", "agrupamiento_raster"]
)
def test_una_receta_con_un_agrupamiento_inventado_no_se_arma(campo):
    """Rompe al importar, no en un mes suelto de producción."""
    import dataclasses

    with pytest.raises(ValueError, match="agrupamiento"):
        dataclasses.replace(RECETA_VIGENTE, **{campo: "quincenal"})
