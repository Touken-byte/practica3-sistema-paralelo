import os, sys, types as T
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.update(SUPABASE_URL="https://x.supabase.co", SUPABASE_SERVICE_ROLE_KEY="k", GEMINI_API_KEY="k")
from fastapi.testclient import TestClient
import main

llamadas = {"rpc": [], "gen": [], "emb": 0}
DOCS = {"familia_bolivia": [
    {"documento_origen": "https://www.lexivox.org/norms/BO-L-N603.html", "articulo_ref": "Artículo 220°", "contenido": "[Código de las familias]\nTexto A", "similitud": 0.8123},
    {"documento_origen": "https://www.lexivox.org/norms/BO-L-N719.html", "articulo_ref": "Artículo 3°", "contenido": "[Ley 719]\nTexto B", "similitud": 0.61}]}

class FakeRPC:
    def __init__(self, nombre, params): self.n, self.p = nombre, params
    def execute(self): llamadas["rpc"].append((self.n, self.p)); return T.SimpleNamespace(data=DOCS.get(self.p["p_coleccion_id"], []))
class FakeSB:
    def rpc(self, nombre, params): return FakeRPC(nombre, params)

modo = {"emb_error": None, "gen_fallos": 0}
class FakeModels:
    def embed_content(self, model, contents, config):
        llamadas["emb"] += 1
        if modo["emb_error"]: raise Exception(modo["emb_error"])
        return T.SimpleNamespace(embeddings=[T.SimpleNamespace(values=[0.1] * 768)])
    def generate_content(self, model, contents, config):
        llamadas["gen"].append((model, contents, config))
        if modo["gen_fallos"] > 0:
            modo["gen_fallos"] -= 1; raise Exception("503 UNAVAILABLE")
        return T.SimpleNamespace(text="Respuesta con cita [Ley Nº 603, Artículo 220°]")
main.get_supabase = lambda: FakeSB()
main.get_ai = lambda: T.SimpleNamespace(models=FakeModels())
main.time.sleep = lambda s: None
c = TestClient(main.app)

# 1) coleccion_id es obligatorio y validado
assert c.post("/api/consultar", json={"pregunta": "hola"}).status_code == 422
assert c.post("/api/consultar", json={"pregunta": "hola", "coleccion_id": "a b/c"}).status_code == 422
print("coleccion_id obligatorio y validado: OK")

# 2) consulta con resultados: RPC correcta, temperatura 0, reglas y contexto en el prompt
r = c.post("/api/consultar", json={"pregunta": "¿Causales de divorcio?", "coleccion_id": "familia_bolivia", "top_k": 3})
assert r.status_code == 200, r.text
j = r.json()
nombre_rpc, params = llamadas["rpc"][-1]
assert nombre_rpc == "match_normativa_coleccion" and params["p_coleccion_id"] == "familia_bolivia" and params["match_count"] == 3
modelo, prompt, cfg = llamadas["gen"][-1]
assert cfg.temperature == 0.0
assert "EXCLUSIVAMENTE con el CONTEXTO" in prompt and "Texto A" in prompt and "derecho de familia" in prompt
assert j["asesor"].startswith("Asesor de Derecho Familiar") and len(j["fuentes"]) == 2 and j["fuentes"][0]["similitud"] == 81.23
print("consulta con contexto, temperature=0.0 y asesor por coleccion: OK")

# 3) coleccion sin antecedentes: mensaje explicito y SIN llamar al LLM
n_gen = len(llamadas["gen"])
r = c.post("/api/consultar", json={"pregunta": "¿Qué dice la ASFI sobre el encaje legal?", "coleccion_id": "familia_bolivia_vacia"})
j = r.json()
assert r.status_code == 200 and j["fuentes"] == [] and "no contiene antecedentes" in j["respuesta"] and len(llamadas["gen"]) == n_gen
print("sin antecedentes -> declara y no invoca al LLM: OK")

# 4) cuota de embeddings -> 429 claro
modo["emb_error"] = "429 RESOURCE_EXHAUSTED"
r = c.post("/api/consultar", json={"pregunta": "x", "coleccion_id": "familia_bolivia"})
assert r.status_code == 429; modo["emb_error"] = None
print("429 de embeddings -> HTTP 429: OK")

# 5) respaldo ante 503 del primer modelo
modo["gen_fallos"] = 2
r = c.post("/api/consultar", json={"pregunta": "x", "coleccion_id": "familia_bolivia"})
assert r.status_code == 200 and llamadas["gen"][-1][0] != main.MODELOS_GENERACION[0]
print("respaldo de modelo ante 503:", llamadas["gen"][-1][0], "OK")

# 6) endpoints auxiliares
assert any(a["coleccion_id"] == "familia_bolivia" for a in c.get("/api/asesores").json())
assert "/api/ingestar-web" in main.app.openapi()["paths"]
print("TODO OK")
