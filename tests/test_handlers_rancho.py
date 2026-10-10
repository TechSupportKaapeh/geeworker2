"""M.4.5: `process_rancho` sobre el pipeline mensual (`handlers/rancho.py`).

Sin GEE, sin bucket y sin base, pero con lo mas posible de verdad:

- las estadisticas pasan por el borde (`ejecucion.traer`) y por `leer`, como en
  `test_handlers_parcela.py`;
- la URL sale de `ejecucion.url_de_descarga`, que le pide `getDownloadURL` a una
  imagen falsa;
- la "descarga" escribe **un GeoTIFF de verdad**, con el centinela donde GEE
  tendria lo enmascarado, y `convert_to_cog` corre con `rio-cogeo`. Asi el test
  ve la mascara del COG que se sube.

Desde M.9.7b (`DECISIONS #73`) el COG del mes es **multibanda**: un archivo con
los cuatro indices como bandas, en enteros por 10.000, y una fila de `layers` por
indice que dice que banda es.
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

from handlers import rancho as handlers_rancho
from pipeline.receta import RECETA_MENSUAL_V1

from handlers import altas, rancho, raster
from pipeline import ejecucion
from pipeline.estadisticas import claves_de_salida
# Estos tests fijan la orquestacion MENSUAL y clavan v1 (ver `mundo`): la lista de
# indices tiene que ser la de v1, no la de la vigente. Hasta M.9.3 coincidian por
# casualidad (cuatro y cuatro); desde v4 la vigente tiene seis.
from pipeline.receta import RECETA_MENSUAL_V1 as RECETA_VIGENTE

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
    # Las claves de los seis indices del registro, y no las de la receta clavada (v1):
    # el test de v4 pide tambien las de SAVI y LAI, y las de mas no molestan.
    from pipeline.indices import INDICES as REGISTRO

    claves = claves_de_salida(list(REGISTRO), RECETA_VIGENTE.estadisticas)
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
    """4x4 en EPSG:4326, una banda por indice en enteros, con la mitad en el centinela.

    Es lo que baja de GEE tras el `unmask` del mapa multibanda: un NDVI de 0,6 guardado
    como 6000, en las cuatro bandas.
    """
    datos = np.full((len(INDICES), 4, 4), 6000, dtype="int16")
    datos[:, :2, :] = raster.NODATA_ENTERO
    perfil = {"driver": "GTiff", "height": 4, "width": 4, "count": len(INDICES), "dtype": "int16",
              "crs": "EPSG:4326", "transform": from_origin(-101.2, 20.5, 0.0001, 0.0001)}
    with rasterio.open(destino, "w", **perfil) as salida:
        salida.write(datos)
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

    def _estadisticas_de_ventanas(roi, ventanas, receta):
        # Desde M.9.7e2 el rancho pide el mes y sus pasadas en UN pedido
        # (`reducciones_de`); con v1 y v2 es una lista de una ventana.
        expresiones = [_estadisticas_de(roi, ventana, receta) for ventana in ventanas]
        return _Expresion(lambda: [e.getInfo() for e in expresiones])

    def _descargar(url, destino):
        estado["descargas"].append((url, destino))
        return _geotiff_como_el_de_gee(destino)

    class _Storage:
        def upload_file(self, key, ruta, tipo):
            with rasterio.open(ruta) as cog:
                datos = cog.read(1, masked=True)
                estado["subidas"].append({"key": key, "ruta": ruta, "tipo": tipo,
                                          "bandas": cog.count, "dtype": cog.dtypes[0],
                                          "enmascarados": int(np.ma.count_masked(datos)),
                                          "min": float(datos.min()), "total": datos.size})
            return key

    def _registrar(job_id, attempt, stage, level, message, detail=None, progress=None):
        estado["bitacora"].append({"etapa": stage, "nivel": level, "mensaje": message,
                                   "detalle": detail or {}, "progreso": progress})

    # **Estos tests fijan la orquestacion MENSUAL, y por eso clavan v1.** Desde el
    # 2026-09-25 la receta vigente es `s2-pasada-v2` (`DECISIONS #70`), que parte el
    # mes en una ventana por pasada: el mismo mes escribe N x 4 filas y cuesta N + 1
    # llamadas a GEE. Lo que estos tests prueban —el plan, un step por mes, la
    # bitacora, la barra— no cambia con eso, y clavando v1 se sigue leyendo cuanto
    # cuesta UN mes. La orquestacion por pasada tiene sus propios tests abajo.
    monkeypatch.setattr(handlers_rancho, "RECETA_VIGENTE", RECETA_MENSUAL_V1)
    monkeypatch.setattr(ejecucion, "estadisticas_de", _estadisticas_de)
    monkeypatch.setattr(ejecucion, "estadisticas_de_ventanas", _estadisticas_de_ventanas)
    def _mapa_multibanda(roi, ventana, receta, indices):
        estado.setdefault("indices_pedidos", []).append(tuple(indices))
        return _Imagen(estado, str(ventana.etiqueta))

    monkeypatch.setattr(rancho, "mapa_multibanda_de", _mapa_multibanda)
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


def test_con_la_vigente_el_mapa_lleva_los_seis_indices():
    """La vigente de verdad (v4, M.9.3): los seis indices, SAVI y LAI detras."""
    from handlers.rancho import indices_del_mapa
    from pipeline import receta

    assert indices_del_mapa(receta.RECETA_VIGENTE) == ("ndvi", "evi", "ndre", "ndmi", "savi", "lai")


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
    """`tenants/{t}/ranchos/{r}/{receta}/{AAAA-MM}.tif` (`DECISIONS #73`), nunca a mano.

    Desde M.9.7b el archivo es multibanda: **uno por mes**, sin el indice en la key.
    """
    _correr(_Step())

    assert [s["key"] for s in mundo["subidas"]] == [
        f"tenants/{TENANT}/ranchos/{RANCHO}/s2-mensual-v1/{m}.tif" for m in MESES_V1
    ]
    assert {s["tipo"] for s in mundo["subidas"]} == {"image/tiff"}


def test_el_archivo_trae_los_indices_como_bandas_de_enteros(mundo):
    """M.9.7b: las bandas en el orden de la receta, que es el `bidx` de cada fila."""
    _correr(_Step())

    assert set(mundo["indices_pedidos"]) == {tuple(INDICES)}
    assert {s["bandas"] for s in mundo["subidas"]} == {len(INDICES)}
    assert {s["dtype"] for s in mundo["subidas"]} == {"int16"}


def test_cada_mes_escribe_una_capa_por_indice(mundo):
    _correr(_Step())

    assert len(mundo["capas"]) == 24 * len(INDICES)
    delMes = [c for c in mundo["capas"] if c["natural_key"].endswith("_2025-03")]
    assert [c["product"] for c in delMes] == INDICES, "uno por indice, en el orden de la receta"

    capa = delMes[INDICES.index("ndvi")]
    # La natural_key no cambio con el formato: reprocesar pisa las mismas filas.
    assert capa["natural_key"] == f"rancho_mensual_ndvi_{RANCHO}_2025-03"
    assert capa["storage_key"].endswith("/s2-mensual-v1/2025-03.tif")
    # Las cuatro filas del mes apuntan al mismo archivo, cada una con su banda.
    assert {c["storage_key"] for c in delMes} == {capa["storage_key"]}
    assert [c["bandas"] for c in delMes] == [[i + 1] for i in range(len(INDICES))]
    assert {c["escala"] for c in delMes} == {10_000}
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

    assert set(mundo["unmask"]) == {(raster.NODATA_ENTERO, False)}
    subida = mundo["subidas"][0]
    assert subida["enmascarados"] > 0, "el centinela tiene que quedar como mascara"
    assert subida["min"] == 6000, "y ningun pixel valido vale el centinela"


def test_la_descarga_va_a_la_escala_de_la_receta_y_por_el_borde(mundo):
    _correr(_Step())

    assert {p["scale"] for p in mundo["parametros"]} == {RECETA_VIGENTE.escala_m}
    assert {p["crs"] for p in mundo["parametros"]} == {"EPSG:4326"}
    # Las estadisticas (una) mas **una** URL: desde M.9.7b los cuatro indices bajan
    # juntos. Antes eran una por indice; es el ahorro del formato, y se ve aca.
    meses = [l for l in mundo["bitacora"] if l["etapa"].startswith("mes-")]
    assert {l["detalle"]["llamadas"] for l in meses} == {2}
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


# --- M.9.7e2: el rancho por pasada con v3 (`DECISIONS #77`) ----------------------


def _mes_v3(monkeypatch, mundo, coberturas, *, cobertura_del_mes=0.8, receta=None):
    """Un mes de v3 con una pasada por cobertura; una cobertura 0 contesta como una
    pasada tapada. Devuelve el resultado del step y las fechas de las pasadas."""
    from pipeline.periodos import Mes
    from pipeline.receta import RECETA_PASADA_V3

    pasadas = tuple(datetime(2026, 8, 3 + 10 * i, 17, 57, 35, tzinfo=UTC)
                    for i in range(len(coberturas)))
    por_etiqueta = {"2026-08": cobertura_del_mes}
    por_etiqueta |= {f"2026-08-{3 + 10 * i:02d}T1757Z": c for i, c in enumerate(coberturas)}
    monkeypatch.setattr(ejecucion, "fechas_de", lambda roi, pedido, receta: pasadas)
    mundo["gee"] = lambda etiqueta: (
        {"cobertura": 0} if por_etiqueta[etiqueta] == 0
        else _respuesta_de_gee(cobertura=por_etiqueta[etiqueta])
    )
    with avance_job.seguimiento("job-r", 0, 3):
        resultado = rancho.procesar_mes(
            rancho_id=RANCHO, tenant_id=TENANT, coordenadas=PAYLOAD["Coordinates"],
            mes=Mes(2026, 8), posicion=1, total=1, receta=receta or RECETA_PASADA_V3,
        )
    return resultado, pasadas


def test_v3_sube_el_mes_y_cada_pasada_con_algun_pixel(monkeypatch, mundo):
    """d37: toda pasada con al menos un pixel despejado. d39: el mensual, al lado."""
    resultado, _ = _mes_v3(monkeypatch, mundo, [0.9, 0, 0.02])

    base = f"tenants/{TENANT}/ranchos/{RANCHO}/s2-pasada-v3"
    assert sorted(s["key"] for s in mundo["subidas"]) == sorted([
        f"{base}/2026-08.tif", f"{base}/2026-08-03T1757Z.tif", f"{base}/2026-08-23T1757Z.tif",
    ])
    # La tapada (13) no se sube. Cinco filas por archivo: cuatro indices y el color.
    assert resultado["mapas"] == 3 * 5
    assert len(mundo["capas"]) == 15


def test_v3_pide_los_numeros_del_mes_y_de_las_pasadas_en_una_llamada(monkeypatch, mundo):
    _mes_v3(monkeypatch, mundo, [0.9, 0, 0.02])

    assert mundo["pedidos"] == ["2026-08", "2026-08-03T1757Z", "2026-08-13T1757Z",
                                "2026-08-23T1757Z"]
    (linea,) = [l for l in mundo["bitacora"] if l["etapa"] == "mes-2026-08"]
    # Una de numeros y una URL por archivo, contadas aunque se pidan desde los
    # hilos (`en_paralelo` copia el contexto). `fechas_de` es un doble y no cuenta.
    assert linea["detalle"]["llamadas"] == 1 + 3
    assert linea["mensaje"].endswith("; 2 de 3 pasadas con mapa")


def test_v3_cada_fila_dice_su_fuente_su_instante_y_sus_bandas(monkeypatch, mundo):
    _, pasadas = _mes_v3(monkeypatch, mundo, [0.9])

    mensual = [c for c in mundo["capas"] if c["source"] == "mensual"]
    pasada = [c for c in mundo["capas"] if c["source"] == "pasada"]
    assert [c["product"] for c in pasada] == ["ndvi", "evi", "ndre", "ndmi", "rgb"]
    assert [c["bandas"] for c in pasada] == [[1], [2], [3], [4], [5, 6, 7]]
    # El instante de la pasada: el mapa del panel filtra por esto (M.9.7c).
    assert {c["acquired_ts"] for c in pasada} == {pasadas[0]}
    assert {c["acquired_ts"] for c in mensual} == {datetime(2026, 8, 1, tzinfo=UTC)}
    assert {c["escala"] for c in mundo["capas"]} == {10_000}
    # El color real no tiene estadisticas de indice: solo las de la ventana.
    (rgb,) = [c for c in pasada if c["product"] == "rgb"]
    assert set(rgb["estadisticas"]) == {"cobertura", "observaciones"}
    # Las natural_key no chocan entre el mensual y la pasada.
    assert len({c["natural_key"] for c in mundo["capas"]}) == len(mundo["capas"])


def test_v3_pide_el_color_real_en_el_archivo(monkeypatch, mundo):
    _mes_v3(monkeypatch, mundo, [0.9])

    assert set(mundo["indices_pedidos"]) == {
        ("ndvi", "evi", "ndre", "ndmi", "rojo", "verde", "azul"),
    }


def test_v3_sin_un_pixel_en_el_mes_no_sube_nada(monkeypatch, mundo):
    resultado, _ = _mes_v3(monkeypatch, mundo, [0, 0], cobertura_del_mes=0)

    assert resultado["mapas"] == 0
    assert mundo["subidas"] == [] and mundo["capas"] == []


def test_v3_si_falla_una_subida_no_escribe_ninguna_fila(monkeypatch, mundo):
    """Las filas van al final: ninguna apunta a un archivo que no llego al bucket."""
    def _descargar(url, destino):
        if "T1757Z" in url and "08-13" in url:
            raise OSError("se corto la descarga")
        return _geotiff_como_el_de_gee(destino)

    monkeypatch.setattr(raster, "descargar_a_archivo", _descargar)
    mundo["url"] = lambda etiqueta: f"https://gee/{etiqueta}"

    with pytest.raises(OSError, match="se corto"):
        _mes_v3(monkeypatch, mundo, [0.9, 0.5, 0.4])

    assert mundo["capas"] == []


def test_v2_sigue_siendo_un_archivo_por_mes_sin_pasadas(monkeypatch, mundo):
    """La vigente no cambia con M.9.7e2: una ventana, cuatro filas, la misma linea."""
    from pipeline.periodos import Mes
    from pipeline.receta import RECETA_POR_PASADA

    with avance_job.seguimiento("job-r", 0, 3):
        resultado = rancho.procesar_mes(
            rancho_id=RANCHO, tenant_id=TENANT, coordenadas=PAYLOAD["Coordinates"],
            mes=Mes(2026, 8), posicion=1, total=1, receta=RECETA_POR_PASADA,
        )

    assert resultado["mapas"] == 4
    assert {c["source"] for c in mundo["capas"]} == {"mensual"}
    (linea,) = [l for l in mundo["bitacora"] if l["etapa"] == "mes-2026-08"]
    assert "pasadas" not in linea["mensaje"]


# --- M.9.3: v4, con SAVI y LAI (`DECISIONS #81`) --------------------------------


def test_v4_cada_fila_lleva_la_escala_de_su_indice(monkeypatch, mundo):
    """El LAI va por 1.000 en el COG, y su fila lo dice: el panel pinta con eso.

    Con una sola escala para todo, el LAI de 3,5 por 10.000 se recortaba al tope del
    int16 sin error, y el panel lo pintaba con la escala equivocada.
    """
    from pipeline.receta import RECETA_PASADA_V4

    _mes_v3(monkeypatch, mundo, [0.9], receta=RECETA_PASADA_V4)

    pasada = [c for c in mundo["capas"] if c["source"] == "pasada"]
    assert [c["product"] for c in pasada] == ["ndvi", "evi", "ndre", "ndmi", "savi", "lai", "rgb"]
    # Los cuatro de siempre en su banda de v3; el color real se corre detras de los dos nuevos.
    assert [c["bandas"] for c in pasada] == [[1], [2], [3], [4], [5], [6], [7, 8, 9]]
    assert [c["escala"] for c in pasada] == [10_000] * 5 + [1_000, 10_000]
    assert {c["receta"] for c in pasada} == {"s2-pasada-v4"}
