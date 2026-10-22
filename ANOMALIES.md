# ANOMALIES

Every anomaly in this project, reproduced with a **fixed interleaving** (DESIGN.md §8.1):
two connections, steps in a set order, no dependence on timing. The tests are in
`backend/tests/test_intercalado.py`; the tooling is in `backend/tests/intercalador.py`.

Common scenario: an event of capacity 2 with one prior reservation, that is, **a single free
seat**, and two buyers, A and B. More than 2 reservations at the end is overselling. Postgres
18.6.

What follows is evidence that the anomaly **exists** and why. How often it shows up under load
and what it costs is in `RESULTS.md`, which comes from the F3 matrix.

*(Translated to English on 2026-10-22; the content is unchanged except for one correction
noted in the pool section. Test names stay in Spanish because they are code identifiers.)*

## Refuted predictions

`PREDICTIONS.md` is not edited after measuring. What failed stays there as it was and is
explained here.

### E4 under SERIALIZABLE with a prior check: it gives `40001`, not `23505`

- **Prediction (19/10):** "same result: no double booking, error `23505` (not `40001`)", citing
  the documentation: the uniqueness conflict is reported as a uniqueness violation "even if the
  seat was checked as free beforehand".
- **What happened:** if A and B run `SELECT count(*) ... WHERE asiento_id = 5` before inserting
  and then A inserts and commits, B's `INSERT` fails with **`40001`** (serialization failure).
  There is no double booking, but the error code is different.
- **Without a prior check**, which is what `app/estrategias.py` does (E4 only inserts), B gets
  **`23505`**, as predicted. The prediction was right for the strategy as actually implemented
  and wrong for the variant it itself mentioned.
- **Why:** this is my reading of what was observed, not verified against the Postgres source.
  The earlier read leaves a predicate lock for B on the seat, and A's `INSERT` falls inside it:
  that forms a dependency SSI detects, and Postgres reports the serialization failure instead of
  the uniqueness one. The prediction read the documentation as "the `23505` holds even in that
  case"; the observed behavior does not match that reading. Before citing the explanation as
  fact, that section of the documentation should be re-read.
- **Practical consequence:** an E4 client that does "check whether it is free" before inserting
  and runs under SERIALIZABLE must treat `40001` as a retry, not just `23505` as "taken".
- **Tests:** `test_e4_serializable_prediccion_con_comprobacion_previa` (strict `xfail`: it
  starts failing the day the prediction comes true) and
  `test_e4_serializable_con_comprobacion_previa` (pins what was observed).

## Reproduced overselling

### E1 under READ COMMITTED and REPEATABLE READ: write skew

The code from §3.1. Script:

```
A: BEGIN; count → 1            B: BEGIN; count → 1
A: INSERT                      B: INSERT
A: COMMIT  ✓                   B: COMMIT  ✓      → 3 reservations, capacity 2
```

Nobody modifies anyone else's row: A inserts *its* row and B *its own*, so there is no write
conflict for Postgres to detect. **That REPEATABLE READ is stricter does not help**: its snapshot
prevents seeing A's row, which is precisely what lets B count wrong. Under SERIALIZABLE the same
script ends with **`40001` on B's `COMMIT`** (both `INSERT`s go through) and 2 reservations
remain.

### E3 under REPEATABLE READ: pessimistic locking oversells

The least obvious prediction and the one that did come true. Script:

```
A: BEGIN; SELECT ... FOR UPDATE  (takes the event row)
B: BEGIN; SELECT ... FOR UPDATE  (waits; its snapshot is taken here)
A: count → 1; INSERT; COMMIT
B: gets the lock; count → 1; INSERT; COMMIT   → 3 reservations, capacity 2
```

B waits, but **the wait does not refresh its snapshot**: it took it when the `FOR UPDATE`
started, before A's `INSERT`. Since A only locked the event row and did not modify it, Postgres
does not abort B. This is why `FOR UPDATE` looks safe and is not at that level. Under READ
COMMITTED B counts with a fresh snapshot, sees 2 and answers "sold out". Under SERIALIZABLE B
counts 1 (old snapshot) but its **`INSERT` fails with `40001`**, not the `COMMIT`.

### E5 under READ COMMITTED and REPEATABLE READ: oversells just like E1

No prior prediction (scope decision of 19/10). E5 is E1 plus a retry on `40001`, and at these
levels that error never appears, so **there is nothing to retry**: the same E1 script gives 3
reservations with capacity 2. Observed, not predicted.

## They do not oversell, but they cost

### E2: the conditional `UPDATE`

- **READ COMMITTED:** B waits on A's `UPDATE`; when A commits, **it re-evaluates the `WHERE`
  on the new row**, finds no room and answers "sold out" with no error. 2 reservations.
- **REPEATABLE READ and SERIALIZABLE:** B waits and, when A commits, gets **`40001`** (`could
  not serialize access due to concurrent update`) and does not re-evaluate the `WHERE`: it
  aborts because the row changed, not because the seats ran out. This is the abort rate the
  design predicted for these levels.

### E4: the `UNIQUE` constraint

- At all three levels B, which tries the same seat, **waits** until A commits. If A commits, B
  gets `23505`; if A does a `ROLLBACK`, B proceeds and buys
  (`test_e4_si_el_primero_hace_rollback_el_segundo_procede`). There are never two reservations
  of the same seat.

### E5 under SERIALIZABLE

