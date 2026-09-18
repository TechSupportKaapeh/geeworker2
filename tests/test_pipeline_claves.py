"""M.4.1: la key del COG mensual con el tenant adentro (`pipeline/claves.py`).

La forma es contrato con el tileserver, que en M.8.1 va a comparar el prefijo
contra el `tenant_id` del token (`DECISIONS #47`). Un cambio aca que no pase por
esa decision rompe A01 sin que falle nada del worker: por eso el primer test fija
la key entera, caracter por caracter.
"""
import dataclasses
import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))

from pipeline.claves import ClavesDeCapa, claves_cog_mensual, prefijo_de_tenant
from pipeline.periodos import Mes
from pipeline.receta import RECETA_VIGENTE
from services import inngest_handlers as handlers

TENANT = "7f3c2a10-5b6d-4e8f-9a01-23456789abcd"
RANCHO = "a1b2c3d4-e5f6-4a7b-8c9d-0e1f2a3b4c5d"


def _claves(**cambios):
    argumentos = {
        "tenant_id": TENANT, "rancho_id": RANCHO, "receta": RECETA_VIGENTE,
        "indice": "ndvi", "mes": Mes(2025, 9),
    }
    return claves_cog_mensual(**(argumentos | cambios))


def test_la_forma_de_la_key_queda_fijada():
    assert _claves() == ClavesDeCapa(
        storage_key=(
            "tenants/7f3c2a10-5b6d-4e8f-9a01-23456789abcd/"
            "ranchos/a1b2c3d4-e5f6-4a7b-8c9d-0e1f2a3b4c5d/"
            "s2-mensual-v1/ndvi/2025-09.tif"
        ),
        natural_key="rancho_mensual_ndvi_a1b2c3d4-e5f6-4a7b-8c9d-0e1f2a3b4c5d_2025-09",
    )


def test_la_key_empieza_con_el_prefijo_del_tenant_con_su_barra():
    """Es la comparacion que va a hacer TiTiler en M.8.1."""
    prefijo = prefijo_de_tenant(TENANT)
    assert prefijo == f"tenants/{TENANT}/"
    assert _claves().storage_key.startswith(prefijo)


def test_otro_tenant_no_comparte_el_prefijo():
    otro = "7f3c2a10-5b6d-4e8f-9a01-23456789abce"
    assert not _claves(tenant_id=otro).storage_key.startswith(prefijo_de_tenant(TENANT))


@pytest.mark.parametrize("forma", [
    str.upper,
    lambda u: u.replace("-", ""),
    lambda u: "{" + u + "}",
    lambda u: "urn:uuid:" + u,
])
def test_los_ids_salen_en_forma_canonica(forma):
    """Geocore escribe el tenant del token en minusculas y con guiones.

    Una key con `7F3C…` y un token con `7f3c…` serian dos tenants distintos para
    un `startswith`, y el dueno del COG recibiria 403.
    """
    assert _claves(tenant_id=forma(TENANT), rancho_id=forma(RANCHO)) == _claves()


@pytest.mark.parametrize("campo", ["tenant_id", "rancho_id"])
@pytest.mark.parametrize("valor", [
    "", "no-es-un-uuid", "../otro-tenant", f"{TENANT}/../x", "a/b",
    "00000000-0000-0000-0000-000000000000",
])
def test_rechaza_ids_que_no_son_un_uuid_o_son_el_nulo(campo, valor):
    """El id viene de un evento: uno que armara otra ruta escribiria fuera del tenant."""
    with pytest.raises(ValueError, match=campo):
        _claves(**{campo: valor})


def test_rechaza_un_id_que_no_es_texto():
    with pytest.raises(TypeError, match="tenant_id"):
        _claves(tenant_id=12345)


def test_rechaza_un_indice_que_la_receta_no_calcula():
    with pytest.raises(ValueError, match="savi"):
        _claves(indice="savi")


def test_los_ids_van_por_nombre():
    """`tenant_id` y `rancho_id` son los dos texto: intercambiarlos no puede ser posible."""
    with pytest.raises(TypeError):
        claves_cog_mensual(TENANT, RANCHO, RECETA_VIGENTE, "ndvi", Mes(2025, 9))


def test_otra_receta_cambia_la_key_pero_no_la_natural_key():
    """La key cambia para que la URL del tile cambie: el cache es inmutable por un ano.

    La natural_key no: la fila de `layers` es una por rancho, indice y mes, y el
    reproceso la apunta a la key nueva.
    """
    v2 = dataclasses.replace(RECETA_VIGENTE, version="s2-mensual-v2")
    assert _claves(receta=v2).storage_key != _claves().storage_key
    assert "/s2-mensual-v2/" in _claves(receta=v2).storage_key
    assert _claves(receta=v2).natural_key == _claves().natural_key


def test_cada_mes_e_indice_es_otra_capa():
    base = _claves()
    for otra in (_claves(mes=Mes(2025, 10)), _claves(indice="evi")):
        assert otra.storage_key != base.storage_key
        assert otra.natural_key != base.natural_key


def test_la_natural_key_no_choca_con_la_de_la_capa_vieja():
    """La capa vieja usa `rancho_{indice}_{id}_{AAAA-MM-DD}` y sus filas siguen en `layers`."""
    _, vieja = handlers.claves_de_capa("rancho", RANCHO, "ndvi", "2025-09-01")
    assert vieja != _claves().natural_key
    assert "/" not in _claves().natural_key
