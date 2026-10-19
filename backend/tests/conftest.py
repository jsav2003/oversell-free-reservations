"""Los tests corren contra el Postgres de docker compose (puerto 5433 del host)."""
import os
import uuid

os.environ.setdefault(
    "DATABASE_URL", "postgresql://reservas:reservas@127.0.0.1:5433/reservas"
)

import asyncpg
import pytest

from app.config import DATABASE_URL


@pytest.fixture
async def pool():
    pool = await asyncpg.create_pool(DATABASE_URL, min_size=2, max_size=5)
    yield pool
    await pool.close()


@pytest.fixture
async def crear_evento(pool):
    """Fábrica de eventos con la capacidad pedida; los borra al terminar el test."""
    creados = []

    async def crear(capacidad: int) -> int:
        evento_id = await pool.fetchval(
            "INSERT INTO eventos (nombre, capacidad) VALUES ($1, $2) RETURNING id",
            f"test-{uuid.uuid4()}", capacidad,
        )
        creados.append(evento_id)
        return evento_id

    yield crear

    await pool.execute("DELETE FROM reservas WHERE evento_id = ANY($1)", creados)
    await pool.execute("DELETE FROM eventos WHERE id = ANY($1)", creados)
