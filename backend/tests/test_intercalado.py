"""F2: cada anomalía predicha (PREDICTIONS.md), reproducida con un intercalado fijo.

Escenario común: un evento con un solo puesto libre y dos compradores, A y B. Si al
final hay más reservas que la capacidad, hubo sobreventa. Los guiones usan las mismas
sentencias que app/estrategias.py, pero con cada paso ordenado a mano.

ESPERADO es la predicción de PREDICTIONS.md, sin tocar. Si una celda falla, es un
resultado: se documenta aparte, no se edita la predicción.
"""
import uuid

import pytest

from app import estrategias as sql
from tests.intercalador import Comprador, esperar_bloqueo

NIVELES = ("read_committed", "repeatable_read", "serializable")
FALLO_SERIALIZACION = "40001"
UNICO = "23505"

# (estrategia, nivel) -> (reservas finales, SQLSTATE del perdedor o None si B no falló)
# Capacidad 2, una reserva previa: más de 2 reservas es sobreventa.
ESPERADO = {
    ("e1", "read_committed"): (3, None),
    ("e1", "repeatable_read"): (3, None),
    ("e1", "serializable"): (2, FALLO_SERIALIZACION),
    ("e2", "read_committed"): (2, None),  # B no falla: su UPDATE reevalúa y responde "agotado"
    ("e2", "repeatable_read"): (2, FALLO_SERIALIZACION),
    ("e2", "serializable"): (2, FALLO_SERIALIZACION),
    ("e3", "read_committed"): (2, None),
    ("e3", "repeatable_read"): (3, None),  # la predicción más interesante
    ("e3", "serializable"): (2, FALLO_SERIALIZACION),
    ("e4", "read_committed"): (1, UNICO),
    ("e4", "repeatable_read"): (1, UNICO),
    ("e4", "serializable"): (1, UNICO),
}


def clave() -> str:
    return str(uuid.uuid4())


@pytest.fixture
async def escenario(pool, crear_evento):
    """Evento de capacidad 2 con una reserva previa (y vendidos = 1 para E2/E3)."""
    evento_id = await crear_evento(2)
    await pool.execute(
        sql.INSERTAR_POR_CAPACIDAD, evento_id, "previo", clave()
    )
    await pool.execute("UPDATE eventos SET vendidos = 1 WHERE id = $1", evento_id)
    return evento_id


@pytest.fixture
async def par():
    a, b = await Comprador.abrir("A"), await Comprador.abrir("B")
    yield a, b
    await a.cerrar()
    await b.cerrar()


async def total(pool, evento_id) -> int:
    return await pool.fetchval(sql.CONTAR, evento_id)


async def _leer_y_luego_insertar(pool, evento_id, a, b, nivel):
    """E1 (y el cuerpo de E5): ambos cuentan antes de que cualquiera inserte."""
    await a.begin(nivel)
    await b.begin(nivel)
    assert (await a.fetchval(sql.CAPACIDAD, evento_id)).valor == 2
    assert (await b.fetchval(sql.CAPACIDAD, evento_id)).valor == 2
    assert (await a.fetchval(sql.CONTAR, evento_id)).valor == 1
    assert (await b.fetchval(sql.CONTAR, evento_id)).valor == 1
    # Ambos ven "queda un cupo" y deciden insertar.
    salida_a = await a.fetchval(sql.INSERTAR_POR_CAPACIDAD, evento_id, "ana", clave())
    salida_b = await b.fetchval(sql.INSERTAR_POR_CAPACIDAD, evento_id, "beto", clave())
    assert salida_a.ok and salida_b.ok
    commit_a = await a.commit()
    commit_b = await b.commit()
    assert commit_a.ok, "A confirmó primero: SSI no debe abortarla"
    return commit_b


