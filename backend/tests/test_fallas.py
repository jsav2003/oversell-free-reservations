"""F4, §8.4: inyección de fallas con una API real en un proceso aparte.

En cada escenario el cliente reintenta con su clave de idempotencia y el verificador de
invariantes (§8.2) decide. Al verificador se le pasa solo lo que el cliente realmente supo: la
respuesta que se perdió no es una afirmación del servidor, así que no cuenta como registro.
"""
import os
import subprocess
import sys
import time
import uuid

import httpx
import pytest

from carga.generador import Registro
from carga.invariantes import verificar
from tests.conftest import DATABASE_URL

PUERTO = 8770
URL = f"http://127.0.0.1:{PUERTO}"


def levantar_api(estrategia, aislamiento, falla="") -> subprocess.Popen:
    entorno = {**os.environ, "DATABASE_URL": DATABASE_URL, "ESTRATEGIA": estrategia,
               "AISLAMIENTO": aislamiento, "POOL_SIZE": "5", "FALLA": falla}
    api = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--port", str(PUERTO),
         "--log-level", "warning"],
        env=entorno, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(100):
        try:
            if httpx.get(f"{URL}/salud", timeout=1).status_code == 200:
                return api
        except httpx.HTTPError:
            time.sleep(0.1)
    api.kill()
    raise RuntimeError("la API de prueba no levantó")


@pytest.fixture
def apis():
    """Las APIs que levante el test, todas apagadas al terminar."""
    lanzadas = []

    def lanzar(*args, **kwargs):
        api = levantar_api(*args, **kwargs)
        lanzadas.append(api)
        return api

    yield lanzar
    for api in lanzadas:
        if api.poll() is None:
            api.kill()
        api.wait()


def pedido(evento_id, clave, asiento=None, usuario="ana"):
    return {"evento_id": evento_id, "usuario_id": usuario,
            "clave_idempotencia": clave, "asiento_id": asiento}


def registro_de(clave, respuesta, asiento=None):
    return Registro("ana", clave, asiento, respuesta["estado"], respuesta["reserva_id"],
                    respuesta["reintentos"], 0.0)


@pytest.mark.parametrize("estrategia,aislamiento", [
    ("e2", "read_committed"), ("e4", "repeatable_read"), ("e5", "serializable")])
async def test_la_api_muere_entre_el_commit_y_la_respuesta(
        pool, crear_evento, apis, estrategia, aislamiento):
    evento_id = await crear_evento(10)
    clave = f"{evento_id}-{uuid.uuid4()}"
    asiento = 4 if estrategia == "e4" else None

    # 1. La API confirma en la base y muere sin responder: el cliente no sabe si compró.
    api = apis(estrategia, aislamiento, falla="morir_tras_commit")
    with pytest.raises(httpx.HTTPError):
        httpx.post(f"{URL}/reservas", json=pedido(evento_id, clave, asiento), timeout=10)
    api.wait(timeout=10)
    assert api.returncode == 1, "la API debía morir sin responder"
    assert await pool.fetchval(
        "SELECT count(*) FROM reservas WHERE clave_idempotencia = $1", clave) == 1, \
        "el commit sí ocurrió: la reserva existe aunque el cliente no lo sepa"

    # 2. Otra instancia, sana. El cliente reintenta con la MISMA clave.
    apis(estrategia, aislamiento)
    respuesta = httpx.post(f"{URL}/reservas", json=pedido(evento_id, clave, asiento),
                           timeout=10).json()

    assert respuesta["estado"] == "exitosa"
    async with pool.acquire() as conn:
        v = await verificar(conn, evento_id, [registro_de(clave, respuesta, asiento)])
    assert v["reservas"] == 1
    assert (v["sobreventa"], v["asientos_duplicados"], v["respuestas_infieles"],
            v["claves_repetidas"], v["retenciones"]) == (0, 0, 0, 0, 0)
    if estrategia == "e2":
        assert await pool.fetchval(
            "SELECT vendidos FROM eventos WHERE id = $1", evento_id) == 1


async def test_el_cliente_corta_antes_de_la_respuesta(pool, crear_evento, apis):
    """El cliente se cansa de esperar y cierra la conexión; el servidor ya había confirmado."""
    evento_id = await crear_evento(1)  # un solo cupo: el reintento no puede decir "agotado"
    clave = f"{evento_id}-{uuid.uuid4()}"
    apis("e1", "read_committed", falla="demora_tras_commit")

    with pytest.raises(httpx.ReadTimeout):
        httpx.post(f"{URL}/reservas", json=pedido(evento_id, clave), timeout=0.5)

    respuesta = httpx.post(f"{URL}/reservas", json=pedido(evento_id, clave), timeout=15).json()

    assert respuesta["estado"] == "exitosa"
    async with pool.acquire() as conn:
        v = await verificar(conn, evento_id, [registro_de(clave, respuesta)])
    assert v["reservas"] == 1 and v["sobreventa"] == 0 and v["respuestas_infieles"] == 0


async def test_pago_pausado_mas_alla_del_vencimiento(pool, crear_evento, apis):
    """Ana retiene, el pago se demora más que la retención, Beto toma el asiento y Ana paga."""
    evento_id = await crear_evento(5)
    apis("e4", "read_committed")

    ana = httpx.post(f"{URL}/retenciones", timeout=10, json={
        "evento_id": evento_id, "usuario_id": "ana", "clave_idempotencia": f"{evento_id}-a",
        "asiento_id": 3, "ttl_segundos": 0.5}).json()
    assert ana["estado"] == "retenida"
    time.sleep(1.0)  # el pago de Ana sigue "en curso" después del vencimiento

    beto = httpx.post(f"{URL}/retenciones", timeout=10, json={
        "evento_id": evento_id, "usuario_id": "beto", "clave_idempotencia": f"{evento_id}-b",
        "asiento_id": 3, "ttl_segundos": 60}).json()
    assert beto["estado"] == "retenida"

    pago_ana = httpx.post(f"{URL}/reservas/{ana['reserva_id']}/confirmar", timeout=10,
                          json={"usuario_id": "ana"}).json()
    pago_beto = httpx.post(f"{URL}/reservas/{beto['reserva_id']}/confirmar", timeout=10,
                           json={"usuario_id": "beto"}).json()

    assert pago_ana["estado"] in ("vencida", "no_existe")
    assert pago_beto["estado"] == "exitosa"
    async with pool.acquire() as conn:
        v = await verificar(conn, evento_id, [])
    assert v["retenciones"] == 0 and v["asientos_duplicados"] == 0
    assert await pool.fetchval(
        "SELECT confirmada_por FROM reservas WHERE evento_id = $1 AND asiento_id = 3",
        evento_id) == "beto"
