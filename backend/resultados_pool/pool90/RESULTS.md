# RESULTS

Generado por `python -m carga.matriz`. Pool de la API: 90 conexiones. Reintentos del cliente ante 40001: hasta 50 (0 = no reintenta). Cada celda: media [mín–máx] de 3 semillas.

Violaciones: **sobreventa** = reservas por encima de la capacidad; **dup** = asientos con más de una reserva; **infiel** = respuestas que no corresponden a la base; **clave** = claves de idempotencia repetidas. Invariante 5 (retenciones): F4.

| estr. | aisl. | n | contención | sobreventa | dup | infiel | clave | exitosas | abortadas | errores | reintentos | compras/s | p50 ms | p99 ms |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| e1 | read_committed | 100 | escasa | 80 [80–80] | 0 [0–0] | 0 [0–0] | 0 [0–0] | 90 [90–90] | 0 [0–0] | 0 [0–0] | 0 [0–0] | 99.9 [88.2–110.6] | 629.9 [577.3–718.0] | 797.3 [757.1–869.0] |
| e1 | read_committed | 1000 | escasa | 80 [80–80] | 0 [0–0] | 0 [0–0] | 0 [0–0] | 180 [180–180] | 0 [0–0] | 0 [0–0] | 0 [0–0] | 14.4 [14.2–14.7] | 9950.3 [9748.1–10260.3] | 12150.3 [11651.9–12657.4] |
| e1 | repeatable_read | 100 | escasa | 80 [80–80] | 0 [0–0] | 0 [0–0] | 0 [0–0] | 90 [90–90] | 0 [0–0] | 0 [0–0] | 0 [0–0] | 103.0 [92.7–114.6] | 646.1 [553.8–724.7] | 776.9 [681.0–866.8] |
| e1 | repeatable_read | 1000 | escasa | 80 [80–80] | 0 [0–0] | 0 [0–0] | 0 [0–0] | 180 [180–180] | 0 [0–0] | 0 [0–0] | 0 [0–0] | 14.3 [13.9–14.5] | 10430.6 [10125.6–10725.1] | 12461.9 [12166.1–12818.3] |
| e3 | read_committed | 100 | escasa | 0 [0–0] | 0 [0–0] | 0 [0–0] | 0 [0–0] | 10 [10–10] | 0 [0–0] | 0 [0–0] | 0 [0–0] | 7.5 [6.7–8.5] | 820.6 [703.0–890.7] | 1109.5 [998.3–1244.0] |
| e3 | read_committed | 1000 | escasa | 0 [0–0] | 0 [0–0] | 0 [0–0] | 0 [0–0] | 100 [100–100] | 0 [0–0] | 0 [0–0] | 0 [0–0] | 5.6 [5.1–6.0] | 15021.7 [14555.5–15831.0] | 17500.8 [16623.8–18310.0] |
| e3 | repeatable_read | 100 | escasa | 89 [89–89] | 0 [0–0] | 0 [0–0] | 0 [0–0] | 99 [99–99] | 0 [0–0] | 0 [0–0] | 0 [0–0] | 75.9 [55.1–86.3] | 815.7 [697.5–1051.9] | 1171.0 [954.6–1595.9] |
| e3 | repeatable_read | 1000 | escasa | 89 [89–89] | 0 [0–0] | 0 [0–0] | 0 [0–0] | 189 [189–189] | 0 [0–0] | 0 [0–0] | 0 [0–0] | 10.6 [10.4–11.0] | 15200.3 [15099.1–15318.0] | 17516.8 [17064.0–17915.1] |
