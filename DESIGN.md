# Oversell-free reservation system

Design document. Written before the code, on purpose.

*(Translated to English on 2026-10-22. The content is unchanged; the original Spanish is in the
git history. Code identifiers, table names and test names stay in Spanish because they are
what the code uses.)*

---

## 1. What this project is

A full stack application for reserving seats at an event (concert tickets, places in a
course, slots in a workshop) that **guarantees a seat is never sold more than once and
capacity is never exceeded**, even when thousands of people buy at the same time, the server
dies in the middle of a purchase, or the client retries.

The real goal is not the reservation app. It is to show, with reproducible evidence,
**which concurrency strategies in Postgres fail, which do not, why, and what each one costs**.

The product of the project is a table: five strategies × three isolation levels × several
concurrency levels, with how many seats were oversold, the throughput, the p99 latency and the
retry rate for each combination. In addition, every anomaly is reproduced deterministically in
a test, not just observed under load.

## 2. What it is NOT

- It is not a real ticket-selling platform or a product.
- It does not process real payments. Payment is a simulated step with configurable latency and
  failures.
- It has no serious authentication: a user identifier is enough.
- There are no microservices, no Kafka, no multiple regions. A single Postgres instance.
- It does not compete on performance with real ticketing systems.

The single-database restriction is deliberate: every problem in this document already shows up
with one Postgres and two connections. Adding distribution would bury the topic under
complexity that adds nothing.

## 3. The underlying problem

### 3.1 Naive overselling

The code almost everyone writes first:

```python
vendidos = await db.fetchval(
    "SELECT count(*) FROM reservas WHERE evento_id = $1", evento_id)
if vendidos < capacidad:
    await db.execute(
        "INSERT INTO reservas (evento_id, usuario_id) VALUES ($1, $2)",
        evento_id, usuario_id)
```

With a single buyer it always works. With two at the same time and one free seat:

```
buyer A                         buyer B
SELECT count → 99 (of 100)
                                SELECT count → 99
INSERT  ✓
                                INSERT  ✓      ← reservation 101 of 100
```

Both read before the other wrote. Both were right about what they saw.

### 3.2 Raising the isolation level does not fix it

Intuition says: if `READ COMMITTED` lets you read data that is about to change, `REPEATABLE
READ` should be enough. **It is not**, and understanding why is the center of the project.

In Postgres, `REPEATABLE READ` is snapshot isolation: each transaction sees the database as it
was when it started. That prevents another transaction from changing a row it is modifying: if
it tries, Postgres aborts with `could not serialize access due to concurrent update`. But in the
code above **nobody modifies the same row**. A reads the count and inserts *its* row; B reads the
count and inserts *another* row. There is no write conflict, so both commit. The business rule
(sold ≤ capacity) depends on a set of rows that neither of them locked.

That anomaly is called **write skew**. The Postgres documentation warns about it directly: under
`REPEATABLE READ` serialization anomalies are possible, and enforcing business rules with
transactions at this level is unlikely to work without careful use of explicit locks.

### 3.3 SERIALIZABLE does fix it, and charges for it

Under `SERIALIZABLE`, Postgres uses *Serializable Snapshot Isolation*: it tracks what each
transaction read through predicate locks and, if it detects that the outcome does not
correspond to any serial order, it aborts one with SQLSTATE `40001`. The example in the official
documentation is almost exactly ours: two transactions that read a sum and insert a row that
changes the other's sum.

The cost:

- **The client has to retry.** An aborted transaction is not a user error; it has to be repeated
  from the start. Without retry logic, `SERIALIZABLE` turns overselling into failed purchases.
- **Under heavy contention, retries pile up.** A thousand buyers for the last ten seats generate
  an abort storm.
- **The abort rate depends on the indexes.** A sequential scan always takes a predicate lock on
  the whole table, and that increases serialization failures. With an index on `evento_id`, the
  lock is finer. Measuring that difference is one of the project's experiments.

### 3.4 The ambiguous commit

A different problem, which isolation does not solve: the server commits the reservation in the
database and dies before responding. The client does not know whether it bought. If it retries,
it may reserve twice; if it does not retry, it may end up without the seat it did buy.

