"""M.4.5: `process_rancho` sobre el pipeline mensual (`handlers/rancho.py`).

Sin GEE, sin bucket y sin base, pero con lo mas posible de verdad:

- las estadisticas pasan por el borde (`ejecucion.traer`) y por `leer`, como en
  `test_handlers_parcela.py`;
- la URL sale de `ejecucion.url_de_descarga`, que le pide `getDownloadURL` a una
  imagen falsa;
- la "descarga" escribe **un GeoTIFF de verdad**, con el centinela donde GEE
  tendria lo enmascarado, y `convert_to_cog` corre con `rio-cogeo`. Asi el test
  ve la mascara del COG que se sube.
"""
import subprocess
import sys
from datetime import UTC, date, datetime
from pathlib import Path

import inngest
import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))

from handlers import altas, rancho, raster
from pipeline import ejecucion
from pipeline.estadisticas import claves_de_salida
from pipeline.receta import RECETA_VIGENTE

INDICES = list(RECETA_VIGENTE.indices)
from handlers import registro as inngest_handlers
from repositories import db_repository
from services import avance_job

HOY = date(2026, 9, 18)
MESES_V1 = [f"{2024 + (8 + i) // 12}-{(8 + i) % 12 + 1:02d}" for i in range(24)]
TENANT = "7d0c1b8a-3e2f-4a5b-8c6d-9e0f1a2b3c4d"
RANCHO = "5e1d2c3b-4a59-4687-9a0b-1c2d3e4f5a6b"
PAYLOAD = {"JobId": "job-r", "RanchoId": RANCHO, "TenantId": TENANT,
           "Coordinates": [{"lat": 20.5, "lng": -101.2}]}


class _Interrupcion(BaseException):
    """Lo que hace el SDK con el error de un step: un BaseException."""


class _Step:
    def __init__(self):
        self.ejecutados = []

    def run(self, nombre, funcion, *args):
        self.ejecutados.append(nombre)
        try:
            return funcion(*args)
        except (inngest.NonRetriableError, inngest.RetryAfterError):
            raise
        except Exception as e:
            raise _Interrupcion(e) from e


class _Ctx:
    def __init__(self, data, attempt=0):
        self.event = type("Evento", (), {"data": data})()
        self.attempt = attempt


def _respuesta_de_gee(cobertura=0.8):
    claves = claves_de_salida(RECETA_VIGENTE.indices, RECETA_VIGENTE.estadisticas)
    respuesta = {clave: (0.55 if est == "mediana" else 0.1) for (_, est), clave in claves.items()}
    respuesta.update(cobertura=cobertura, observaciones=3.0)
    return respuesta


class _Expresion:
    def __init__(self, contestar):
        self._contestar = contestar

    def getInfo(self):  # el nombre que usa ee
        return self._contestar()


class _Imagen:
    """La imagen del mes: registra el `unmask` y contesta `getDownloadURL`."""

    def __init__(self, mundo, mes):
        self.mundo, self.mes = mundo, mes

    def unmask(self, valor, sameFootprint=True):  # los nombres de ee
        self.mundo["unmask"].append((valor, sameFootprint))
        return self

    def getDownloadURL(self, parametros):  # el nombre de ee
        self.mundo["parametros"].append(parametros)
        return self.mundo["url"](self.mes)


def _geotiff_como_el_de_gee(destino):
    """4x4 en EPSG:4326, con la mitad en el centinela: lo que baja de GEE tras el `unmask`."""
    datos = np.full((4, 4), 0.6, dtype="float32")
    datos[:2, :] = rancho.NODATA_COG
    perfil = {"driver": "GTiff", "height": 4, "width": 4, "count": 1, "dtype": "float32",
              "crs": "EPSG:4326", "transform": from_origin(-101.2, 20.5, 0.0001, 0.0001)}
    with rasterio.open(destino, "w", **perfil) as salida:
        salida.write(datos, 1)
    return destino


