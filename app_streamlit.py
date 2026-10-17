import requests
import streamlit as st

# Dentro de la red de Docker (rag_net) el backend se llama por el nombre del servicio.
API_BASE = "http://backend:8000"
API_URL = f"{API_BASE}/api/consultar"

st.set_page_config(
    page_title="Asistente Normativo Multi-Dominio",
    page_icon="🏛️",
    layout="wide"
)

# Respaldo por si /api/asesores no responde
ASESORES_RESPALDO = [
    {"coleccion_id": "asfi_normativa", "nombre": "Asesor Normativo Bancario (ASFI / Banco Unión)"},
    {"coleccion_id": "familia_bolivia", "nombre": "Asesor de Derecho Familiar (Bolivia)"},
    {"coleccion_id": "violencia_familiar", "nombre": "Asesor en Violencia Familiar y de Género (Bolivia)"},
]

SALUDOS = {
    "asfi_normativa": "Hola. Puedo asistirte en la interpretación y localización de resoluciones de la ASFI y circulares aplicadas a Banco Unión. ¿En qué puedo orientarte hoy?",
    "familia_bolivia": "Hola. Soy un asesor orientativo en derecho de familia de Bolivia (divorcio, asistencia familiar, guarda, filiación...). Respondo solo con el Código de las Familias y normas conexas indexadas. No reemplazo a un abogado. ¿Cuál es tu consulta?",
    "violencia_familiar": "Hola. Puedo orientarte con la normativa boliviana sobre violencia contra las mujeres y violencia familiar (medidas de protección, denuncias, sanciones). No reemplazo a un abogado. ¿Cuál es tu consulta?",
}
EJEMPLOS = {
    "asfi_normativa": "ej. facultades de fiscalización, suficiencia patrimonial...",
    "familia_bolivia": "ej. ¿cómo se solicita el divorcio?, asistencia familiar...",
    "violencia_familiar": "ej. medidas de protección, cómo denunciar...",
}


@st.cache_data(ttl=60, show_spinner=False)
def cargar_asesores():
    try:
        r = requests.get(f"{API_BASE}/api/asesores", timeout=5)
        if r.status_code == 200 and r.json():
            return r.json()
    except Exception:
        pass
    return ASESORES_RESPALDO


def saludo(coleccion_id: str) -> dict:
    texto = SALUDOS.get(coleccion_id, "Hola. ¿Qué deseas consultar en esta colección?")
    return {"role": "assistant", "content": texto, "fuentes": []}


def mostrar_fuentes(fuentes):
    with st.expander("📚 Fuentes y referencias normativas"):
        for idx, f in enumerate(fuentes, start=1):
            doc = f["documento"]
            etiqueta = f"[{doc.rsplit('/', 1)[-1]}]({doc})" if doc.startswith("http") else doc
            st.markdown(f"**[{idx}] {etiqueta}** — *{f['articulo_ref']}* (Coincidencia: `{f['similitud']}%`)")
            st.text(f['contenido'][:300] + ("..." if len(f['contenido']) > 300 else ""))


# ---------------- Barra lateral ----------------
asesores = cargar_asesores()
ids = [a["coleccion_id"] for a in asesores]
nombres = {a["coleccion_id"]: a["nombre"] for a in asesores}

with st.sidebar:
    st.image("https://upload.wikimedia.org/wikipedia/commons/thumb/c/c5/Escudo_de_Bolivia.svg/200px-Escudo_de_Bolivia.svg.png", width=70)
    st.title("⚖️ Control Normativo")
    coleccion_id = st.selectbox("Asesor (colección):", ids, format_func=lambda c: nombres[c])
    st.caption(f"Colección activa: `{coleccion_id}`")
    st.divider()

    st.subheader("Configuración de Recuperación")
    top_k = st.slider("Documentos a recuperar (top_k):", min_value=1, max_value=8, value=4)
    threshold = st.slider("Umbral mínimo de similitud:", min_value=0.20, max_value=0.80, value=0.35, step=0.05)

    st.divider()
    if st.button("Limpiar historial"):
        st.session_state.messages = [saludo(coleccion_id)]
        st.rerun()

# Al cambiar de asesor se reinicia la conversación: cada chat queda atado a UNA colección.
if st.session_state.get("coleccion_actual") != coleccion_id:
    st.session_state.coleccion_actual = coleccion_id
    st.session_state.messages = [saludo(coleccion_id)]

st.title(f"🏛️ {nombres[coleccion_id]}")
st.caption("Consultas semánticas con recuperación aislada por colección: cada asesor solo ve su propio corpus.")

# ---------------- Historial ----------------
for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])
        if msg.get("fuentes"):
            mostrar_fuentes(msg["fuentes"])

# ---------------- Consulta ----------------
if prompt := st.chat_input(f"Escribe tu consulta ({EJEMPLOS.get(coleccion_id, '...')})"):
    st.session_state.messages.append({"role": "user", "content": prompt, "fuentes": []})
    with st.chat_message("user"):
        st.markdown(prompt)

    with st.chat_message("assistant"):
        with st.spinner("Consultando la colección y generando la respuesta..."):
            payload = {
                "pregunta": prompt,
                "coleccion_id": coleccion_id,
                "top_k": top_k,
                "match_threshold": threshold
            }
            try:
                response = requests.post(API_URL, json=payload, timeout=120)
                if response.status_code == 200:
                    data = response.json()
                    respuesta_texto = data.get("respuesta", "")
                    fuentes = data.get("fuentes", [])

                    st.markdown(respuesta_texto)
                    if fuentes:
                        mostrar_fuentes(fuentes)
                    else:
                        st.caption(f"Sin fragmentos recuperados en la colección `{coleccion_id}`.")

                    st.session_state.messages.append({
                        "role": "assistant",
                        "content": respuesta_texto,
                        "fuentes": fuentes
                    })
                elif response.status_code == 429:
                    st.warning("Se alcanzó el límite de peticiones por minuto de la API externa. Espera unos segundos y vuelve a intentar.")
                else:
                    st.error(f"Error en el servidor API (Código {response.status_code}): {response.text}")
            except requests.exceptions.ConnectionError:
                st.error(f"No se pudo conectar con el servidor FastAPI en {API_BASE}. Verifica que el servicio `backend` esté activo (`docker compose ps`).")
            except Exception as e:
                st.error(f"Error inesperado: {e}")
