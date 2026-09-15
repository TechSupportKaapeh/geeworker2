"""M.1.3: el registro de estadisticas y las claves con que GEE las devuelve.

La aceptacion del tablero: tests de las claves de salida con uno y con varios
indices. La regla de GEE depende de cuantas estadisticas lleva el reductor (una
salida: la clave es la banda; varias: `banda_sufijo`), asi que los casos cruzan
las dos cosas.
"""
import subprocess
import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))

from pipeline.estadisticas import ESTADISTICAS, Estadistica, claves_de_salida
from pipeline.indices import INDICES
from pipeline.registro import registro

SIETE = ["mediana", "media", "min", "max", "p10", "p90", "desvio"]


# --- El registro ----------------------------------------------------------


def test_la_receta_v1_trae_las_siete_en_orden():
    assert list(ESTADISTICAS) == SIETE


def test_los_sufijos_son_los_nombres_de_salida_de_gee():
    sufijos = {nombre: e.sufijo for nombre, e in ESTADISTICAS.items()}
    assert sufijos == {
        "mediana": "median", "media": "mean", "min": "min", "max": "max",
        "p10": "p10", "p90": "p90", "desvio": "stdDev",
    }
    # Dos estadisticas con el mismo sufijo darian la misma clave.
    assert len(set(sufijos.values())) == len(sufijos)


def test_una_fabrica_no_arma_nada_antes_de_ee_initialize():
    """Por esto el registro guarda fabricas y no reductores (ARQUITECTURA §3.2).

    Con earthengine-api 1.7.41 `ee.Reducer.median` existe al importar, pero
    llamarlo sin `ee.Initialize()` levanta EEException. Si el registro guardara
    reductores ya armados, importar el modulo fallaria.

    Corre en un proceso aparte porque el estado de `ee` es global al
    interprete. Dentro de la suite, `test_http_surface` llama a `_startup()`:
    reemplaza `app.init_ee`, pero `registrar_conexiones()` importa el suyo
    (`utils_pkg/conexiones.py`), y con un `.env` local inicializa GEE de verdad
    y hace un round-trip. En el CI no hay `.env`. Registrado el 2026-09-15.
    """
    codigo = (
        "import ee\n"
        "from pipeline.estadisticas import ESTADISTICAS\n"
        "for nombre, estadistica in ESTADISTICAS.items():\n"
        "    try:\n"
        "        estadistica.fabrica()\n"
        "    except ee.EEException as error:\n"
        "        assert 'not initialized' in str(error), error\n"
        "    else:\n"
        "        raise AssertionError(nombre + ' armo un reductor sin Initialize')\n"
        "print('ninguna armo nada')\n"
    )
    resultado = subprocess.run(
        [sys.executable, "-c", codigo],
        cwd=str(RAIZ), capture_output=True, text=True, timeout=120, check=False,
    )
    assert resultado.returncode == 0, resultado.stderr
    assert "ninguna armo nada" in resultado.stdout


@pytest.mark.parametrize("sufijo", ["p_10", "", "10p", "std dev"])
def test_un_sufijo_invalido_se_rechaza(sufijo):
    with pytest.raises(ValueError, match="sufijo"):
        Estadistica("p10", lambda: None, sufijo=sufijo)


def test_nombres_repetidos_se_rechazan():
    mediana = ESTADISTICAS["mediana"]
    with pytest.raises(ValueError, match="repetido"):
        registro(mediana, mediana)


# --- Las claves de salida -------------------------------------------------


def test_un_indice_con_las_siete():
    claves = claves_de_salida(["ndvi"], SIETE)
    assert list(claves.values()) == [
        "ndvi_median", "ndvi_mean", "ndvi_min", "ndvi_max",
        "ndvi_p10", "ndvi_p90", "ndvi_stdDev",
    ]
    assert list(claves) == [("ndvi", nombre) for nombre in SIETE]


def test_los_cuatro_indices_con_las_siete():
    claves = claves_de_salida(list(INDICES), SIETE)
    assert len(claves) == 28
    assert len(set(claves.values())) == 28  # ninguna choca
    assert claves[("ndmi", "desvio")] == "ndmi_stdDev"
    assert claves[("evi", "p90")] == "evi_p90"
    for (indice, nombre), clave in claves.items():
        assert clave == f"{indice}_{ESTADISTICAS[nombre].sufijo}"


def test_con_una_sola_estadistica_la_clave_es_la_banda():
    assert claves_de_salida(["ndvi"], ["mediana"]) == {("ndvi", "mediana"): "ndvi"}
    assert claves_de_salida(["ndvi", "evi"], ["p10"]) == {
        ("ndvi", "p10"): "ndvi",
        ("evi", "p10"): "evi",
    }


def test_respeta_el_orden_pedido():
    claves = claves_de_salida(["evi", "ndvi"], ["p90", "mediana"])
    assert list(claves) == [
        ("evi", "p90"), ("evi", "mediana"), ("ndvi", "p90"), ("ndvi", "mediana"),
    ]


@pytest.mark.parametrize("indices,estadisticas,mensaje", [
    ([], SIETE, "al menos un"),
    (["ndvi"], [], "al menos un"),
    (["ndvi", "ndvi"], SIETE, "repetido"),
    (["ndvi"], ["mediana", "mediana"], "repetido"),
    (["savi"], SIETE, "fuera del registro"),
    (["ndvi"], ["p25"], "fuera del registro"),
])
def test_rechaza_listas_invalidas(indices, estadisticas, mensaje):
    with pytest.raises(ValueError, match=mensaje):
        claves_de_salida(indices, estadisticas)
