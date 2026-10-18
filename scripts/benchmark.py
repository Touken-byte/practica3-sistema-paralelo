#!/usr/bin/env python3
"""Benchmark de la ingesta concurrente: T_p, Speedup, Eficiencia y desglose por etapa.

Se ejecuta DENTRO del contenedor backend (usa su .env para limpiar las colecciones temporales):
    docker compose exec backend python scripts/benchmark.py
    docker compose exec backend python scripts/benchmark.py --dry --reps 1 --cooldown 0    # prueba sin gastar cuota

Diseno del experimento
  * 16 URLs (>= 10 exigidas) y max_chars_por_url = 3000: todas las paginas se recortan al mismo tamano.
  * workers p in {1, 2, 4, 8}; 3 repeticiones por configuracion; orden aleatorio (semilla fija) para no
    sesgar por la deriva de la red o de la cuota.
  * Cada corrida usa una coleccion temporal 'bench_*' que se borra al terminar (la limpieza no se cronometra).
  * Pausa entre corridas para que la ventana de tokens por minuto de la API se vacie.
  * Una corrida con reintentos por 429 queda 'contaminada' (incluye esperas de backoff) y se repite una vez.
"""
import argparse
import csv
import json
import os
import random
import statistics
import sys
import time
import urllib.request
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

LEYES = (719, 369, 243, 263, 348, 548, 603, 807, 464, 342, 223, 1639, 1226, 1060, 18, 1173)
URLS_DEFECTO = [f"https://www.lexivox.org/norms/BO-L-N{n}.html" for n in LEYES]
ETAPAS = ("red", "chunking", "embeddings", "insercion")


