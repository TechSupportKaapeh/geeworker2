"""La cuenta que decide la mascara del raster por pasada (M.9.7a).

`scripts/check_pipeline_real.py --mascaras` compara tres mascaras contra "lo que
dicen las pasadas despejadas". Esa referencia es de la clase de codigo que no
falla, miente: una ventana mal armada o una pasada que se compara contra si misma
hace empatar a las tres mascaras y la decision sale por defecto. Estos tests fijan
la cuenta, sin GEE.
"""

import importlib.util
from pathlib import Path

_RUTA = Path(__file__).resolve().parent.parent / "scripts" / "check_pipeline_real.py"
_spec = importlib.util.spec_from_file_location("check_pipeline_real", _RUTA)
check = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(check)


def fila(fecha, **variantes):
    """Una pasada: `receta=(cob, val)`, etc. Lo que no se pasa, tapado."""
    base = {"fecha": fecha}
    for v in check.VARIANTES_DE_MASCARA:
        cob, val = variantes.get(v, (0.0, None))
        base[f"cob_{v}"] = cob
        base[f"val_{v}"] = val
    return base


def despejada(fecha, valor):
    """Una pasada que las dos mascaras dejan entera: de consenso."""
    return fila(fecha, receta=(1.0, valor), cloudscore=(1.0, valor), ambas=(1.0, valor))


def test_una_pasada_no_se_compara_contra_si_misma():
    # Si se contara, cada pasada de consenso daria apartamiento 0 y las tres
    # mascaras empatarian por construccion.
    filas = [despejada("2025-07-01T15:32Z", 0.50), despejada("2025-07-10T15:32Z", 0.60)]
    refs = check._referencias(filas)
    assert refs["2025-07-01T15:32Z"] == 0.60
    assert refs["2025-07-10T15:32Z"] == 0.50


def test_la_referencia_es_la_mediana_de_las_despejadas_cercanas():
    filas = [
        despejada("2025-07-01T15:32Z", 0.50),
        despejada("2025-07-05T15:32Z", 0.56),
        despejada("2025-07-09T15:32Z", 0.58),
        fila("2025-07-07T15:32Z", receta=(0.7, 0.40)),
    ]
    assert check._referencias(filas)["2025-07-07T15:32Z"] == 0.56


def test_una_despejada_lejos_no_cuenta():
    # A mas de 16 dias el cultivo ya cambio: no sirve de referencia.
    filas = [despejada("2025-06-01T15:32Z", 0.30), fila("2025-07-20T15:32Z", receta=(0.7, 0.60))]
    assert check._referencias(filas)["2025-07-20T15:32Z"] is None


def test_una_pasada_que_solo_una_mascara_deja_entera_no_es_referencia():
    # El consenso es de las dos: si Cloud Score+ la ve tapada, no es "despejada".
    filas = [
        fila("2025-07-01T15:32Z", receta=(1.0, 0.30), cloudscore=(0.4, 0.60)),
        fila("2025-07-05T15:32Z", receta=(0.7, 0.55)),
    ]
    assert check._referencias(filas)["2025-07-05T15:32Z"] is None


def test_el_resumen_cuenta_utiles_comparadas_y_malas():
    filas = [
        despejada("2025-07-01T15:32Z", 0.56),
        fila("2025-07-14T15:42Z", receta=(0.72, 0.47), ambas=(0.61, 0.58)),
        fila("2025-07-11T15:32Z", receta=(0.14, 0.24)),  # bajo el minimo: no es util
    ]
    refs = check._referencias(filas)
    receta = check._resumen_de_variante(filas, refs, "receta", 0.3)
    ambas = check._resumen_de_variante(filas, refs, "ambas", 0.3)
    assert receta["utiles"] == 2 and ambas["utiles"] == 2
    # La despejada del 1 no tiene otra despejada cerca: se cuenta util pero no se compara.
    assert receta["comparadas"] == 1
    assert receta["malas"] == 0  # 0,47 contra 0,56: se aparta 0,09
    assert round(ambas["apartamientos"][0], 3) == 0.02


def test_una_pasada_que_se_aparta_mas_de_una_decima_es_mala():
    filas = [despejada("2025-07-01T15:32Z", 0.56), fila("2025-07-05T15:32Z", receta=(0.5, 0.40))]
    refs = check._referencias(filas)
    assert check._resumen_de_variante(filas, refs, "receta", 0.3)["malas"] == 1


def test_unir_variantes_no_pierde_la_pasada_que_una_mascara_no_trae():
    respuesta = {
        "receta": [{"fecha": "2025-07-14T15:42Z", "cobertura": 0.72, "valor": 0.47}],
        "cloudscore": [],
        "ambas": [{"fecha": "2025-07-14T15:42Z", "cobertura": 0.61}],  # sin valor: tapada
    }
    (f,) = check._unir_variantes(respuesta)
    assert f["cob_cloudscore"] == 0.0 and f["val_cloudscore"] is None
    assert f["val_ambas"] is None and f["cob_ambas"] == 0.61
