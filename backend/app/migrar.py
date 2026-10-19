"""Aplica las migraciones SQL de backend/migrations en orden, una transacción por archivo.

Sin Alembic: depende de SQLAlchemy, y este proyecto no usa ORM. El SQL se queda a la vista.
Uso: python -m app.migrar
"""
import asyncio
from pathlib import Path

import asyncpg

from app.config import DATABASE_URL

DIRECTORIO = Path(__file__).resolve().parent.parent / "migrations"
# Serializa dos ejecuciones simultáneas del migrador (clave arbitraria, fija).
LOCK_ID = 7_302_001


async def migrar() -> None:
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        await conn.execute("SELECT pg_advisory_lock($1)", LOCK_ID)
        await conn.execute(
            "CREATE TABLE IF NOT EXISTS migraciones ("
            " nombre text PRIMARY KEY,"
            " aplicada_en timestamptz NOT NULL DEFAULT now())"
        )
        aplicadas = {r["nombre"] for r in await conn.fetch("SELECT nombre FROM migraciones")}
        for archivo in sorted(DIRECTORIO.glob("*.sql")):
            if archivo.name in aplicadas:
                continue
            async with conn.transaction():
                await conn.execute(archivo.read_text(encoding="utf-8"))
                await conn.execute("INSERT INTO migraciones (nombre) VALUES ($1)", archivo.name)
            print(f"migración aplicada: {archivo.name}", flush=True)
        print("migraciones al día", flush=True)
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(migrar())