The solution is an **idempotency key**: the client generates a unique identifier per purchase
attempt and sends it on every retry. The database stores the key with the reservation, under a
`UNIQUE` constraint, and a retry with the same key returns the existing reservation instead of
creating another.

## 4. The strategies to compare

Two data models, because the problem changes depending on whether seats have an identity:

- **Capacity model:** the event has N indistinguishable places (a course with 30 places).
- **Seat model:** every seat has an identity (row C, seat 14).

| # | Strategy | Model | What it does | Prediction |
|---|---|---|---|---|
| E1 | Read, then insert | Capacity | The code from §3.1 | Oversells under RC and RR |
| E2 | Atomic conditional `UPDATE` | Capacity | `UPDATE eventos SET vendidos = vendidos + 1 WHERE id = $1 AND vendidos < capacidad RETURNING` | Does not oversell under RC; under RR it aborts and requires a retry |
| E3 | Pessimistic lock | Capacity | `SELECT ... FROM eventos WHERE id = $1 FOR UPDATE` before counting | Does not oversell under RC; **under RR it can oversell** (see below); throughput limited by the hot row |
| E4 | `UNIQUE` constraint | Seat | `INSERT` with `UNIQUE (evento_id, asiento_id)`; the conflict is translated to "taken" | No double booking of the same seat |
| E5 | `SERIALIZABLE` with retry | Capacity | E1 inside `SERIALIZABLE`, retrying on `40001` | Does not oversell; high abort rate under contention |

**The prediction column is written before measuring and is not edited afterwards.** If a
prediction fails, that is a result, and it goes into the report with its explanation.

Why E2 works under `READ COMMITTED`: when two `UPDATE`s compete for the same row, the second
waits for the first to commit and **re-evaluates the `WHERE` on the new version of the row**. If
`vendidos < capacidad` no longer holds, it updates nothing. It is documented Postgres behavior,
not a timing accident.

Why E3 could fail under `REPEATABLE READ`, and it is the most interesting prediction in the
table: at that level the database snapshot is taken at the start of the transaction's first
statement. If buyer B ends up waiting on the `FOR UPDATE` while A inserts its reservation and
commits, B gets the lock, but its snapshot predates A's insert. Since A only locked the event row
and did not modify it, Postgres does not abort B. B counts 99 and sells seat 101. Pessimistic
locking, which looks like the safe option, oversells at exactly the "strictest" level. Under
`READ COMMITTED` it does not happen, because each statement takes a fresh snapshot. It is a
reasoned hypothesis, not a checked one: F2 confirms or refutes it with a deterministic test.

### 4.1 Extension: sharded counters

With a very popular event, E2 and E3 serialize all buyers on a single row. The variant splits
the capacity into K shards (for example 100 places in 10 rows of 10) and each purchase takes one
at random. It gains throughput and pays in complexity: when a shard runs out another has to be
tried, and the last free place can be in any of them. It is optional (see §9).

## 5. Temporary holds

A real buyer does not pay instantly: they select the seat, hold it for a few minutes and pay.
That adds a state and a clock:

```
available → held (with expiry) → confirmed
               └── expires unpaid → available
```

Problems it introduces:

- **The hold expires while the payment is in progress.** The buyer pays at second 9:59 of a
  10-minute hold, someone else takes the seat at 10:00, and the first payment confirms. The
  confirmation has to verify, in the same transaction, that the hold is still theirs and has not
  expired.
- **Releasing expired holds.** A periodic process, or the expiry condition evaluated in every
  query. One is chosen and justified.
- **Expired holds collide with E4's `UNIQUE` constraint.** If the expired hold stays in the
  table, it blocks the next buyer of that seat until something deletes it. A partial index does
  not work, because it cannot depend on `now()`. A choice has to be made between cleaning up at
  the moment of the conflict or releasing with a periodic process, and measuring what happens in
  the window between expiry and cleanup.
- **Whose clock.** Expiry is compared against the database's `now()`, never against the
  application server's clock, so that several API instances do not disagree.

## 6. Architecture

```
Next.js (frontend)  ──HTTP──▶  FastAPI (API)  ──asyncpg──▶  PostgreSQL
       ▲                            │
       └────────── SSE ─────────────┘   (live seat state)

Load generator (Python asyncio) ──HTTP──▶ FastAPI
        └── records every request and its result → invariant verifier
```

