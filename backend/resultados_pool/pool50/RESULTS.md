# RESULTS

Generado por `python -m carga.matriz`. Pool de la API: 50 conexiones. Reintentos del cliente ante 40001: hasta 50 (0 = no reintenta). Cada celda: media [mín–máx] de 3 semillas.

Violaciones: **sobreventa** = reservas por encima de la capacidad; **dup** = asientos con más de una reserva; **infiel** = respuestas que no corresponden a la base; **clave** = claves de idempotencia repetidas. Invariante 5 (retenciones): F4.

| estr. | aisl. | n | contención | sobreventa | dup | infiel | clave | exitosas | abortadas | errores | reintentos | compras/s | p50 ms | p99 ms |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| e1 | read_committed | 100 | escasa | 40 [40–40] | 0 [0–0] | 0 [0–0] | 0 [0–0] | 50 [50–50] | 0 [0–0] | 0 [0–0] | 0 [0–0] | 58.8 [54.1–63.8] | 594.2 [541.4–630.4] | 672.4 [636.8–707.0] |
| e1 | read_committed | 1000 | escasa | 0 [0–0] | 0 [0–0] | 0 [0–0] | 0 [0–0] | 100 [100–100] | 0 [0–0] | 0 [0–0] | 0 [0–0] | 8.1 [8.0–8.2] | 10331.6 [9923.8–10536.3] | 12140.8 [12097.2–12191.2] |
| e1 | repeatable_read | 100 | escasa | 40 [40–40] | 0 [0–0] | 0 [0–0] | 0 [0–0] | 50 [50–50] | 0 [0–0] | 0 [0–0] | 0 [0–0] | 48.8 [37.9–57.5] | 767.7 [588.1–1043.7] | 878.0 [687.3–1135.1] |
| e1 | repeatable_read | 1000 | escasa | 0 [0–0] | 0 [0–0] | 0 [0–0] | 0 [0–0] | 100 [100–100] | 0 [0–0] | 0 [0–0] | 0 [0–0] | 7.4 [6.2–8.1] | 11235.7 [10208.4–13188.6] | 13431.4 [11957.4–15889.6] |
| e3 | read_committed | 100 | escasa | 0 [0–0] | 0 [0–0] | 0 [0–0] | 0 [0–0] | 10 [10–10] | 0 [0–0] | 0 [0–0] | 0 [0–0] | 6.9 [6.1–7.5] | 1069.9 [1006.9–1193.7] | 1200.7 [1107.4–1343.2] |
| e3 | read_committed | 1000 | escasa | 0 [0–0] | 0 [0–0] | 0 [0–0] | 0 [0–0] | 100 [100–100] | 0 [0–0] | 0 [0–0] | 0 [0–0] | 5.7 [5.7–5.8] | 15032.8 [14688.5–15625.9] | 17201.2 [17044.1–17499.1] |
| e3 | repeatable_read | 100 | escasa | 49 [49–49] | 0 [0–0] | 0 [0–0] | 0 [0–0] | 59 [59–59] | 0 [0–0] | 0 [0–0] | 0 [0–0] | 43.7 [39.0–46.6] | 880.2 [767.5–1050.7] | 1155.9 [1027.4–1343.8] |
| e3 | repeatable_read | 1000 | escasa | 49 [49–49] | 0 [0–0] | 0 [0–0] | 0 [0–0] | 149 [149–149] | 0 [0–0] | 0 [0–0] | 0 [0–0] | 8.7 [8.5–8.9] | 15057.6 [14541.4–15519.9] | 16869.9 [16480.8–17128.9] |