def llamar_api(api: str, payload: dict, timeout: int = 1800) -> dict:
    req = urllib.request.Request(api + "/api/ingestar-web", data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"})
    t0 = time.perf_counter()
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = json.load(r)
    data["_cliente_s"] = time.perf_counter() - t0
    return data


def limpiar_coleccion(coleccion: str):
    if not coleccion.startswith("bench_"):   # seguro: jamas borra colecciones reales
        raise ValueError("solo se pueden borrar colecciones bench_*")
    from ingesta_web import get_supabase, TABLA
    get_supabase().table(TABLA).delete().eq("coleccion_id", coleccion).execute()


def fila_de_respuesta(rep, p, intento, coleccion, d, n_urls, dry):
    t = d["tiempo_por_etapa_s"]
    pct = d["porcentaje_por_etapa"]
    valida = d["urls_ok"] == n_urls
    limpia = valida and (dry or d["reintentos_api"] == 0)
    fila = {"rep": rep, "p": p, "intento": intento, "coleccion": coleccion,
            "T_s": d["tiempo_total_s"], "cliente_s": round(d["_cliente_s"], 3),
            "urls_ok": d["urls_ok"], "urls_fallidas": d["urls_fallidas"], "chunks": d["chunks_insertados"],
            "reintentos_api": d["reintentos_api"], "valida": valida, "limpia": limpia}
    for e in ETAPAS:
        fila[f"{e}_s"] = t[e]
        fila[f"pct_{e}"] = pct[e]
    return fila


def resumir(filas, ps):
    """Una fila por p con T media/desv., Speedup, Eficiencia y fraccion serial de Amdahl estimada."""
    buenas = [f for f in filas if f["limpia"]] or [f for f in filas if f["valida"]]
    por_p = {p: [f for f in buenas if f["p"] == p] for p in ps}
    medias = {p: statistics.mean(f["T_s"] for f in fs) for p, fs in por_p.items() if fs}
    t1 = medias.get(1)
    resumen = []
    for p in ps:
        fs = por_p.get(p, [])
        if not fs:
            continue
        tp = medias[p]
        sp = (t1 / tp) if t1 else None
        ep = (sp / p) if sp else None
        f_serial = ((1 / sp - 1 / p) / (1 - 1 / p)) if (sp and p > 1) else None   # Sp = 1 / (f + (1-f)/p)
        fila = {"p": p, "n_corridas": len(fs), "T_media_s": round(tp, 3),
                "T_desv_s": round(statistics.stdev([f["T_s"] for f in fs]), 3) if len(fs) > 1 else 0.0,
                "T_min_s": round(min(f["T_s"] for f in fs), 3), "T_max_s": round(max(f["T_s"] for f in fs), 3),
                "Speedup": round(sp, 3) if sp else None, "Eficiencia": round(ep, 3) if ep else None,
                "f_serial_amdahl": round(f_serial, 3) if f_serial is not None else None,
                "reintentos_api": sum(f["reintentos_api"] for f in fs)}
        for e in ETAPAS:
            fila[f"pct_{e}"] = round(statistics.mean(f[f"pct_{e}"] for f in fs), 1)
            fila[f"{e}_s"] = round(statistics.mean(f[f"{e}_s"] for f in fs), 3)
        resumen.append(fila)
    return resumen


def imprimir_resumen(resumen):
    print(f"\n{'p':>2} {'n':>2} {'T_p(s)':>9} {'desv':>7} {'S_p':>6} {'E_p':>6} {'f_serial':>9}   % red / chunk / emb / insert   429")
    for r in resumen:
        print(f"{r['p']:>2} {r['n_corridas']:>2} {r['T_media_s']:>9.2f} {r['T_desv_s']:>7.2f} "
              f"{(r['Speedup'] if r['Speedup'] is not None else float('nan')):>6.2f} "
              f"{(r['Eficiencia'] if r['Eficiencia'] is not None else float('nan')):>6.2f} "
              f"{(r['f_serial_amdahl'] if r['f_serial_amdahl'] is not None else float('nan')):>9.3f}   "
              f"{r['pct_red']:>5.1f} /{r['pct_chunking']:>5.1f} /{r['pct_embeddings']:>5.1f} /{r['pct_insercion']:>5.1f}   {r['reintentos_api']}")


def guardar(salida, filas, resumen, config):
    os.makedirs(salida, exist_ok=True)
    for nombre, datos in (("benchmark_corridas.csv", filas), ("benchmark_resumen.csv", resumen)):
        if datos:
            with open(os.path.join(salida, nombre), "w", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, fieldnames=list(datos[0].keys()))
                w.writeheader()
                w.writerows(datos)
    with open(os.path.join(salida, "benchmark.json"), "w", encoding="utf-8") as f:
        json.dump({"config": config, "resumen": resumen, "corridas": filas}, f, ensure_ascii=False, indent=2)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", default="http://127.0.0.1:8000")
    ap.add_argument("--workers", type=int, nargs="+", default=[1, 2, 4, 8])
    ap.add_argument("--reps", type=int, default=3)
    ap.add_argument("--cap", type=int, default=3000, help="max_chars_por_url (iguala el tamano de todas las URLs)")
    ap.add_argument("--cooldown", type=float, default=70.0, help="segundos de pausa entre corridas (vaciar la ventana de tokens/min)")
    ap.add_argument("--batch", type=int, default=16)
    ap.add_argument("--chunk-size", type=int, default=1000)
    ap.add_argument("--overlap", type=int, default=200)
    ap.add_argument("--urls", nargs="+", default=URLS_DEFECTO)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--dry", action="store_true", help="solo red + chunking (no gasta cuota ni escribe en Supabase)")
    ap.add_argument("--salida", default="resultados")
    a = ap.parse_args(argv)

    ps = sorted(set(a.workers))
    config = {k: v for k, v in vars(a).items() if k != "urls"} | {"n_urls": len(a.urls), "fecha": datetime.now().isoformat(timespec="seconds")}
    n_corridas = len(ps) * a.reps
    print(f"Plan: {len(a.urls)} URLs, cap={a.cap} chars, workers={ps}, {a.reps} repeticiones = {n_corridas} corridas "
          f"({'DRY' if a.dry else 'REAL'}); pausa {a.cooldown:.0f}s -> ~{n_corridas * (a.cooldown + 20) / 60:.0f} min. Ctrl+C para cancelar (5 s)...")
    time.sleep(5)

    rng = random.Random(a.seed)
    filas = []
    for rep in range(1, a.reps + 1):
        orden = ps[:]
        rng.shuffle(orden)
        for p in orden:
            for intento in (1, 2):
                coleccion = f"bench_p{p}_r{rep}_{datetime.now():%H%M%S}"
                payload = {"coleccion_id": coleccion, "urls": a.urls, "chunk_size": a.chunk_size,
                           "chunk_overlap": a.overlap, "concurrency_workers": p, "batch_size": a.batch,
                           "max_chars_por_url": a.cap, "dry_run": a.dry}
                try:
                    d = llamar_api(a.api, payload)
                except Exception as e:
                    print(f"  [rep {rep} p={p} intento {intento}] ERROR de API: {e}")
                    fila = None
                    break
                finally:
                    if not a.dry:
                        try:
                            limpiar_coleccion(coleccion)
                        except Exception as e:
                            print(f"  [!] no se pudo limpiar {coleccion}: {e}")
                fila = fila_de_respuesta(rep, p, intento, coleccion, d, len(a.urls), a.dry)
                marca = "ok" if fila["limpia"] else ("CONTAMINADA (429)" if fila["valida"] else "INVALIDA (URLs fallidas)")
                print(f"  rep {rep} p={p} intento {intento}: T={fila['T_s']:.2f}s  urls_ok={fila['urls_ok']}/{len(a.urls)}  "
                      f"chunks={fila['chunks']}  429={fila['reintentos_api']}  -> {marca}")
                filas.append(fila)
                if fila["limpia"]:
                    break
                time.sleep(a.cooldown * 1.5)   # reintento con pausa mayor
            time.sleep(a.cooldown)

    resumen = resumir(filas, ps)
    imprimir_resumen(resumen)
    guardar(a.salida, filas, resumen, config)
    print(f"\nResultados guardados en {a.salida}/ (benchmark_corridas.csv, benchmark_resumen.csv, benchmark.json)")


if __name__ == "__main__":
    main()