@pytest.fixture
def mundo(monkeypatch):
    estado = {"pedidos": [], "unmask": [], "parametros": [], "descargas": [], "subidas": [],
              "capas": [], "bitacora": [], "jobs": [],
              "gee": lambda mes: _respuesta_de_gee(), "url": lambda mes: f"https://gee/{mes}"}

    def _estadisticas_de(roi, ventana, receta):
        # `ventana.etiqueta` de una ventana mensual es el mismo `AAAA-MM` que antes
        # era `str(mes)`: lo que el doble graba no cambia con M.9.0b.
        estado["pedidos"].append(ventana.etiqueta)
        return _Expresion(lambda: estado["gee"](ventana.etiqueta))

    def _descargar(url, destino):
        estado["descargas"].append((url, destino))
        return _geotiff_como_el_de_gee(destino)

    class _Storage:
        def upload_file(self, key, ruta, tipo):
            with rasterio.open(ruta) as cog:
                datos = cog.read(1, masked=True)
                estado["subidas"].append({"key": key, "ruta": ruta, "tipo": tipo,
                                          "enmascarados": int(np.ma.count_masked(datos)),
                                          "min": float(datos.min()), "total": datos.size})
            return key

    def _registrar(job_id, attempt, stage, level, message, detail=None, progress=None):
        estado["bitacora"].append({"etapa": stage, "nivel": level, "mensaje": message,
                                   "detalle": detail or {}, "progreso": progress})

    monkeypatch.setattr(ejecucion, "estadisticas_de", _estadisticas_de)
    monkeypatch.setattr(rancho, "mapa_de", lambda roi, mes, receta, indice: _Imagen(estado, str(mes)))
    # La descarga y la subida viven en `handlers/raster.py` desde M.6.2b: las
    # comparte con el mapa a demanda.
    monkeypatch.setattr(raster, "descargar_a_archivo", _descargar)
    monkeypatch.setattr(raster, "get_storage_service", _Storage)
    monkeypatch.setattr(rancho, "insert_layer", lambda **kw: estado["capas"].append(kw))
    monkeypatch.setattr(rancho, "init_ee", lambda: None)
    monkeypatch.setattr(rancho, "coords_to_geometry", lambda c: "roi")
    monkeypatch.setattr(altas, "hoy_utc", lambda: HOY)
    monkeypatch.setattr(avance_job, "registrar_evento_job", _registrar)
    monkeypatch.setattr(db_repository, "update_processing_job",
                        lambda job_id, status, **kw: estado["jobs"].append((status, kw)))
    return estado


def _correr(step, payload=PAYLOAD, attempt=0):
    return rancho.process_rancho._handler(_Ctx(payload, attempt), step)


# --- 1. Los steps y lo que escribe cada mes -------------------------------------


def test_un_plan_y_un_mapa_por_mes(mundo):
    step = _Step()
    resultado = _correr(step)

    assert step.ejecutados == [
        "mark-job-running", "plan", *[f"mes-{m}" for m in MESES_V1], "mark-job-completed",
    ]
    # Un mapa por indice y por mes (decision del usuario, 2026-09-20).
    assert resultado == {"status": "success", "receta": "s2-mensual-v1", "meses": 24,
                         "mapas": 24 * len(INDICES)}
    assert [s for s, _ in mundo["jobs"]] == ["running", "completed"]
    progresos = [l["progreso"] for l in mundo["bitacora"] if l["progreso"] is not None]
    assert progresos == sorted(progresos) and progresos[-1] == 100


def test_el_mapa_es_de_todos_los_indices_de_la_receta(mundo):
    """Decision del usuario (2026-09-20): antes era solo NDVI.

    Se afirma contra `RECETA_VIGENTE.indices` y no contra una lista escrita a mano:
    sumar un indice a la receta tiene que sumar su mapa, no dejarlo afuera en silencio.
    """
    from handlers.rancho import indices_del_mapa

    assert indices_del_mapa(RECETA_VIGENTE) == RECETA_VIGENTE.indices
    assert len(RECETA_VIGENTE.indices) == 4


def test_cada_capa_lleva_las_estadisticas_de_SU_indice(mundo):
    """Si todas copiaran las del NDVI, el mapa de NDMI mentiria en su ficha."""
    _correr(_Step())

    delMes = [c for c in mundo["capas"] if c["natural_key"].endswith("_2025-03")]
    porIndice = {c["product"]: c["estadisticas"] for c in delMes}
    # `_respuesta_de_gee` da la misma mediana a todos los indices, asi que se compara
    # la clave que los distingue: cada capa trae la entrada de su propio indice.
    assert set(porIndice) == set(RECETA_VIGENTE.indices)
    for estadisticas in porIndice.values():
        assert set(estadisticas) == {*RECETA_VIGENTE.estadisticas, "cobertura", "observaciones"}


