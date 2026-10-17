"""
Fase 2 - Pipeline concurrente de scraping e ingesta vectorial multi-coleccion.

Cada URL recorre 4 etapas dentro de un mismo "worker" asyncio:
  1. RED        : descarga HTTP con httpx (async, I/O-bound)
  2. CHUNKING   : limpieza de HTML + particion en fragmentos (CPU-bound, en hilo aparte)
  3. EMBEDDINGS : Gemini por lotes, con pausa global y backoff exponencial ante 429
  4. INSERCION  : insercion masiva en Supabase (I/O-bound, en hilo aparte)

Concurrencia: asyncio.Semaphore(concurrency_workers) limita cuantas URLs estan
en vuelo a la vez. Ninguna tarea modifica estado compartido salvo contadores
simples (asyncio es monohilo: entre dos `await` no hay interrupciones), y el
trabajo en hilos devuelve sus resultados en lugar de escribir en variables globales.
"""
import asyncio
import os
import random
import re
import time
from bisect import bisect_left, bisect_right
from functools import lru_cache
from typing import Dict, List, Optional
from urllib.parse import urlparse

import httpx
import warnings
from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning
from bs4.element import NavigableString
from dotenv import load_dotenv
from fastapi import APIRouter, HTTPException
from google import genai
from google.genai import types
from pydantic import BaseModel, Field
from supabase import create_client

load_dotenv()
warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

MODEL_EMBEDDING = "models/gemini-embedding-001"
TABLA = "normativa_bancaria"
MAX_REINTENTOS_API = 6        # reintentos ante 429/503 por lote de embeddings
ESPERA_INICIAL_429 = 5.0      # segundos; se duplica en cada reintento (tope 60 s)
MAX_REINTENTOS_RED = 3
MIN_CHARS_CHUNK = 80
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; RAG-Practica-UAJMS/1.0)"}

router = APIRouter()


# --------------------------------------------------------------------------
# Clientes (se crean al primer uso, no al importar el modulo)
# --------------------------------------------------------------------------
@lru_cache(maxsize=1)
def get_supabase():
    return create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_SERVICE_ROLE_KEY"))


@lru_cache(maxsize=1)
def get_ai():
    return genai.Client(api_key=os.getenv("GEMINI_API_KEY"))


# --------------------------------------------------------------------------
# Modelo de la peticion
# --------------------------------------------------------------------------
class IngestaWebRequest(BaseModel):
    coleccion_id: str = Field(..., min_length=1, max_length=50, pattern=r"^[A-Za-z0-9_\-]+$")
    urls: List[str] = Field(..., min_length=1, max_length=200)
    chunk_size: int = Field(1000, ge=200, le=4000)
    chunk_overlap: int = Field(200, ge=0)
    concurrency_workers: int = Field(4, ge=1, le=16)
    # Parametros opcionales (extra a la guia)
    batch_size: int = Field(16, ge=1, le=100, description="Textos por peticion de embeddings")
    max_chars_por_url: Optional[int] = Field(None, ge=500, description="Recorta el texto de cada URL (util en benchmarks para cuidar la cuota)")
    reemplazar: bool = Field(False, description="Si la URL ya existe en la coleccion, la borra y la vuelve a ingerir")
    dry_run: bool = Field(False, description="Solo descarga y hace chunking; no llama a Gemini ni escribe en Supabase")


# --------------------------------------------------------------------------
# Etapa 1: RED
# --------------------------------------------------------------------------
def _crear_cliente_http(workers: int) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        headers=HEADERS,
        timeout=httpx.Timeout(20.0, connect=10.0),
        follow_redirects=True,
        limits=httpx.Limits(max_connections=workers),
    )


async def descargar(client: httpx.AsyncClient, url: str):
    """Devuelve (html, segundos). Reintenta con backoff ante errores transitorios."""
    t0 = time.perf_counter()
    ultimo = None
    for intento in range(MAX_REINTENTOS_RED):
        try:
            r = await client.get(url)
            if r.status_code in (429, 500, 502, 503, 504):
                raise httpx.HTTPStatusError(f"HTTP {r.status_code}", request=r.request, response=r)
            r.raise_for_status()
            return r.text, time.perf_counter() - t0
        except httpx.HTTPStatusError as e:
            ultimo = e
            if e.response.status_code not in (429, 500, 502, 503, 504):
                raise
        except (httpx.TimeoutException, httpx.TransportError) as e:
            ultimo = e
        await asyncio.sleep(2 ** intento + random.uniform(0, 0.5))
    raise RuntimeError(f"Descarga fallida tras {MAX_REINTENTOS_RED} intentos: {type(ultimo).__name__}: {ultimo}")


