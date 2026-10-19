# NO-GOALS

Fijado el 2026-10-19, antes de escribir código. Lo que está aquí no se agrega "solo un
poquito". Si alguna vez parece buena idea, vuelve a leer por qué está en esta lista.

El objetivo del proyecto es demostrar, con evidencia reproducible, qué estrategias de
concurrencia en Postgres fallan, cuáles no, por qué y cuánto cuestan (DESIGN.md §1). Todo
lo que no sirve a eso es ruido.

## No se hace

- **Una plataforma de venta de boletas real ni un producto.** Nadie va a usar esto para
  vender nada.
- **Pagos reales.** El pago es un paso simulado, con latencia y fallas configurables.
- **Autenticación seria.** Un identificador de usuario basta. Sin registro, sin sesiones,
  sin contraseñas, sin tabla de usuarios.
- **Microservicios, Kafka, colas, varias regiones.** Una sola instancia de Postgres.
- **Competir en rendimiento con sistemas de ticketing reales.** Las conclusiones son
  comparaciones entre estrategias en una misma máquina, no números absolutos.
- **ORM en el camino crítico.** El SQL y el nivel de aislamiento de cada transacción tienen
  que ser visibles en el código.
- **k6, Locust u otro generador de carga genérico.** El verificador necesita el registro
  exacto de cada solicitud.

## Por qué una sola base de datos

Todos los problemas del diseño aparecen ya con un Postgres y dos conexiones. Agregar
distribución escondería el tema bajo complejidad que no aporta (DESIGN.md §2).

## Qué se recorta primero si hay retraso

En este orden, según DESIGN.md §9: contadores fragmentados (§4.1), experimento de índices
(§8.5), y después la página de laboratorio se reduce a mostrar resultados ya calculados.

**No se recorta:** los tests de intercalado determinista (F2) y el verificador de
invariantes. Son el proyecto.
