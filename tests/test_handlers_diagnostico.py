"""M.4.10: la funcion que mide la espera de Inngest entre steps vacios.

Se prueba que no haga nada mas que medir (ni GEE, ni base, ni bitacora), que
tope la cantidad de steps para no gastar la cuota del plan, y que la hora se
tome **dentro** del step, que es lo que queda memoizado.
"""
import sys
import time
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))

from handlers import diagnostico
from services import inngest_handlers


class _Step:
    """Ejecuta cada step con una pausa, como si Inngest tardara en mandarlo."""

    def __init__(self, pausa=0.0):
        self.pausa, self.ejecutados = pausa, []

    def run(self, nombre, funcion, *args):
        time.sleep(self.pausa)
        self.ejecutados.append(nombre)
        return funcion(*args)


class _Ctx:
    def __init__(self, data):
        self.event = type("Evento", (), {"data": data})()
        self.run_id = "01TEST"
        self.attempt = 0


def test_mide_los_huecos_entre_steps_vacios():
    step = _Step(pausa=0.05)

    resultado = diagnostico.diagnostico_latencia._handler(_Ctx({"steps": 4}), step)

    assert step.ejecutados == ["vacio-1", "vacio-2", "vacio-3", "vacio-4"]
    assert resultado["steps"] == 4
    assert len(resultado["huecos_s"]) == 3
    # La pausa entre steps es lo que se mide: un hueco es la espera de Inngest.
    assert all(h >= 0.04 for h in resultado["huecos_s"])
    assert resultado["total_s"] == pytest.approx(sum(resultado["huecos_s"]), abs=0.01)


@pytest.mark.parametrize("datos,esperados", [
    ({}, diagnostico.STEPS_POR_DEFECTO),
    ({"steps": 3}, 3),
    ({"steps": 500}, diagnostico.STEPS_MAXIMOS),  # no gasta la cuota del plan
    ({"steps": 0}, 1),
    ({"steps": "7"}, diagnostico.STEPS_POR_DEFECTO),
    ({"steps": True}, diagnostico.STEPS_POR_DEFECTO),
    (None, diagnostico.STEPS_POR_DEFECTO),
])
def test_la_cantidad_de_steps_esta_acotada(datos, esperados):
    assert diagnostico.cuantos_steps(datos) == esperados


def test_con_un_solo_step_no_hay_huecos():
    resultado = diagnostico.diagnostico_latencia._handler(_Ctx({"steps": 1}), _Step())

    assert resultado == {"steps": 1, "huecos_s": [], "total_s": 0.0}


def test_no_toca_gee_ni_la_base(monkeypatch):
    """Si tocara algo, dejaria de medir solo a Inngest."""
    from repositories import db_repository
    from services.ee import ee_client

    def _prohibido(*a, **k):
        raise AssertionError("el diagnostico no puede tocar esto")

    monkeypatch.setattr(db_repository, "get_connection", _prohibido)
    monkeypatch.setattr(ee_client, "init_ee", _prohibido)

    diagnostico.diagnostico_latencia._handler(_Ctx({"steps": 2}), _Step())


def test_esta_registrada_y_escucha_su_evento():
    funcion = diagnostico.diagnostico_latencia
    assert funcion in inngest_handlers.all_functions
    (trigger,) = funcion.get_config("https://worker/api/inngest").main.triggers
    assert trigger.event == "terra/diagnostico.latencia"
    assert not funcion.is_handler_async
