"""La matriz de DESIGN.md §8.3: estrategia x aislamiento x concurrencia x contención x semilla.

Para cada (estrategia, aislamiento) levanta una API con esa configuración (son parámetros
de la API, §6), corre las celdas contra ella y apaga el servidor. Escribe un JSON por
corrida y, al final, RESULTS.md con media y rango por celda.

Uso:  python -m carga.matriz [--rapida] [--salida DIR]
"""
import argparse
import asyncio
import json
import os
import statistics
import subprocess
import sys
import time
import uuid
from pathlib import Path

import asyncpg
import httpx

from carga.generador import correr
from carga.invariantes import verificar

ESTRATEGIAS = ("e1", "e2", "e3", "e4", "e5")
NIVELES = ("read_committed", "repeatable_read", "serializable")
SEMILLAS = (1, 2, 3)
PUERTO = 8765
DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql://reservas:reservas@127.0.0.1:5433/reservas")


def contenciones(compradores: int) -> dict[str, int]:
    """Capacidad holgada (sobran puestos) y escasa (1 de cada 10 compradores alcanza)."""
    return {"holgada": compradores * 2, "escasa": max(1, compradores // 10)}


def levantar_api(estrategia: str, aislamiento: str, pool_size: int) -> subprocess.Popen:
    entorno = {**os.environ, "DATABASE_URL": DATABASE_URL, "ESTRATEGIA": estrategia,
               "AISLAMIENTO": aislamiento, "POOL_SIZE": str(pool_size)}
    api = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--port", str(PUERTO),
         "--log-level", "warning"],
        env=entorno, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(100):
        try:
            if httpx.get(f"http://127.0.0.1:{PUERTO}/salud", timeout=1).status_code == 200:
                return api
        except httpx.HTTPError:
            time.sleep(0.1)
    api.kill()
    raise RuntimeError(f"la API de {estrategia}/{aislamiento} no levantó")


def percentil(valores: list[float], p: float) -> float:
    ordenados = sorted(valores)
    return ordenados[min(len(ordenados) - 1, int(p * len(ordenados)))]


async def una_corrida(conn, estrategia, aislamiento, compradores, contencion, capacidad, semilla,
                      max_reintentos_cliente):
    evento_id = await conn.fetchval(
        "INSERT INTO eventos (nombre, capacidad) VALUES ($1, $2) RETURNING id",
        f"matriz-{uuid.uuid4()}", capacidad)
    corrida = await correr(f"http://127.0.0.1:{PUERTO}", evento_id, compradores,
                           capacidad, estrategia == "e4", semilla,
                           max_reintentos_cliente)
    violaciones = await verificar(conn, evento_id, corrida.registros)
    regs = corrida.registros
    exitosas = [r for r in regs if r.estado == "exitosa"]
    latencias = [r.latencia for r in regs]
    return {
        "estrategia": estrategia, "aislamiento": aislamiento, "compradores": compradores,
        "contencion": contencion, "capacidad": capacidad, "semilla": semilla,
        **violaciones,
        "exitosas": len(exitosas),
        "abortadas": sum(r.estado == "abortada" for r in regs),
        "errores": sum(r.estado == "error" for r in regs),
        "reintentos": sum(r.reintentos for r in regs),
        "throughput": len(exitosas) / corrida.duracion,
        "p50_ms": percentil(latencias, 0.50) * 1000,
        "p99_ms": percentil(latencias, 0.99) * 1000,
    }


async def correr_matriz(concurrencias, salida: Path, pool_size: int,
                        max_reintentos_cliente: int) -> list[dict]:
    resultados = []
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        for estrategia in ESTRATEGIAS:
            for aislamiento in NIVELES:
                api = levantar_api(estrategia, aislamiento, pool_size)
                try:
                    for compradores in concurrencias:
                        for contencion, capacidad in contenciones(compradores).items():
                            for semilla in SEMILLAS:
                                r = await una_corrida(conn, estrategia, aislamiento,
                                                      compradores, contencion, capacidad, semilla,
                                                      max_reintentos_cliente)
                                resultados.append(r)
                                print(f"{estrategia} {aislamiento} n={compradores} "
                                      f"{contencion} s={semilla}: sobreventa={r['sobreventa']} "
                                      f"exitosas={r['exitosas']} abortadas={r['abortadas']}",
                                      flush=True)
                finally:
                    api.kill()
                    api.wait()
    finally:
        await conn.close()
    salida.mkdir(parents=True, exist_ok=True)
    (salida / "corridas.json").write_text(json.dumps(resultados, indent=1), encoding="utf-8")
    return resultados


def celda(valores: list[float], decimales: int = 0) -> str:
    media = statistics.mean(valores)
    return f"{media:.{decimales}f} [{min(valores):.{decimales}f}–{max(valores):.{decimales}f}]"


def escribir_resultados(resultados: list[dict], destino: Path, pool_size: int,
                        reintentos_cliente: int) -> None:
    """Media y rango por celda; nunca la mejor corrida (§8.3)."""
    grupos: dict[tuple, list[dict]] = {}
    for r in resultados:
        clave = (r["estrategia"], r["aislamiento"], r["compradores"], r["contencion"])
        grupos.setdefault(clave, []).append(r)
    lineas = [
        "# RESULTS", "",
        f"Generado por `python -m carga.matriz`. Pool de la API: {pool_size} conexiones. "
        f"Reintentos del cliente ante 40001: hasta {reintentos_cliente} (0 = no reintenta). "
        f"Cada celda: media [mín–máx] de {len(SEMILLAS)} semillas.", "",
        "Violaciones: **sobreventa** = reservas por encima de la capacidad; **dup** = asientos "
        "con más de una reserva; **infiel** = respuestas que no corresponden a la base; "
        "**clave** = claves de idempotencia repetidas. Invariante 5 (retenciones): F4.", "",
        "| estr. | aisl. | n | contención | sobreventa | dup | infiel | clave | exitosas "
        "| abortadas | errores | reintentos | compras/s | p50 ms | p99 ms |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for (estrategia, aislamiento, n, contencion), corridas in grupos.items():
        def col(campo, d=0):
            return celda([c[campo] for c in corridas], d)
        lineas.append(
            f"| {estrategia} | {aislamiento} | {n} | {contencion} | {col('sobreventa')} "
            f"| {col('asientos_duplicados')} | {col('respuestas_infieles')} "
            f"| {col('claves_repetidas')} | {col('exitosas')} | {col('abortadas')} "
            f"| {col('errores')} | {col('reintentos')} | {col('throughput', 1)} "
            f"| {col('p50_ms', 1)} | {col('p99_ms', 1)} |")
    destino.write_text("\n".join(lineas) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rapida", action="store_true", help="solo 10 y 100 compradores")
    parser.add_argument("--salida", type=Path, default=Path("resultados"))
    parser.add_argument("--pool", type=int, default=20)
    parser.add_argument("--reintentos-cliente", type=int, default=0,
                        help="veces que el cliente repite una compra abortada (40001)")
    args = parser.parse_args()
    concurrencias = (10, 100) if args.rapida else (10, 100, 1000)
    resultados = asyncio.run(correr_matriz(concurrencias, args.salida, args.pool,
                                         args.reintentos_cliente))
    escribir_resultados(resultados, args.salida / "RESULTS.md", args.pool,
                        args.reintentos_cliente)


if __name__ == "__main__":
    main()
