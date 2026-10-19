# PREDICTIONS

Escrito el **2026-10-19**, antes de medir nada: a esta fecha no existe ninguna estrategia
implementada ni ninguna corrida de carga (solo el andamiaje de la F0).

**Este archivo no se edita después de medir.** Si una predicción falla, eso es un
resultado: va a `RESULTS.md` y `ANOMALIES.md` con su explicación, y aquí se queda como
estaba. Las únicas adiciones permitidas son nuevas predicciones, con su propia fecha, y
solo antes de la medición que las pondría a prueba.

Fuente: columna "Predicción" de la tabla de DESIGN.md §4, copiada sin cambios.

## Predicciones

| # | Estrategia | Modelo | Predicción |
|---|---|---|---|
| E1 | Leer y luego insertar | Capacidad | Sobrevende en READ COMMITTED y en REPEATABLE READ |
| E2 | `UPDATE` condicional atómico | Capacidad | No sobrevende en READ COMMITTED; en REPEATABLE READ aborta y exige reintento |
| E3 | Bloqueo pesimista (`SELECT ... FOR UPDATE`) | Capacidad | No sobrevende en READ COMMITTED; **en REPEATABLE READ puede sobrevender**; throughput limitado por la fila caliente |
| E4 | Restricción `UNIQUE` | Asiento | No hay doble reserva del mismo asiento |
| E5 | `SERIALIZABLE` con reintento | Capacidad | No sobrevende; tasa de abortos alta bajo contención |

### Razonamiento detrás de las dos predicciones menos obvias

- **E2 en READ COMMITTED no sobrevende:** cuando dos `UPDATE` compiten por la misma fila,
  el segundo espera al primero y reevalúa el `WHERE` sobre la versión nueva de la fila. Es
  comportamiento documentado de Postgres, no timing.
- **E3 en REPEATABLE READ sobrevende:** la foto de la base se toma al inicio de la primera
  sentencia. Si B espera en el `FOR UPDATE` mientras A inserta y confirma, B obtiene el
  bloqueo con una foto anterior a la inserción de A. A solo bloqueó la fila del evento, no
  la modificó, así que Postgres no aborta a B: B cuenta 99 y vende el puesto 101. Hipótesis
  razonada, no comprobada; la F2 la confirma o la refuta con un test determinista.

## Combinaciones sin predicción explícita en el diseño

La tabla de DESIGN.md §4 no dice nada sobre estas combinaciones. La F2 exige declarar para
cada estrategia y nivel "la anomalía esperada o su ausencia".

### Añadidas el 2026-10-19 (antes de medir)

Razonadas desde la documentación de PostgreSQL, "Transaction Isolation" (§13.2) y "Index
Uniqueness Checks". Son hipótesis: la F2 las confirma o las refuta.

**E1 en SERIALIZABLE — no sobrevende; la segunda transacción aborta con `40001`.**
El `count(*)` de cada comprador deja un bloqueo de predicado y el `INSERT` del otro cae
dentro de lo que leyó: dependencia circular, sin orden serial posible, así que SSI aborta
una. Es el ejemplo de la propia documentación; sin reintento, la sobreventa se vuelve compra fallida.

**E2 en SERIALIZABLE — no sobrevende; el perdedor aborta con `could not serialize access due to concurrent update`.**
Serializable "funciona igual que Repeatable Read" más el rastreo de dependencias, y el
`UPDATE` sí modifica la fila, así que el segundo aborta aunque aún queden cupos (no
reevalúa el `WHERE` como en READ COMMITTED). Abortos concentrados en la fila caliente.

**E3 en SERIALIZABLE — no sobrevende; el comprador que esperó en el `FOR UPDATE` aborta con `40001`.**
El bloqueo no basta (A solo bloqueó la fila, no la modificó), pero SSI conserva los
bloqueos de predicado de A después de su commit mientras B siga en curso. B cuenta con su
foto vieja e inserta dentro de lo que A leyó: ciclo, y aborta B. Paga la espera y además el aborto.

**E4 en READ COMMITTED — nunca hay dos reservas del mismo asiento; el segundo recibe `23505` (unique_violation).**
La comprobación de unicidad no usa la foto de la transacción: si el otro `INSERT` está sin
confirmar, el segundo espera; si confirma, hay violación, y si hace rollback, procede.

**E4 en REPEATABLE READ — mismo resultado: ninguna doble reserva, error `23505`.**
El índice único mira el estado de commit de las filas, no el snapshot, así que la foto
vieja de la transacción no deja pasar un duplicado. Que el nivel sea más estricto no cambia nada aquí.

**E4 en SERIALIZABLE — mismo resultado: ninguna doble reserva, error `23505` (no `40001`).**
La documentación dice que los choques de unicidad entre transacciones serializables se
siguen reportando como violación de unicidad, incluso a veces donde un orden serial no los
habría producido, incluso si antes se comprobó que el asiento estaba libre.

### Decisiones de alcance

- Resuelto el 2026-10-19: **E5 en READ COMMITTED y REPEATABLE READ — se mide, sin
  predicción previa.** Las celdas no se marcan "no aplica"; lo que salga es un resultado.
