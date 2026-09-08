"""Tests del reporte de arranque.

Lo que se cuida aca es sobre todo **que el reporte no filtre secretos**. Un
reporte de configuracion es codigo que imprime, a nivel INFO, en un log que
alguien mas va a leer: es el peor lugar posible para un descuido.

El resto de los tests cuidan que las tres cosas que costaron un deploy cada una
—el modo, el valor de desarrollo de la event key, y los espacios en los
bordes— se vean sin tener que buscarlas.
"""

import pytest

from utils_pkg.arranque import (
    EVENT_KEY_DE_DESARROLLO,
    PUBLICO,
    SECRETO,
    Variable,
    describir_valor,
    reporte_de_arranque,
)


def texto(entorno, es_produccion=True):
    return "\n".join(reporte_de_arranque(entorno, es_produccion))


# --------------------------------------------------------------------------
# lo primero: que no se escape ningun secreto
# --------------------------------------------------------------------------

SECRETOS = {
    "INNGEST_SIGNING_KEY": "signkey-prod-ABCDEF0123456789",
    "INNGEST_EVENT_KEY": "evtkey-prod-ZYXWVU9876543210",
    "MINIO_ACCESS_KEY": "AKIAIOSFODNN7EXAMPLE",
    "MINIO_SECRET_KEY": "wJalrXUtnFEMI-K7MDENG-bPxRfiCYEXAMPLEKEY",
    "DB_PASSWORD": "Contrasena-Muy-Secreta-1",
    "EE_SERVICE_ACCOUNT_KEY_JSON": '{"private_key":"-----BEGIN PRIVATE KEY-----"}',
}


@pytest.mark.parametrize("nombre,valor", sorted(SECRETOS.items()))
def test_ningun_secreto_aparece_en_el_reporte(nombre, valor):
    salida = texto(dict(SECRETOS, ENVIRONMENT="production"))
    assert valor not in salida
    # Ni siquiera un pedazo: un prefijo de 8 caracteres ya reduce muchisimo el
    # espacio de busqueda de quien tenga el log.
    assert valor[:8] not in salida
    assert valor[-8:] not in salida
    # Pero la variable si tiene que figurar, con su largo.
    assert nombre in salida
    assert "(%d chars)" % len(valor) in salida


def test_los_valores_publicos_si_se_muestran():
    # El endpoint y el bucket son justamente lo que uno necesita ver para
    # diagnosticar, y no son secretos.
    salida = texto({
        "ENVIRONMENT": "production",
        "MINIO_ENDPOINT": "bucket-production.up.railway.app",
        "DB_USER": "postgres.abcdefghijklmnop",
    })
    assert "bucket-production.up.railway.app" in salida
    assert "postgres.abcdefghijklmnop" in salida


# --------------------------------------------------------------------------
# el modo, que es la pregunta que este reporte existe para contestar
# --------------------------------------------------------------------------

def test_produccion_dice_produccion_y_no_avisa_de_la_firma():
    salida = texto({"ENVIRONMENT": "production"}, es_produccion=True)
    assert "MODO: PRODUCCION" in salida
    assert "no se verifica la firma" not in salida


def test_desarrollo_avisa_que_la_firma_no_se_verifica():
    salida = texto({"ENVIRONMENT": "development"}, es_produccion=False)
    assert "MODO: DESARROLLO" in salida
    assert "no se verifica la firma" in salida


def test_un_valor_desconocido_se_marca_como_posible_typo():
    # `config` ya trata lo desconocido como produccion, que es la direccion
    # segura. Pero casi siempre es un typo, asi que hay que verlo.
    salida = texto({"ENVIRONMENT": "prodction"}, es_produccion=True)
    assert "MODO: PRODUCCION" in salida
    assert "typo" in salida


def test_production_a_secas_no_se_marca_como_typo():
    for valor in ("production", "prod", "PRODUCTION"):
        salida = texto({"ENVIRONMENT": valor}, es_produccion=True)
        assert "typo" not in salida, valor


def test_no_recalcula_el_modo_por_su_cuenta():
    # El reporte recibe `es_produccion` ya resuelto por `config`. Un segundo
    # criterio para lo mismo es exactamente el bug que costo tener el worker
    # sin verificacion de firma con ENVIRONMENT=prod.
    salida = texto({"ENVIRONMENT": "development"}, es_produccion=True)
    assert "MODO: PRODUCCION" in salida


