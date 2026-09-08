"""Verifica la conexion a `geodata` y separa "mala contrasena" de todo lo demas.

QUE HACE
--------
Toma la misma configuracion que usa `repositories/db_repository.py` y recorre
la conexion escalon por escalon: forma de las variables, coherencia entre el
host y el usuario, DNS, TCP, autenticacion, y por ultimo si el esquema
`geodata` existe y se puede escribir. Se detiene en el primer escalon que falla
y dice cual es.

PARA QUE SIRVE
--------------
PostgreSQL responde `password authentication failed for user "postgres"` a
varias causas que no tienen nada que ver con la contrasena, y el mensaje no las
distingue. Contra el pooler de Supabase hay tres que se ven identicas:

  1. la contrasena esta mal de verdad;
  2. `DB_USER` es `postgres` a secas cuando el host es el pooler, que exige
     `postgres.<project-ref>` para saber a que proyecto enrutar;
  3. la variable llego con un espacio o unas comillas pegadas, cosa que
     `python-dotenv` recorta al leer el `.env` pero que en Railway **no recorta
     nadie**: ahi el valor es literal, byte por byte.

La 3 es la mas cruel porque el mismo secreto funciona en local y falla
desplegado, que es exactamente el sintoma que hay que explicar. Este repo ya
tiene el caso: el `.env` local trae un espacio despues del `=` y anda igual.

QUE LOGRAMOS
------------
Poder decir de que lado esta el problema sin adivinar. Si este script pasa en
local y el deploy sigue fallando, las credenciales son buenas y lo que esta mal
es la configuracion de Railway — no hay que tocar Supabase ni el codigo.

NUNCA IMPRIME LA CONTRASENA. Solo su largo y si tiene bordes sospechosos. Es el
mismo criterio que `check_write_path.py` aplica al secreto de MinIO.

La salida es ASCII a proposito: la consola de Windows es cp1252 y un caracter
fuera de esa tabla tumba el script con `UnicodeEncodeError`, o sea que un
chequeo que paso entero termina reportando fallo.
"""

import os
import socket
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

load_dotenv()

COMILLAS = ('"', chr(39))


def _describir_secreto(valor):
    """Describe la contrasena sin mostrar ni un caracter de su contenido."""
    if valor is None:
        return "(ausente)"
    partes = ["(presente, %d chars)" % len(valor)]
    if valor != valor.strip():
        izq = len(valor) - len(valor.lstrip())
        der = len(valor) - len(valor.rstrip())
        partes.append("ESPACIOS EN LOS BORDES (%d al inicio, %d al final)" % (izq, der))
    if valor[:1] in COMILLAS or valor[-1:] in COMILLAS:
        partes.append("COMILLAS EN LOS BORDES")
    if not all(32 <= ord(c) < 127 for c in valor):
        partes.append("TIENE CARACTERES NO-ASCII O DE CONTROL")
    return "  ".join(partes)


