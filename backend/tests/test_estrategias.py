"""F1: cada estrategia compra un puesto. Los intercalados y las anomalías son de la F2."""
import uuid

import pytest

from app import config
from app.estrategias import ESTRATEGIAS, comprar

NIVELES = config.NIVELES_VALIDOS
POR_CAPACIDAD = ("e1", "e2", "e3", "e5")


def clave() -> str:
    return str(uuid.uuid4())


async def reservas_de(pool, evento_id):
    return await pool.fetch("SELECT * FROM reservas WHERE evento_id = $1", evento_id)


def test_hay_una_funcion_por_estrategia_configurable():
    assert set(ESTRATEGIAS) == set(config.ESTRATEGIAS_VALIDAS)


@pytest.mark.parametrize("aislamiento", NIVELES)
@pytest.mark.parametrize("estrategia", config.ESTRATEGIAS_VALIDAS)
async def test_compra_un_puesto(pool, crear_evento, estrategia, aislamiento):
    evento_id = await crear_evento(10)
    asiento = 3 if estrategia == "e4" else None

    r = await comprar(
        pool, evento_id, "ana", clave(), asiento, estrategia=estrategia, aislamiento=aislamiento
    )

    assert r.estado == "exitosa"
    filas = await reservas_de(pool, evento_id)
    assert len(filas) == 1
    assert filas[0]["id"] == r.reserva_id
    assert filas[0]["asiento_id"] == asiento
    assert filas[0]["estado"] == "confirmada"


async def test_e2_incrementa_el_contador(pool, crear_evento):
    evento_id = await crear_evento(10)
    await comprar(pool, evento_id, "ana", clave(), estrategia="e2", aislamiento="read_committed")
    vendidos = await pool.fetchval("SELECT vendidos FROM eventos WHERE id = $1", evento_id)
    assert vendidos == 1


@pytest.mark.parametrize("estrategia", POR_CAPACIDAD)
async def test_sin_cupo_responde_agotado(pool, crear_evento, estrategia):
    evento_id = await crear_evento(1)
    kw = dict(estrategia=estrategia, aislamiento="read_committed")
    assert (await comprar(pool, evento_id, "ana", clave(), **kw)).estado == "exitosa"

    r = await comprar(pool, evento_id, "beto", clave(), **kw)

    assert r.estado == "agotado"
    assert len(await reservas_de(pool, evento_id)) == 1


@pytest.mark.parametrize("asiento", [0, -1, 6, None])
async def test_e4_rechaza_asientos_fuera_de_rango(pool, crear_evento, asiento):
    evento_id = await crear_evento(5)
    r = await comprar(pool, evento_id, "ana", clave(), asiento, estrategia="e4")
    assert r.estado == "asiento_invalido"
    assert await reservas_de(pool, evento_id) == []


@pytest.mark.parametrize("asiento", [1, 5])
async def test_e4_acepta_los_extremos(pool, crear_evento, asiento):
    evento_id = await crear_evento(5)
    r = await comprar(pool, evento_id, "ana", clave(), asiento, estrategia="e4")
    assert r.estado == "exitosa"


@pytest.mark.parametrize("aislamiento", NIVELES)
async def test_e4_asiento_ocupado(pool, crear_evento, aislamiento):
    evento_id = await crear_evento(5)
    kw = dict(estrategia="e4", aislamiento=aislamiento)
    assert (await comprar(pool, evento_id, "ana", clave(), 2, **kw)).estado == "exitosa"

    r = await comprar(pool, evento_id, "beto", clave(), 2, **kw)

    assert r.estado == "ocupado"
    assert len(await reservas_de(pool, evento_id)) == 1


async def test_la_configuracion_por_defecto_elige_la_estrategia(pool, crear_evento):
    evento_id = await crear_evento(10)
    r = await comprar(pool, evento_id, "ana", clave())
    assert r.estado == "exitosa"
