"""
ENTRENADOR DEL MOTOR V2 - Football Predictor Pro  (v2 corregida)
=================================================================
CORRECCIÓN: antes identificaba la liga de cada partido cruzando nombres
de equipo contra los archivos data_json/. Las ligas nuevas (Polonia,
Japón, Alemania 2, etc.) no tienen esos archivos, así que sus partidos
quedaban sin etiqueta y se descartaban.

Ahora usa la columna 'liga' de la tabla partidos cuando está cargada,
y solo recurre al mapeo por nombres cuando está vacía.

Uso:
    python entrenar_motor.py

Genera: modelos/motor_v2.pkl
"""

import os
import sys
import math
import pickle
import numpy as np
import pandas as pd
from scipy.stats import poisson
from sklearn.isotonic import IsotonicRegression

try:
    from backtesting_engine import (
        cargar_partidos,
        construir_mapa_equipo_liga,
        liga_de_equipo,
    )
    from modelo_dixon_coles import (
        tau,
        ajustar_dixon_coles,
        REAJUSTE_CADA,
        MIN_PARTIDOS_AJUSTE,
        MAX_HISTORIA_AJUSTE,
        MAX_GOLES,
    )
except ImportError as e:
    print(f"❌ Falta un archivo requerido: {e}")
    print("   Necesitás backtesting_engine.py y modelo_dixon_coles.py en esta carpeta.")
    sys.exit(1)

CARPETA_MODELOS = "modelos"
RUTA_PKL = os.path.join(CARPETA_MODELOS, "motor_v2.pkl")
MERCADOS_CAL = ["1", "X", "2", "1X", "X2", "12"]

# Aviso si una liga entrena con muy pocos partidos
MIN_RECOMENDADO = 200


def matriz(lam, mu, rho):
    M = np.zeros((MAX_GOLES, MAX_GOLES))
    for i in range(MAX_GOLES):
        pi = poisson.pmf(i, lam)
        for j in range(MAX_GOLES):
            M[i, j] = max(pi * poisson.pmf(j, mu) * tau(i, j, lam, mu, rho), 0.0)
    s = M.sum()
    return M / s if s > 0 else M


def probs_1x2(modelo, local, visita):
    idx = modelo["idx"]
    if local not in idx or visita not in idx:
        return None
    il, iv = idx[local], idx[visita]
    lam = min(max(math.exp(modelo["ataque"][il] - modelo["defensa"][iv] + modelo["home_adv"]), 0.05), 8.0)
    mu = min(max(math.exp(modelo["ataque"][iv] - modelo["defensa"][il]), 0.05), 8.0)
    M = matriz(lam, mu, modelo["rho"])
    ar = np.arange(MAX_GOLES)
    dif = ar[:, None] - ar[None, :]
    return {
        "1": float(M[dif > 0].sum()),
        "X": float(M[dif == 0].sum()),
        "2": float(M[dif < 0].sum()),
    }


