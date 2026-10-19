-- Esquema base. Las decisiones que parecen omisiones son deliberadas: este esquema
-- no puede traer redes de seguridad que escondan las anomalías que el proyecto mide.

CREATE TABLE eventos (
    id         bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    nombre     text        NOT NULL,
    capacidad  integer     NOT NULL CHECK (capacidad > 0),
    -- Contador de la estrategia E2/E3. Sin CHECK (vendidos <= capacidad): si lo hubiera,
    -- una sobreventa se vería como un error de la base y no como una violación medida.
    -- La regla vive en el WHERE de la estrategia, que es lo que se está evaluando.
    vendidos   integer     NOT NULL DEFAULT 0 CHECK (vendidos >= 0),
    creado_en  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE reservas (
    id                  bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    evento_id           bigint      NOT NULL REFERENCES eventos (id),
    -- "Un identificador de usuario basta" (NO-GOALS): sin tabla de usuarios.
    usuario_id          text        NOT NULL,
    -- NULL en el modelo por capacidad; 1..capacidad en el modelo por asiento.
    asiento_id          integer     CHECK (asiento_id > 0),
    estado              text        NOT NULL DEFAULT 'confirmada'
                                    CHECK (estado IN ('retenida', 'confirmada')),
    -- Vencimiento de la retención (§5). Se compara siempre con now() de la base.
    vence_en            timestamptz,
    clave_idempotencia  text        NOT NULL,
    creado_en           timestamptz NOT NULL DEFAULT now(),
    CHECK (estado <> 'retenida' OR vence_en IS NOT NULL),
    -- §3.4: un reintento con la misma clave no puede crear una segunda reserva.
    CONSTRAINT reservas_clave_idempotencia_key UNIQUE (clave_idempotencia)
);

-- E4: un asiento, una reserva. Es un índice parcial a propósito: un UNIQUE completo sobre
-- (evento_id, asiento_id) también serviría a las consultas por evento_id y dejaría sin
-- sentido el experimento de la §8.5 (E5 con y sin índice sobre evento_id). Con el
-- predicado "asiento_id IS NOT NULL" el planner no puede usarlo para count(*) por evento.
-- Pendiente para la F4 (§5): una retención vencida sigue ocupando el asiento en este
-- índice hasta que algo la borre.
CREATE UNIQUE INDEX reservas_asiento_unico
    ON reservas (evento_id, asiento_id)
    WHERE asiento_id IS NOT NULL;

-- El índice del experimento §8.5. Se mantiene separado y con nombre propio para poder
-- hacer DROP INDEX / CREATE INDEX entre corridas sin tocar nada más.
CREATE INDEX reservas_evento_id_idx ON reservas (evento_id);
