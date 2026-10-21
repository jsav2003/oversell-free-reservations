from contextlib import asynccontextmanager

import asyncio
import os
from dataclasses import asdict

import asyncpg
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from app.config import AISLAMIENTO, DATABASE_URL, ESTRATEGIA, POOL_SIZE
from app.estrategias import comprar
from app.retenciones import confirmar, retener
from app.vivo import router as vivo


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.pool = await asyncpg.create_pool(
        DATABASE_URL, min_size=POOL_SIZE, max_size=POOL_SIZE
    )
    yield
    await app.state.pool.close()


app = FastAPI(title="Reservas sin sobreventa", lifespan=lifespan)
# El navegador (puerto 3000) llama a la API (puerto 8000): otro origen.
app.add_middleware(
    CORSMiddleware, allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_methods=["*"], allow_headers=["*"])
app.include_router(vivo)


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
    if resultado.estado == "exitosa":
        await _falla_tras_commit()
    return asdict(resultado)


# Inyección de fallas (§8.4). Solo para los escenarios de carga/fallas.py: sin la variable
# FALLA no hace nada. Se dispara después del COMMIT y antes de responder, que es la ventana
# del commit ambiguo (§3.4).
FALLA = os.environ.get("FALLA", "")


async def _falla_tras_commit():
    if FALLA == "morir_tras_commit":
        os._exit(1)  # el proceso muere sin responder ni cerrar nada, como un kill -9
    if FALLA == "demora_tras_commit":
        await asyncio.sleep(2)  # el cliente se cansa de esperar y corta antes de la respuesta


class Retencion(BaseModel):
    evento_id: int
    usuario_id: str
    clave_idempotencia: str
    asiento_id: int
    ttl_segundos: float = 600


@app.post("/retenciones")
async def retener_asiento(r: Retencion):
    resultado = await retener(
        app.state.pool, r.evento_id, r.usuario_id, r.clave_idempotencia,
        r.asiento_id, r.ttl_segundos)
    return {**asdict(resultado),
            "vence_en": resultado.vence_en.isoformat() if resultado.vence_en else None}


class Confirmacion(BaseModel):
    usuario_id: str


@app.post("/reservas/{reserva_id}/confirmar")
async def confirmar_reserva(reserva_id: int, c: Confirmacion):
    return asdict(await confirmar(app.state.pool, reserva_id, c.usuario_id))
