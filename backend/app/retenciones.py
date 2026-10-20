"""Reservas temporales (DESIGN.md §5): disponible -> retenido (con vencimiento) -> confirmado.

Todo el reloj es el de la base (now()), nunca el del servidor de aplicación, para que varias
instancias de la API no discrepen.

Decisión sobre las retenciones vencidas (§5, tercer punto): se **limpian en el momento del
conflicto**. Quien retiene un asiento borra primero la retención vencida de ese mismo asiento,
en la misma transacción que su INSERT. Se descartó el proceso periódico porque deja una
ventana entre el vencimiento y la limpieza en la que un asiento libre sigue "ocupado" para el
índice único de E4; con la limpieza en el conflicto esa ventana no existe y no hay un segundo
proceso que coordinar. Costo: un DELETE más por cada intento de retener (un índice parcial lo
acota al asiento), y las retenciones vencidas que nadie vuelve a pedir se quedan en la tabla
sin molestar a nadie: el índice único solo las cuenta si alguien pide ese asiento.
"""
from dataclasses import dataclass
from datetime import datetime

import asyncpg

LIMPIAR_VENCIDA = (
    "DELETE FROM reservas WHERE evento_id = $1 AND asiento_id = $2 "
    "AND estado = 'retenida' AND vence_en <= now()"
)
RETENER = (
    "INSERT INTO reservas (evento_id, usuario_id, asiento_id, estado, vence_en, "
    "clave_idempotencia) VALUES ($1, $2, $3, 'retenida', now() + make_interval(secs => $4), $5) "
    "RETURNING id, vence_en"
)
CONFIRMAR = (
    "UPDATE reservas SET estado = 'confirmada', confirmada_en = now(), confirmada_por = $2 "
    "WHERE id = $1 AND usuario_id = $2 AND estado = 'retenida' AND vence_en > now() "
    "RETURNING id"
)
# Para explicar por qué CONFIRMAR no actualizó nada. vencida se calcula con el mismo now().
DIAGNOSTICO = (
    "SELECT usuario_id, estado, vence_en <= now() AS vencida FROM reservas WHERE id = $1"
)
POR_CLAVE = (
    "SELECT id, evento_id, usuario_id, asiento_id, estado, vence_en "
    "FROM reservas WHERE clave_idempotencia = $1"
)


@dataclass(frozen=True)
class Retencion:
    # retenida | ocupado | asiento_invalido | clave_reutilizada
    estado: str
    reserva_id: int | None = None
    vence_en: datetime | None = None  # lo calcula la base; la UI cuenta desde aquí (§7.1)


@dataclass(frozen=True)
class Confirmacion:
    # exitosa | vencida | ajena | no_existe
    estado: str
    reserva_id: int | None = None


async def retener(pool, evento_id, usuario_id, clave, asiento_id, ttl_segundos) -> Retencion:
    async with pool.acquire() as conn:
        capacidad = await conn.fetchval("SELECT capacidad FROM eventos WHERE id = $1", evento_id)
        if capacidad is None or not 1 <= asiento_id <= capacidad:
            return Retencion("asiento_invalido")
        try:
            async with conn.transaction():  # READ COMMITTED: el defecto de Postgres
                await conn.execute(LIMPIAR_VENCIDA, evento_id, asiento_id)
                fila = await conn.fetchrow(
                    RETENER, evento_id, usuario_id, asiento_id, float(ttl_segundos), clave)
            return Retencion("retenida", fila["id"], fila["vence_en"])
        except asyncpg.UniqueViolationError as error:
            if error.constraint_name == "reservas_asiento_unico":
                existente = await conn.fetchrow(POR_CLAVE, clave)
                # Un reintento de quien ya retiene este asiento no es "ocupado".
                if existente and existente["usuario_id"] == usuario_id \
                        and existente["asiento_id"] == asiento_id:
                    return Retencion(
                        "retenida" if existente["estado"] == "retenida" else "ocupado",
                        existente["id"], existente["vence_en"])
                return Retencion("ocupado")
            existente = await conn.fetchrow(POR_CLAVE, clave)  # reservas_clave_idempotencia_key
            if existente["evento_id"] != evento_id or existente["usuario_id"] != usuario_id:
                return Retencion("clave_reutilizada")
            return Retencion("retenida", existente["id"], existente["vence_en"])


async def confirmar(pool, reserva_id, usuario_id) -> Confirmacion:
    """Confirma la retención solo si sigue siendo de este usuario y no venció, en un único
    UPDATE: la verificación y la confirmación no pueden separarse (§5, primer punto)."""
    async with pool.acquire() as conn:
        if await conn.fetchval(CONFIRMAR, reserva_id, usuario_id) is not None:
            return Confirmacion("exitosa", reserva_id)
        fila = await conn.fetchrow(DIAGNOSTICO, reserva_id)
        if fila is None:
            return Confirmacion("no_existe")
        if fila["usuario_id"] != usuario_id:
            return Confirmacion("ajena")
        if fila["estado"] == "confirmada":
            return Confirmacion("exitosa", reserva_id)  # reintento de una confirmación ya hecha
        return Confirmacion("vencida")