- **Backend:** FastAPI with `asyncpg` directly, **no ORM on the critical path**. The SQL and the
  isolation level of every transaction have to be explicit and visible in the code; an ORM hides
  them, and hiding them is exactly what this project cannot do.
- **Database:** PostgreSQL in Docker, pinned version.
- **Frontend:** Next.js with TypeScript. Details in §7.
- **Load generator:** our own, in Python with `asyncio`, instead of k6 or Locust. The reason: the
  verifier needs the exact record of every request (what was asked, what was answered, with
  which idempotency key), and a generic load tool does not give that record conveniently. Fixed
  seed so that every run is repeatable.
- **Everything starts with one command:** `docker compose up`.

The active strategy (E1 to E5) and the isolation level are configuration parameters of the API,
not code branches, so that the full matrix can be run without touching anything.

## 7. The frontend

The frontend is not decoration: it has two jobs that demand the same thing as the backend.

### 7.1 Live seat map

While you look at the map, others are buying. The seat you see as free may have been taken 200
ms ago.

- **Live state through Server-Sent Events:** every seat state change reaches the browser without
  reloading.
- **Optimistic UI with reconciliation:** on click, the seat is immediately marked as "yours" and
  the request is sent. If the server answers that it was already taken, the UI reverts and says
  so clearly. Showing "done" and then "oops" with no explanation is the typical failure.
- **The hold timer** is computed from the expiry the server returns, not from the browser's
  clock.

### 7.2 Lab page

You choose the strategy, the isolation level, the number of buyers and the capacity, and launch
an experiment. You see the map fill up live and, at the end, the result: seats sold against
capacity, overselling, throughput, p99 and retries. Overselling is marked in red on the
duplicated seat.