# --------------------------------------------------------------------------
# Etapa 2: CHUNKING (CPU-bound; se ejecuta en un hilo para no bloquear el event loop)
# --------------------------------------------------------------------------
PATRON_ARTICULO = re.compile(r"(?im)^\s*(art[ií]culo\s+(?:transitorio\s+|final\s+)?(?:\d+|[uú]nico)[°º.\-–\s]*(?:\([^)\n]{1,80}\))?)")


BLOQUES = {"p", "div", "h1", "h2", "h3", "h4", "h5", "h6", "li", "tr", "td", "th", "dt", "dd",
           "blockquote", "table", "ul", "ol", "section", "article", "pre"}


def _bloque_de(nodo):
    for padre in nodo.parents:
        if padre.name in BLOQUES:
            return padre
    return None


def _texto_de(nodos, fin=None) -> str:
    """Concatena el texto de los nodos con un salto de linea cada vez que se cambia de bloque
    (p, div, h1, li...), y texto en linea sin cortar (asi 'la <a>Ley 439</a> de 2013' queda en una
    sola linea). Solo toma NavigableString puros: se excluyen scripts, estilos y comentarios."""
    partes, bloque_prev = [], object()
    for nodo in nodos:
        if fin is not None and nodo is fin:
            break
        if type(nodo) is NavigableString:
            bloque = _bloque_de(nodo)
            if bloque is not bloque_prev:
                partes.append("\n")
                bloque_prev = bloque
            partes.append(str(nodo))
        elif getattr(nodo, "name", None) == "br":
            partes.append("\n")
    return "".join(partes)


def _normalizar(texto: str) -> str:
    texto = texto.replace("\x00", "").replace("\xa0", " ")
    texto = re.sub(r"[ \t]+", " ", texto)
    texto = re.sub(r" ?\n ?", "\n", texto)
    texto = re.sub(r"\n{2,}", "\n\n", texto)
    return texto.strip()


def limpiar_html_con_titulo(html: str):
    """Devuelve (texto_limpio, titulo).
    Lexivox repite en cada pagina barra de herramientas, indice, ficha tecnica, enlaces y aviso legal.
    Sus paginas marcan el cuerpo de la norma entre las anclas id="norm" e id="dcmi"; si existen,
    se conserva solo ese tramo. En otros sitios se toma el <main> o el <body> completo."""
    soup = BeautifulSoup(html, "lxml")
    titulo = " ".join(soup.title.get_text().split())[:150] if soup.title and soup.title.get_text() else ""
    for tag in soup(["script", "style", "nav", "header", "footer", "aside", "form", "noscript"]):
        tag.decompose()
    ancla_ini, ancla_fin = soup.find(id="norm"), soup.find(id="dcmi")
    if ancla_ini is not None and ancla_fin is not None:
        texto = _texto_de(ancla_ini.next_elements, ancla_fin)
    else:
        cuerpo = soup.find("main") or soup.body or soup
        texto = _texto_de(cuerpo.descendants)
    return _normalizar(texto), titulo


def limpiar_html(html: str) -> str:
    return limpiar_html_con_titulo(html)[0]


