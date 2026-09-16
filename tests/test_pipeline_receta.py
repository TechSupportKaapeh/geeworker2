"""M.1.4 y M.1.7: la receta versionada y su huella.

La aceptacion de M.1.4: un test fija la huella de la receta, y cambiar un
parametro sin subir la version lo rompe. `HUELLAS` es ese registro, una linea por
version. Los controles negativos prueban que la huella se mueve con cada
parametro y con lo que la receta toma de los registros.

La de M.1.7: la receta fija lo que el pedido a GEE podria cambiar sin avisar, el
remuestreo de las bandas de 20 m y la distancia de sombra en pixeles.
"""
import dataclasses
import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))

import pipeline.receta as modulo_receta
from pipeline.estadisticas import ESTADISTICAS, Estadistica, Tipo
from pipeline.indices import INDICES, Indice
from pipeline.receta import RECETA_VIGENTE
from pipeline.registro import registro

# Una linea por version. Si este test sale rojo porque cambiaste un parametro:
#   1. subi la version de RECETA_VIGENTE (s2-mensual-v2);
#   2. agrega aca su huella, sin borrar las anteriores;
#   3. las filas guardadas con la version vieja quedan para reprocesar.
#
# Una version se congela cuando escribe su primera fila (M.4.3). Antes se puede
# re-fijar, porque no hay ningun numero que rastrear con ella (DECISIONS #36).
# s2-mensual-v1 se re-fijo asi el 2026-09-15:
#   - M.1.4: 328a9a778020fc0c9ce769a44436ef6d280ee65f1fcdd20eacbc8780fbb39305
#   - M.1.6: 333dedb7ab255d2b98dcf381e993038d7f38107ce07a9dc9addd801e10af767f
#            (las estadisticas pasaron a ser declarativas: tipo y percentil)
#   - M.1.7: se sumo `remuestreo`.
#            8b9790029510baee1447c3647dca6ec7542e451d8d652703857f1261b2cd5b32
#   - 2026-09-16: se sumaron `nubes_erosion_px` y `acotar_indices`, los dos en el
#     valor de la capa vieja (0 y False), asi que **ningun numero cambia**. Estan
#     para que M.2.6 compare las dos variantes cambiando la receta, en vez de
#     parchear codigo (DECISIONS #39 y #41).
HUELLAS = {
    "s2-mensual-v1": "920554f366bbdc5e45887148702c10a242baa1a0394a29a2830fe78fd2bf751f",
}

# Un cambio por campo de Receta, salvo la version. Si se suma un campo, tiene
# que sumarse aca: lo exige `test_el_control_negativo_cubre_todos_los_campos`.
CAMBIOS = {
    "coleccion": "COPERNICUS/S2_SR",
    "coleccion_nubes": "GOOGLE/CLOUD_SCORE_PLUS/V1/S2_HARMONIZED",
    "indices": ("ndvi", "evi", "ndre"),
    "estadisticas": ("mediana", "media", "min", "max", "p10", "p90"),
    "cobertura_minima": 0.31,
    "meses_historico": 25,
    "escala_m": 20,
    "remuestreo": "bilinear",
    "nubes_max_prob": 50,
    "nubes_dilatacion_m": 100,
    "sombras_nir_oscuro": 0.2,
    "sombras_distancia_m": 2000,
    "nubes_erosion_px": 2,
    "acotar_indices": True,
}


# --- La huella fijada -----------------------------------------------------


def test_la_huella_de_la_receta_vigente_esta_fijada():
    assert RECETA_VIGENTE.version in HUELLAS, "version nueva sin huella en HUELLAS"
    assert RECETA_VIGENTE.huella() == HUELLAS[RECETA_VIGENTE.version], (
        "cambio el contenido de la receta sin cambiar la version: subi la version "
        "y agrega su huella a HUELLAS"
    )


def test_dos_versiones_no_comparten_huella():
    assert len(set(HUELLAS.values())) == len(HUELLAS)


