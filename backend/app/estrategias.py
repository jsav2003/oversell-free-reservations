"""Las cinco estrategias de compra de DESIGN.md §4.

Sin ORM: el SQL y el nivel de aislamiento de cada transacción están a la vista. Todas
comparten la firma (pool, aislamiento, evento_id, usuario_id, clave, asiento_id) y
devuelven un Resultado. Se asume que el evento existe.

El estado de la compra es el contenido de la tabla reservas. El contador eventos.vendidos
solo lo usa E2; E1, E3 y E5 cuentan filas de reservas.

Un SQLSTATE 40001 (fallo de serialización) devuelve estado "abortada": el reintento es
asunto del cliente, salvo en E5, que reintenta por diseño.
"""
from dataclasses import dataclass, replace

import asyncpg

from app.config import AISLAMIENTO, ESTRATEGIA, MAX_REINTENTOS


@dataclass(frozen=True)
class Resultado:
    # exitosa | agotado | ocupado | asiento_invalido | abortada
    estado: str
    reserva_id: int | None = None
    reintentos: int = 0


CAPACIDAD = "SELECT capacidad FROM eventos WHERE id = $1"
CAPACIDAD_BLOQUEANDO = "SELECT capacidad FROM eventos WHERE id = $1 FOR UPDATE"
CONTAR = "SELECT count(*) FROM reservas WHERE evento_id = $1"
INSERTAR_POR_CAPACIDAD = (
    "INSERT INTO reservas (evento_id, usuario_id, clave_idempotencia) "
    "VALUES ($1, $2, $3) RETURNING id"
)
INSERTAR_POR_ASIENTO = (
    "INSERT INTO reservas (evento_id, usuario_id, asiento_id, clave_idempotencia) "
    "VALUES ($1, $2, $3, $4) RETURNING id"
)
TOMAR_CUPO = (
    "UPDATE eventos SET vendidos = vendidos + 1 "
    "WHERE id = $1 AND vendidos < capacidad RETURNING id"
)


async def _intentar(pool, aislamiento, cuerpo, *args) -> Resultado:
    """Un intento: una transacción al nivel pedido. 40001 se traduce a "abortada"."""
    async with pool.acquire() as conn:
        try:
            async with conn.transaction(isolation=aislamiento):
                return await cuerpo(conn, *args)
        except asyncpg.SerializationError:
            return Resultado("abortada")


async def _leer_y_luego_insertar(conn, evento_id, usuario_id, clave, asiento_id):
    """El código de la §3.1: contar y, si hay cupo, insertar. Nada protege el conteo."""
    capacidad = await conn.fetchval(CAPACIDAD, evento_id)
    vendidos = await conn.fetchval(CONTAR, evento_id)
    if vendidos >= capacidad:
        return Resultado("agotado")
    reserva_id = await conn.fetchval(INSERTAR_POR_CAPACIDAD, evento_id, usuario_id, clave)
    return Resultado("exitosa", reserva_id)


async def e1(pool, aislamiento, evento_id, usuario_id, clave, asiento_id=None):
    """E1: leer y luego insertar."""
    return await _intentar(
        pool, aislamiento, _leer_y_luego_insertar, evento_id, usuario_id, clave, asiento_id
    )


async def _update_condicional(conn, evento_id, usuario_id, clave, asiento_id):
    # El WHERE decide: si otro confirmó antes, en READ COMMITTED se reevalúa sobre la fila nueva.
    if await conn.fetchval(TOMAR_CUPO, evento_id) is None:
        return Resultado("agotado")
    reserva_id = await conn.fetchval(INSERTAR_POR_CAPACIDAD, evento_id, usuario_id, clave)
    return Resultado("exitosa", reserva_id)


async def e2(pool, aislamiento, evento_id, usuario_id, clave, asiento_id=None):
    """E2: UPDATE condicional atómico sobre el contador del evento."""
    return await _intentar(
        pool, aislamiento, _update_condicional, evento_id, usuario_id, clave, asiento_id
    )


async def _bloqueo_pesimista(conn, evento_id, usuario_id, clave, asiento_id):
    # FOR UPDATE antes de contar. En REPEATABLE READ la foto es anterior al bloqueo (§4).
    capacidad = await conn.fetchval(CAPACIDAD_BLOQUEANDO, evento_id)
    vendidos = await conn.fetchval(CONTAR, evento_id)
    if vendidos >= capacidad:
        return Resultado("agotado")
    reserva_id = await conn.fetchval(INSERTAR_POR_CAPACIDAD, evento_id, usuario_id, clave)
    return Resultado("exitosa", reserva_id)


