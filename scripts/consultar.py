#!/usr/bin/env python3
"""Consulta /api/consultar y muestra la respuesta y las fuentes de forma compacta.

Uso:  python3 scripts/consultar.py familia_bolivia "¿Qué causales permiten el divorcio?"
"""
import argparse
import json
import urllib.error
import urllib.request


def mostrar(r: dict, max_chars: int = 1400):
    print(f"Asesor: {r['asesor']}   [coleccion: {r['coleccion_id']}]")
    print("Pregunta:", r["pregunta"])
    print("-" * 80)
    resp = r["respuesta"].strip()
    print(resp if len(resp) <= max_chars else resp[:max_chars] + " [...]")
    print("-" * 80)
    print(f"Fuentes ({len(r['fuentes'])}):")
    for f in r["fuentes"]:
        doc = f["documento"].replace("https://www.lexivox.org/norms/", "")
        print(f"  {f['similitud']:>6}%  {doc[:20]:<20} {f['articulo_ref'][:48]}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("coleccion_id")
    ap.add_argument("pregunta")
    ap.add_argument("--k", type=int, default=4)
    ap.add_argument("--umbral", type=float, default=0.35)
    ap.add_argument("--api", default="http://127.0.0.1:8000")
    a = ap.parse_args()
    body = {"pregunta": a.pregunta, "coleccion_id": a.coleccion_id, "top_k": a.k, "match_threshold": a.umbral}
    req = urllib.request.Request(a.api + "/api/consultar", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            mostrar(json.load(resp))
    except urllib.error.HTTPError as e:
        print(f"HTTP {e.code}:", e.read().decode()[:400])


if __name__ == "__main__":
    main()