def test_la_key_es_la_de_claves_cog_mensual(mundo):
    """`tenants/{t}/ranchos/{r}/{receta}/{indice}/{AAAA-MM}.tif` (`DECISIONS #47`), nunca a mano."""
    _correr(_Step())

    # El indice va en la key: cuatro mapas por mes, ninguno pisa a otro.
    assert [s["key"] for s in mundo["subidas"]] == [
        f"tenants/{TENANT}/ranchos/{RANCHO}/s2-mensual-v1/{i}/{m}.tif"
        for m in MESES_V1 for i in INDICES
    ]
    assert {s["tipo"] for s in mundo["subidas"]} == {"image/tiff"}


def test_cada_mes_escribe_una_capa_por_indice(mundo):
    _correr(_Step())

    assert len(mundo["capas"]) == 24 * len(INDICES)
    delMes = [c for c in mundo["capas"] if c["natural_key"].endswith("_2025-03")]
    assert [c["product"] for c in delMes] == INDICES, "uno por indice, en el orden de la receta"

    capa = delMes[INDICES.index("ndvi")]
    assert capa["natural_key"] == f"rancho_mensual_ndvi_{RANCHO}_2025-03"
    assert capa["storage_key"].endswith("/ndvi/2025-03.tif")
    assert capa["acquired_ts"] == datetime(2025, 3, 1, tzinfo=UTC)
    assert capa["source"] == "mensual"
    assert capa["receta"] == "s2-mensual-v1"
    assert capa["product"] == "ndvi"
    assert capa["rancho_id"] == RANCHO and capa["tenant_id"] == TENANT
    assert "parcela_id" not in capa
    assert len(capa["bbox"]) == 4
    # Los números del mapa (D-2): los del índice, con la cobertura y las observaciones.
    assert capa["estadisticas"]["mediana"] == 0.55
    assert capa["estadisticas"]["cobertura"] == 0.8
    assert capa["estadisticas"]["observaciones"] == 3.0
    assert set(capa["estadisticas"]) == {*RECETA_VIGENTE.estadisticas, "cobertura", "observaciones"}


# --- 2. El COG ----------------------------------------------------------------


def test_lo_enmascarado_no_se_sube_como_ndvi_cero(mundo):
    """El GeoTIFF de GEE no declara nodata: lo enmascarado llegaria como 0.

    Se rellena con el centinela (`unmask`, tambien fuera del poligono) y el COG
    lo convierte en su mascara. Sin eso, una nube se pinta como suelo desnudo.
    """
    _correr(_Step())

    assert set(mundo["unmask"]) == {(rancho.NODATA_COG, False)}
    subida = mundo["subidas"][0]
    assert subida["enmascarados"] > 0, "el centinela tiene que quedar como mascara"
    assert subida["min"] == pytest.approx(0.6), "y ningun pixel valido vale el centinela"


def test_la_descarga_va_a_la_escala_de_la_receta_y_por_el_borde(mundo):
    _correr(_Step())

    assert {p["scale"] for p in mundo["parametros"]} == {RECETA_VIGENTE.escala_m}
    assert {p["crs"] for p in mundo["parametros"]} == {"EPSG:4326"}
    # Las estadisticas (una) mas una URL por indice. Las cuenta el borde, y es el
    # numero que sube al sumar mapas: conviene verlo en un test y no en la factura.
    meses = [l for l in mundo["bitacora"] if l["etapa"].startswith("mes-")]
    assert {l["detalle"]["llamadas"] for l in meses} == {1 + len(INDICES)}
    assert {l["detalle"]["mapas"] for l in meses} == {len(INDICES)}


def test_los_temporales_no_quedan_en_disco(mundo):
    _correr(_Step())

    for _, crudo in mundo["descargas"]:
        assert not Path(crudo).exists()
    for subida in mundo["subidas"]:
        assert not Path(subida["ruta"]).exists()


# --- 3. El mes sin dato ------------------------------------------------------


