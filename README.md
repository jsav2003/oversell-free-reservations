# reservas-sin-sobreventa

Una aplicación de reservas (FastAPI + asyncpg + Postgres 18.6 + Next.js) cuyo objetivo no es
la app: es **demostrar con evidencia qué estrategias de concurrencia en Postgres sobrevenden,
cuáles no, por qué y cuánto cuesta cada una**. El producto es una tabla de 5 estrategias × 3
niveles de aislamiento × varias concurrencias, y cada anomalía reproducida de forma
determinista en un test.

## Lo que hay que ver en 30 segundos

**E1 ("contar y luego insertar") sobrevende en READ COMMITTED y también en REPEATABLE READ.**
Que el nivel sea más estricto no ayuda: su foto de la base es justo lo que impide ver la
reserva del otro comprador.

Sobreventa medida bajo carga (270 corridas, 18 por combinación, pool de 20 conexiones):

| Estrategia | READ COMMITTED | REPEATABLE READ | SERIALIZABLE |
|---|---|---|---|
| E1 · leer y luego insertar | **sobrevende** (62 puestos de más) | **sobrevende** (66) | no |
| E2 · `UPDATE` condicional | no | no | no |
| E3 · `SELECT … FOR UPDATE` | no | **sobrevende** (141) | no |
| E4 · restricción `UNIQUE` (por asiento) | no | no | no |
| E5 · SERIALIZABLE con reintento | **sobrevende** (57) | **sobrevende** (61) | no |

E3 es el resultado menos obvio: el bloqueo pesimista parece seguro y en REPEATABLE READ no
lo es, porque quien espera el `FOR UPDATE` conserva la foto de antes de la espera.
Las que no sobrevenden cuestan otra cosa (abortos y reintentos, latencia): ver
[`RESULTS.md`](RESULTS.md).

## Documentos

| Archivo | Qué es |
|---|---|
| [`DESIGN.md`](DESIGN.md) | El diseño: modelos, estrategias, invariantes, plan por fases |
| [`NO-GOALS.md`](NO-GOALS.md) | Lo que el proyecto no hace, fijado antes de escribir código |
| [`PREDICTIONS.md`](PREDICTIONS.md) | Predicciones fechadas **antes** de medir; no se editan después |
| [`ANOMALIES.md`](ANOMALIES.md) | Cada anomalía con su intercalado exacto, el test que la reproduce, las predicciones refutadas y cuánto aparece bajo carga |
| [`RESULTS.md`](RESULTS.md) | La matriz completa (media y rango de 3 semillas por celda), generada |
| `backend/resultados_final/corridas.json` | Las 270 corridas de la matriz final, una por una |

Una predicción falló y se documenta: E4 en SERIALIZABLE con comprobación previa del asiento
da `40001`, no `23505` (`ANOMALIES.md`).

## Cómo está medido

- **Evidencia de que la anomalía existe:** tests de intercalado determinista
  (`backend/tests/test_intercalado.py`): dos conexiones, pasos en orden fijo, sin sleeps.
- **Frecuencia y costo:** generador de carga propio (asyncio + httpx) y un verificador que
  comprueba, contra la base, cinco invariantes: sobreventa, asiento duplicado, respuestas
  fieles a la base, claves de idempotencia y retenciones.
- **Matriz:** estrategia × aislamiento × concurrencia (10, 100, 1.000) × contención
  (holgada, escasa) × 3 semillas. Cada celda reporta media y rango, nunca la mejor corrida.
  El cliente reintenta hasta 50 veces ante `40001` para que el throughput sea comparable.

### Entorno de medición y límites

- Portátil con AMD Ryzen 5 7520U (4 núcleos, 8 hilos), 15 GB de RAM, Windows 11, Docker
  Desktop (8 CPU, 7,9 GB para Docker), Postgres 18.6, Python 3.12.
- **El generador de carga, la API y Postgres comparten la misma máquina.** Las conclusiones
  valen como comparaciones entre estrategias, no como números absolutos.
- **Pool de 20 conexiones en la API.** Las latencias con 1.000 compradores (decenas de
  segundos) son sobre todo cola del pool, no trabajo de la base. Con 20 conexiones, la
  sobreventa medida queda acotada por cuántas transacciones coinciden: las cifras no son una
  tasa y probablemente subestiman lo que pasaría con más conexiones (no se midió con otro pool).
- **Tope de 50 reintentos:** con 1.000 compradores, 224 compras (21 corridas, en E1·SER,
  E2·RR, E2·SER y E3·SER) lo agotaron. Se reporta como costo de la estrategia.
- La carga no prueba ausencia de sobreventa; eso lo hacen los tests de intercalado.

## Cómo correrlo

Requisitos: Docker y Python 3.12.

```bash
# Postgres, migraciones, API (puerto 8000) y frontend (puerto 3000)
docker compose up -d --build

# Tests (con el compose arriba): 103 pasan y 1 xfail estricto
cd backend
python -m venv .venv && .venv/Scripts/pip install -r requirements-dev.txt   # en Linux/Mac: .venv/bin/
.venv/Scripts/python -m pytest
```

La API elige estrategia y nivel por variables de entorno (`ESTRATEGIA` = `e1`…`e5`,
`AISLAMIENTO` = `read_committed` | `repeatable_read` | `serializable`; por defecto
`e5` en `serializable`). El frontend tiene un mapa de asientos en vivo (`/mapa`) y una
página de laboratorio (`/laboratorio`) que corre una demo de compradores concurrentes y
marca la sobreventa en rojo.

### Reproducir la matriz

La matriz levanta una API por celda y toca la base, así que conviene una Postgres aparte:

```bash
docker run -d --name pg-matriz -p 127.0.0.1:5434:5432 \
  -e POSTGRES_DB=reservas -e POSTGRES_USER=reservas -e POSTGRES_PASSWORD=reservas \
  postgres:18.6-bookworm
cd backend
export DATABASE_URL=postgresql://reservas:reservas@127.0.0.1:5434/reservas
.venv/Scripts/python -m app.migrar
.venv/Scripts/python -m carga.matriz --reintentos-cliente 50 --salida resultados_final
```

Son 270 corridas y tarda horas (la de 1.000 compradores con SERIALIZABLE es la lenta).
No toques `backend/app/` mientras corre: cada celda importa ese código. `--rapida` corre
solo 10 y 100 compradores. Los números cambian de una máquina a otra; lo que debe
repetirse es *qué combinaciones sobrevenden y cuáles no*.

## Estructura

```
backend/app/        API: estrategias E1–E5, retenciones, idempotencia, estado en vivo (SSE)
backend/carga/      generador de carga, verificador de invariantes, runner de la matriz
backend/tests/      intercalado determinista, invariantes, fallas, idempotencia
backend/migrations/ SQL plano (sin ORM ni Alembic)
frontend/           Next.js: mapa de asientos y laboratorio
```

## Fuera de alcance

Pagos reales, autenticación, microservicios, varias regiones y competir en rendimiento con
sistemas de ticketing: ver [`NO-GOALS.md`](NO-GOALS.md).
