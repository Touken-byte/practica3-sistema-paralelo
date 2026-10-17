import os
import time
from typing import List, Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from google.genai import types
from pydantic import BaseModel, Field

from ingesta_web import router as router_ingesta, get_ai, get_supabase

load_dotenv()

if not all([os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_SERVICE_ROLE_KEY"), os.getenv("GEMINI_API_KEY")]):
    raise RuntimeError("Faltan variables de entorno requeridas en el archivo .env")

MODEL_EMBEDDING = "models/gemini-embedding-001"
MODELOS_GENERACION = ["gemini-3.6-flash", "gemini-flash-latest", "gemini-3.5-flash"]

# Un "asesor" por coleccion. Cualquier otra coleccion usa el asesor generico.
ASESORES = {
    "asfi_normativa": {
        "nombre": "Asesor Normativo Bancario (ASFI / Banco Unión)",
        "rol": "Eres un asesor legal bancario especializado en normativa de la ASFI y del Banco Unión.",
    },
    "familia_bolivia": {
        "nombre": "Asesor de Derecho Familiar (Bolivia)",
        "rol": ("Eres un asesor orientativo en derecho de familia de Bolivia: Código de las Familias y del Proceso "
                "Familiar y normas conexas (matrimonio, divorcio, asistencia familiar, guarda, filiación, etc.)."),
    },
    "violencia_familiar": {
        "nombre": "Asesor en Violencia Familiar y de Género (Bolivia)",
        "rol": ("Eres un asesor orientativo en normativa boliviana sobre violencia contra las mujeres y violencia "
                "familiar: medidas de protección, denuncias, competencias y sanciones."),
    },
}
ASESOR_GENERICO = {
    "nombre": "Asistente de consulta normativa",
    "rol": "Eres un asistente de consulta normativa.",
}

REGLAS = """REGLAS ESTRICTAS:
1. Responde EXCLUSIVAMENTE con el CONTEXTO provisto; no uses conocimiento externo ni supongas datos.
2. Cita siempre el respaldo entre corchetes con el documento y la referencia, por ejemplo [Ley Nº 603, Artículo 220°].
3. Si el contexto no contiene lo necesario para responder, declara expresamente: "El contexto disponible no contiene antecedentes suficientes sobre este punto". No completes con suposiciones.
4. Si la pregunta no corresponde al ámbito de este asesor, indícalo y no intentes responderla.
5. Tu respuesta es informativa y orientativa; no reemplaza el asesoramiento de un abogado.
6. Responde en español, de forma clara y estructurada."""

app = FastAPI(
    title="API RAG Multi-Dominio - Normativa (ASFI / Familia / Violencia)",
    version="2.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router_ingesta)  # /api/ingestar-web


class ConsultaRequest(BaseModel):
    pregunta: str = Field(..., min_length=1, max_length=2000)
    coleccion_id: str = Field(..., min_length=1, max_length=50, pattern=r"^[A-Za-z0-9_\-]+$")  # obligatorio
    top_k: Optional[int] = Field(4, ge=1, le=20)
    match_threshold: Optional[float] = Field(0.35, ge=0.0, le=1.0)


class FuenteNormativa(BaseModel):
    documento: str
    articulo_ref: str
    similitud: float
    contenido: str


class ConsultaResponse(BaseModel):
    pregunta: str
    coleccion_id: str
    asesor: str
    respuesta: str
    fuentes: List[FuenteNormativa]


def buscar_contexto(pregunta: str, coleccion_id: str, top_k: int, match_threshold: float):
    """Busqueda vectorial restringida a UNA coleccion (RPC match_normativa_coleccion)."""
    try:
        res_emb = get_ai().models.embed_content(
            model=MODEL_EMBEDDING,
            contents=[pregunta],
            config=types.EmbedContentConfig(output_dimensionality=768),
        )
    except Exception as e:
        err = str(e)
        if "429" in err or "RESOURCE_EXHAUSTED" in err:
            raise HTTPException(status_code=429, detail="Cuota de embeddings agotada por el momento; reintenta en unos segundos.")
        raise HTTPException(status_code=502, detail=f"Error generando el embedding de la pregunta: {e}")

    query_vector = res_emb.embeddings[0].values
    rpc_res = get_supabase().rpc("match_normativa_coleccion", {
        "query_embedding": query_vector,
        "p_coleccion_id": coleccion_id,
        "match_threshold": match_threshold,
        "match_count": top_k,
    }).execute()
    return rpc_res.data or []


def generar_con_respaldo(prompt: str) -> str:
    """Genera con temperature=0.0 (determinista); reintenta y cae a otro modelo si hay 503."""
    ultimo_error = None
    for modelo in MODELOS_GENERACION:
        for _ in range(2):
            try:
                res = get_ai().models.generate_content(
                    model=modelo,
                    contents=prompt,
                    config=types.GenerateContentConfig(temperature=0.0),
                )
                return res.text
            except Exception as e:
                ultimo_error = e
                err_str = str(e)
                print(f"[DEBUG] Falló modelo '{modelo}': {err_str}")
                if "503" in err_str or "UNAVAILABLE" in err_str:
                    time.sleep(2)
                    continue
                break
    raise HTTPException(status_code=503, detail=f"Servicio LLM no disponible temporalmente: {ultimo_error}")


def construir_prompt(asesor: dict, pregunta: str, docs: list) -> str:
    contexto = ""
    for i, d in enumerate(docs, 1):
        contexto += (f"\n--- [{i}] DOCUMENTO: {d.get('documento_origen')} | "
                     f"REFERENCIA: {d.get('articulo_ref')} ---\n{d.get('contenido')}\n")
    return f"""{asesor['rol']}

{REGLAS}

CONTEXTO NORMATIVO:
{contexto}

PREGUNTA:
{pregunta}

RESPUESTA:"""


@app.get("/")
def estado():
    return {"status": "online", "mensaje": "API RAG multi-dominio activa", "version": app.version}


@app.get("/api/asesores")
def listar_asesores():
    return [{"coleccion_id": k, "nombre": v["nombre"]} for k, v in ASESORES.items()]


@app.post("/api/consultar", response_model=ConsultaResponse)
def consultar_normativa(req: ConsultaRequest):
    if not req.pregunta.strip():
        raise HTTPException(status_code=400, detail="La pregunta no puede estar vacía.")

    asesor = ASESORES.get(req.coleccion_id, ASESOR_GENERICO)

    try:
        docs = buscar_contexto(req.pregunta, req.coleccion_id, req.top_k, req.match_threshold)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error en búsqueda vectorial: {e}")

    # Regla de no-alucinacion: sin contexto en la coleccion NO se llama al LLM.
    if not docs:
        return ConsultaResponse(
            pregunta=req.pregunta,
            coleccion_id=req.coleccion_id,
            asesor=asesor["nombre"],
            respuesta=(f"La colección '{req.coleccion_id}' no contiene antecedentes suficientes para responder "
                       "esta consulta con certeza. No es posible ofrecer una respuesta sin respaldo normativo."),
            fuentes=[],
        )

    fuentes = [FuenteNormativa(
        documento=d.get("documento_origen", ""),
        articulo_ref=d.get("articulo_ref", ""),
        similitud=round((d.get("similitud") or 0) * 100, 2),
        contenido=d.get("contenido", ""),
    ) for d in docs]

    respuesta = generar_con_respaldo(construir_prompt(asesor, req.pregunta, docs))

    return ConsultaResponse(
        pregunta=req.pregunta,
        coleccion_id=req.coleccion_id,
        asesor=asesor["nombre"],
        respuesta=respuesta,
        fuentes=fuentes,
    )
