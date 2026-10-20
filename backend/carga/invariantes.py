"""Verificador de invariantes (DESIGN.md §8.2), sobre la base final y el registro de la corrida.

Devuelve cuántas violaciones hubo de cada invariante. El 5 (retenciones) no aplica hasta
la F4: todavía no hay retenciones, así que se reporta None y no 0, para no afirmar lo que
no se verificó.
"""
from collections import Counter

import asyncpg

from carga.generador import Registro


async def verificar(conn: asyncpg.Connection, evento_id: int,
                    registros: list[Registro]) -> dict:
    capacidad = await conn.fetchval("SELECT capacidad FROM eventos WHERE id = $1", evento_id)
    reservas = await conn.fetch(
        "SELECT id, asiento_id, clave_idempotencia FROM reservas WHERE evento_id = $1",
        evento_id)

    # 1. Capacidad: reservas confirmadas <= capacidad. Se reporta cuántas sobran.
    sobreventa = max(0, len(reservas) - capacidad)

    # 2. Unicidad: ningún asiento con más de una reserva activa.
    por_asiento = Counter(r["asiento_id"] for r in reservas if r["asiento_id"] is not None)
    asientos_duplicados = sum(1 for n in por_asiento.values() if n > 1)

    # 3. Respuesta fiel: "exitosa" <=> exactamente una reserva con esa clave (y ese id).
    #    Cualquier otra respuesta (agotado, ocupado, abortada, error...) => ninguna.
    por_clave: dict[str, list] = {}
    for r in reservas:
        por_clave.setdefault(r["clave_idempotencia"], []).append(r["id"])
    respuestas_infieles = 0
    for reg in registros:
        ids = por_clave.get(reg.clave, [])
        if reg.estado == "exitosa":
            if ids != [reg.reserva_id]:
                respuestas_infieles += 1
        elif ids:
            respuestas_infieles += 1

    # 4. Idempotencia: la misma clave nunca corresponde a más de una reserva. El esquema lo
    #    impide con UNIQUE; se verifica igual por si alguna vez deja de ser así.
    claves_repetidas = sum(1 for ids in por_clave.values() if len(ids) > 1)

    return {
        "capacidad": capacidad,
        "reservas": len(reservas),
        "sobreventa": sobreventa,
        "asientos_duplicados": asientos_duplicados,
        "respuestas_infieles": respuestas_infieles,
        "claves_repetidas": claves_repetidas,
        "retenciones": None,  # F4
    }