def main():
    os.makedirs(CARPETA_MODELOS, exist_ok=True)

    print("[1/4] Cargando partidos...")
    df = cargar_partidos()

    tiene_col_liga = "liga" in df.columns
    print(f"      Columna 'liga' en la base: {'SÍ' if tiene_col_liga else 'NO'}")

    print("[2/4] Etiquetando ligas...")
    mapa = construir_mapa_equipo_liga()
    cache = {}

    def liga_por_nombre(n):
        if n not in cache:
            cache[n] = liga_de_equipo(n, mapa)
        return cache[n]

    partidos = []
    n_por_columna = 0
    n_por_nombre = 0
    n_sin_liga = 0

    for row in df.itertuples(index=False):
        liga = None

        # 1) Preferir la columna 'liga' de la base de datos
        if tiene_col_liga:
            valor = getattr(row, "liga", None)
            if isinstance(valor, str) and valor.strip():
                liga = valor.strip()
                n_por_columna += 1

        # 2) Si está vacía, deducirla por los nombres de equipo
        if not liga:
            liga = liga_por_nombre(row.equipo_local) or liga_por_nombre(row.equipo_visita)
            if liga:
                n_por_nombre += 1
            else:
                n_sin_liga += 1

        partidos.append({
            "fecha": row.fecha_parseada,
            "local": row.equipo_local,
            "visita": row.equipo_visita,
            "gl": int(row.goles_local),
            "gv": int(row.goles_visita),
            "liga": liga,
        })

    print(f"      Etiquetados por columna 'liga': {n_por_columna}")
    print(f"      Etiquetados por nombre de equipo: {n_por_nombre}")
    print(f"      Sin liga identificada (se descartan): {n_sin_liga}")

    print("[3/4] Backtest interno para aprender la calibración...")
    liga_hist = {}
    modelos_tmp = {}
    contador = {}
    registros = []
    ajustes = 0

    for n, p in enumerate(partidos, 1):
        if n % 5000 == 0:
            print(f"      ... {n}/{len(partidos)} ({ajustes} ajustes)")
        liga = p["liga"]
        m = modelos_tmp.get(liga) if liga else None

        if m:
            pr = probs_1x2(m, p["local"], p["visita"])
            if pr:
                gl, gv = p["gl"], p["gv"]
                real = "1" if gl > gv else ("X" if gl == gv else "2")
                registros.append({
                    "liga": liga,
                    "p1": pr["1"], "pX": pr["X"], "p2": pr["2"],
                    "r1": int(real == "1"), "rX": int(real == "X"), "r2": int(real == "2"),
                })

        if liga:
            liga_hist.setdefault(liga, []).append(p)
            contador[liga] = contador.get(liga, 0) + 1
            h = liga_hist[liga]
            if len(h) >= MIN_PARTIDOS_AJUSTE and contador[liga] >= REAJUSTE_CADA:
                contador[liga] = 0
                ventana = h[-MAX_HISTORIA_AJUSTE:]
                ref = p["fecha"]
                fmt = [{
                    "local": x["local"], "visita": x["visita"],
                    "gl": x["gl"], "gv": x["gv"],
                    "dias_atras": max((ref - x["fecha"]).days, 0),
                } for x in ventana]
                nuevo = ajustar_dixon_coles(fmt)
                if nuevo:
                    modelos_tmp[liga] = nuevo
                    ajustes += 1

    df_reg = pd.DataFrame(registros)
    print(f"      Registros para calibrar: {len(df_reg)}")

    calibradores = {}
    if not df_reg.empty:
        for mkt, col_p, col_r in [("1", "p1", "r1"), ("X", "pX", "rX"), ("2", "p2", "r2")]:
            iso = IsotonicRegression(y_min=0.0, y_max=1.0, out_of_bounds="clip")
            iso.fit(df_reg[col_p].values, df_reg[col_r].values)
            calibradores[mkt] = iso

        df_reg["p1X"] = df_reg["p1"] + df_reg["pX"]
        df_reg["pX2"] = df_reg["pX"] + df_reg["p2"]
        df_reg["p12"] = df_reg["p1"] + df_reg["p2"]
        df_reg["r1X"] = ((df_reg["r1"] + df_reg["rX"]) > 0).astype(int)
        df_reg["rX2"] = ((df_reg["rX"] + df_reg["r2"]) > 0).astype(int)
        df_reg["r12"] = ((df_reg["r1"] + df_reg["r2"]) > 0).astype(int)
        for mkt in ["1X", "X2", "12"]:
            iso = IsotonicRegression(y_min=0.0, y_max=1.0, out_of_bounds="clip")
            iso.fit(df_reg[f"p{mkt}"].values, df_reg[f"r{mkt}"].values)
            calibradores[mkt] = iso

    print("[4/4] Ajustando el modelo FINAL de cada liga (datos más recientes)...")
    modelos_finales = {}
    info_ligas = {}
    avisos = []

    for liga, h in sorted(liga_hist.items(), key=lambda x: -len(x[1])):
        if len(h) < MIN_PARTIDOS_AJUSTE:
            avisos.append(f"      ✗ {liga}: solo {len(h)} partidos (mínimo {MIN_PARTIDOS_AJUSTE})")
            continue
        ventana = h[-MAX_HISTORIA_AJUSTE:]
        ref = max(x["fecha"] for x in ventana)
        fmt = [{
            "local": x["local"], "visita": x["visita"],
            "gl": x["gl"], "gv": x["gv"],
            "dias_atras": max((ref - x["fecha"]).days, 0),
        } for x in ventana]
        m = ajustar_dixon_coles(fmt)
        if m:
            modelos_finales[liga] = m
            info_ligas[liga] = {
                "n_partidos": len(h),
                "equipos": sorted(m["idx"].keys()),
                "ultima_fecha": str(ref.date()) if hasattr(ref, "date") else str(ref),
            }
            marca = "✓" if len(h) >= MIN_RECOMENDADO else "⚠"
            extra = "" if len(h) >= MIN_RECOMENDADO else "  (muestra chica)"
            print(f"      {marca} {liga}: {len(m['idx'])} equipos, {len(h)} partidos{extra}")
        else:
            avisos.append(f"      ✗ {liga}: el ajuste no convergió")

    for a in avisos:
        print(a)

    paquete = {
        "modelos": modelos_finales,
        "calibradores": calibradores,
        "info_ligas": info_ligas,
        "version": "v2",
    }
    with open(RUTA_PKL, "wb") as f:
        pickle.dump(paquete, f)

    print(f"\n✅ Motor entrenado y guardado en {RUTA_PKL}")
    print(f"   Ligas con modelo: {len(modelos_finales)}")
    chicas = [l for l, i in info_ligas.items() if i["n_partidos"] < MIN_RECOMENDADO]
    if chicas:
        print(f"   ⚠️  Con muestra chica (<{MIN_RECOMENDADO}): {', '.join(chicas)}")
        print("      Sus probabilidades son menos confiables.")


if __name__ == "__main__":
    main()
