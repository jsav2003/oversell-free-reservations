# ANOMALIES

Cada anomalía de este proyecto, reproducida con un **intercalado fijo** (DESIGN.md §8.1):
dos conexiones, pasos en orden determinado, sin depender del timing. Los tests están en
`backend/tests/test_intercalado.py`; las herramientas, en `backend/tests/intercalador.py`.

Escenario común: un evento de capacidad 2 con una reserva previa, o sea **un solo puesto
libre**, y dos compradores, A y B. Más de 2 reservas al final es sobreventa. Postgres 18.6.

Lo que sigue es evidencia de que la anomalía **existe** y por qué. La frecuencia con que
aparece bajo carga y su costo van en `RESULTS.md`, que sale de la matriz de la F3.

## Predicciones refutadas

`PREDICTIONS.md` no se edita después de medir. Lo que falló se queda allí como estaba y se
explica aquí.

### E4 en SERIALIZABLE con comprobación previa: sale `40001`, no `23505`

- **Predicción (19/10):** "mismo resultado: ninguna doble reserva, error `23505` (no
  `40001`)", y se citaba la documentación: el choque de unicidad se reporta como violación
  de unicidad "incluso si antes se comprobó que el asiento estaba libre".
- **Qué pasó:** si A y B consultan `SELECT count(*) ... WHERE asiento_id = 5` antes de
  insertar y luego A inserta y confirma, el `INSERT` de B falla con **`40001`** (fallo de
  serialización). No hay doble reserva, pero el código de error es otro.
- **Sin comprobación previa**, que es lo que hace `app/estrategias.py` (E4 solo inserta),
  B recibe **`23505`**, como se predijo. La predicción acertó en el caso real de la
  estrategia y falló en la variante que ella misma mencionaba.
- **Por qué:** esto es una lectura mía de lo observado, no está verificado contra el código
  de Postgres. La lectura previa deja un bloqueo de predicado de B sobre el asiento, y el
  `INSERT` de A cae dentro de él: eso forma una dependencia que SSI detecta, y Postgres
  informa el fallo de serialización en lugar del de unicidad. La predicción leyó la
  documentación como "el `23505` se mantiene incluso en ese caso"; el comportamiento
  observado no coincide con esa lectura. Antes de citar la explicación como hecho conviene
  releer esa sección de la documentación.
- **Consecuencia práctica:** un cliente de E4 que haga "mirar si está libre" antes de
  insertar y corra en SERIALIZABLE debe tratar `40001` como reintento, no solo `23505`
  como "ocupado".
- **Tests:** `test_e4_serializable_prediccion_con_comprobacion_previa` (`xfail` estricto:
  deja de pasar el día que la predicción se cumpla) y
  `test_e4_serializable_con_comprobacion_previa` (fija lo observado).

## Sobreventas reproducidas

### E1 en READ COMMITTED y REPEATABLE READ: write skew

El código de la §3.1. Guion:

```
A: BEGIN; cuenta → 1            B: BEGIN; cuenta → 1
A: INSERT                       B: INSERT
A: COMMIT  ✓                    B: COMMIT  ✓      → 3 reservas, capacidad 2
```

Nadie modifica la fila de otro: A inserta *su* fila y B *la suya*, así que no hay conflicto
de escritura que Postgres pueda detectar. **Que REPEATABLE READ sea más estricto no ayuda**:
su snapshot impide ver la fila de A, que es justo lo que permite que B cuente mal.
En SERIALIZABLE el mismo guion termina con **`40001` en el `COMMIT` de B** (los dos
`INSERT` pasan) y quedan 2 reservas.

### E3 en REPEATABLE READ: el bloqueo pesimista sobrevende

La predicción menos obvia y la que sí se cumplió. Guion:

```
A: BEGIN; SELECT ... FOR UPDATE  (toma la fila del evento)
B: BEGIN; SELECT ... FOR UPDATE  (queda esperando; su snapshot se toma aquí)
A: cuenta → 1; INSERT; COMMIT
B: obtiene el bloqueo; cuenta → 1; INSERT; COMMIT   → 3 reservas, capacidad 2
```

B espera, pero **la espera no actualiza su foto**: la tomó al empezar el `FOR UPDATE`, antes
del `INSERT` de A. Como A solo bloqueó la fila del evento y no la modificó, Postgres no
aborta a B. Es la razón por la que el `FOR UPDATE` parece seguro y no lo es en ese nivel.
En READ COMMITTED B cuenta con una foto nueva, ve 2 y responde "agotado". En SERIALIZABLE B
cuenta 1 (foto vieja) pero su **`INSERT` falla con `40001`**, no el `COMMIT`.

### E5 en READ COMMITTED y REPEATABLE READ: sobrevende igual que E1

Sin predicción previa (decisión de alcance del 19/10). E5 es E1 más reintento ante `40001`,
y en estos niveles ese error no aparece, así que **no hay nada que reintentar**: el mismo
guion de E1 da 3 reservas con capacidad 2. Lo observado, no lo predicho.

## No sobrevenden, pero cuestan

### E2: el `UPDATE` condicional

- **READ COMMITTED:** B espera al `UPDATE` de A; cuando A confirma, **reevalúa el `WHERE`
  sobre la fila nueva**, no encuentra cupo y responde "agotado" sin error. 2 reservas.
- **REPEATABLE READ y SERIALIZABLE:** B espera y, al confirmar A, recibe **`40001`** (`could
  not serialize access due to concurrent update`) y no reevalúa
  el `WHERE`: aborta porque la fila cambió, no porque se haya agotado el cupo. Es la tasa
  de abortos que el diseño predijo para estos niveles.

### E4: la restricción `UNIQUE`

- En los tres niveles B, que intenta el mismo asiento, **queda esperando** mientras A no
  confirme. Si A confirma, B recibe `23505`; si A hace `ROLLBACK`, B procede y compra
  (`test_e4_si_el_primero_hace_rollback_el_segundo_procede`). Nunca hay dos reservas del
  mismo asiento.

### E5 en SERIALIZABLE

B aborta con `40001` y, al reintentar en una transacción nueva, cuenta 2 y responde
"agotado" (`test_e5_reintenta_y_termina_en_agotado`). No sobrevende; el costo está en los
reintentos, que `RESULTS.md` cuantifica.

## Qué no se probó aquí

- **Que el test sea la prueba de la frecuencia.** Reproducir la anomalía una vez demuestra
  que existe; cuántas veces ocurre depende de la contención y se mide en la matriz.
- **Más de dos compradores.** Los guiones son de A contra B. Los efectos con decenas de
  transacciones (por ejemplo, qué transacción elige SSI como víctima) los cubre la carga,
  no estos tests.
- **Retenciones y commit ambiguo** (§3.4 y §5): son la F4.
