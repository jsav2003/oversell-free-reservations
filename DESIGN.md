# Sistema de reservas sin sobreventa

Documento de diseño. Escrito antes del código, a propósito.

---

## 1. Qué es este proyecto

Una aplicación full stack para reservar puestos de un evento (boletas de un concierto,
cupos de un curso, turnos de un taller) que **garantiza que nunca se vende un puesto más
de una vez ni se supera la capacidad**, aunque miles de personas compren al mismo tiempo,
el servidor se caiga en medio de una compra o el cliente reintente.

El objetivo real no es la aplicación de reservas. Es demostrar, con evidencia
reproducible, **qué estrategias de concurrencia en Postgres fallan, cuáles no, por qué, y
cuánto cuesta cada una**.

El producto del proyecto es una tabla: cinco estrategias × tres niveles de aislamiento ×
varios niveles de concurrencia, con cuántos puestos se sobrevendieron, el throughput, la
latencia p99 y la tasa de reintentos de cada combinación. Además, cada anomalía queda
reproducida de forma determinista en un test, no solo observada bajo carga.

## 2. Qué NO es

- No es una plataforma de venta de boletas real ni un producto.
- No procesa pagos reales. El pago es un paso simulado con latencia y fallas
  configurables.
- No tiene autenticación seria: un identificador de usuario basta.
- No hay microservicios, ni Kafka, ni varias regiones. Una sola instancia de Postgres.
- No compite en rendimiento con sistemas de ticketing reales.

La restricción de una sola base de datos es deliberada: todos los problemas de este
documento aparecen ya con un Postgres y dos conexiones. Agregar distribución escondería
el tema bajo complejidad que no aporta.

## 3. El problema de fondo

### 3.1 La sobreventa ingenua

El código que casi todo el mundo escribe primero:

```python
vendidos = await db.fetchval(
    "SELECT count(*) FROM reservas WHERE evento_id = $1", evento_id)
if vendidos < capacidad:
    await db.execute(
        "INSERT INTO reservas (evento_id, usuario_id) VALUES ($1, $2)",
        evento_id, usuario_id)
```

Con un solo comprador funciona siempre. Con dos simultáneos y un solo puesto libre:

```
comprador A                     comprador B
SELECT count → 99 (de 100)
                                SELECT count → 99
INSERT  ✓
                                INSERT  ✓      ← reserva 101 de 100
```

Ambos leyeron antes de que el otro escribiera. Ambos tenían razón con lo que vieron.

### 3.2 Subir el nivel de aislamiento no lo arregla

La intuición dice: si `READ COMMITTED` deja leer datos que van a cambiar, `REPEATABLE
READ` debería bastar. **No basta**, y entender por qué es el centro del proyecto.

En Postgres, `REPEATABLE READ` es aislamiento por snapshot: cada transacción ve la base
como estaba al empezar. Eso impide que otra transacción le cambie una fila que ella está
modificando: si lo intenta, Postgres aborta con `could not serialize access due to
concurrent update`. Pero en el código de arriba **nadie modifica la misma fila**. A lee
el conteo e inserta *su* fila; B lee el conteo e inserta *otra* fila. No hay conflicto
de escritura, así que ambas confirman. La regla de negocio (vendidos ≤ capacidad) depende
de un conjunto de filas que ninguna de las dos bloqueó.

Esa anomalía se llama **write skew**. La documentación de Postgres lo advierte
directamente: en `REPEATABLE READ` las anomalías de serialización son posibles, y
"los intentos de imponer reglas de negocio con transacciones en este nivel probablemente
no funcionen correctamente sin un uso cuidadoso de bloqueos explícitos".

### 3.3 SERIALIZABLE sí lo arregla, y cobra por ello

En `SERIALIZABLE`, Postgres usa *Serializable Snapshot Isolation*: rastrea qué leyó cada
transacción mediante bloqueos de predicado y, si detecta que el resultado no corresponde
a ningún orden serial, aborta una con SQLSTATE `40001`. El ejemplo de la documentación
oficial es casi exactamente el nuestro: dos transacciones que leen una suma e insertan
una fila que cambia la suma de la otra.

El costo:

