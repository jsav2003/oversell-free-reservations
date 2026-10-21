"""F5: estado en vivo por SSE y laboratorio, contra una API real en un proceso aparte."""
import json

import httpx
import pytest

from tests.test_fallas import apis, URL  # noqa: F401  (apis es una fixture)


async def eventos_sse(respuesta):
    """Itera los mensajes (nombre, datos) de una respuesta text/event-stream."""
    nombre = None
    async for linea in respuesta.aiter_lines():
        if linea.startswith("event: "):
            nombre = linea[7:]
        elif linea.startswith("data: "):
            yield nombre, json.loads(linea[6:])


async def siguiente(generador, nombre):
    async for n, datos in generador:
        if n == nombre:
            return datos
    raise AssertionError(f"el stream terminó sin '{nombre}'")


async def test_estado_y_evento_inexistente(pool, apis):
    apis("e4", "read_committed")
    creado = httpx.post(f"{URL}/eventos", json={"nombre": "t", "capacidad": 7}, timeout=10).json()
    try:
        estado = httpx.get(f"{URL}/eventos/{creado['evento_id']}/estado", timeout=10).json()
        assert estado["capacidad"] == 7 and estado["total"] == 0 and estado["asientos"] == {}
        assert "ahora" in estado
        assert httpx.get(f"{URL}/eventos/999999999/estado", timeout=10).status_code == 404
        assert httpx.get(f"{URL}/eventos/999999999/stream", timeout=10).status_code == 404
    finally:
        await pool.execute("DELETE FROM eventos WHERE id = $1", creado["evento_id"])


async def test_el_stream_cuenta_retencion_y_confirmacion(pool, apis):
    apis("e4", "read_committed")
    evento_id = httpx.post(f"{URL}/eventos", json={"capacidad": 10}, timeout=10).json()["evento_id"]
    try:
        async with httpx.AsyncClient(timeout=15) as cliente:
            async with cliente.stream("GET", f"{URL}/eventos/{evento_id}/stream") as r:
                assert r.headers["content-type"].startswith("text/event-stream")
                mensajes = eventos_sse(r)
                inicial = await siguiente(mensajes, "estado")
                assert inicial["asientos"] == {} and inicial["capacidad"] == 10

                ret = (await cliente.post(f"{URL}/retenciones", json={
                    "evento_id": evento_id, "usuario_id": "ana", "clave_idempotencia":
                    f"v-{evento_id}", "asiento_id": 3, "ttl_segundos": 60})).json()
                cambio = await siguiente(mensajes, "asiento")
                assert (cambio["asiento_id"], cambio["estado"], cambio["usuario_id"]) \
                    == (3, "retenida", "ana")
                assert cambio["vence_en"] and cambio["ahora"]  # la UI cuenta desde el servidor
                assert (await siguiente(mensajes, "resumen"))["total"] == 1

                await cliente.post(f"{URL}/reservas/{ret['reserva_id']}/confirmar",
                                   json={"usuario_id": "ana"})
                cambio = await siguiente(mensajes, "asiento")
                assert cambio["estado"] == "confirmada"
    finally:
        await pool.execute("DELETE FROM reservas WHERE evento_id = $1", evento_id)
        await pool.execute("DELETE FROM eventos WHERE id = $1", evento_id)


async def test_un_asiento_que_se_libera_se_anuncia(pool, apis):
    apis("e4", "read_committed")
    evento_id = httpx.post(f"{URL}/eventos", json={"capacidad": 10}, timeout=10).json()["evento_id"]
    try:
        async with httpx.AsyncClient(timeout=15) as cliente:
            await cliente.post(f"{URL}/retenciones", json={
                "evento_id": evento_id, "usuario_id": "ana", "clave_idempotencia":
                f"v-{evento_id}", "asiento_id": 5, "ttl_segundos": 60})
            async with cliente.stream("GET", f"{URL}/eventos/{evento_id}/stream") as r:
                mensajes = eventos_sse(r)
                assert "5" in (await siguiente(mensajes, "estado"))["asientos"]
                await pool.execute("DELETE FROM reservas WHERE evento_id = $1", evento_id)
                cambio = await siguiente(mensajes, "asiento")
                assert cambio["asiento_id"] == 5 and cambio["estado"] is None
    finally:
        await pool.execute("DELETE FROM reservas WHERE evento_id = $1", evento_id)
        await pool.execute("DELETE FROM eventos WHERE id = $1", evento_id)


@pytest.mark.parametrize("estrategia,aislamiento", [
    ("e2", "read_committed"), ("e4", "serializable"), ("e5", "serializable")])
async def test_laboratorio_no_sobrevende_con_estrategias_correctas(
        pool, apis, estrategia, aislamiento):
    apis("e5", "serializable")
    evento_id = httpx.post(f"{URL}/eventos", json={"capacidad": 10}, timeout=10).json()["evento_id"]
    try:
        r = httpx.post(f"{URL}/laboratorio/{evento_id}/correr", timeout=120, json={
            "estrategia": estrategia, "aislamiento": aislamiento, "compradores": 40,
            "reintentar_abortadas": True}).json()
        assert r["violaciones"]["sobreventa"] == 0
        assert r["violaciones"]["respuestas_infieles"] == 0
        assert sum(r["respuestas"].values()) == 40
        assert {"p50_ms", "p99_ms", "throughput", "duracion", "reintentos"} <= r.keys()
        # Segunda corrida sobre el mismo evento: no se mezclan resultados.
        otra = httpx.post(f"{URL}/laboratorio/{evento_id}/correr", timeout=30, json={
            "estrategia": estrategia, "aislamiento": aislamiento, "compradores": 5})
        assert otra.status_code == 409
    finally:
        await pool.execute("DELETE FROM reservas WHERE evento_id = $1", evento_id)
        await pool.execute("DELETE FROM eventos WHERE id = $1", evento_id)


async def test_laboratorio_rechaza_parametros_invalidos(pool, apis):
    apis("e5", "serializable")
    evento_id = httpx.post(f"{URL}/eventos", json={"capacidad": 10}, timeout=10).json()["evento_id"]
    try:
        r = httpx.post(f"{URL}/laboratorio/{evento_id}/correr", timeout=10, json={
            "estrategia": "e9", "aislamiento": "serializable", "compradores": 5})
        assert r.status_code == 422
    finally:
        await pool.execute("DELETE FROM eventos WHERE id = $1", evento_id)