This page is the project's demo. In an interview, opening it, choosing E1 under `REPEATABLE
READ` with 500 buyers and watching the overselling appear explains the topic faster than any
paragraph.

## 8. Verification

This section is the project. Without it, what is left is one more reservation app.

### 8.1 Deterministic interleaving tests

Load tests find anomalies **by probability**: a run may not show the overselling even though it
exists. That is why every anomaly is also reproduced with a test that forces the exact
interleaving:

```python
# two connections, steps in a fixed order, no dependence on timing
a = await conectar(); b = await conectar()
await a.execute("BEGIN ISOLATION LEVEL REPEATABLE READ")
await b.execute("BEGIN ISOLATION LEVEL REPEATABLE READ")
assert await a.fetchval(CONTAR) == 99
assert await b.fetchval(CONTAR) == 99
await a.execute(INSERTAR, ...)
await b.execute(INSERTAR, ...)
await a.execute("COMMIT")
await b.execute("COMMIT")          # under RR it commits: write skew demonstrated
assert await contar_final() == 101
```

The same script under `SERIALIZABLE` must end with a `40001` on the second `COMMIT`. One test per
strategy and level, each with the expected anomaly **or its absence** declared. If the
prediction in §4 says "oversells" and the test cannot reproduce it, either the test or the
prediction is wrong, and it has to be found out which.

### 8.2 Invariant verifier

After every load run, over the final database and the request log:

1. **Capacity:** for every event, confirmed reservations ≤ capacity.
2. **Uniqueness:** no seat has more than one active reservation.
3. **Faithful response:** every request that received "purchase succeeded" has exactly one
   reservation in the database; every request that received "sold out" has none.
4. **Idempotency:** all retries with the same key correspond to a single reservation.
5. **Holds:** no reservation was confirmed with an expired or someone else's hold.

Invariant 3 is the most demanding and the most forgotten: a system may not oversell and still
tell someone "you bought" without a reservation, or "sold out" to someone who does have one.

### 8.3 The matrix

Every combination of strategy × isolation (RC, RR, SERIALIZABLE) × concurrency (10, 100, 1,000
buyers) × contention (ample capacity and scarce capacity) is run with **three seeds**. The mean
and the range are reported, not the best run. Columns:

- oversold seats (the main result),
- violations of each invariant,
- throughput (successful purchases per second),
- p50 and p99 latency,
- abort and retry rate.

### 8.4 Fault injection

For the ambiguous commit (§3.4) and the holds (§5):

- Kill the API process between the `COMMIT` and the HTTP response.
- Cut the client's connection after sending the request.
- Pause a payment past the expiry of its hold.

In each case the client retries with its idempotency key, and the verifier of §8.2 decides.

### 8.5 Index experiment

E5 run with and without an index on `evento_id`, to measure how much the abort rate rises when
Postgres has to take a predicate lock on the whole table. The documentation anticipates it; the
project quantifies it.

## 9. Plan by phases

Estimate: ~78 hours.

| Phase | Hours | Done when |
|---|---|---|
| F0 · Scaffolding | 6 | `docker compose up` starts Postgres, API and an empty frontend; schema with migrations |
| F1 · Strategies E1–E5 | 14 | The five strategies implemented behind a configuration parameter |
| F2 · Interleaving tests | 10 | Every predicted anomaly reproduced deterministically, and every absence checked |
| F3 · Load, invariants and matrix | 14 | The full matrix runs with one command and produces `RESULTS.md` |
| F4 · Holds, idempotency and failures | 12 | The three scenarios of §8.4 pass the verifier |
| F5 · Frontend | 14 | Live map with optimistic UI and lab page working |
| F6 · Report | 8 | `RESULTS.md`, `ANOMALIES.md` and the README written, everything reproducible |

### Rescue rules

- If there is a delay, the sharded counters (§4.1) and the index experiment (§8.5) are cut.
- If the frontend runs long, the lab page is reduced to showing already-computed results,
  without launching experiments from the browser. The live map stays.
- **F2 and the invariant verifier are not cut.** They are the project.

## 10. Risks

| Risk | Mitigation |
|---|---|
| Load does not reproduce an anomaly that does exist | The deterministic F2 tests demonstrate it without depending on timing |
| The connection pool becomes the bottleneck and contaminates the measurements | Pool size fixed and reported; a control run measures latency without contention |
| Conclusions that only hold for one machine | Hardware, Postgres version and configuration in the report; conclusions framed as comparisons between strategies, not as absolute numbers |
| The frontend eats the time | The F5 rescue rule |
| Temptation to add real payments, authentication or microservices | `NO-GOALS.md` fixed from day one |

## 11. What stays in the repository

- `DESIGN.md` — this document.
- `NO-GOALS.md` — the limits.
- `ANOMALIES.md` — every anomaly with its exact interleaving, the level where it appears, the
  test that reproduces it and the explanation.
- `RESULTS.md` — the full matrix, generated.
- `PREDICTIONS.md` — the prediction column of §4, dated, written before measuring.
- A GIF of the lab page showing live overselling, in the README.

What someone should see in thirty seconds: the table where E1 oversells under `READ COMMITTED`
**and also under `REPEATABLE READ`**, and the explanation of why. That second fact is the one
that separates someone who read about isolation from someone who understood it.

## 12. Recommended reading

- PostgreSQL documentation, chapter "Transaction Isolation". It is short and answers half the
  project.
- Martin Kleppmann, *Designing Data-Intensive Applications*, chapter 7. It explains write skew
  with an on-call doctors example that is the same pattern as §3.2.

## 13. Glossary

- **Isolation level:** how much a transaction can see of what the others do while both are in
  progress.
- **READ COMMITTED:** each query sees what has been committed up to that moment. It is
  Postgres's default level.
- **REPEATABLE READ:** the transaction sees a fixed snapshot of the database taken when it
  started (snapshot isolation).
- **SERIALIZABLE:** the result must be equivalent to running the transactions one after the
  other; if it is not, Postgres aborts one.
- **Write skew:** two transactions read the same data, write different things, and together
  break a rule that each one respected on its own.
- **Predicate lock:** a record of which condition a transaction read, used by `SERIALIZABLE` to
  detect conflicts. In Postgres it blocks nobody; it only detects.
- **SQLSTATE 40001:** the serialization failure error code. It means "repeat the transaction",
  not "user error".
- **Idempotency key:** an identifier the client sends on every attempt of the same operation,
  so that repeating it does not duplicate it.
- **Ambiguous commit:** when the client does not know whether its operation was committed,
  because the response was lost.
- **Hot row:** a row that every buyer needs to modify at the same time, and which therefore
  serializes the whole system.