- **El cliente tiene que reintentar.** Una transacción abortada no es un error del
  usuario; hay que repetirla desde el principio. Sin lógica de reintento, `SERIALIZABLE`
  convierte la sobreventa en compras fallidas.
- **Bajo mucha contención, los reintentos se acumulan.** Mil compradores por los últimos
  diez puestos generan una tormenta de abortos.
- **La tasa de abortos depende de los índices.** Un escaneo secuencial siempre toma un
  bloqueo de predicado sobre la tabla entera, y eso aumenta los fallos de serialización.
  Con un índice sobre `evento_id`, el bloqueo es más fino. Medir esa diferencia es uno
  de los experimentos del proyecto.

### 3.4 El commit ambiguo

Un problema distinto, que el aislamiento no resuelve: el servidor confirma la reserva en
la base de datos y se cae antes de responder. El cliente no sabe si compró. Si reintenta,
puede reservar dos veces; si no reintenta, puede quedarse sin el puesto que sí compró.

La solución es una **clave de idempotencia**: el cliente genera un identificador único por
intento de compra y lo manda en cada reintento. La base guarda la clave con la reserva,
con una restricción `UNIQUE`, y un reintento con la misma clave devuelve la reserva
existente en vez de crear otra.

## 4. Las estrategias a comparar

Dos modelos de datos, porque el problema cambia según si los puestos tienen identidad:

- **Modelo por capacidad:** el evento tiene N cupos indistintos (un curso con 30 cupos).
- **Modelo por asiento:** cada puesto tiene identidad (fila C, asiento 14).

| # | Estrategia | Modelo | Qué hace | Predicción |
|---|---|---|---|---|
| E1 | Leer y luego insertar | Capacidad | El código de la §3.1 | Sobrevende en RC y en RR |
| E2 | `UPDATE` condicional atómico | Capacidad | `UPDATE eventos SET vendidos = vendidos + 1 WHERE id = $1 AND vendidos < capacidad RETURNING` | No sobrevende en RC; en RR aborta y exige reintento |
| E3 | Bloqueo pesimista | Capacidad | `SELECT ... FROM eventos WHERE id = $1 FOR UPDATE` antes de contar | No sobrevende en RC; **en RR puede sobrevender** (ver abajo); throughput limitado por la fila caliente |
| E4 | Restricción `UNIQUE` | Asiento | `INSERT` con `UNIQUE (evento_id, asiento_id)`; el conflicto se traduce a "ocupado" | No hay doble reserva del mismo asiento |
| E5 | `SERIALIZABLE` con reintento | Capacidad | E1 dentro de `SERIALIZABLE`, con reintento en `40001` | No sobrevende; tasa de abortos alta bajo contención |

**La columna de predicción se escribe antes de medir y no se edita después.** Si una
predicción falla, ese es un resultado, y va al informe con su explicación.

Por qué E2 funciona en `READ COMMITTED`: cuando dos `UPDATE` compiten por la misma fila,
el segundo espera a que el primero confirme y **reevalúa el `WHERE` sobre la versión
nueva de la fila**. Si `vendidos < capacidad` ya no se cumple, no actualiza nada. Es un
comportamiento documentado de Postgres, no una casualidad del timing.

Por qué E3 podría fallar en `REPEATABLE READ`, y es la predicción más interesante de la
tabla: en ese nivel la foto de la base se toma al inicio de la primera sentencia de la
transacción. Si el comprador B queda esperando en el `FOR UPDATE` mientras A inserta su
reserva y confirma, B obtiene el bloqueo, pero su foto es anterior a la inserción de A.
Como A solo bloqueó la fila del evento y no la modificó, Postgres no aborta a B. B cuenta
99 y vende el puesto 101. El bloqueo pesimista, que parece la opción segura, sobrevende
justo en el nivel "más estricto". En `READ COMMITTED` no pasa, porque cada sentencia
toma una foto nueva. Es una hipótesis razonada, no comprobada: la F2 la confirma o la
refuta con un test determinista.

### 4.1 Extensión: contadores fragmentados

