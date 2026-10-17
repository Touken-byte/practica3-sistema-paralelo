import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import ingesta_web as m

def lexivox(n_art):
    arts = "".join(f"<p><b>Artículo {i}°.- (Tema {i})</b> Texto del artículo número {i} que regula derechos y obligaciones familiares con detalle suficiente. " * 2 + "</p>" for i in range(1, n_art + 1))
    return f"""<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml"><head><title>Bolivia: Ley Nº 719, 7 de agosto de 2015</title><script>var a=1;</script></head><body>
<div><a>Aumentar el tamaño del texto</a><a>Reducir el tamaño del texto</a><a>Ver el documento PDF</a><a>Ficha Técnica</a><a>Contenido</a><a>Enlaces con otros documentos</a></div>
<h1>Contenido</h1><h1><a>Bolivia: Ley Nº 719, 7 de agosto de 2015</a></h1><h1><a>Ficha Técnica (DCMI)</a></h1><h1><a>Enlaces con otros documentos</a></h1><h1><a>Nota importante</a></h1>
<h1>Bolivia: Ley Nº 719, 7 de agosto de 2015</h1><p>LEY DE 06 DE AGOSTO DE 2015</p>{arts}
<h1>Ficha Técnica (DCMI (Dublin Core Metadata Iniciative))</h1><table><tr><td>Norma</td><td>Bolivia: Ley Nº 719</td></tr></table>
<h1>Enlaces con otros documentos</h1><p>Véase también BO-L-N439</p>
<h1>Nota importante</h1><p>Lexivox ofrece esta publicación de normas como una ayuda. El presente documento de ninguna manera puede ser utilizado como referencia legal.</p>
</body></html>"""

texto, titulo = m.limpiar_html_con_titulo(lexivox(6))
print("titulo:", titulo)
for basura in ["Aumentar el tamaño", "Ficha Técnica", "Véase también", "Lexivox ofrece", "var a=1", "Nota importante"]:
    assert basura not in texto, basura
assert texto.startswith("Bolivia: Ley") and "Artículo 6°" in texto
print("cuerpo limpio OK, chars:", len(texto)); print(texto[:160].replace("\n", " | "))
# sin estructura Lexivox: no toca nada
t2, _ = m.limpiar_html_con_titulo("<html><body><p>Hola mundo " + "texto " * 40 + "</p></body></html>")
assert t2.startswith("Hola mundo"); print("pagina generica intacta OK")
ch = m.dividir_en_chunks(texto, 1000, 200)
print("chunks:", len(ch), "| refs:", [c["articulo_ref"][:22] for c in ch[:3]])
