import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import ingesta_web as m


def lexivox(n_art):
    """Imita la estructura real de Lexivox: anclas vacias <a id=...> dentro de <h1>, indice con enlaces #ancla,
    encabezado de la ficha partido en varios nodos y enlaces en linea dentro de los parrafos."""
    arts = "".join(
        f'<p><b>Artículo {i}°.- (Tema {i})</b> Modifica la <a href="/norms/BO-L-N439.html">Ley Nº 439</a> de 19 de noviembre de 2013 '
        f"y regula derechos y obligaciones familiares con el detalle suficiente para el caso {i}. " * 2 + "</p>"
        for i in range(1, n_art + 1))
    return f"""<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml"><head><title>Bolivia: Ley Nº 719, 7 de agosto de 2015</title><script>var a=1;</script></head><body>
<div><a href="#normText">Aumentar el tamaño del texto</a><a href="#dcmi">Ficha Técnica</a><a href="#toc">Contenido</a><a href="#references">Enlaces con otros documentos</a></div>
<h1><a id="toc"></a>Contenido</h1>
<h1><a href="#norm">Bolivia: Ley Nº 719, 7 de agosto de 2015</a></h1><h1><a href="#dcmi">Ficha Técnica (DCMI)</a></h1>
<h1><a href="#references">Enlaces con otros documentos</a></h1><h1><a href="#disclaimer">Nota importante</a></h1>
<h1><a id="norm"></a>Bolivia: Ley Nº 719, 7 de agosto de 2015</h1><a href="/norms/BO-L-N719.html">Ley Nº 719</a><br/>LEY DE 06 DE AGOSTO DE 2015{arts}
<p>Fdo. Alguien, Otro Alguien.</p>
<h1><a id="dcmi"></a>Ficha <span>Técnica</span> (<span>DCMI</span> (Dublin Core Metadata Iniciative))</h1><table><tr><td>Norma</td><td>Ley 719</td></tr></table>
<h1><a id="references"></a>Enlaces con otros documentos</h1><p>Véase también BO-L-N439</p>
<h1><a id="disclaimer"></a>Nota importante</h1><p>Lexivox ofrece esta publicación de normas como una ayuda. No puede utilizarse como referencia legal.</p>
</body></html>"""


texto, titulo = m.limpiar_html_con_titulo(lexivox(6))
print("titulo:", titulo)
for basura in ["Aumentar el tamaño", "Ficha Técnica", "Ficha", "Véase también", "Lexivox ofrece", "var a=1", "Nota importante", "Contenido"]:
    assert basura not in texto, basura
assert texto.startswith("Bolivia: Ley Nº 719") and "Artículo 6°" in texto and texto.rstrip().endswith("Otro Alguien.")
assert "de 2015\nLey Nº 719" in texto, "el titulo no debe pegarse a la linea siguiente"
assert "Ley Nº 439 de 19 de noviembre de 2013" in texto, "los enlaces en linea no deben partir la oracion"
print("cuerpo limpio OK, chars:", len(texto))
print(texto[:230].replace("\n", " | "))

# Pagina generica (sin anclas): se conserva el cuerpo, sin scripts ni menus
t2, _ = m.limpiar_html_con_titulo("<html><body><nav>menu</nav><script>x=1</script><p>Hola mundo " + "texto " * 40 + "</p></body></html>")
assert t2.startswith("Hola mundo") and "menu" not in t2 and "x=1" not in t2
print("pagina generica OK")

ch = m.dividir_en_chunks(texto, 1000, 200)
print("chunks:", len(ch), "| refs:", [c["articulo_ref"][:20] for c in ch[:3]])
assert ch[0]["articulo_ref"].startswith("Preámbulo") and all(len(c["contenido"]) <= 1000 for c in ch)
assert ch[1]["articulo_ref"].startswith("Artículo 2°.- (Tema 2)"), ch[1]["articulo_ref"]
assert any(" → " in c["articulo_ref"] for c in ch), "un fragmento que abarca varios articulos debe indicar el rango"
for linea, esperado in [("\nArtículo transitorio 2°.- Los recursos", "Artículo transitorio 2°"), ("\nArtículo final Único.- Se amplía", "Artículo final Único"), ("\nARTICULO 5º.- (Objeto) texto", "ARTICULO 5º.- (Objeto)")]:
    g = m.PATRON_ARTICULO.search(linea)
    assert g and g.group(1).startswith(esperado), (linea, g and g.group(1))
print("referencias OK:", ch[1]["articulo_ref"][:60])
print("TODO OK")