Con un evento muy popular, E2 y E3 serializan a todos los compradores sobre una sola fila.
La variante divide la capacidad en K fragmentos (por ejemplo 100 cupos en 10 filas de 10)
y cada compra toma uno al azar. Gana throughput y paga con complejidad: cuando un
fragmento se agota hay que probar otro, y el último cupo libre puede estar en cualquiera.
Es opcional (ver §9).

## 5. Reservas temporales

Un comprador real no paga al instante: selecciona el asiento, lo retiene unos minutos y
paga. Eso agrega un estado y un reloj:

```
disponible → retenido (con vencimiento) → confirmado
                 └── vence sin pagar → disponible
```

Problemas que introduce:

- **La retención vence mientras el pago está en curso.** El comprador paga en el segundo
  9:59 de una retención de 10 minutos, otro toma el asiento en el 10:00, y el primer pago
  confirma. La confirmación tiene que verificar, en la misma transacción, que la
  retención sigue siendo suya y no venció.
- **Liberar las retenciones vencidas.** Un proceso periódico, o la condición de
  vencimiento evaluada en cada consulta. Se elige uno y se justifica.
- **Las retenciones vencidas chocan con la restricción `UNIQUE` de E4.** Si la retención
  vencida sigue en la tabla, bloquea al siguiente comprador de ese asiento hasta que algo
  la borre. Un índice parcial no sirve, porque no puede depender de `now()`. Hay que
  decidir entre limpiar en el momento del conflicto o liberar con un proceso periódico, y
  medir qué pasa en la ventana entre el vencimiento y la limpieza.
- **La hora de quién.** El vencimiento se compara con `now()` de la base de datos, nunca
  con el reloj del servidor de aplicación, para que varias instancias de la API no
  discrepen.

## 6. Arquitectura

```
Next.js (frontend)  ──HTTP──▶  FastAPI (API)  ──asyncpg──▶  PostgreSQL
       ▲                            │
       └────────── SSE ─────────────┘   (estado de asientos en vivo)

Generador de carga (Python asyncio) ──HTTP──▶ FastAPI
        └── registra cada solicitud y su resultado → verificador de invariantes
```

- **Backend:** FastAPI con `asyncpg` directo, **sin ORM en el camino crítico**. El SQL y
  el nivel de aislamiento de cada transacción tienen que ser explícitos y visibles en el
  código; un ORM los esconde, y esconderlos es justo lo que este proyecto no puede hacer.
- **Base de datos:** PostgreSQL en Docker, versión fijada.
- **Frontend:** Next.js con TypeScript. Detalle en la §7.
- **Generador de carga:** propio, en Python con `asyncio`, en vez de k6 o Locust. La razón:
  el verificador necesita el registro exacto de cada solicitud (qué se pidió, qué se
  respondió, con qué clave de idempotencia), y una herramienta de carga genérica no da ese
  registro de forma cómoda. Semilla fija para que cada corrida sea repetible.
- **Todo levanta con un comando:** `docker compose up`.

La estrategia activa (E1 a E5) y el nivel de aislamiento son parámetros de configuración
de la API, no ramas del código, para poder correr la matriz completa sin tocar nada.

## 7. El frontend

El frontend no es decoración: tiene dos trabajos que exigen lo mismo que el backend.

### 7.1 Mapa de asientos en vivo

Cuando tú miras el mapa, otros están comprando. El asiento que ves libre puede estar
ocupado hace 200 ms.

- **Estado en vivo por Server-Sent Events:** cada cambio de estado de un asiento llega al
  navegador sin recargar.
- **UI optimista con reconciliación:** al hacer clic, el asiento se marca de inmediato
  como "tuyo" y se manda la solicitud. Si el servidor responde que ya estaba tomado, la UI
  revierte y lo dice con claridad. Mostrar "listo" y después "ups" sin explicación es el
  fallo típico.
- **El temporizador de retención** se calcula desde el vencimiento que devuelve el
  servidor, no desde el reloj del navegador.

### 7.2 Página de laboratorio

Eliges estrategia, nivel de aislamiento, número de compradores y capacidad, y lanzas un
experimento. Ves el mapa llenarse en vivo y, al terminar, el resultado: puestos vendidos
contra capacidad, sobreventas, throughput, p99 y reintentos. Una sobreventa se marca en
rojo sobre el asiento duplicado.

