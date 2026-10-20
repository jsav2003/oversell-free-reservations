"""El verificador tiene que detectar lo que dice detectar: se prueba con bases sembradas a mano."""
from carga.generador import Registro
from carga.invariantes import verificar


def reg(clave, estado, reserva_id=None, asiento=None):
    return Registro("u", clave, asiento, estado, reserva_id, 0, 0.01)


async def insertar(pool, evento_id, clave, asiento=None):
    return await pool.fetchval(
        "INSERT INTO reservas (evento_id, usuario_id, asiento_id, clave_idempotencia) "
        "VALUES ($1, 'u', $2, $3) RETURNING id", evento_id, asiento, clave)


async def test_corrida_limpia_no_tiene_violaciones(pool, crear_evento):
    evento_id = await crear_evento(2)
    k1, k2 = f"{evento_id}-a", f"{evento_id}-b"
    r1 = await insertar(pool, evento_id, k1)
    registros = [reg(k1, "exitosa", r1), reg(k2, "agotado")]
    async with pool.acquire() as conn:
        v = await verificar(conn, evento_id, registros)
    assert (v["sobreventa"], v["asientos_duplicados"], v["respuestas_infieles"],
            v["claves_repetidas"]) == (0, 0, 0, 0)
    assert v["retenciones"] is None  # no verificado hasta la F4


async def test_detecta_sobreventa(pool, crear_evento):
    evento_id = await crear_evento(1)
    ids = [await insertar(pool, evento_id, f"{evento_id}-{i}") for i in range(3)]
    registros = [reg(f"{evento_id}-{i}", "exitosa", ids[i]) for i in range(3)]
    async with pool.acquire() as conn:
        v = await verificar(conn, evento_id, registros)
    assert v["sobreventa"] == 2
    assert v["respuestas_infieles"] == 0


async def test_detecta_asiento_duplicado(pool, crear_evento):
    evento_id = await crear_evento(5)
    await insertar(pool, evento_id, f"{evento_id}-a", asiento=3)
    # El índice único lo impide en la base real; se retira solo para sembrar la violación.
    await pool.execute("DROP INDEX reservas_asiento_unico")
    try:
        await insertar(pool, evento_id, f"{evento_id}-b", asiento=3)
        async with pool.acquire() as conn:
            v = await verificar(conn, evento_id, [])
        assert v["asientos_duplicados"] == 1
    finally:
        await pool.execute("DELETE FROM reservas WHERE evento_id = $1", evento_id)
        await pool.execute(
            "CREATE UNIQUE INDEX reservas_asiento_unico ON reservas (evento_id, asiento_id) "
            "WHERE asiento_id IS NOT NULL")


async def test_detecta_respuestas_infieles(pool, crear_evento):
    """Dijo "exitosa" sin reserva, y dijo "agotado" teniendo reserva: ambas son infieles."""
    evento_id = await crear_evento(5)
    k_con_reserva = f"{evento_id}-con"
    await insertar(pool, evento_id, k_con_reserva)
    registros = [
        reg(f"{evento_id}-fantasma", "exitosa", reserva_id=999999),
        reg(k_con_reserva, "agotado"),
    ]
    async with pool.acquire() as conn:
        v = await verificar(conn, evento_id, registros)
    assert v["respuestas_infieles"] == 2
