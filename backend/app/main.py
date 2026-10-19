from contextlib import asynccontextmanager

import asyncpg
from fastapi import FastAPI

from app.config import DATABASE_URL, POOL_SIZE


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
    return {"estado": "ok", "postgres": version, "pool": POOL_SIZE}
