#!/usr/bin/env python3
"""Genera las figuras del informe a partir de resultados/benchmark.json.

Requiere matplotlib:  sudo apt install -y python3-matplotlib      (o: pip install matplotlib)
Uso:  python3 scripts/graficar.py
"""
import json
import os
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

RUTA = sys.argv[1] if len(sys.argv) > 1 else "resultados"
ETAPAS = [("red", "Red (scraping)", "#4C78A8"), ("chunking", "Chunking (CPU)", "#F58518"),
          ("embeddings", "API Embeddings", "#54A24B"), ("insercion", "Inserción Supabase", "#B279A2")]

with open(os.path.join(RUTA, "benchmark.json"), encoding="utf-8") as f:
    datos = json.load(f)
res = [r for r in datos["resumen"]]
ps = [r["p"] for r in res]
T = [r["T_media_s"] for r in res]
sd = [r["T_desv_s"] for r in res]
S = [r["Speedup"] for r in res]
E = [r["Eficiencia"] for r in res]
fs = [r["f_serial_amdahl"] for r in res if r["f_serial_amdahl"] is not None]
f_hat = sum(fs) / len(fs) if fs else None
n_urls = datos["config"].get("n_urls", "?")

# ---------- Figura 1: tiempo, speedup, eficiencia ----------
fig, ax = plt.subplots(1, 3, figsize=(13, 3.6))
ax[0].bar([str(p) for p in ps], T, yerr=sd, capsize=4, color="#4C78A8")
ax[0].set_title(f"Tiempo total $T_p$ ({n_urls} URLs)"); ax[0].set_xlabel("workers (p)"); ax[0].set_ylabel("segundos")
for i, v in enumerate(T):
    ax[0].text(i, v + sd[i] + max(T) * 0.01, f"{v:.1f}", ha="center", va="bottom", fontsize=8)

pp = list(range(1, max(ps) + 1))
ax[1].plot(pp, pp, "--", color="gray", label="Ideal (lineal)")
if f_hat is not None:
    ax[1].plot(pp, [1 / (f_hat + (1 - f_hat) / x) for x in pp], ":", color="#E45756", label=f"Amdahl (f≈{f_hat:.2f})")
ax[1].plot(ps, S, "o-", color="#4C78A8", label="Medido")
ax[1].set_title("Aceleración $S_p=T_1/T_p$"); ax[1].set_xlabel("workers (p)"); ax[1].set_ylabel("speedup"); ax[1].legend(fontsize=8)
ax[1].set_xticks(ps)

ax[2].bar([str(p) for p in ps], E, color="#54A24B")
ax[2].axhline(1.0, ls="--", color="gray")
ax[2].set_ylim(0, 1.15); ax[2].set_title("Eficiencia $E_p=S_p/p$"); ax[2].set_xlabel("workers (p)")
for i, v in enumerate(E):
    ax[2].text(i, v + 0.02, f"{v:.2f}", ha="center", fontsize=8)
fig.tight_layout()
fig.savefig(os.path.join(RUTA, "fig_speedup_eficiencia.png"), dpi=160)

# ---------- Figura 2: desglose porcentual por etapa ----------
fig, ax = plt.subplots(figsize=(7, 3.6))
base = [0.0] * len(ps)
for clave, nombre, color in ETAPAS:
    vals = [r[f"pct_{clave}"] for r in res]
    ax.bar([str(p) for p in ps], vals, bottom=base, label=nombre, color=color)
    for i, v in enumerate(vals):
        if v >= 4:
            ax.text(i, base[i] + v / 2, f"{v:.0f}%", ha="center", va="center", fontsize=8, color="white")
    base = [b + v for b, v in zip(base, vals)]
ax.set_title("Desglose del tiempo por etapa"); ax.set_xlabel("workers (p)"); ax.set_ylabel("% del tiempo de trabajo")
ax.legend(fontsize=8, loc="center left", bbox_to_anchor=(1.0, 0.5))
fig.tight_layout()
fig.savefig(os.path.join(RUTA, "fig_desglose.png"), dpi=160)
print("Figuras generadas en", RUTA, "-> fig_speedup_eficiencia.png, fig_desglose.png")
