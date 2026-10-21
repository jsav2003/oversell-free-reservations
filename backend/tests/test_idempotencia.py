"""F4, §3.4: un reintento con la misma clave devuelve la reserva existente, no crea otra."""
import asyncio
import uuid

import pytest

from app import config
from app.estrategias import comprar

NIVELES = config.NIVELES_VALIDOS
ESTRATEGIAS = config.ESTRATEGIAS_VALIDAS


def clave() -> str:
    return str(uuid.uuid4())


async def reservas(pool, evento_id):
    return await pool.fetch("SELECT * FROM reservas WHERE evento_id = $1", evento_id)


@pytest.mark.parametrize("aislamiento", NIVELES)
@pytest.mark.parametrize("estrategia", ESTRATEGIAS)
async def test_reintento_devuelve_la_misma_reserva(pool, crear_evento, estrategia, aislamiento):
    evento_id = await crear_evento(10)
    k = clave()
    asiento = 3 if estrategia == "e4" else None
    kw = dict(estrategia=estrategia, aislamiento=aislamiento)

    primera = await comprar(pool, evento_id, "ana", k, asiento, **kw)
    segunda = await comprar(pool, evento_id, "ana", k, asiento, **kw)

    assert primera.estado == "exitosa"
    assert segunda.estado == "exitosa" and segunda.reserva_id == primera.reserva_id
    assert len(await reservas(pool, evento_id)) == 1


@pytest.mark.parametrize("estrategia", ("e1", "e2", "e3", "e5"))
async def test_reintento_cuando_ya_no_queda_cupo_sigue_siendo_exitoso(
        pool, crear_evento, estrategia):
    """El caso clave: Ana compró el último cupo y el reintento no puede decirle "agotado"."""
    evento_id = await crear_evento(1)
    k = clave()
    kw = dict(estrategia=estrategia, aislamiento="read_committed")
    primera = await comprar(pool, evento_id, "ana", k, **kw)
    assert primera.estado == "exitosa"

    reintento = await comprar(pool, evento_id, "ana", k, **kw)

    assert reintento.estado == "exitosa" and reintento.reserva_id == primera.reserva_id


async def test_e4_el_reintento_no_es_ocupado(pool, crear_evento):
    evento_id = await crear_evento(5)
    k = clave()
    kw = dict(estrategia="e4", aislamiento="read_committed")
    primera = await comprar(pool, evento_id, "ana", k, 2, **kw)
    reintento = await comprar(pool, evento_id, "ana", k, 2, **kw)
    assert reintento.estado == "exitosa" and reintento.reserva_id == primera.reserva_id


@pytest.mark.parametrize("estrategia", ESTRATEGIAS)
async def test_la_misma_clave_a_la_vez_crea_una_sola_reserva(pool, crear_evento, estrategia):
    evento_id = await crear_evento(10)
    k = clave()
    asiento = 3 if estrategia == "e4" else None

    resultados = await asyncio.gather(*(
        comprar(pool, evento_id, "ana", k, asiento, estrategia=estrategia,
                aislamiento="read_committed")
        for _ in range(8)))

    filas = await reservas(pool, evento_id)
    assert len(filas) == 1
    assert {r.reserva_id for r in resultados if r.estado == "exitosa"} == {filas[0]["id"]}


async def test_e2_el_reintento_no_cuenta_dos_veces_el_cupo(pool, crear_evento):
    evento_id = await crear_evento(5)
    k = clave()
    for _ in range(3):
        await comprar(pool, evento_id, "ana", k, estrategia="e2", aislamiento="read_committed")
    assert await pool.fetchval("SELECT vendidos FROM eventos WHERE id = $1", evento_id) == 1


async def test_clave_de_otro_usuario_no_es_un_reintento(pool, crear_evento):
    evento_id = await crear_evento(5)
    k = clave()
    kw = dict(estrategia="e1", aislamiento="read_committed")
    await comprar(pool, evento_id, "ana", k, **kw)
    r = await comprar(pool, evento_id, "beto", k, **kw)
    assert r.estado == "clave_reutilizada"
    assert len(await reservas(pool, evento_id)) == 1
