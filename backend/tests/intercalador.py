"""Herramientas para forzar un intercalado exacto entre dos conexiones (DESIGN.md §8.1).

Sin sleeps: los pasos se ordenan a mano. Cuando una sentencia tiene que quedar bloqueada
esperando a la otra transacción, se lanza como tarea y se espera a que Postgres la
reporte en pg_stat_activity con wait_event_type = 'Lock', no un tiempo fijo.
"""
import asyncio
from dataclasses import dataclass

import asyncpg

from app.config import DATABASE_URL

NIVEL_SQL = {
    "read_committed": "READ COMMITTED",
    "repeatable_read": "REPEATABLE READ",
    "serializable": "SERIALIZABLE",
}


@dataclass(frozen=True)
class Salida:
    """Lo que devolvió un paso: el valor, o el SQLSTATE si Postgres lo rechazó."""
    valor: object = None
    sqlstate: str | None = None

    @property
    def ok(self) -> bool:
        return self.sqlstate is None


async def intentar(awaitable) -> Salida:
    try:
        return Salida(valor=await awaitable)
    except asyncpg.PostgresError as error:
        return Salida(sqlstate=error.sqlstate)


class Comprador:
    """Una conexión propia con su transacción abierta a mano."""

    def __init__(self, conn: asyncpg.Connection, nombre: str):
        self.conn = conn
        self.nombre = nombre
        self.pid = conn.get_server_pid()

    @classmethod
    async def abrir(cls, nombre: str) -> "Comprador":
        return cls(await asyncpg.connect(DATABASE_URL), nombre)

    async def begin(self, aislamiento: str) -> None:
        await self.conn.execute(f"BEGIN ISOLATION LEVEL {NIVEL_SQL[aislamiento]}")

    async def fetchval(self, sql: str, *args) -> Salida:
        return await intentar(self.conn.fetchval(sql, *args))

    async def commit(self) -> Salida:
        return await intentar(self.conn.execute("COMMIT"))

    async def rollback(self) -> None:
        await self.conn.execute("ROLLBACK")

    def lanzar(self, sql: str, *args) -> asyncio.Task:
        """Lanza una sentencia que se espera que quede bloqueada; ver esperar_bloqueo."""
        return asyncio.create_task(intentar(self.conn.fetchval(sql, *args)))

    async def cerrar(self) -> None:
        await self.conn.close()


async def esperar_bloqueo(observador: asyncpg.Connection, comprador: Comprador, tarea: asyncio.Task):
    """Espera a que la sentencia del comprador esté esperando un bloqueo en el servidor.

    Si la tarea termina antes de bloquearse, el guion no hizo lo que se creía: falla fuerte.
    """
    for _ in range(500):
        if tarea.done():
            raise AssertionError(
                f"{comprador.nombre} no se bloqueó: la sentencia terminó con {tarea.result()}"
            )
        espera = await observador.fetchval(
            "SELECT wait_event_type FROM pg_stat_activity WHERE pid = $1", comprador.pid
        )
        if espera == "Lock":
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"{comprador.nombre} no llegó a esperar un bloqueo en 5 s")