async def e3(pool, aislamiento, evento_id, usuario_id, clave, asiento_id=None):
    """E3: bloqueo pesimista de la fila del evento."""
    return await _intentar(
        pool, aislamiento, _bloqueo_pesimista, evento_id, usuario_id, clave, asiento_id
    )


async def _insertar_asiento(conn, evento_id, usuario_id, clave, asiento_id):
    capacidad = await conn.fetchval(CAPACIDAD, evento_id)
    # El esquema solo exige asiento_id > 0; el tope lo pone la capacidad del evento.
    if asiento_id is None or not 1 <= asiento_id <= capacidad:
        return Resultado("asiento_invalido")
    reserva_id = await conn.fetchval(
        INSERTAR_POR_ASIENTO, evento_id, usuario_id, asiento_id, clave
    )
    return Resultado("exitosa", reserva_id)


async def e4(pool, aislamiento, evento_id, usuario_id, clave, asiento_id=None):
    """E4: INSERT contra el índice único (evento_id, asiento_id); el conflicto es "ocupado"."""
    try:
        return await _intentar(
            pool, aislamiento, _insertar_asiento, evento_id, usuario_id, clave, asiento_id
        )
    except asyncpg.UniqueViolationError as error:
        # La transacción ya hizo rollback. Otra restricción única (la clave) no es "ocupado".
        if error.constraint_name != "reservas_asiento_unico":
            raise
        return Resultado("ocupado")


async def e5(pool, aislamiento, evento_id, usuario_id, clave, asiento_id=None):
    """E5: el cuerpo de E1 con reintento ante 40001 (pensada para SERIALIZABLE)."""
    reintentos = 0
    while True:
        resultado = await _intentar(
            pool, aislamiento, _leer_y_luego_insertar, evento_id, usuario_id, clave, asiento_id
        )
        if resultado.estado != "abortada" or reintentos >= MAX_REINTENTOS:
            return replace(resultado, reintentos=reintentos)
        reintentos += 1


ESTRATEGIAS = {"e1": e1, "e2": e2, "e3": e3, "e4": e4, "e5": e5}


POR_CLAVE = "SELECT id, evento_id, usuario_id FROM reservas WHERE clave_idempotencia = $1"
CLAVE_UNICA = "reservas_clave_idempotencia_key"


async def _reserva_de_la_clave(pool, clave, evento_id, usuario_id) -> Resultado | None:
    """La reserva que ya existe para esta clave (§3.4), o None. Una clave que ya sirvió a
    otro evento u otro usuario no es un reintento: es un error del cliente."""
    fila = await pool.fetchrow(POR_CLAVE, clave)
    if fila is None:
        return None
    if fila["evento_id"] != evento_id or fila["usuario_id"] != usuario_id:
        return Resultado("clave_reutilizada")
    return Resultado("exitosa", fila["id"])


async def comprar(
    pool, evento_id, usuario_id, clave, asiento_id=None,
    *, estrategia=ESTRATEGIA, aislamiento=AISLAMIENTO,
):
    """Compra un puesto con la estrategia y el nivel configurados (o los pedidos).

    Idempotente por `clave` (§3.4): un reintento con la misma clave devuelve la reserva que ya
    existe en vez de crear otra. El camino feliz no paga nada extra: la búsqueda por clave solo
    se hace cuando la compra no salió bien, que es cuando puede ser un reintento de una compra
    que sí se confirmó (agotado y ocupado también pueden ser la respuesta a un reintento).
    """
    try:
        resultado = await ESTRATEGIAS[estrategia](
            pool, aislamiento, evento_id, usuario_id, clave, asiento_id
        )
    except asyncpg.UniqueViolationError as error:
        # Dos solicitudes con la misma clave a la vez: la segunda pierde contra el UNIQUE.
        if error.constraint_name != CLAVE_UNICA:
            raise
        return (await _reserva_de_la_clave(pool, clave, evento_id, usuario_id)
                or Resultado("abortada"))
    if resultado.estado == "exitosa":
        return resultado
    return await _reserva_de_la_clave(pool, clave, evento_id, usuario_id) or resultado
