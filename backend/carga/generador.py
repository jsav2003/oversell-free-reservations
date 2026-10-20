"""Generador de carga (DESIGN.md §6): asyncio + httpx contra la API, con semilla fija.

Cada comprador manda una sola solicitud. El registro guarda lo que se pidió y lo que se
respondió, que es lo que el verificador necesita (§8.2, invariante 3).
"""
import asyncio
import random
import time
import uuid
from dataclasses import dataclass

import httpx


@dataclass(frozen=True)
class Registro:
    usuario_id: str
    clave: str
    asiento_id: int | None
    estado: str  # exitosa | agotado | ocupado | asiento_invalido | abortada | error
    reserva_id: int | None
    reintentos: int
    latencia: float  # segundos


@dataclass(frozen=True)
class Corrida:
    registros: list[Registro]
    duracion: float  # segundos de pared de toda la corrida


async def _comprar(cliente, evento_id, usuario_id, clave, asiento_id) -> Registro:
    inicio = time.perf_counter()
    try:
        respuesta = await cliente.post("/reservas", json={
            "evento_id": evento_id, "usuario_id": usuario_id,
            "clave_idempotencia": clave, "asiento_id": asiento_id,
        })
        if respuesta.status_code == 200:
            cuerpo = respuesta.json()
            estado, reserva_id, reintentos = (
                cuerpo["estado"], cuerpo["reserva_id"], cuerpo["reintentos"])
        else:
            estado, reserva_id, reintentos = "error", None, 0
    except httpx.HTTPError:
        estado, reserva_id, reintentos = "error", None, 0
    return Registro(usuario_id, clave, asiento_id, estado, reserva_id, reintentos,
                    time.perf_counter() - inicio)


async def correr(url_api: str, evento_id: int, compradores: int, capacidad: int,
                 por_asiento: bool, semilla: int) -> Corrida:
    """Lanza `compradores` solicitudes a la vez. Con `por_asiento` (E4) cada una elige un
    asiento al azar entre 1..capacidad: dos compradores pueden querer el mismo."""
    azar = random.Random(semilla)
    # Claves y asientos salen de la semilla: la corrida se puede repetir (§6). La clave lleva
    # el evento porque UNIQUE(clave_idempotencia) es global y la semilla se repite entre corridas.
    pedidos = [
        (f"u{i}", f"{evento_id}-{uuid.UUID(int=azar.getrandbits(128), version=4)}",
         azar.randint(1, capacidad) if por_asiento else None)
        for i in range(compradores)
    ]
    limites = httpx.Limits(max_connections=compradores, max_keepalive_connections=compradores)
    async with httpx.AsyncClient(base_url=url_api, limits=limites, timeout=300) as cliente:
        inicio = time.perf_counter()
        registros = await asyncio.gather(
            *(_comprar(cliente, evento_id, u, c, a) for u, c, a in pedidos))
        duracion = time.perf_counter() - inicio
    return Corrida(list(registros), duracion)
