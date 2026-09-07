"""Introspecciona el esquema `geodata` que administra Geocore (EF Core).

Los nombres de columna de `geodata` son contrato entre repos: Geocore crea las
filas de `processing_jobs`, pero el worker escribe `layers` y `measurements`.
No hay compilador que agarre un desajuste, asi que se verifica contra la DB real.

Uso:  python check_schema.py
"""
import os
import psycopg2
from dotenv import load_dotenv

load_dotenv()

conn = None
try:
    conn = psycopg2.connect(
        host=os.getenv('DB_HOST', 'localhost'),
        port=os.getenv('DB_PORT', '5432'),
        dbname=os.getenv('DB_NAME', 'terra'),
        user=os.getenv('DB_USER', 'postgres'),
        password=os.getenv('DB_PASSWORD', 'postgres'),
        connect_timeout=10,
    )
    cur = conn.cursor()

    print("=== COLUMNAS ===")
    cur.execute("""
        SELECT table_name, column_name, data_type, is_nullable
        FROM information_schema.columns
        WHERE table_schema = 'geodata'
        ORDER BY table_name, ordinal_position;
    """)
    rows = cur.fetchall()
    if not rows:
        print("(el esquema 'geodata' no existe o esta vacio)")
    current = None
    for table, col, dtype, nullable in rows:
        if table != current:
            print(f"\n[{table}]")
            current = table
        null_mark = '' if nullable == 'NO' else ' NULL'
        print(f"  {col:<24} {dtype}{null_mark}")

    # Los ON CONFLICT del worker necesitan una constraint unica que coincida
    # exactamente con las columnas listadas; si no existe, el INSERT falla.
    print("\n=== CONSTRAINTS (PK / UNIQUE) ===")
    cur.execute("""
        SELECT tc.table_name, tc.constraint_type, tc.constraint_name,
               string_agg(kcu.column_name, ', ' ORDER BY kcu.ordinal_position)
        FROM information_schema.table_constraints tc
        JOIN information_schema.key_column_usage kcu
          ON tc.constraint_name = kcu.constraint_name
         AND tc.table_schema = kcu.table_schema
        WHERE tc.table_schema = 'geodata'
          AND tc.constraint_type IN ('PRIMARY KEY', 'UNIQUE')
        GROUP BY tc.table_name, tc.constraint_type, tc.constraint_name
        ORDER BY tc.table_name, tc.constraint_type;
    """)
    for table, ctype, name, cols in cur.fetchall():
        print(f"  {table:<20} {ctype:<12} ({cols})")

    print("\n=== EXTENSIONES ===")
    cur.execute("SELECT extname FROM pg_extension ORDER BY extname;")
    print("  " + ", ".join(r[0] for r in cur.fetchall()))

except Exception as e:
    print("Error:", e)
finally:
    if conn:
        conn.close()
