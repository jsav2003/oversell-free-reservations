"""F5 (DESIGN.md §7): estado en vivo por SSE y el laboratorio.

El estado en vivo se obtiene **sondeando** la tabla mientras alguien mira, no con un trigger
LISTEN/NOTIFY: un trigger encarecería cada INSERT del camino que se está midiendo, y el
sondeo solo cuesta cuando hay un navegador conectado. El precio es una latencia de hasta
INTERVALO_SONDEO entre el commit y lo que ve la pantalla.

Cada mensaje trae `ahora`, la hora de la base de datos. La UI calcula el temporizador de una
retención como `vence_en - ahora`, nunca con el reloj del navegador (§7.1).
"""
import asyncio
import json
import random
import time
from dataclasses import asdict

import asyncpg
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.config import ESTRATEGIAS_VALIDAS, NIVELES_VALIDOS
from app.estrategias import comprar
from carga.generador import Registro
from carga.invariantes import verificar

INTERVALO_SONDEO = 0.25  # segundos
MAX_REINTENTOS_CLIENTE = 50

router = APIRouter()

OCUPADOS = (
    "SELECT asiento_id, estado, usuario_id, vence_en FROM reservas "
    "WHERE evento_id = $1 AND asiento_id IS NOT NULL"
)
RESUMEN = (
    "SELECT e.capacidad, (SELECT count(*) FROM reservas WHERE evento_id = e.id) AS total, "
    "now() AS ahora FROM eventos e WHERE e.id = $1"
)


class NuevoEvento(BaseModel):
    nombre: str = "evento"
    capacidad: int = Field(gt=0, le=5000)


@router.post("/eventos")
async def crear_evento(e: NuevoEvento, request: Request):
    evento_id = await request.app.state.pool.fetchval(
        "INSERT INTO eventos (nombre, capacidad) VALUES ($1, $2) RETURNING id",
        e.nombre, e.capacidad)
    return {"evento_id": evento_id, "capacidad": e.capacidad}


def _asientos(filas) -> dict[int, dict]:
    return {
        f["asiento_id"]: {"estado": f["estado"], "usuario_id": f["usuario_id"],
                          "vence_en": f["vence_en"].isoformat() if f["vence_en"] else None}
        for f in filas
    }


async def _foto(pool, evento_id):
    async with pool.acquire() as conn:
        resumen = await conn.fetchrow(RESUMEN, evento_id)
        if resumen is None:
            return None
        return resumen, _asientos(await conn.fetch(OCUPADOS, evento_id))


@router.get("/eventos/{evento_id}/estado")
async def estado(evento_id: int, request: Request):
    foto = await _foto(request.app.state.pool, evento_id)
    if foto is None:
        raise HTTPException(404, "evento inexistente")
    resumen, asientos = foto
    return {"capacidad": resumen["capacidad"], "total": resumen["total"],
            "ahora": resumen["ahora"].isoformat(), "asientos": asientos}


def _mensaje(evento: str, datos: dict) -> str:
    return f"event: {evento}\ndata: {json.dumps(datos)}\n\n"


@router.get("/eventos/{evento_id}/stream")
async def stream(evento_id: int, request: Request):
    pool = request.app.state.pool
    if await _foto(pool, evento_id) is None:
        raise HTTPException(404, "evento inexistente")

    async def generar():
        anterior: dict[int, dict] | None = None
        total_anterior = None
        while not await request.is_disconnected():
            resumen, actual = await _foto(pool, evento_id)
            ahora = resumen["ahora"].isoformat()
            if anterior is None:
                yield _mensaje("estado", {"capacidad": resumen["capacidad"],
                                          "total": resumen["total"], "ahora": ahora,
                                          "asientos": actual})
            else:
                for asiento, dato in actual.items():
                    if anterior.get(asiento) != dato:
                        yield _mensaje("asiento", {"asiento_id": asiento, "ahora": ahora, **dato})
                for asiento in anterior.keys() - actual.keys():
                    # La fila desapareció (retención vencida que otro limpió): vuelve a estar libre.
                    yield _mensaje("asiento", {"asiento_id": asiento, "estado": None,
                                               "usuario_id": None, "vence_en": None,
                                               "ahora": ahora})
                if resumen["total"] != total_anterior:
                    yield _mensaje("resumen", {"total": resumen["total"],
                                               "capacidad": resumen["capacidad"], "ahora": ahora})
            anterior, total_anterior = actual, resumen["total"]
            await asyncio.sleep(INTERVALO_SONDEO)

    return StreamingResponse(generar(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


class Experimento(BaseModel):
    estrategia: str
    aislamiento: str
    compradores: int = Field(gt=0, le=2000)
    semilla: int = 1
    reintentar_abortadas: bool = False


def _percentil(valores: list[float], p: float) -> float:
    ordenados = sorted(valores)
    return ordenados[min(len(ordenados) - 1, int(p * len(ordenados)))]


@router.post("/laboratorio/{evento_id}/correr")
async def correr_experimento(evento_id: int, x: Experimento, request: Request):
    """Lanza `compradores` compras a la vez sobre un evento ya creado y verifica el resultado.

    Corre dentro de la API, llamando a `comprar` sin pasar por HTTP: es una demo, no una
    medición. La matriz de la F3 es la que mide (con HTTP, procesos separados y tres semillas).
    """
    if x.estrategia not in ESTRATEGIAS_VALIDAS or x.aislamiento not in NIVELES_VALIDOS:
        raise HTTPException(422, "estrategia o aislamiento inválidos")
    pool = request.app.state.pool
    capacidad = await pool.fetchval("SELECT capacidad FROM eventos WHERE id = $1", evento_id)
    if capacidad is None:
        raise HTTPException(404, "evento inexistente")
    if await pool.fetchval("SELECT count(*) FROM reservas WHERE evento_id = $1", evento_id):
        raise HTTPException(409, "el evento ya tiene reservas: crea uno nuevo")

    azar = random.Random(x.semilla)
    pedidos = [(f"u{i}", f"lab-{evento_id}-{i}",
                azar.randint(1, capacidad) if x.estrategia == "e4" else None)
               for i in range(x.compradores)]

    async def uno(usuario, clave, asiento) -> Registro:
        inicio = time.perf_counter()
        reintentos = 0
        while True:
            r = await comprar(pool, evento_id, usuario, clave, asiento,
                              estrategia=x.estrategia, aislamiento=x.aislamiento)
            if (r.estado != "abortada" or not x.reintentar_abortadas
                    or reintentos >= MAX_REINTENTOS_CLIENTE):
                break
            reintentos += 1
        return Registro(usuario, clave, asiento, r.estado, r.reserva_id,
                        r.reintentos + reintentos, time.perf_counter() - inicio)

    inicio = time.perf_counter()
    registros = await asyncio.gather(*(uno(*p) for p in pedidos))
    duracion = time.perf_counter() - inicio

    async with pool.acquire() as conn:
        violaciones = await verificar(conn, evento_id, list(registros))
    estados: dict[str, int] = {}
    for r in registros:
        estados[r.estado] = estados.get(r.estado, 0) + 1
    latencias = [r.latencia for r in registros]
    return {
        "evento_id": evento_id, "capacidad": capacidad, "duracion": duracion,
        "respuestas": estados,
        "reintentos": sum(r.reintentos for r in registros),
        "throughput": estados.get("exitosa", 0) / duracion,
        "p50_ms": _percentil(latencias, 0.50) * 1000,
        "p99_ms": _percentil(latencias, 0.99) * 1000,
        "violaciones": violaciones,
    }
