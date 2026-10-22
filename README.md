# reservas-sin-sobreventa

A reservation app (FastAPI + asyncpg + Postgres 18.6 + Next.js) whose point is not the app:
it is to **show, with evidence, which concurrency strategies in Postgres oversell, which
do not, why, and what each one costs**. The product is a table of 5 strategies × 3
isolation levels × several concurrency levels, plus every anomaly reproduced
deterministically in a test.

> The project name, the code identifiers and the UI text are in Spanish (the project started
> as a Spanish-language course project). The documentation is in English.

## What to see in 30 seconds

**E1 ("count, then insert") oversells under READ COMMITTED and also under REPEATABLE READ.**
A stricter level does not help: its snapshot is exactly what stops it from seeing the other
buyer's reservation.

Overselling measured under load (270 runs, 18 per combination, API pool of 20 connections):

| Strategy | READ COMMITTED | REPEATABLE READ | SERIALIZABLE |
|---|---|---|---|
| E1 · read, then insert | **oversells** (62 extra seats) | **oversells** (66) | no |
| E2 · conditional `UPDATE` | no | no | no |
| E3 · `SELECT … FOR UPDATE` | no | **oversells** (141) | no |
| E4 · `UNIQUE` constraint (per seat) | no | no | no |
| E5 · SERIALIZABLE with retry | **oversells** (57) | **oversells** (61) | no |

E3 is the least obvious result: pessimistic locking looks safe and under REPEATABLE READ it
is not, because the buyer waiting on `FOR UPDATE` keeps the snapshot from before the wait.
The strategies that do not oversell pay in something else (aborts and retries, latency):
see [`RESULTS.md`](RESULTS.md).

## Documents

| File | What it is |
|---|---|
| [`DESIGN.md`](DESIGN.md) | The design: models, strategies, invariants, plan by phases |
| [`NO-GOALS.md`](NO-GOALS.md) | What the project does not do, fixed before writing any code |
| [`PREDICTIONS.md`](PREDICTIONS.md) | Predictions dated **before** measuring; not edited afterwards |
| [`ANOMALIES.md`](ANOMALIES.md) | Every anomaly with its exact interleaving, the test that reproduces it, the refuted predictions, and how often each shows up under load |
| [`RESULTS.md`](RESULTS.md) | The full matrix (mean and range over 3 seeds per cell), generated |
| `backend/resultados_final/corridas.json` | The 270 runs of the final matrix, one by one |

One prediction failed and is documented: E4 under SERIALIZABLE with a prior check of the seat
gives `40001`, not `23505` (`ANOMALIES.md`).

## How it is measured

- **Evidence that the anomaly exists:** deterministic interleaving tests
  (`backend/tests/test_intercalado.py`): two connections, steps in a fixed order, no sleeps.
- **Frequency and cost:** a purpose-built load generator (asyncio + httpx) and a verifier
  that checks five invariants against the database: overselling, duplicate seat,
  responses faithful to the database, idempotency keys and holds.
- **Matrix:** strategy × isolation × concurrency (10, 100, 1,000) × contention (ample,
  scarce) × 3 seeds. Each cell reports mean and range, never the best run. The client
  retries up to 50 times on `40001` so that throughput is comparable.

### Measurement environment and limits

- Laptop with an AMD Ryzen 5 7520U (4 cores, 8 threads), 15 GB of RAM, Windows 11, Docker
  Desktop (8 CPUs, 7.9 GB for Docker), Postgres 18.6, Python 3.12.
- **The load generator, the API and Postgres share the same machine.** Conclusions hold as
  comparisons between strategies, not as absolute numbers.
- **API pool of 20 connections.** Latencies with 1,000 buyers (tens of seconds) are mostly
  pool queueing, not database work. The measured overselling is bounded by the pool size:
  for E3 under REPEATABLE READ it is the pool size minus one (19, 49 and 89 with a pool of 20,
  50 and 90; `backend/resultados_pool/`). The overselling figures are not a rate, and with
  more connections they would be larger.
- **Retry cap of 50:** with 1,000 buyers, 224 purchases (21 runs, in E1·SER, E2·RR, E2·SER
  and E3·SER) exhausted it. It is reported as a cost of the strategy.
- Load does not prove the absence of overselling; the interleaving tests do.

## How to run it

Requirements: Docker and Python 3.12.

```bash
# Postgres, migrations, API (port 8000) and frontend (port 3000)
docker compose up -d --build

# Tests (with compose up): 103 pass and 1 strict xfail
cd backend
python -m venv .venv && .venv/Scripts/pip install -r requirements-dev.txt   # Linux/Mac: .venv/bin/
.venv/Scripts/python -m pytest
```

The API picks the strategy and level from environment variables (`ESTRATEGIA` = `e1`…`e5`,
`AISLAMIENTO` = `read_committed` | `repeatable_read` | `serializable`; default `e5` under
`serializable`). The frontend has a live seat map (`/mapa`) and a lab page (`/laboratorio`)
that runs a demo with concurrent buyers and marks overselling in red.

### Reproducing the matrix

The matrix starts one API per cell and writes to the database, so use a separate Postgres:

```bash
docker run -d --name pg-matriz -p 127.0.0.1:5434:5432 \
  -e POSTGRES_DB=reservas -e POSTGRES_USER=reservas -e POSTGRES_PASSWORD=reservas \
  postgres:18.6-bookworm
cd backend
export DATABASE_URL=postgresql://reservas:reservas@127.0.0.1:5434/reservas
.venv/Scripts/python -m app.migrar
.venv/Scripts/python -m carga.matriz --reintentos-cliente 50 --salida resultados_final
```

That is 270 runs and takes hours (the 1,000-buyer SERIALIZABLE cells are the slow ones).
Do not touch `backend/app/` while it runs: every cell imports that code. `--rapida` runs
only 10 and 100 buyers, and `--estrategias`, `--aislamientos`, `--contenciones` and
`--concurrencias` run a subset. `--regenerar DIR` rewrites `DIR/RESULTS.md` from
`DIR/corridas.json` without running anything. The numbers change from one machine to
another; what should repeat is *which combinations oversell and which do not*.

## Layout

```
backend/app/        API: strategies E1–E5, holds, idempotency, live state (SSE)
backend/carga/      load generator, invariant verifier, matrix runner
backend/tests/      deterministic interleaving, invariants, failures, idempotency
backend/migrations/ plain SQL (no ORM, no Alembic)
frontend/           Next.js: seat map and lab
```

## Out of scope

Real payments, authentication, microservices, multiple regions, and competing on performance
with real ticketing systems: see [`NO-GOALS.md`](NO-GOALS.md).
