import os

DATABASE_URL = os.environ["DATABASE_URL"]
# Tamaño fijo del pool (min = max). Se reporta con cada medición: ver riesgo del pool en §10.
POOL_SIZE = int(os.environ.get("POOL_SIZE", "20"))
