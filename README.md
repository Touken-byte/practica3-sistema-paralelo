# LexBancario AI - RAG multi-dominio con ingesta web concurrente

Sistema RAG (FastAPI + Streamlit + Supabase/pgvector + Gemini) con **aislamiento vectorial por colección** e
**ingesta concurrente** de páginas web (asyncio + httpx). Cada consulta se ata a una colección (`coleccion_id`):
el asesor de una colección nunca ve los vectores de otra.

## Arquitectura
Streamlit (:8501) -> FastAPI (:8000) -> Gemini (embeddings / generación) + Supabase (tabla `normativa_bancaria`, RPC `match_normativa_coleccion`)

- `POST /api/ingestar-web`: descarga URLs en paralelo, limpia HTML, hace chunking, embeddings por lotes (con backoff ante 429) e inserta en Supabase.
- `POST /api/consultar`: consulta con `coleccion_id` obligatorio, `temperature=0.0` y regla de no-alucinación.
- `GET /api/asesores`: asesores disponibles (uno por colección).

## Puesta en marcha
1. `cp .env.example .env` y completar `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY` y `GEMINI_API_KEY`.
2. En el SQL Editor de Supabase ejecutar `sql/00_base.sql` y luego `sql/01_multitenant.sql`.
3. `docker compose up --build`
4. API: http://localhost:8000/docs - Interfaz: http://localhost:8501

El "scraper" es el endpoint `/api/ingestar-web` del backend y la base de datos es Supabase (remota), configurada por `.env`.

## Ejemplos
```bash
# Ingesta concurrente (dry_run=true no gasta cuota ni escribe)
curl -X POST http://localhost:8000/api/ingestar-web -H "Content-Type: application/json" -d '{
  "coleccion_id": "familia_bolivia",
  "urls": ["https://www.lexivox.org/norms/BO-L-N719.html"],
  "chunk_size": 1000, "chunk_overlap": 200, "concurrency_workers": 4 }'

# Consulta restringida a una colección
python3 scripts/consultar.py familia_bolivia "¿Cómo se puede solicitar el divorcio según el Código de las Familias?"
```

## Pruebas (con Supabase y Gemini simulados)
```bash
docker compose exec backend python tests/test_ingesta_mock.py
docker compose exec backend python tests/test_limpieza_lexivox.py
docker compose exec backend python tests/test_consultar_mock.py
```

## Estructura
`main.py` (API de consulta) - `ingesta_web.py` (pipeline concurrente) - `app_streamlit.py` (interfaz) - `sql/` (DDL y RPC) - `scripts/` (utilitarios) - `tests/`

Las respuestas son informativas y orientativas; no reemplazan el asesoramiento de un abogado.
