import os

DATABASE_URL = os.environ["DATABASE_URL"]
# Tamaño fijo del pool (min = max). Se reporta con cada medición: ver riesgo del pool en §10.
POOL_SIZE = int(os.environ.get("POOL_SIZE", "20"))

# Parámetros de la matriz (§6): la estrategia y el nivel son configuración, no ramas de código.
ESTRATEGIAS_VALIDAS = ("e1", "e2", "e3", "e4", "e5")
# Los nombres son los que acepta asyncpg en conn.transaction(isolation=...).
NIVELES_VALIDOS = ("read_committed", "repeatable_read", "serializable")

ESTRATEGIA = os.environ.get("ESTRATEGIA", "e5")
AISLAMIENTO = os.environ.get("AISLAMIENTO", "serializable")
# Tope de reintentos de E5 ante SQLSTATE 40001, para que una tormenta de abortos termine.
MAX_REINTENTOS = int(os.environ.get("MAX_REINTENTOS", "50"))

if ESTRATEGIA not in ESTRATEGIAS_VALIDAS:
    raise ValueError(f"ESTRATEGIA={ESTRATEGIA!r}; debe ser una de {ESTRATEGIAS_VALIDAS}")
if AISLAMIENTO not in NIVELES_VALIDOS:
    raise ValueError(f"AISLAMIENTO={AISLAMIENTO!r}; debe ser uno de {NIVELES_VALIDOS}")