def dividir_en_chunks(texto: str, chunk_size: int, overlap: int) -> List[Dict[str, str]]:
    """Ventana deslizante con solapamiento. articulo_ref = ultimo 'Articulo N' visto antes del chunk."""
    marcas = [(m.start(), m.group(1).strip()[:100]) for m in PATRON_ARTICULO.finditer(texto)]
    posiciones = [p for p, _ in marcas]
    n = len(texto)
    chunks, ini = [], 0
    while ini < n:
        fin = min(ini + chunk_size, n)
        if fin < n:  # intenta cortar en salto de linea o espacio, no a mitad de palabra
            corte = texto.rfind("\n", ini + chunk_size // 2, fin)
            if corte == -1:
                corte = texto.rfind(" ", ini + chunk_size // 2, fin)
            if corte > ini:
                fin = corte
        contenido = texto[ini:fin].strip()
        if len(contenido) >= MIN_CHARS_CHUNK:
            idx = bisect_right(posiciones, ini) - 1          # encabezado vigente al inicio del fragmento
            idx_fin = bisect_left(posiciones, fin) - 1        # ultimo encabezado que empieza dentro del fragmento
            ref = marcas[idx][1] if idx >= 0 else "Preámbulo"
            if idx_fin > idx:
                ref = f"{ref} → {marcas[idx_fin][1]}"
            chunks.append({"articulo_ref": ref[:250], "contenido": contenido})
        if fin >= n:
            break
        siguiente = fin - overlap
        ini = siguiente if siguiente > ini else fin
    return chunks


def _limpiar_y_dividir(html: str, chunk_size: int, overlap: int, max_chars: Optional[int]):
    """Funcion para hilo: devuelve (chunks, n_caracteres, titulo, segundos)."""
    t0 = time.perf_counter()
    texto, titulo = limpiar_html_con_titulo(html)
    if max_chars:
        texto = texto[:max_chars]
    chunks = dividir_en_chunks(texto, chunk_size, overlap)
    return chunks, len(texto), titulo, time.perf_counter() - t0


# --------------------------------------------------------------------------
# Etapa 3: EMBEDDINGS con control de tasa
# --------------------------------------------------------------------------
class LimitadorEmbeddings:
    """Pausa global compartida: cuando una peticion recibe 429, TODOS los workers
    esperan (con asyncio.sleep, sin bloquear el event loop) en vez de seguir golpeando la API."""

    def __init__(self):
        self._hasta = 0.0

    async def esperar(self):
        while True:
            restante = self._hasta - time.monotonic()
            if restante <= 0:
                return
            await asyncio.sleep(restante)

    def pausar(self, segundos: float):
        self._hasta = max(self._hasta, time.monotonic() + segundos)


async def embeber_lote(textos: List[str], limitador: LimitadorEmbeddings, contadores: Dict[str, int]):
    espera = ESPERA_INICIAL_429
    for _ in range(MAX_REINTENTOS_API):
        await limitador.esperar()
        try:
            res = await get_ai().aio.models.embed_content(
                model=MODEL_EMBEDDING,
                contents=textos,
                config=types.EmbedContentConfig(output_dimensionality=768),
            )
            return [e.values for e in res.embeddings]
        except Exception as e:
            err = str(e)
            if "PerDay" in err or "per day" in err.lower():
                raise RuntimeError("Cuota DIARIA de embeddings agotada; reintentar manana o usar otra API key") from e
            if "429" in err or "RESOURCE_EXHAUSTED" in err or "503" in err or "UNAVAILABLE" in err:
                contadores["reintentos_api"] += 1
                pausa = min(espera, 60.0) + random.uniform(0, 1.5)
                limitador.pausar(pausa)
                espera *= 2
                continue
            raise
    raise RuntimeError(f"Cuota de la API agotada tras {MAX_REINTENTOS_API} reintentos")


# --------------------------------------------------------------------------
# Etapa 4: INSERCION en Supabase (cliente sincrono, ejecutado en hilos)
# --------------------------------------------------------------------------
def _preparar_url_en_bd(url: str, coleccion: str, reemplazar: bool) -> bool:
    """Devuelve True si la URL debe OMITIRSE (ya indexada y reemplazar=False)."""
    sb = get_supabase()
    q = sb.table(TABLA).select("id").eq("documento_origen", url).eq("coleccion_id", coleccion).limit(1).execute()
    if not q.data:
        return False
    if reemplazar:
        sb.table(TABLA).delete().eq("documento_origen", url).eq("coleccion_id", coleccion).execute()
        return False
    return True


def _insertar_sync(filas: List[dict]):
    get_supabase().table(TABLA).insert(filas).execute()


def _borrar_url_sync(url: str, coleccion: str):
    get_supabase().table(TABLA).delete().eq("documento_origen", url).eq("coleccion_id", coleccion).execute()


# --------------------------------------------------------------------------
# Pipeline por URL
# --------------------------------------------------------------------------
async def procesar_url(url, req, client, sem, limitador, contadores) -> dict:
    tiempos = {"red": 0.0, "chunking": 0.0, "embeddings": 0.0, "insercion": 0.0}
    res = {"url": url, "titulo": "", "estado": "ok", "chunks": 0, "chars": 0, "error": None}
    insertado = False
    async with sem:
        try:
            if not req.dry_run:
                t0 = time.perf_counter()
                omitir = await asyncio.to_thread(_preparar_url_en_bd, url, req.coleccion_id, req.reemplazar)
                tiempos["insercion"] += time.perf_counter() - t0
                if omitir:
                    res.update(estado="omitida", error="ya indexada en la coleccion")
                    res["tiempos"] = tiempos
                    return res

            html, t_red = await descargar(client, url)
            tiempos["red"] += t_red

            chunks, n_chars, titulo, t_chunk = await asyncio.to_thread(
                _limpiar_y_dividir, html, req.chunk_size, req.chunk_overlap, req.max_chars_por_url
            )
            tiempos["chunking"] += t_chunk
            res["chars"] = n_chars
            res["titulo"] = titulo
            if not chunks:
                raise ValueError("la pagina no contiene texto util")

            if not req.dry_run:
                dominio = urlparse(url).netloc
                for i in range(0, len(chunks), req.batch_size):
                    lote = chunks[i:i + req.batch_size]

                    t0 = time.perf_counter()
                    vectores = await embeber_lote([c["contenido"] for c in lote], limitador, contadores)
                    tiempos["embeddings"] += time.perf_counter() - t0

                    filas = [{
                        "documento_origen": url,
                        "organismo": dominio,
                        "tipo_norma": "Web",
                        "jerarquia": "web",
                        "articulo_ref": c["articulo_ref"],
                        "contenido": c["contenido"],
                        "embedding": v,
                        "coleccion_id": req.coleccion_id,
                    } for c, v in zip(lote, vectores)]

                    t0 = time.perf_counter()
                    await asyncio.to_thread(_insertar_sync, filas)
                    insertado = True
                    tiempos["insercion"] += time.perf_counter() - t0
                    res["chunks"] += len(filas)
            else:
                res["chunks"] = len(chunks)

        except Exception as e:
            res.update(estado="error", error=f"{type(e).__name__}: {e}"[:300])
            if insertado:  # no dejar una URL a medias en la base
                try:
                    await asyncio.to_thread(_borrar_url_sync, url, req.coleccion_id)
                    res["chunks"] = 0
                except Exception:
                    pass
    res["tiempos"] = tiempos
    return res


# --------------------------------------------------------------------------
# Endpoint
# --------------------------------------------------------------------------
@router.post("/api/ingestar-web")
async def ingestar_web(req: IngestaWebRequest):
    if req.chunk_overlap >= req.chunk_size:
        raise HTTPException(status_code=400, detail="chunk_overlap debe ser menor que chunk_size")

    urls = list(dict.fromkeys(u.strip() for u in req.urls if u.strip()))
    invalidas = [u for u in urls if urlparse(u).scheme not in ("http", "https") or not urlparse(u).netloc]
    if invalidas:
        raise HTTPException(status_code=400, detail=f"URLs no validas (solo http/https): {invalidas[:3]}")

    sem = asyncio.Semaphore(req.concurrency_workers)
    limitador = LimitadorEmbeddings()
    contadores = {"reintentos_api": 0}

    t_inicio = time.perf_counter()
    async with _crear_cliente_http(req.concurrency_workers) as client:
        resultados = await asyncio.gather(
            *(procesar_url(u, req, client, sem, limitador, contadores) for u in urls)
        )
    total = time.perf_counter() - t_inicio

    etapas = ("red", "chunking", "embeddings", "insercion")
    suma = {k: sum(r["tiempos"][k] for r in resultados) for k in etapas}
    suma_total = sum(suma.values()) or 1.0

    return {
        "coleccion_id": req.coleccion_id,
        "concurrency_workers": req.concurrency_workers,
        "dry_run": req.dry_run,
        "tiempo_total_s": round(total, 3),
        "urls_ok": sum(r["estado"] == "ok" for r in resultados),
        "urls_omitidas": sum(r["estado"] == "omitida" for r in resultados),
        "urls_fallidas": sum(r["estado"] == "error" for r in resultados),
        "chunks_insertados": 0 if req.dry_run else sum(r["chunks"] for r in resultados),
        "reintentos_api": contadores["reintentos_api"],
        "tiempo_por_etapa_s": {k: round(v, 3) for k, v in suma.items()},
        "porcentaje_por_etapa": {k: round(100 * v / suma_total, 1) for k, v in suma.items()},
        "detalle": [
            {k: (round(v, 3) if isinstance(v, float) else v) for k, v in r.items() if k != "tiempos"}
            | {"tiempos_s": {k: round(v, 3) for k, v in r["tiempos"].items()}}
            for r in resultados
        ],
    }