Esta página es la demo del proyecto. En una entrevista, abrirla, elegir E1 en
`REPEATABLE READ` con 500 compradores y ver aparecer la sobreventa explica el tema más
rápido que cualquier párrafo.

## 8. Verificación

Esta sección es el proyecto. Sin ella queda una app de reservas más.

### 8.1 Tests de intercalado determinista

Las pruebas de carga encuentran anomalías **por probabilidad**: una corrida puede no
mostrar la sobreventa aunque exista. Por eso cada anomalía se reproduce también con un
test que fuerza el intercalado exacto:

```python
# dos conexiones, pasos en orden fijo, sin depender del timing
a = await conectar(); b = await conectar()
await a.execute("BEGIN ISOLATION LEVEL REPEATABLE READ")
await b.execute("BEGIN ISOLATION LEVEL REPEATABLE READ")
assert await a.fetchval(CONTAR) == 99
assert await b.fetchval(CONTAR) == 99
await a.execute(INSERTAR, ...)
await b.execute(INSERTAR, ...)
await a.execute("COMMIT")
await b.execute("COMMIT")          # en RR confirma: write skew demostrado
assert await contar_final() == 101
```

El mismo guion en `SERIALIZABLE` debe terminar con un `40001` en el segundo `COMMIT`. Un
test por estrategia y nivel, cada uno con la anomalía esperada **o su ausencia**
declarada. Si la predicción de la §4 dice "sobrevende" y el test no logra reproducirla,
el test o la predicción están mal, y hay que averiguar cuál.

### 8.2 Verificador de invariantes

Después de cada corrida de carga, sobre la base de datos final y el registro de
solicitudes:

1. **Capacidad:** para cada evento, reservas confirmadas ≤ capacidad.
2. **Unicidad:** ningún asiento tiene más de una reserva activa.
3. **Respuesta fiel:** toda solicitud que recibió "compra exitosa" tiene exactamente una
   reserva en la base; toda solicitud que recibió "agotado" no tiene ninguna.
4. **Idempotencia:** todos los reintentos con la misma clave corresponden a una sola
   reserva.
5. **Retenciones:** ninguna reserva se confirmó con una retención vencida o ajena.

El invariante 3 es el más exigente y el más olvidado: un sistema puede no sobrevender y
aun así decirle "compraste" a alguien sin reserva, o "agotado" a alguien que sí la tiene.

### 8.3 La matriz

Cada combinación de estrategia × aislamiento (RC, RR, SERIALIZABLE) × concurrencia
(10, 100, 1.000 compradores) × contención (capacidad holgada y capacidad escasa) se corre
con **tres semillas**. Se reporta la media y el rango, no la mejor corrida. Columnas:

- puestos sobrevendidos (el resultado principal),
- violaciones de cada invariante,
- throughput (compras exitosas por segundo),
- latencia p50 y p99,
- tasa de abortos y reintentos.

### 8.4 Inyección de fallas

Para el commit ambiguo (§3.4) y las retenciones (§5):

- Matar el proceso de la API entre el `COMMIT` y la respuesta HTTP.
- Cortar la conexión del cliente después de enviar la solicitud.
- Pausar un pago más allá del vencimiento de su retención.

En cada caso el cliente reintenta con su clave de idempotencia, y el verificador de la
§8.2 decide.

### 8.5 Experimento de índices

E5 corrido con y sin índice sobre `evento_id`, para medir cuánto sube la tasa de abortos
cuando Postgres tiene que tomar un bloqueo de predicado sobre la tabla entera. La
documentación lo anticipa; el proyecto lo cuantifica.

## 9. Plan por fases

Estimación: ~78 horas.

