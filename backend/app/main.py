from contextlib import asynccontextmanager

from dataclasses import asdict

import asyncpg
from fastapi import FastAPI
from pydantic import BaseModel

from app.config import AISLAMIENTO, DATABASE_URL, ESTRATEGIA, POOL_SIZE
from app.estrategias import comprar


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.pool = await asyncpg.create_pool(
        DATABASE_URL, min_size=POOL_SIZE, max_size=POOL_SIZE
    )
    yield
    await app.state.pool.close()


app = FastAPI(title="Reservas sin sobreventa", lifespan=lifespan)


@app.get("/salud")
async def salud():
    async with app.state.pool.acquire() as conn:
        version = await conn.fetchval("SHOW server_version")
    return {
        "estado": "ok",
        "postgres": version,
        "pool": POOL_SIZE,
        "estrategia": ESTRATEGIA,
        "aislamiento": AISLAMIENTO,
    }


class Compra(BaseModel):
    evento_id: int
    usuario_id: str
    clave_idempotencia: str
    asiento_id: int | None = None


@app.post("/reservas")
async def reservar(compra: Compra):
    """Compra un puesto con la estrategia y el nivel configurados (§6)."""
    resultado = await comprar(
        app.state.pool, compra.evento_id, compra.usuario_id,
        compra.clave_idempotencia, compra.asiento_id,
    )
    return asdict(resultado)