def test_la_huella_es_un_sha256_estable():
    huella = RECETA_VIGENTE.huella()
    assert len(huella) == 64
    assert set(huella) <= set("0123456789abcdef")
    assert huella == RECETA_VIGENTE.huella()


def test_la_receta_v1_es_la_decidida():
    """DECISIONS #31, los parametros de sombras de la mascara vieja (§8.7) y el
    remuestreo que GEE usa si no se le pide otro (DECISIONS #36)."""
    receta = RECETA_VIGENTE
    assert receta.version == "s2-mensual-v1"
    assert receta.coleccion == "COPERNICUS/S2_SR_HARMONIZED"
    assert receta.coleccion_nubes == "COPERNICUS/S2_CLOUD_PROBABILITY"
    assert receta.indices == ("ndvi", "evi", "ndre", "ndmi")
    assert receta.estadisticas == ("mediana", "media", "min", "max", "p10", "p90", "desvio")
    assert receta.cobertura_minima == 0.3
    assert receta.meses_historico == 24
    assert receta.escala_m == 10
    assert receta.remuestreo == "nearest"
    assert receta.nubes_max_prob == 45
    assert receta.nubes_dilatacion_m == 50
    assert receta.sombras_nir_oscuro == 0.15
    assert receta.sombras_distancia_m == 1000
    assert receta.sombras_distancia_px == 100
    # Los dos en el valor de la capa vieja: M.2.6 compara entre iguales y despues
    # el usuario elige (DECISIONS #39 y #41).
    assert receta.nubes_erosion_px == 0
    assert receta.acotar_indices is False


# --- Controles negativos: que la huella de verdad se mueva ----------------


def test_el_control_negativo_cubre_todos_los_campos():
    campos = {campo.name for campo in dataclasses.fields(RECETA_VIGENTE)}
    assert set(CAMBIOS) == campos - {"version"}


@pytest.mark.parametrize("campo", CAMBIOS)
def test_cambiar_un_parametro_cambia_la_huella(campo):
    otra = dataclasses.replace(RECETA_VIGENTE, **{campo: CAMBIOS[campo]})
    assert otra.huella() != RECETA_VIGENTE.huella()


def test_cambiar_una_formula_del_registro_cambia_la_huella(monkeypatch):
    # Sin tocar la receta: alguien "corrige" NDVI en el registro.
    ndvi = Indice("ndvi", "(NIR - RED) / (NIR + RED + 0.1)", rango=(-1.0, 1.0), tema="x")
    otros = (indice for nombre, indice in INDICES.items() if nombre != "ndvi")
    monkeypatch.setattr(modulo_receta, "INDICES", registro(ndvi, *otros))
    assert RECETA_VIGENTE.huella() != HUELLAS["s2-mensual-v1"]


def test_cambiar_un_percentil_del_registro_cambia_la_huella(monkeypatch):
    # Sin tocar la receta: alguien pasa `p10` al percentil 20 en el registro.
    # Antes de M.1.6 la huella veia solo el sufijo, no el reductor.
    p10 = Estadistica("p10", Tipo.PERCENTIL, percentil=20)
    otras = (e for nombre, e in ESTADISTICAS.items() if nombre != "p10")
    monkeypatch.setattr(modulo_receta, "ESTADISTICAS", registro(p10, *otras))
    assert RECETA_VIGENTE.huella() != HUELLAS["s2-mensual-v1"]


def test_cambiar_el_tipo_de_una_estadistica_cambia_la_huella(monkeypatch):
    # La mediana calculada como media: el nombre y la receta siguen iguales.
    mediana = Estadistica("mediana", Tipo.MEDIA)
    otras = (e for nombre, e in ESTADISTICAS.items() if nombre != "mediana")
    monkeypatch.setattr(modulo_receta, "ESTADISTICAS", registro(mediana, *otras))
    assert RECETA_VIGENTE.huella() != HUELLAS["s2-mensual-v1"]


# --- Lo que no cambia ningun numero no mueve la huella --------------------


def test_cambiar_solo_la_version_no_cambia_la_huella():
    otra = dataclasses.replace(RECETA_VIGENTE, version="s2-mensual-v2")
    assert otra.huella() == RECETA_VIGENTE.huella()