async def _update_condicional(pool, evento_id, observador, a, b, nivel):
    """E2: A toma el cupo; B espera en el UPDATE y reacciona cuando A confirma."""
    await a.begin(nivel)
    await b.begin(nivel)
    assert (await a.fetchval(sql.TOMAR_CUPO, evento_id)).valor is not None
    tarea_b = b.lanzar(sql.TOMAR_CUPO, evento_id)
    await esperar_bloqueo(observador, b, tarea_b)
    await a.fetchval(sql.INSERTAR_POR_CAPACIDAD, evento_id, "ana", clave())
    assert (await a.commit()).ok
    salida_b = await tarea_b
    if not salida_b.ok:
        await b.rollback()
        return salida_b
    if salida_b.valor is None:
        # El WHERE reevaluado sobre la fila nueva no se cumple: "agotado", sin escribir nada.
        assert (await b.commit()).ok
        return salida_b
    await b.fetchval(sql.INSERTAR_POR_CAPACIDAD, evento_id, "beto", clave())
    return await b.commit()


async def _bloqueo_pesimista(pool, evento_id, observador, a, b, nivel):
    """E3: A bloquea la fila del evento; B espera. A inserta y confirma; B retoma."""
    await a.begin(nivel)
    await b.begin(nivel)
    assert (await a.fetchval(sql.CAPACIDAD_BLOQUEANDO, evento_id)).valor == 2
    # La primera sentencia de B es el FOR UPDATE: su foto (RR/SER) se toma aquí, antes de
    # que A inserte. Ese es el punto de la predicción.
    tarea_b = b.lanzar(sql.CAPACIDAD_BLOQUEANDO, evento_id)
    await esperar_bloqueo(observador, b, tarea_b)
    assert (await a.fetchval(sql.CONTAR, evento_id)).valor == 1
    assert (await a.fetchval(sql.INSERTAR_POR_CAPACIDAD, evento_id, "ana", clave())).ok
    assert (await a.commit()).ok
    salida_b = await tarea_b
    assert salida_b.ok and salida_b.valor == 2, "el FOR UPDATE de B no falla: A no modificó la fila"
    contado = await b.fetchval(sql.CONTAR, evento_id)
    if not contado.ok:
        await b.rollback()
        return contado
    if contado.valor >= 2:
        await b.commit()  # "agotado": B vio la reserva de A
        return contado
    insertada = await b.fetchval(sql.INSERTAR_POR_CAPACIDAD, evento_id, "beto", clave())
    if not insertada.ok:
        await b.rollback()
        return insertada
    return await b.commit()


async def _unico(pool, evento_id, observador, a, b, nivel, comprobar_antes=False):
    """E4: A inserta el asiento 5 sin confirmar; B intenta el mismo y espera la unicidad.

    comprobar_antes: ambos consultan que el asiento esté libre antes de insertar (no lo
    hace app/estrategias.py; es el caso que PREDICTIONS.md menciona para SERIALIZABLE).
    """
    await a.begin(nivel)
    await b.begin(nivel)
    if comprobar_antes:
        asiento_libre = "SELECT count(*) FROM reservas WHERE evento_id = $1 AND asiento_id = 5"
        assert (await a.fetchval(asiento_libre, evento_id)).valor == 0
        assert (await b.fetchval(asiento_libre, evento_id)).valor == 0
    assert (await a.fetchval(sql.INSERTAR_POR_ASIENTO, evento_id, "ana", 5, clave())).ok
    tarea_b = b.lanzar(sql.INSERTAR_POR_ASIENTO, evento_id, "beto", 5, clave())
    await esperar_bloqueo(observador, b, tarea_b)
    assert (await a.commit()).ok
    salida_b = await tarea_b
    if not salida_b.ok:
        await b.rollback()
        return salida_b
    return await b.commit()


GUIONES = {"e1": _leer_y_luego_insertar, "e2": _update_condicional,
           "e3": _bloqueo_pesimista, "e4": _unico}
SIN_OBSERVADOR = {"e1"}


