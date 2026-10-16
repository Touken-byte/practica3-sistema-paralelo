-- Fase 1: aislamiento vectorial multi-tenant por coleccion_id

ALTER TABLE normativa_bancaria
  ADD COLUMN IF NOT EXISTS coleccion_id VARCHAR(50);

UPDATE normativa_bancaria
   SET coleccion_id = 'asfi_normativa'
 WHERE coleccion_id IS NULL;

ALTER TABLE normativa_bancaria
  ALTER COLUMN coleccion_id SET NOT NULL;

CREATE INDEX IF NOT EXISTS idx_normativa_coleccion
  ON normativa_bancaria USING btree (coleccion_id);

CREATE OR REPLACE FUNCTION match_normativa_coleccion(
  query_embedding vector(768),
  p_coleccion_id  VARCHAR,
  match_threshold FLOAT DEFAULT 0.3,
  match_count     INT   DEFAULT 5
)
RETURNS TABLE (
  id               BIGINT,
  documento_origen TEXT,
  articulo_ref     TEXT,
  contenido        TEXT,
  coleccion_id     VARCHAR,
  similitud        FLOAT
)
LANGUAGE plpgsql STABLE
AS $$
BEGIN
  RETURN QUERY
  SELECT n.id,
         n.documento_origen,
         n.articulo_ref,
         n.contenido,
         n.coleccion_id,
         (1 - (n.embedding <=> query_embedding))::FLOAT AS similitud
    FROM normativa_bancaria n
   WHERE n.coleccion_id = p_coleccion_id
     AND (1 - (n.embedding <=> query_embedding)) > match_threshold
   ORDER BY n.embedding <=> query_embedding
   LIMIT match_count;
END;
$$;
