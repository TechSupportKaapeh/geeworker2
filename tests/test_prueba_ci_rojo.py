"""Prueba de aceptacion de M.0.1: un test roto tiene que poner el CI en rojo.

Vive solo en la rama m0-1-prueba-rojo. El PR se cierra sin mergear.
"""


def test_el_ci_tiene_que_salir_rojo():
    assert False, "Prueba M.0.1: si este PR sale verde, el CI no es compuerta."
