-- F4 (DESIGN.md §5): lo que el verificador necesita para comprobar el invariante 5.
-- Una retención confirmada tiene que poder auditarse después: quién la confirmó y cuándo,
-- contra el vencimiento que tenía. Ambas columnas las pone la base (now()), nunca la aplicación.
-- Las compras directas (E1-E5) las dejan en NULL: no pasan por una retención.

ALTER TABLE reservas
    ADD COLUMN confirmada_en  timestamptz,
    ADD COLUMN confirmada_por text,
    ADD CONSTRAINT reservas_confirmacion_completa
        CHECK ((confirmada_en IS NULL) = (confirmada_por IS NULL));