def test_un_mes_sin_un_pixel_limpio_no_tiene_mapa(mundo):
    """Decision del usuario (2026-09-18): con cobertura 0 no se baja ni se sube nada."""
    mundo["gee"] = lambda mes: {"cobertura": 0} if mes == "2026-05" else _respuesta_de_gee()

    resultado = _correr(_Step())

    assert resultado["mapas"] == 23 * len(INDICES)
    assert not any(s["key"].endswith("2026-05.tif") for s in mundo["subidas"])
    assert not any(c["natural_key"].endswith("2026-05") for c in mundo["capas"])
    (linea,) = [l for l in mundo["bitacora"] if l["etapa"] == "mes-2026-05"]
    assert linea["nivel"] == "warning" and "sin mapa" in linea["mensaje"]
    assert linea["detalle"]["llamadas"] == 1  # ni siquiera se pidio la URL


def test_un_mes_bajo_el_minimo_igual_tiene_mapa(mundo):
    """Con cualquier cobertura mayor que 0 el mapa va: muestra lo que se vio."""
    mundo["gee"] = lambda mes: _respuesta_de_gee(cobertura=0.1)

    resultado = _correr(_Step())

    assert resultado["mapas"] == 24 * len(INDICES)
    assert {c["estadisticas"]["cobertura"] for c in mundo["capas"]} == {0.1}


# --- 4. Los errores ----------------------------------------------------------


def test_un_rancho_que_no_entra_en_una_descarga_falla_sin_reintentos(mundo):
    """El tope de `getDownloadURL`: reintentarlo da lo mismo (`DECISIONS #51`)."""
    def _grande(mes):
        raise RuntimeError("Total request size (61234567 bytes) must be less than or "
                           "equal to 50331648 bytes.")
    mundo["url"] = _grande
    step = _Step()

    with pytest.raises(inngest.NonRetriableError, match="2024-09"):
        _correr(step, attempt=0)

    assert step.ejecutados[-1] == "mark-job-failed"
    assert [s for s, _ in mundo["jobs"]] == ["running", "failed"]
    assert mundo["subidas"] == [] and mundo["capas"] == []


def test_un_error_pasajero_se_reintenta(mundo):
    def _ocupado(mes):
        raise RuntimeError("Too many concurrent aggregations.")
    mundo["url"] = _ocupado

    with pytest.raises(_Interrupcion):
        _correr(_Step(), attempt=0)

    assert [s for s, _ in mundo["jobs"]] == ["running"]


@pytest.mark.parametrize("campo,valor", [("RanchoId", "no-es-un-uuid"),
                                         ("TenantId", "00000000-0000-0000-0000-000000000000")])
def test_un_id_que_no_es_un_uuid_falla_sin_pedirle_nada_a_gee(mundo, campo, valor):
    """La key no se puede armar: reintentar no cambia el id."""
    with pytest.raises(inngest.NonRetriableError, match="ids válidos"):
        _correr(_Step(), payload={**PAYLOAD, campo: valor})

    assert mundo["pedidos"] == []
    assert [s for s, _ in mundo["jobs"]] == ["running", "failed"]


@pytest.mark.parametrize("falta", ["RanchoId", "TenantId", "Coordinates"])
def test_un_evento_incompleto_falla_sin_reintentos(mundo, falta):
    payload = {k: v for k, v in PAYLOAD.items() if k != falta}

    with pytest.raises(inngest.NonRetriableError):
        _correr(_Step(), payload=payload)

    assert mundo["pedidos"] == []


# --- 5. El registro -----------------------------------------------------------


def test_el_alta_de_rancho_registrada_es_la_del_pipeline():
    ids = [f.id for f in inngest_handlers.all_functions]
    assert ids.count("geeworker-process-rancho") == 1
    assert rancho.process_rancho in inngest_handlers.all_functions
    # `register_layer` escuchaba `terra/raster.ingested`, que ya no emite nadie.
    assert "geeworker-register-layer" not in ids
    assert not hasattr(inngest_handlers, "register_layer")


def test_el_handler_no_importa_la_capa_vieja_ni_abre_conexiones():
    codigo = (
        "import socket, sys\n"
        "def prohibido(*a, **k):\n"
        "    raise AssertionError('se intento abrir una conexion')\n"
        "socket.socket.connect = prohibido\n"
        "socket.create_connection = prohibido\n"
        "socket.getaddrinfo = prohibido\n"
        "import handlers.rancho\n"
        "assert 'services.inngest_handlers' not in sys.modules, 'importo la capa vieja'\n"
    )
    resultado = subprocess.run(
        [sys.executable, "-c", codigo], cwd=str(RAIZ),
        capture_output=True, text=True, timeout=120, check=False,
    )
    assert resultado.returncode == 0, resultado.stderr