@pytest.mark.parametrize("nivel", NIVELES)
@pytest.mark.parametrize("estrategia", ("e1", "e2", "e3", "e4"))
async def test_intercalado(pool, crear_evento, escenario, par, estrategia, nivel):
    a, b = par
    if estrategia == "e4":
        evento_id = await crear_evento(10)  # los asientos se numeran 1..capacidad
    else:
        evento_id = escenario
    reservas_finales, sqlstate_esperado = ESPERADO[(estrategia, nivel)]

    async with pool.acquire() as observador:
        if estrategia in SIN_OBSERVADOR:
            salida_b = await GUIONES[estrategia](pool, evento_id, a, b, nivel)
        else:
            salida_b = await GUIONES[estrategia](pool, evento_id, observador, a, b, nivel)

    assert salida_b.sqlstate == sqlstate_esperado
    # E4 parte de cero reservas (el evento propio no tiene la "previa").
    assert await total(pool, evento_id) == reservas_finales
    if estrategia == "e4":
        asiento_5 = await pool.fetchval(
            "SELECT count(*) FROM reservas WHERE evento_id = $1 AND asiento_id = 5", evento_id
        )
        assert asiento_5 == 1, "nunca dos reservas del mismo asiento"


@pytest.mark.xfail(strict=True, reason=(
    "Predicción refutada (PREDICTIONS.md, E4 en SERIALIZABLE): con comprobación previa del "
    "asiento, Postgres reporta 40001 y no 23505. Ver test_e4_serializable_con_comprobacion_previa."
))
async def test_e4_serializable_prediccion_con_comprobacion_previa(pool, crear_evento, par):
    a, b = par
    evento_id = await crear_evento(10)
    async with pool.acquire() as observador:
        salida_b = await _unico(pool, evento_id, observador, a, b, "serializable", True)
    assert salida_b.sqlstate == UNICO


async def test_e4_serializable_con_comprobacion_previa(pool, crear_evento, par):
    """Resultado observado: la lectura previa del asiento convierte 23505 en 40001."""
    a, b = par
    evento_id = await crear_evento(10)
    async with pool.acquire() as observador:
        salida_b = await _unico(pool, evento_id, observador, a, b, "serializable", True)
    assert salida_b.sqlstate == FALLO_SERIALIZACION
    assert await total(pool, evento_id) == 1


async def test_e4_si_el_primero_hace_rollback_el_segundo_procede(pool, crear_evento, par):
    """La otra mitad de la predicción de E4: el que espera no falla si el otro se arrepiente."""
    a, b = par
    evento_id = await crear_evento(10)
    async with pool.acquire() as observador:
        for nivel in NIVELES:
            await pool.execute("DELETE FROM reservas WHERE evento_id = $1", evento_id)
            await a.begin(nivel)
            await b.begin(nivel)
            assert (await a.fetchval(sql.INSERTAR_POR_ASIENTO, evento_id, "ana", 5, clave())).ok
            tarea_b = b.lanzar(sql.INSERTAR_POR_ASIENTO, evento_id, "beto", 5, clave())
            await esperar_bloqueo(observador, b, tarea_b)
            await a.rollback()
            assert (await tarea_b).ok, nivel
            assert (await b.commit()).ok
            assert await total(pool, evento_id) == 1
            usuario = await pool.fetchval(
                "SELECT usuario_id FROM reservas WHERE evento_id = $1", evento_id
            )
            assert usuario == "beto"


async def test_e5_reintenta_y_termina_en_agotado(pool, escenario, par):
    """E5 en SERIALIZABLE: B aborta con 40001 y, al reintentar, ve la reserva de A."""
    a, b = par
    evento_id = escenario
    fallo = await _leer_y_luego_insertar(pool, evento_id, a, b, "serializable")
    assert fallo.sqlstate == FALLO_SERIALIZACION
    await b.begin("serializable")
    assert (await b.fetchval(sql.CONTAR, evento_id)).valor == 2  # ya no queda cupo
    await b.rollback()
    assert await total(pool, evento_id) == 2


@pytest.mark.parametrize("nivel", ("read_committed", "repeatable_read"))
async def test_e5_sin_serializable_se_mide_sin_prediccion(pool, escenario, par, nivel):
    """E5 en RC/RR (PREDICTIONS.md, decisiones de alcance): sin predicción previa.

    El guion es el de E1 porque E5 es E1 más reintento ante 40001, y en estos niveles
    ese error no aparece en este escenario. Lo que salga se registra tal cual.
    """
    a, b = par
    salida_b = await _leer_y_luego_insertar(pool, escenario, a, b, nivel)
    # Observado, no predicho: sin 40001 no hay reintento y E5 sobrevende igual que E1.
    assert salida_b.ok
    assert await total(pool, escenario) == 3
