#!/usr/bin/env python3
"""Ejecuta un dry_run de /api/ingestar-web (no gasta cuota de Gemini ni escribe en Supabase)
y muestra una tabla compacta: titulo, caracteres y fragmentos de cada norma de Lexivox.

Uso:  python3 scripts/dry_run_tabla.py 719 1639 369 243 --workers 4 --cap 8000
"""
import argparse
import json
import urllib.request


def imprimir(r: dict):
    print(f"total_s={r['tiempo_total_s']}  ok={r['urls_ok']}  fallidas={r['urls_fallidas']}  workers={r['concurrency_workers']}")
    print("porcentaje por etapa:", r["porcentaje_por_etapa"])
    print(f"{'URL':<12}{'estado':<8}{'chars':>8}{'chunks':>8}  titulo")
    for d in r["detalle"]:
        nombre = d["url"].rstrip("/").split("/")[-1].replace("BO-L-", "").replace(".html", "")
        extra = d["titulo"][:55] if d["estado"] == "ok" else (d["error"] or "")[:55]
        print(f"{nombre:<12}{d['estado']:<8}{d['chars']:>8}{d['chunks']:>8}  {extra}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("leyes", nargs="+", help="numeros de ley de Lexivox, ej: 719 1639")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--cap", type=int, default=0, help="max_chars_por_url (0 = sin limite)")
    ap.add_argument("--api", default="http://127.0.0.1:8000")
    a = ap.parse_args()

    body = {
        "coleccion_id": "dry_run",
        "urls": [f"https://www.lexivox.org/norms/BO-L-N{n}.html" for n in a.leyes],
        "chunk_size": 1000, "chunk_overlap": 200,
        "concurrency_workers": a.workers, "dry_run": True,
    }
    if a.cap:
        body["max_chars_por_url"] = a.cap
    req = urllib.request.Request(a.api + "/api/ingestar-web", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=180) as resp:
        imprimir(json.load(resp))


if __name__ == "__main__":
    main()
