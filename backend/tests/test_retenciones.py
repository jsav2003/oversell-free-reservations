"""F4, §5: retenciones con vencimiento. Los vencimientos usan TTL de décimas de segundo."""
import asyncio
import uuid

from app.retenciones import confirmar, retener


def clave() -> str:
    return str(uuid.uuid4())


async def test_retener_y_confirmar(pool, crear_evento):
    evento_id = await crear_evento(5)
    r = await retener(pool, evento_id, "ana", clave(), 3, 60)
    assert r.estado == "retenida" and r.vence_en is not None

    c = await confirmar(pool, r.reserva_id, "ana")

    assert c.estado == "exitosa"
    fila = await pool.fetchrow("SELECT * FROM reservas WHERE id = $1", r.reserva_id)
    assert fila["estado"] == "confirmada"
    assert fila["confirmada_por"] == "ana" and fila["confirmada_en"] <= fila["vence_en"]


async def test_el_vencimiento_lo_pone_la_base(pool, crear_evento):
    evento_id = await crear_evento(5)
    r = await retener(pool, evento_id, "ana", clave(), 1, 60)
    ahora_base = await pool.fetchval("SELECT now()")
    assert 55 < (r.vence_en - ahora_base).total_seconds() <= 60


async def test_asiento_retenido_esta_ocupado_para_otro(pool, crear_evento):
    evento_id = await crear_evento(5)
    await retener(pool, evento_id, "ana", clave(), 2, 60)
    assert (await retener(pool, evento_id, "beto", clave(), 2, 60)).estado == "ocupado"


async def test_confirmar_con_la_retencion_vencida_falla(pool, crear_evento):
    """El pago llega después del vencimiento: la confirmación se rechaza, no se cuela."""
    evento_id = await crear_evento(5)
    r = await retener(pool, evento_id, "ana", clave(), 2, 0.2)
    await asyncio.sleep(0.4)

    assert (await confirmar(pool, r.reserva_id, "ana")).estado == "vencida"
    assert await pool.fetchval(
        "SELECT estado FROM reservas WHERE id = $1", r.reserva_id) == "retenida"


async def test_vencida_la_toma_otro_y_el_primero_ya_no_puede_confirmar(pool, crear_evento):
    """Ana paga tarde, Beto ya tomó el asiento: solo Beto puede quedarse con él."""
    evento_id = await crear_evento(5)
    ana = await retener(pool, evento_id, "ana", clave(), 2, 0.2)
    await asyncio.sleep(0.4)

    beto = await retener(pool, evento_id, "beto", clave(), 2, 60)
    assert beto.estado == "retenida"
    assert (await confirmar(pool, ana.reserva_id, "ana")).estado == "no_existe"
    assert (await confirmar(pool, beto.reserva_id, "beto")).estado == "exitosa"
    assert await pool.fetchval(
        "SELECT count(*) FROM reservas WHERE evento_id = $1 AND asiento_id = 2", evento_id) == 1


async def test_no_se_puede_confirmar_la_retencion_de_otro(pool, crear_evento):
    evento_id = await crear_evento(5)
    r = await retener(pool, evento_id, "ana", clave(), 2, 60)
    assert (await confirmar(pool, r.reserva_id, "intruso")).estado == "ajena"
    assert await pool.fetchval(
        "SELECT estado FROM reservas WHERE id = $1", r.reserva_id) == "retenida"


async def test_confirmar_dos_veces_es_idempotente(pool, crear_evento):
    evento_id = await crear_evento(5)
    r = await retener(pool, evento_id, "ana", clave(), 2, 60)
    assert (await confirmar(pool, r.reserva_id, "ana")).estado == "exitosa"
    assert (await confirmar(pool, r.reserva_id, "ana")).estado == "exitosa"


async def test_confirmar_una_reserva_que_no_existe(pool):
    assert (await confirmar(pool, 999_999_999, "ana")).estado == "no_existe"


async def test_reintento_de_la_misma_retencion_devuelve_la_misma(pool, crear_evento):
    evento_id = await crear_evento(5)
    k = clave()
    primera = await retener(pool, evento_id, "ana", k, 2, 60)
    segunda = await retener(pool, evento_id, "ana", k, 2, 60)
    assert segunda.estado == "retenida" and segunda.reserva_id == primera.reserva_id


async def test_asiento_fuera_de_rango(pool, crear_evento):
    evento_id = await crear_evento(5)
    assert (await retener(pool, evento_id, "ana", clave(), 6, 60)).estado == "asiento_invalido"


async def test_muchos_compiten_por_un_asiento_vencido_y_gana_uno(pool, crear_evento):
    """La limpieza en el conflicto bajo carga: 30 compradores sobre un asiento ya vencido."""
    evento_id = await crear_evento(5)
    await retener(pool, evento_id, "viejo", clave(), 4, 0.2)
    await asyncio.sleep(0.4)

    resultados = await asyncio.gather(
        *(retener(pool, evento_id, f"u{i}", clave(), 4, 60) for i in range(30)))

    assert sum(r.estado == "retenida" for r in resultados) == 1
    assert sum(r.estado == "ocupado" for r in resultados) == 29
    assert await pool.fetchval(
        "SELECT count(*) FROM reservas WHERE evento_id = $1 AND asiento_id = 4", evento_id) == 1
