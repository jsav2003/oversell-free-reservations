# PREDICTIONS

Written on **2026-10-19**, before measuring anything: on that date no strategy was
implemented and no load run existed (only the F0 scaffolding).

**This file is not edited after measuring.** If a prediction fails, that is a result: it goes
to `RESULTS.md` and `ANOMALIES.md` with its explanation, and here it stays as it was. The only
additions allowed are new predictions, with their own date, and only before the measurement
that would put them to the test.

*(Translated to English on 2026-10-22. The content is unchanged: the translation is of the
2026-10-19 text, not a revision of it. The original Spanish is in the git history.)*

Source: the "Prediction" column of the table in DESIGN.md §4, copied unchanged.

## Predictions

| # | Strategy | Model | Prediction |
|---|---|---|---|
| E1 | Read, then insert | Capacity | Oversells under READ COMMITTED and under REPEATABLE READ |
| E2 | Atomic conditional `UPDATE` | Capacity | Does not oversell under READ COMMITTED; under REPEATABLE READ it aborts and requires a retry |
| E3 | Pessimistic lock (`SELECT ... FOR UPDATE`) | Capacity | Does not oversell under READ COMMITTED; **under REPEATABLE READ it can oversell**; throughput limited by the hot row |
| E4 | `UNIQUE` constraint | Seat | No double booking of the same seat |
| E5 | `SERIALIZABLE` with retry | Capacity | Does not oversell; high abort rate under contention |

### Reasoning behind the two least obvious predictions

- **E2 under READ COMMITTED does not oversell:** when two `UPDATE`s compete for the same row,
  the second waits for the first and re-evaluates the `WHERE` on the new version of the row.
  This is documented Postgres behavior, not timing.
- **E3 under REPEATABLE READ oversells:** the database snapshot is taken at the start of the
  first statement. If B waits on the `FOR UPDATE` while A inserts and commits, B gets the lock
  with a snapshot from before A's insert. A only locked the event row, it did not modify it,
  so Postgres does not abort B: B counts 99 and sells seat 101. A reasoned hypothesis, not
  checked; F2 confirms or refutes it with a deterministic test.

## Combinations with no explicit prediction in the design

The table in DESIGN.md §4 says nothing about these combinations. F2 requires declaring, for
each strategy and level, "the expected anomaly or its absence".

### Added on 2026-10-19 (before measuring)

Reasoned from the PostgreSQL documentation, "Transaction Isolation" (§13.2) and "Index
Uniqueness Checks". They are hypotheses: F2 confirms or refutes them.

**E1 under SERIALIZABLE — does not oversell; the second transaction aborts with `40001`.**
Each buyer's `count(*)` leaves a predicate lock and the other's `INSERT` falls inside what was
read: a circular dependency, no serial order possible, so SSI aborts one. It is the
documentation's own example; without a retry, overselling turns into a failed purchase.

**E2 under SERIALIZABLE — does not oversell; the loser aborts with `could not serialize access due to concurrent update`.**
Serializable "works the same as Repeatable Read" plus dependency tracking, and the `UPDATE`
does modify the row, so the second one aborts even if seats remain (it does not re-evaluate
the `WHERE` as in READ COMMITTED). Aborts concentrated on the hot row.

**E3 under SERIALIZABLE — does not oversell; the buyer that waited on the `FOR UPDATE` aborts with `40001`.**
The lock is not enough (A only locked the row, it did not modify it), but SSI keeps A's
predicate locks after its commit as long as B is still running. B counts with its old
snapshot and inserts inside what A read: a cycle, and B aborts. It pays for the wait and for
the abort.

**E4 under READ COMMITTED — there are never two reservations of the same seat; the second gets `23505` (unique_violation).**
The uniqueness check does not use the transaction's snapshot: if the other `INSERT` is
uncommitted, the second one waits; if it commits, there is a violation, and if it rolls back,
it proceeds.

**E4 under REPEATABLE READ — same result: no double booking, error `23505`.**
The unique index looks at the commit state of the rows, not the snapshot, so the
transaction's old snapshot does not let a duplicate through. A stricter level changes nothing
here.

**E4 under SERIALIZABLE — same result: no double booking, error `23505` (not `40001`).**
The documentation says that uniqueness conflicts between serializable transactions are still
reported as a uniqueness violation, even sometimes where a serial order would not have
produced them, even if the seat was checked as free beforehand.

### Scope decisions

- Settled on 2026-10-19: **E5 under READ COMMITTED and REPEATABLE READ — measured, with no
  prior prediction.** The cells are not marked "not applicable"; whatever comes out is a
  result.