def main():
    host = os.getenv("DB_HOST", "localhost")
    port = os.getenv("DB_PORT", "5432")
    name = os.getenv("DB_NAME", "terra")
    user = os.getenv("DB_USER", "postgres")
    password = os.getenv("DB_PASSWORD")

    print("=" * 70)
    print("CONFIGURACION")
    print("=" * 70)
    print("  DB_HOST     : %s" % host)
    print("  DB_PORT     : %s" % port)
    print("  DB_NAME     : %s" % name)
    print("  DB_USER     : %s" % user)
    print("  DB_PASSWORD : %s" % _describir_secreto(password))
    print()

    problemas = []

    # ---- 1. coherencia host <-> usuario -----------------------------------
    # El pooler multiplexa muchos proyectos sobre un mismo hostname, asi que el
    # ref del proyecto viaja **dentro del usuario**. Sin el, el pooler no sabe a
    # que base enrutar y contesta un error de autenticacion, no de ruteo: por eso
    # este error se confunde tanto con una contrasena mala.
    es_pooler = "pooler.supabase.com" in host
    es_directo = host.endswith(".supabase.co")
    print("=" * 70)
    print("1. COHERENCIA ENTRE EL HOST Y EL USUARIO")
    print("=" * 70)
    if es_pooler:
        print("  host: pooler de Supabase")
        if "." in user:
            print("  OK    DB_USER trae el ref del proyecto (%s)" % user.split(".", 1)[1])
        else:
            print("  FALLA DB_USER es '%s', sin ref de proyecto." % user)
            print("        Contra el pooler tiene que ser 'postgres.<project-ref>'.")
            print("        Sin el ref el error que sale es de contrasena, aunque")
            print("        la contrasena este bien.")
            problemas.append("DB_USER sin ref de proyecto contra el pooler")
        if port == "5432":
            print("  OK    puerto 5432 = modo sesion, que es el que quiere un pool")
        elif port == "6543":
            print("  AVISO puerto 6543 = modo transaccion. SimpleConnectionPool")
            print("        conecta igual, pero no hay sesion estable y el")
            print("        'options=-c search_path' puede no sobrevivir.")
        else:
            print("  AVISO puerto %s no es ni 5432 ni 6543" % port)
    elif es_directo:
        print("  host: conexion directa a Supabase")
        if "." in user:
            print("  FALLA DB_USER es '%s'." % user)
            print("        La conexion directa NO lleva el ref: va 'postgres' solo.")
            problemas.append("DB_USER con ref contra el host directo")
        else:
            print("  OK    DB_USER es 'postgres', que es lo correcto aca")
    else:
        print("  host no es de Supabase; no aplica la regla del ref")
    print()

    # ---- 2. forma de la contrasena ----------------------------------------
    print("=" * 70)
    print("2. FORMA DE LA CONTRASENA")
    print("=" * 70)
    if password is None:
        print("  FALLA DB_PASSWORD no esta definida.")
        problemas.append("DB_PASSWORD ausente")
    elif password != password.strip() or password[:1] in COMILLAS:
        print("  FALLA el valor trae bordes que no deberian estar ahi.")
        print("        En el .env local python-dotenv los recorta, por eso aca")
        print("        funciona. En Railway el valor es literal: el mismo secreto")
        print("        falla desplegado.")
        problemas.append("DB_PASSWORD con bordes sucios")
    else:
        print("  OK    sin espacios ni comillas pegadas")
    print()

    # ---- 3. DNS y TCP ------------------------------------------------------
    print("=" * 70)
    print("3. DNS Y TCP")
    print("=" * 70)
    try:
        ip = socket.gethostbyname(host)
        print("  OK    %s resuelve a %s" % (host, ip))
    except socket.gaierror as e:
        print("  FALLA no resuelve: %s" % e)
        print("        Con el host mal el error NO habria sido de contrasena, asi")
        print("        que esto no explica el sintoma que estamos persiguiendo.")
        problemas.append("DNS")
        _cerrar(problemas)
        return 1
    try:
        s = socket.create_connection((host, int(port)), timeout=10)
        s.close()
        print("  OK    acepta TCP en el puerto %s" % port)
    except OSError as e:
        print("  FALLA no acepta TCP: %s" % e)
        problemas.append("TCP")
        _cerrar(problemas)
        return 1
    print()

    # ---- 4. autenticacion --------------------------------------------------
    print("=" * 70)
    print("4. AUTENTICACION")
    print("=" * 70)
    import psycopg2

    try:
        conn = psycopg2.connect(
            host=host, port=port, database=name,
            user=user, password=password,
            options="-c search_path=geodata,public",
            connect_timeout=15,
        )
    except psycopg2.OperationalError as e:
        texto = str(e).strip()
        print("  FALLA %s" % texto.splitlines()[0])
        if "password authentication failed" in texto:
            print()
            print("        Este mensaje NO prueba que la contrasena este mal.")
            print("        Las tres causas que lo producen, por frecuencia:")
            print("          a) el valor desplegado no es el que crees. Railway")
            print("             deja los cambios de variables en espera hasta el")
            print("             siguiente deploy: verifica que el deploy activo")
            print("             sea POSTERIOR al cambio;")
            print("          b) DB_USER sin el ref del proyecto contra el pooler;")
            print("          c) la contrasena esta mal de verdad.")
        problemas.append("autenticacion")
        _cerrar(problemas)
        return 1
    print("  OK    autentico como %s" % user)
    print()

    # ---- 5. esquema y escritura -------------------------------------------
    print("=" * 70)
    print("5. ESQUEMA GEODATA")
    print("=" * 70)
    codigo = 0
    try:
        cur = conn.cursor()
        cur.execute("SELECT current_user, current_database(), version()")
        cu, cdb, ver = cur.fetchone()
        print("  usuario efectivo : %s" % cu)
        print("  base             : %s" % cdb)
        print("  motor            : %s" % ver.split(",")[0])

        cur.execute(
            "SELECT 1 FROM information_schema.schemata WHERE schema_name = 'geodata'"
        )
        if cur.fetchone():
            print("  OK    el esquema geodata existe")
        else:
            print("  FALLA no existe el esquema geodata")
            problemas.append("falta el esquema geodata")
            codigo = 1

        cur.execute("SHOW search_path")
        print("  search_path      : %s" % cur.fetchone()[0])

        # Las tres tablas que el worker toca. `layers` y `measurements` las crea
        # EF Core desde Geocore; `sentinel2_dates` la crea el `init_db` del
        # worker, que es la unica tabla que EF no administra.
        for tabla in ("layers", "measurements", "sentinel2_dates"):
            cur.execute("SELECT to_regclass(%s)", ("geodata." + tabla,))
            existe = cur.fetchone()[0] is not None
            print("  %-16s : %s" % (tabla, "existe" if existe else "NO EXISTE"))
            if not existe:
                problemas.append("falta la tabla %s" % tabla)
                codigo = 1

        # Permiso de escritura sin dejar rastro: se abre y se revierte.
        try:
            cur.execute("CREATE TEMP TABLE _check_db_escritura (x int)")
            conn.rollback()
            print("  OK    puede escribir (probado y revertido)")
        except psycopg2.Error as e:
            conn.rollback()
            print("  FALLA no puede escribir: %s" % str(e).strip().splitlines()[0])
            problemas.append("sin permiso de escritura")
            codigo = 1
        cur.close()
    finally:
        conn.close()
    print()
    _cerrar(problemas)
    return codigo


def _cerrar(problemas):
    print("=" * 70)
    if problemas:
        print("RESULTADO: %d problema(s)" % len(problemas))
        for p in problemas:
            print("  - %s" % p)
    else:
        print("RESULTADO: todo bien. Las credenciales sirven desde esta maquina.")
        print("Si el deploy sigue fallando, lo que esta mal es la configuracion")
        print("de Railway, no las credenciales ni el codigo.")
    print("=" * 70)


if __name__ == "__main__":
    sys.exit(main())