def test_reordenar_los_indices_no_cambia_la_huella():
    otra = dataclasses.replace(RECETA_VIGENTE, indices=("ndmi", "ndre", "evi", "ndvi"))
    assert otra.huella() == RECETA_VIGENTE.huella()


# --- La sombra en pixeles (M.1.7) -----------------------------------------


@pytest.mark.parametrize("escala_m,distancia_m,esperado", [
    (10, 1000, 100), (20, 1000, 50), (60, 1000, 17), (30, 100, 4), (10, 0, 0),
])
def test_la_sombra_en_pixeles_sale_de_la_escala(escala_m, distancia_m, esperado):
    receta = dataclasses.replace(
        RECETA_VIGENTE, escala_m=escala_m, sombras_distancia_m=distancia_m
    )
    assert receta.sombras_distancia_px == esperado


@pytest.mark.parametrize("escala_m", [10, 20, 30, 60, 7])
def test_la_sombra_en_pixeles_cubre_la_distancia_de_la_receta(escala_m):
    """Redondeada hacia arriba: nunca proyecta menos que lo que dice la receta, y
    como mucho un pixel mas."""
    receta = dataclasses.replace(RECETA_VIGENTE, escala_m=escala_m)
    metros = receta.sombras_distancia_px * escala_m
    assert receta.sombras_distancia_m <= metros < receta.sombras_distancia_m + escala_m


def test_a_60_m_la_sombra_ya_no_llega_a_6_km():
    """La capa vieja pasaba `1000 / 10` pixeles fijos: pedida a 60 m, como la serie
    vieja, eso eran 100 x 60 m = 6 km de sombra."""
    a_60 = dataclasses.replace(RECETA_VIGENTE, escala_m=60)
    assert a_60.sombras_distancia_px * 60 < 1100


# --- Validacion contra los registros --------------------------------------


@pytest.mark.parametrize("remuestreo", ["nearest", "bilinear", "bicubic"])
def test_los_remuestreos_de_gee_se_aceptan(remuestreo):
    otra = dataclasses.replace(RECETA_VIGENTE, remuestreo=remuestreo)
    assert otra.remuestreo == remuestreo


@pytest.mark.parametrize("campo,valor,mensaje", [
    ("version", "S2 Mensual", "versión"),
    ("version", "", "versión"),
    ("cobertura_minima", 0.0, "cobertura_minima"),
    ("cobertura_minima", 1.5, "cobertura_minima"),
    ("meses_historico", 0, "meses_historico"),
    ("escala_m", 0, "escala_m"),
    ("remuestreo", "cubic", "remuestreo"),
    ("remuestreo", "Bilinear", "remuestreo"),
    ("nubes_max_prob", 101, "nubes_max_prob"),
    ("nubes_max_prob", -1, "nubes_max_prob"),
    ("nubes_dilatacion_m", -1, "nubes_dilatacion_m"),
    # La escala de la capa vieja (0.15 * 10000): la receta va en reflectancia 0-1.
    ("sombras_nir_oscuro", 1500.0, "sombras_nir_oscuro"),
    ("sombras_distancia_m", -1, "sombras_distancia_m"),
    ("nubes_erosion_px", -1, "nubes_erosion_px"),
    ("indices", (), "al menos un"),
    ("indices", ("ndvi", "savi"), "fuera del registro"),
    ("estadisticas", ("mediana", "mediana"), "repetid"),
])
def test_valores_invalidos_se_rechazan(campo, valor, mensaje):
    with pytest.raises(ValueError, match=mensaje):
        dataclasses.replace(RECETA_VIGENTE, **{campo: valor})


def test_una_lista_en_vez_de_tupla_se_rechaza():
    with pytest.raises(TypeError, match="tuplas"):
        dataclasses.replace(RECETA_VIGENTE, indices=["ndvi"])


def test_la_receta_es_inmutable():
    with pytest.raises(dataclasses.FrozenInstanceError):
        RECETA_VIGENTE.escala_m = 20
