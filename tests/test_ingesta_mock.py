import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import asyncio, time, types as pytypes
import httpx
import ingesta_web as m

# ---- HTML de prueba parecido a una ley ----
def ley_html(n_art):
    arts = "".join(f"<p>Artículo {i}°.- (Tema {i}) El presente artículo regula la materia número {i} de las familias, "
                   f"estableciendo derechos, deberes y obligaciones de sus integrantes. " * 3 + "</p>\n" for i in range(1, n_art + 1))
    return f"<html><head><script>x=1</script></head><body><nav>menu</nav><main><h1>Ley de prueba</h1>{arts}</main><footer>pie</footer></body></html>"

llamadas = {"/ok1": 0, "/flaky": 0}
def handler(request):
    p = request.url.path
    if p in ("/ok1", "/ok2", "/ok3", "/ok4"):
        return httpx.Response(200, text=ley_html(40))
    if p == "/flaky":
        llamadas["/flaky"] += 1
        return httpx.Response(503) if llamadas["/flaky"] == 1 else httpx.Response(200, text=ley_html(10))
    if p == "/404":
        return httpx.Response(404)
    if p == "/vacia":
        return httpx.Response(200, text="<html><body></body></html>")
    return httpx.Response(500)

m._crear_cliente_http = lambda w: httpx.AsyncClient(transport=httpx.MockTransport(handler), limits=httpx.Limits(max_connections=w))
m.ESPERA_INICIAL_429 = 0.05

# ---- Fakes de Supabase y Gemini ----
BD = []
def _prep(url, col, rep):
    existe = any(f["documento_origen"] == url and f["coleccion_id"] == col for f in BD)
    if existe and rep:
        BD[:] = [f for f in BD if not (f["documento_origen"] == url and f["coleccion_id"] == col)]
        return False
    return existe
m._preparar_url_en_bd = _prep
m._insertar_sync = lambda filas: BD.extend(filas)
m._borrar_url_sync = lambda url, col: BD.__setitem__(slice(None), [f for f in BD if not (f["documento_origen"] == url and f["coleccion_id"] == col)])

estado_ai = {"n": 0, "max_conc": 0, "conc": 0}
class FakeModels:
    async def embed_content(self, model, contents, config):
        estado_ai["n"] += 1
        estado_ai["conc"] += 1; estado_ai["max_conc"] = max(estado_ai["max_conc"], estado_ai["conc"])
        await asyncio.sleep(0.02)
        estado_ai["conc"] -= 1
        if estado_ai["n"] in (2, 3):
            raise Exception("429 RESOURCE_EXHAUSTED. quota exceeded")
        emb = [pytypes.SimpleNamespace(values=[0.1] * 768) for _ in contents]
        return pytypes.SimpleNamespace(embeddings=emb)
m.get_ai = lambda: pytypes.SimpleNamespace(aio=pytypes.SimpleNamespace(models=FakeModels()))

B = "http://sitio.test"
async def correr(**kw):
    req = m.IngestaWebRequest(coleccion_id="test_col", urls=[B + p for p in kw.pop("paths")], **kw)
    return await m.ingestar_web(req)

async def main():
    # 1) chunking y referencia de articulo
    txt = m.limpiar_html(ley_html(5))
    ch = m.dividir_en_chunks(txt, 400, 100)
    print("chunks:", len(ch), "| primer ref:", ch[0]["articulo_ref"][:40], "| ultimo ref:", ch[-1]["articulo_ref"][:40])
    assert "menu" not in txt and "pie" not in txt and "x=1" not in txt
    assert all(len(c["contenido"]) <= 400 for c in ch)

    # 2) pipeline completo con errores mezclados
    r = await correr(paths=["/ok1", "/ok2", "/flaky", "/404", "/vacia", "/ok3"], concurrency_workers=3, batch_size=8)
    print("ok/omit/fall:", r["urls_ok"], r["urls_omitidas"], r["urls_fallidas"], "| chunks:", r["chunks_insertados"], "| 429:", r["reintentos_api"])
    for d in r["detalle"]: print("  ", d["url"][-7:], d["estado"], d["chunks"], d["error"])
    print("  %:", r["porcentaje_por_etapa"])
    assert r["urls_ok"] == 4 and r["urls_fallidas"] == 2 and r["reintentos_api"] == 2
    assert len(BD) == r["chunks_insertados"] and all(f["coleccion_id"] == "test_col" for f in BD)
    print("  concurrencia maxima en embeddings:", estado_ai["max_conc"])

    # 3) idempotencia: segunda corrida omite todo
    r2 = await correr(paths=["/ok1", "/ok2"], concurrency_workers=2)
    print("2da corrida omitidas:", r2["urls_omitidas"]); assert r2["urls_omitidas"] == 2
    # 4) reemplazar no duplica
    n0 = len(BD); r3 = await correr(paths=["/ok1"], reemplazar=True)
    print("reemplazar -> filas antes/despues:", n0, len(BD))
    # 5) dry_run no escribe
    n1 = len(BD); r4 = await correr(paths=["/ok4"], dry_run=True)
    print("dry_run chunks:", r4["detalle"][0]["chunks"], "| filas BD sin cambio:", n1 == len(BD))
    # 6) validaciones
    for bad in [dict(chunk_overlap=1000), ]:
        try: await correr(paths=["/ok1"], **bad); print("FALLO validacion")
        except Exception as e: print("validacion OK:", type(e).__name__)
    # 7) limpieza si falla a mitad: embeddings fallan en el 2do lote
    BD.clear(); estado_ai.update(n=0)
    orig = FakeModels.embed_content
    n = {"c": 0}
    async def roto(self, model, contents, config):
        n["c"] += 1
        if n["c"] >= 2: raise ValueError("error no recuperable")
        return await orig(self, model, contents, config)
    FakeModels.embed_content = roto
    estado_ai["n"] = 99
    r5 = await correr(paths=["/ok1"], batch_size=5)
    print("fallo a mitad ->", r5["detalle"][0]["estado"], "| filas residuales en BD:", len(BD)); assert len(BD) == 0
    print("TODO OK")
asyncio.run(main())
