"""M.1.3 y M.1.6: el registro de estadisticas, el plan de reduccion y las claves.

La aceptacion de M.1.3: las claves de salida con uno y con varios indices. La
regla de GEE depende de cuantas estadisticas lleva el reductor (una salida: la
clave es la banda; varias: `banda_sufijo`), asi que los casos cruzan las dos
cosas.

La de M.1.6: la receta v1 arma un solo histograma. Antes cada estadistica era un
reductor, y mediana, p10 y p90 armaban tres (`DECISIONS #36`).
"""
import subprocess
import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))

import pipeline.estadisticas as modulo
from pipeline.estadisticas import (
    ESTADISTICAS,
    Estadistica,
    Reductor,
    Tipo,
    claves_de_salida,
    plan_de_reduccion,
)
from pipeline.indices import INDICES
from pipeline.registro import registro

SIETE = ["mediana", "media", "min", "max", "p10", "p90", "desvio"]


# --- El registro ----------------------------------------------------------


def test_la_receta_v1_trae_las_siete_en_orden():
    assert list(ESTADISTICAS) == SIETE


def test_los_sufijos_salen_del_tipo():
    sufijos = {nombre: e.sufijo for nombre, e in ESTADISTICAS.items()}
    assert sufijos == {
        "mediana": "p50", "media": "mean", "min": "min", "max": "max",
        "p10": "p10", "p90": "p90", "desvio": "stdDev",
    }
    # Dos estadisticas con el mismo sufijo darian la misma clave.
    assert len(set(sufijos.values())) == len(sufijos)


def test_el_modulo_no_importa_ee():
    """El registro describe; el reductor lo arma M.2.4. Proceso aparte porque
    dentro de la suite otros modulos ya importaron `ee`."""
    codigo = (
        "import sys\n"
        "import pipeline.estadisticas\n"
        "print('ee' in sys.modules)\n"
    )
    resultado = subprocess.run(
        [sys.executable, "-c", codigo],
        cwd=str(RAIZ), capture_output=True, text=True, timeout=120, check=False,
    )
    assert resultado.returncode == 0, resultado.stderr
    assert resultado.stdout.strip() == "False"


@pytest.mark.parametrize("percentil", [-1, 101, 50.0, True, None])
def test_un_percentil_invalido_se_rechaza(percentil):
    with pytest.raises(ValueError, match="percentil"):
        Estadistica("p50", Tipo.PERCENTIL, percentil=percentil)


def test_solo_un_percentil_lleva_percentil():
    with pytest.raises(ValueError, match="solo un percentil"):
        Estadistica("media", Tipo.MEDIA, percentil=50)


def test_el_tipo_tiene_que_ser_un_tipo():
    with pytest.raises(TypeError):
        Estadistica("media", "media")


def test_nombres_repetidos_se_rechazan():
    mediana = ESTADISTICAS["mediana"]
    with pytest.raises(ValueError, match="repetido"):
        registro(mediana, mediana)


# --- El plan de reduccion -------------------------------------------------


def test_el_plan_de_la_receta_v1():
    assert plan_de_reduccion(SIETE) == (
        Reductor("percentile", salidas=("p10", "p50", "p90"), percentiles=(10, 50, 90)),
        Reductor("minMax", salidas=("min", "max")),
        Reductor("mean", salidas=("mean",)),
        Reductor("stdDev", salidas=("stdDev",)),
    )


def test_la_receta_v1_arma_un_solo_histograma():
    """Mediana y percentiles salen de histogramas; antes eran tres."""
    metodos = [r.metodo for r in plan_de_reduccion(SIETE)]
    assert sum(m in {"percentile", "median"} for m in metodos) == 1


@pytest.mark.parametrize("pedidas", [
    SIETE, ["mediana"], ["p90", "p10"], ["min"], ["max"], ["min", "max"],
    ["media", "desvio"], ["max", "mediana", "desvio"],
])
def test_las_salidas_del_plan_son_los_sufijos_pedidos(pedidas):
    salidas = [s for r in plan_de_reduccion(pedidas) for s in r.salidas]
    assert sorted(salidas) == sorted(ESTADISTICAS[n].sufijo for n in pedidas)


def test_los_percentiles_van_ordenados_aunque_se_pidan_desordenados():
    assert plan_de_reduccion(["p90", "mediana", "p10"]) == (
        Reductor("percentile", salidas=("p10", "p50", "p90"), percentiles=(10, 50, 90)),
    )


def test_min_o_max_solos_no_usan_minmax():
    assert plan_de_reduccion(["min"]) == (Reductor("min", salidas=("min",)),)
    assert plan_de_reduccion(["max", "media"]) == (
        Reductor("max", salidas=("max",)),
        Reductor("mean", salidas=("mean",)),
    )


def test_dos_estadisticas_con_la_misma_salida_se_rechazan(monkeypatch):
    p50 = Estadistica("p50", Tipo.PERCENTIL, percentil=50)
    monkeypatch.setattr(modulo, "ESTADISTICAS", registro(*ESTADISTICAS.values(), p50))
    with pytest.raises(ValueError, match="calculan lo mismo"):
        plan_de_reduccion(["mediana", "p50"])


# --- Las claves de salida -------------------------------------------------


def test_un_indice_con_las_siete():
    claves = claves_de_salida(["ndvi"], SIETE)
    assert list(claves.values()) == [
        "ndvi_p50", "ndvi_mean", "ndvi_min", "ndvi_max",
        "ndvi_p10", "ndvi_p90", "ndvi_stdDev",
    ]
    assert list(claves) == [("ndvi", nombre) for nombre in SIETE]


def test_los_cuatro_indices_con_las_siete():
    claves = claves_de_salida(list(INDICES), SIETE)
    assert len(claves) == 28
    assert len(set(claves.values())) == 28  # ninguna choca
    assert claves[("ndmi", "desvio")] == "ndmi_stdDev"
    assert claves[("evi", "mediana")] == "evi_p50"
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
    (["ndvi", "ndvi"], SIETE, "repetid"),
    (["ndvi"], ["mediana", "mediana"], "repetid"),
    (["savi"], SIETE, "fuera del registro"),
    (["ndvi"], ["p25"], "fuera del registro"),
])
def test_rechaza_listas_invalidas(indices, estadisticas, mensaje):
    with pytest.raises(ValueError, match=mensaje):
        claves_de_salida(indices, estadisticas)