# --------------------------------------------------------------------------
# los tres modos de fallo que ya nos costaron un deploy
# --------------------------------------------------------------------------

def test_la_event_key_de_desarrollo_se_marca_como_problema():
    salida = texto({
        "ENVIRONMENT": "production",
        "INNGEST_EVENT_KEY": EVENT_KEY_DE_DESARROLLO,
    })
    assert "ES EL VALOR DE DESARROLLO" in salida
    assert "INNGEST_EVENT_KEY: no se puede emitir" in salida


def test_los_espacios_en_los_bordes_se_ven():
    # python-dotenv los recorta al leer el .env y Railway no recorta nada: el
    # mismo valor anda en local y falla desplegado.
    salida = texto({"ENVIRONMENT": "production", "DB_PASSWORD": " secreta "})
    assert "ESPACIOS EN LOS BORDES" in salida


def test_las_comillas_en_los_bordes_se_ven():
    salida = texto({"ENVIRONMENT": "production", "DB_PASSWORD": '"secreta"'})
    assert "COMILLAS EN LOS BORDES" in salida


def test_una_variable_definida_pero_vacia_cuenta_como_ausente():
    # El .env local tiene `INNGEST_SIGNING_KEY=` y el codigo la trata como None
    # (`INNGEST_SIGNING_KEY or None`). El reporte tiene que coincidir con eso.
    salida = texto({"ENVIRONMENT": "production", "INNGEST_SIGNING_KEY": ""})
    assert "definida pero vacia" in salida
    assert "INNGEST_SIGNING_KEY: sin firma" in salida


# --------------------------------------------------------------------------
# el resumen final
# --------------------------------------------------------------------------

def test_sin_configurar_nada_lista_los_faltantes_en_produccion():
    salida = texto({"ENVIRONMENT": "production"})
    assert "VAN A FALLAR" in salida
    for nombre in ("INNGEST_SIGNING_KEY", "INNGEST_EVENT_KEY",
                   "MINIO_ACCESS_KEY", "MINIO_SECRET_KEY", "DB_PASSWORD",
                   "EE_SERVICE_ACCOUNT_KEY_JSON"):
        assert nombre in salida


def test_en_desarrollo_lo_que_solo_aplica_a_produccion_no_es_problema():
    # Sin credenciales, en local, no hay nada roto: es el modo esperado.
    salida = texto({"ENVIRONMENT": "development"}, es_produccion=False)
    assert "Configuracion completa" in salida
    assert "VAN A FALLAR" not in salida


def test_todo_puesto_no_reporta_problemas():
    entorno = dict(SECRETOS)
    entorno.update({
        "ENVIRONMENT": "production",
        "MINIO_ENDPOINT": "bucket.up.railway.app",
        "DB_HOST": "aws-0-us-east-1.pooler.supabase.com",
        "DB_USER": "postgres.abcdefghijklmnop",
        "EE_SERVICE_ACCOUNT_EMAIL": "worker@proyecto.iam.gserviceaccount.com",
    })
    salida = texto(entorno)
    assert "Configuracion completa" in salida
    assert "VAN A FALLAR" not in salida


# --------------------------------------------------------------------------
# la salida en si
# --------------------------------------------------------------------------

def test_la_salida_es_ascii():
    # Un em-dash o un tick verde tumban el proceso en una consola cp1252, y el
    # fallo aparece como si el arranque hubiera fallado. Ya paso dos veces.
    salida = texto(dict(SECRETOS, ENVIRONMENT="production"))
    salida.encode("ascii")


def test_ninguna_linea_trae_saltos_internos():
    # `registrar_arranque` manda una linea por registro para que el formateador
    # JSON no las escape. Si una linea trajera un \n adentro, el JSON quedaria
    # con un `\\n` literal y el bloque ilegible.
    for linea in reporte_de_arranque({"ENVIRONMENT": "production"}, True):
        assert "\n" not in linea


# --------------------------------------------------------------------------
# `describir_valor` sola
# --------------------------------------------------------------------------

def test_describir_valor_ausente_con_defecto_no_es_problema():
    v = Variable("X", PUBLICO, defecto="./outputs")
    texto_, problema = describir_valor(v, None)
    assert "por defecto" in texto_ and "./outputs" in texto_
    assert problema is False


def test_describir_valor_ausente_sin_defecto_es_problema():
    v = Variable("X", SECRETO)
    texto_, problema = describir_valor(v, None)
    assert texto_ == "AUSENTE"
    assert problema is True