| Fase | Horas | Terminada cuando |
|---|---|---|
| F0 · Andamiaje | 6 | `docker compose up` levanta Postgres, API y frontend vacíos; esquema con migraciones |
| F1 · Estrategias E1–E5 | 14 | Las cinco estrategias implementadas detrás de un parámetro de configuración |
| F2 · Tests de intercalado | 10 | Cada anomalía predicha reproducida de forma determinista, y cada ausencia comprobada |
| F3 · Carga, invariantes y matriz | 14 | La matriz completa corre con un comando y produce `RESULTS.md` |
| F4 · Retenciones, idempotencia y fallas | 12 | Los tres escenarios de la §8.4 pasan el verificador |
| F5 · Frontend | 14 | Mapa en vivo con UI optimista y página de laboratorio funcionando |
| F6 · Informe | 8 | `RESULTS.md`, `ANOMALIES.md` y README escritos, todo reproducible |

### Reglas de rescate

- Si hay retraso, se recortan los contadores fragmentados (§4.1) y el experimento de
  índices (§8.5).
- Si el frontend se alarga, la página de laboratorio se reduce a mostrar los resultados ya
  calculados, sin lanzar experimentos desde el navegador. El mapa en vivo se mantiene.
- **La F2 y el verificador de invariantes no se recortan.** Son el proyecto.

## 10. Riesgos

| Riesgo | Mitigación |
|---|---|
| La carga no reproduce una anomalía que sí existe | Los tests deterministas de la F2 la demuestran sin depender del timing |
| El pool de conexiones se vuelve el cuello de botella y contamina las mediciones | Tamaño del pool fijado y reportado; una corrida de control mide la latencia sin contención |
| Conclusiones que solo valen para una máquina | Hardware, versión de Postgres y configuración en el informe; las conclusiones se plantean como comparaciones entre estrategias, no como números absolutos |
| El frontend se come el tiempo | La regla de rescate de la F5 |
| Tentación de agregar pagos reales, autenticación o microservicios | `NO-GOALS.md` fijo desde el día uno |

## 11. Qué queda en el repositorio

- `DESIGN.md` — este documento.
- `NO-GOALS.md` — los límites.
- `ANOMALIES.md` — cada anomalía con su intercalado exacto, el nivel donde aparece, el
  test que la reproduce y la explicación.
- `RESULTS.md` — la matriz completa, generada.
- `PREDICTIONS.md` — la columna de predicción de la §4, con fecha, escrita antes de medir.
- Un GIF de la página de laboratorio mostrando una sobreventa en vivo, en el README.

Lo que debería ver alguien en treinta segundos: la tabla donde E1 sobrevende en
`READ COMMITTED` **y también en `REPEATABLE READ`**, y la explicación de por qué. Ese
segundo dato es el que separa a quien leyó sobre aislamiento de quien lo entendió.

## 12. Lectura recomendada

- Documentación de PostgreSQL, capítulo "Transaction Isolation". Es corta y responde la
  mitad del proyecto.
- Martin Kleppmann, *Designing Data-Intensive Applications*, capítulo 7. Explica write
  skew con un ejemplo de médicos de turno que es el mismo patrón que la §3.2.

## 13. Glosario

- **Nivel de aislamiento:** cuánto puede ver una transacción de lo que hacen las demás
  mientras ambas están en curso.
- **READ COMMITTED:** cada consulta ve lo confirmado hasta ese momento. Es el nivel por
  defecto de Postgres.
- **REPEATABLE READ:** la transacción ve una foto fija de la base tomada al empezar
  (aislamiento por snapshot).
- **SERIALIZABLE:** el resultado debe ser equivalente a ejecutar las transacciones una
  detrás de otra; si no lo es, Postgres aborta una.
- **Write skew:** dos transacciones leen el mismo dato, escriben cosas distintas, y juntas
  rompen una regla que cada una respetaba por separado.
- **Bloqueo de predicado:** registro de qué condición leyó una transacción, usado por
  `SERIALIZABLE` para detectar conflictos. En Postgres no bloquea a nadie; solo detecta.
- **SQLSTATE 40001:** el código de error de fallo de serialización. Significa "repite la
  transacción", no "error del usuario".
- **Clave de idempotencia:** identificador que el cliente manda en cada intento de una
  misma operación, para que repetirla no la duplique.
- **Commit ambiguo:** cuando el cliente no sabe si su operación se confirmó, porque la
  respuesta se perdió.
- **Fila caliente:** una fila que todos los compradores necesitan modificar a la vez, y
  que por eso serializa el sistema entero.