B aborts with `40001` and, when retrying in a new transaction, counts 2 and answers "sold out"
(`test_e5_reintenta_y_termina_en_agotado`). It does not oversell; the cost is in the retries,
which `RESULTS.md` quantifies.

## How often it shows up under load

Figures from the final matrix (`RESULTS.md`, `backend/resultados_final/corridas.json`): 270
runs, 18 per strategy and level combination (3 concurrency levels × 2 contention levels × 3
seeds), API pool of 20 connections, up to 50 client retries on `40001`.

| Combination | Runs with overselling | Extra seats (sum) |
|---|---|---|
| E1 · READ COMMITTED | 9 of 18 | 62 |
| E1 · REPEATABLE READ | 8 of 18 | 66 |
| E3 · REPEATABLE READ | 9 of 18 | 141 |
| E5 · READ COMMITTED | 6 of 18 | 57 |
| E5 · REPEATABLE READ | 9 of 18 | 61 |
| the other 10 combinations | 0 of 18 each | 0 |

The five combinations that oversell are exactly the ones the previous section reproduces with a
fixed interleaving; the other ten are the ones that end in `40001`, "sold out" or `23505` there.
Load and tests say the same thing. Across the 270 runs there were 0 seats with more than one
reservation, 0 unfaithful responses, 0 repeated idempotency keys and 0 violations of the holds
invariant.

**Overselling only showed up under scarce contention** (fewer seats than buyers). With ample
capacity none of the 45 runs of these five combinations oversold: if seats are left over, the
wrong count does not change the outcome.

**How much it oversells depends on how many transactions coincide, and here the pool bounds
that.** Extra seats per run under scarce contention:

| Buyers (capacity) | E1 · RC | E1 · RR | E3 · RR | E5 · RC | E5 · RR |
|---|---|---|---|---|---|
| 10 (1) | 9, 9, 9 | 9, 9, 9 | 9, 9, 9 | 9, 9, 9 | 9, 9, 9 |
| 100 (10) | 10, 10, 10 | 10, 10, 10 | 19, 19, 19 | 10, 10, 10 | 10, 10, 10 |
| 1000 (100) | 1, 2, 2 | 0, 2, 7 | 19, 19, 19 | 0, 0, 0 | 2, 1, 1 |

With 10 buyers everyone buys the single seat. With 100, E1 and E5 end with 20 reservations (the
pool size) and E3 with 29. With 1,000 it drops to a small figure except for E3, which stays at
19. **Checked with a different pool size** (`backend/resultados_pool/`: E1 and E3, scarce
contention, 3 seeds, pool of 50 and 90 instead of 20):

| Extra seats per run | Pool 20 | Pool 50 | Pool 90 |
|---|---|---|---|
| E1 · RC and RR, 100 buyers (capacity 10) | 10 | 40 | 80 |
| E3 · RR, 100 buyers (capacity 10) | 19 | 49 | 89 |
| E3 · RR, 1000 buyers (capacity 100) | 19 | 49 | 89 |
| E3 · RC (control), 100 and 1000 buyers | 0 | 0 | 0 |

With 100 buyers and E3 the overselling is **the pool size minus one**, and with E1 it is the
pool size minus the capacity: what oversells is the batch of transactions running at the same
time on an old snapshot, and the pool bounds that batch. The control (E3 under READ COMMITTED)
stays at 0. That is, **the magnitude is an artifact of the pool and not a property of the
strategy**: these figures should not be read as a rate, and with more connections they would
oversell more.

What remains unexplained: E1 with 1,000 buyers does not follow the rule (pool 20: 0–7; pool 50:
0; pool 90: 80 in all three seeds). *Correction of 2026-10-22: the Spanish text offered a
reading here (that with capacity 100 the initial batch fits inside the pool of 50 without
crossing capacity). It does not hold up, because a pool of 90 is also smaller than 100 and still
oversold by 80. I have no verified explanation.*

**What the ones that do not oversell cost.** The client retry makes E1, E2 and E3 under
SERIALIZABLE (and E2 under REPEATABLE READ) sell everything, but with 1,000 buyers 21 runs have
purchases that exhausted the 50 retries (224 in total: E1·SER 52, E2·RR 61, E2·SER 71, E3·SER
40). E5 under SERIALIZABLE lost none. The median latency with 1,000 buyers and ample capacity
ranges from about 10 s (E1·RC) to about 122 s (E3·SER); a good part of that is pool queueing,
not database work, so those latencies are compared between strategies, not as absolute numbers.

**E4 sells fewer than there are, and that is correct.** Every buyer asks for a random seat, so
with 1,000 buyers and 2,000 seats there are repeats and the repeated one gets `ocupado`: 783
sales on average, 0 duplicate seats.

## What was not tested here

- **That the test is the proof of frequency.** Reproducing the anomaly once shows that it
  exists; how often it happens depends on contention and is measured in the matrix.
- **More than two buyers.** The scripts are A against B. The effects with dozens of
  transactions (for example, which transaction SSI picks as the victim) are covered by the
  load, not by these tests.
- **Holds and ambiguous commit** (§3.4 and §5): they are not isolation anomalies and are not
  here. The three failure scenarios of §8.4 (killing the API between the `COMMIT` and the
  response, cutting the client, payment after expiry) are in `backend/tests/test_fallas.py`; in
  all three, the retry with the same idempotency key leaves 0 violations.
- **Another pool size or another machine.** Everything was measured with the pool at 20 (plus
  the pool experiment above) and the client, the API and Postgres on the same laptop (see the
  README).
