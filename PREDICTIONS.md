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
cada estrategia y nivel "la anomalía esperada o su ausencia", así que **hay que escribir
estas predicciones aquí, con fecha, antes de empezar la F2**:

- [ ] E1 en SERIALIZABLE
- [ ] E2 en SERIALIZABLE
- [ ] E3 en SERIALIZABLE
- [ ] E4 en READ COMMITTED, REPEATABLE READ y SERIALIZABLE (la tabla solo dice "no hay
      doble reserva del mismo asiento", sin nivel)
- [ ] E5 en READ COMMITTED y REPEATABLE READ (por definición corre en SERIALIZABLE; falta
      decidir si esas celdas se miden o se marcan como no aplica)
