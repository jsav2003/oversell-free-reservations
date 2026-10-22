# NO-GOALS

Fixed on **2026-10-19**, before writing any code. What is listed here does not get added
"just a little". If it ever looks like a good idea, re-read why it is on this list.

The goal of the project is to show, with reproducible evidence, which concurrency strategies
in Postgres fail, which do not, why, and what they cost (DESIGN.md §1). Anything that does not
serve that is noise.

*(Translated to English on 2026-10-22; the content is unchanged. The original Spanish is in
the git history.)*

## What is not done

- **A real ticket-selling platform or a product.** Nobody is going to use this to sell
  anything.
- **Real payments.** Payment is a simulated step, with configurable latency and failures.
- **Serious authentication.** A user identifier is enough. No sign-up, no sessions, no
  passwords, no users table.
- **Microservices, Kafka, queues, multiple regions.** A single Postgres instance.
- **Competing on performance with real ticketing systems.** The conclusions are comparisons
  between strategies on one machine, not absolute numbers.
- **An ORM on the critical path.** The SQL and the isolation level of every transaction have
  to be visible in the code.
- **k6, Locust or any other generic load generator.** The verifier needs the exact record of
  every request.

## Why a single database

Every problem in the design already shows up with one Postgres and two connections. Adding
distribution would bury the topic under complexity that adds nothing (DESIGN.md §2).

## What gets cut first if there is a delay

In this order, according to DESIGN.md §9: sharded counters (§4.1), the index experiment
(§8.5), and then the lab page is reduced to showing already-computed results.

**Not cut:** the deterministic interleaving tests (F2) and the invariant verifier. They are
the project.
