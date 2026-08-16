"""
MODELO DIXON-COLES + BACKTEST COMPARATIVO
Football Predictor Pro (Beta)
==========================================================
Compara 3 modelos sobre exactamente los mismos partidos:

  1. BASELINE_ACTUAL  -> la fórmula de dashboard.py (0.5/0.2/0.3)
  2. BASELINE_MEJOR   -> la mejor combinación de pesos hallada (0.4/0.1/0.5)
  3. DIXON_COLES      -> modelo con fuerza de ataque/defensa por equipo,
                          localía estimada de los datos, decaimiento temporal
                          y corrección de empates (tau)

Uso: colocar en la raíz del proyecto (junto a backtesting_engine.py) y correr:
    python modelo_dixon_coles.py

Salidas:
  - comparacion_modelos.txt   -> métricas de los 3 modelos, global y por liga
  - dc_detalle.csv            -> predicciones partido a partido del modelo DC

NOTA: Dixon-Coles reajusta sus parámetros periódicamente (cada REAJUSTE_CADA
partidos por liga) usando SOLO partidos anteriores. Nunca ve el futuro.
Por eso tarda más que el backtest simple: esperá varios minutos.
"""

import os
import sys
import math
import numpy as np
import pandas as pd
from scipy.stats import poisson
from scipy.optimize import minimize

try:
    from backtesting_engine import (
        cargar_partidos,
        construir_mapa_equipo_liga,
        liga_de_equipo,
        promedio,
        N_TEMPORADA,
        N_RACHA,
        N_LOCALIA,
        N_LIGA_GOLES,
        MIN_PARTIDOS_EQUIPO,
        MIN_PARTIDOS_LIGA,
    )
except ImportError:
    print("❌ Falta backtesting_engine.py en esta carpeta.")
    sys.exit(1)


# =========================================================
# CONFIGURACIÓN
# =========================================================
XI_DECAY = 0.0045          # decaimiento temporal (por día). ~0.0045 => vida media ~5 meses
REAJUSTE_CADA = 30         # cada cuántos partidos de una liga se reajustan los parámetros
MIN_PARTIDOS_AJUSTE = 60   # mínimo de partidos en una liga para poder ajustar Dixon-Coles
MAX_HISTORIA_AJUSTE = 600  # cuántos partidos previos usar como máximo para el ajuste
MAX_GOLES = 8              # tope de goles en la matriz de probabilidades

OUT_TXT = "comparacion_modelos.txt"
OUT_CSV = "dc_detalle.csv"


# =========================================================
# DIXON-COLES
# =========================================================
def tau(x, y, lambda_, mu, rho):
    """Corrección de Dixon-Coles para marcadores bajos (0-0, 1-0, 0-1, 1-1)."""
    if x == 0 and y == 0:
        return 1.0 - lambda_ * mu * rho
    elif x == 0 and y == 1:
        return 1.0 + lambda_ * rho
    elif x == 1 and y == 0:
        return 1.0 + mu * rho
    elif x == 1 and y == 1:
        return 1.0 - rho
    return 1.0


def log_likelihood_dc(params, equipos_idx, datos, pesos):
    """Log-verosimilitud negativa (la minimizamos)."""
    n = len(equipos_idx)
    ataque = params[:n]
    defensa = params[n:2 * n]
    home_adv = params[2 * n]
    rho = params[2 * n + 1]

    total = 0.0
    for (i_l, i_v, gl, gv), w in zip(datos, pesos):
        lam = math.exp(ataque[i_l] - defensa[i_v] + home_adv)
        mu = math.exp(ataque[i_v] - defensa[i_l])
        lam = min(max(lam, 1e-6), 12.0)
        mu = min(max(mu, 1e-6), 12.0)

        t = tau(gl, gv, lam, mu, rho)
        if t <= 0:
            t = 1e-9

        ll = (math.log(t)
              + gl * math.log(lam) - lam - math.lgamma(gl + 1)
              + gv * math.log(mu) - mu - math.lgamma(gv + 1))
        total += w * ll

    return -total


def ajustar_dixon_coles(historial):
    """
    historial: lista de dicts {local, visita, gl, gv, dias_atras}
    Devuelve (ataque, defensa, home_adv, rho) o None si no se pudo ajustar.
    """
    equipos = sorted({h["local"] for h in historial} | {h["visita"] for h in historial})
    if len(equipos) < 4:
        return None
    idx = {e: i for i, e in enumerate(equipos)}
    n = len(equipos)

    datos = [(idx[h["local"]], idx[h["visita"]], int(h["gl"]), int(h["gv"])) for h in historial]
    pesos = [math.exp(-XI_DECAY * h["dias_atras"]) for h in historial]

    x0 = np.concatenate([
        np.zeros(n),          # ataque
        np.zeros(n),          # defensa
        np.array([0.25]),     # ventaja de localía inicial
        np.array([-0.05]),    # rho inicial
    ])

    # restricción: la media de los ataques = 0 (evita soluciones infinitas equivalentes)
    cons = [{"type": "eq", "fun": lambda p, n=n: np.sum(p[:n])}]
    bounds = [(-3, 3)] * n + [(-3, 3)] * n + [(-1, 1.5)] + [(-0.4, 0.4)]

    try:
        res = minimize(
            log_likelihood_dc,
            x0,
            args=(idx, datos, pesos),
            method="SLSQP",
            bounds=bounds,
            constraints=cons,
            options={"maxiter": 120, "ftol": 1e-4},
        )
        if not res.success and not np.all(np.isfinite(res.x)):
            return None
        p = res.x
        return {
            "idx": idx,
            "ataque": p[:n],
            "defensa": p[n:2 * n],
            "home_adv": p[2 * n],
            "rho": p[2 * n + 1],
        }
    except Exception:
        return None


def predecir_dc(modelo, local, visita):
    """Devuelve (prob_L, prob_E, prob_V) o None si algún equipo no está en el modelo."""
    idx = modelo["idx"]
    if local not in idx or visita not in idx:
        return None
    il, iv = idx[local], idx[visita]

    lam = math.exp(modelo["ataque"][il] - modelo["defensa"][iv] + modelo["home_adv"])
    mu = math.exp(modelo["ataque"][iv] - modelo["defensa"][il])
    lam = min(max(lam, 0.05), 8.0)
    mu = min(max(mu, 0.05), 8.0)
    rho = modelo["rho"]

    pl = pe = pv = 0.0
    for i in range(MAX_GOLES):
        pi = poisson.pmf(i, lam)
        for j in range(MAX_GOLES):
            p = pi * poisson.pmf(j, mu) * tau(i, j, lam, mu, rho)
            if p < 0:
                p = 0.0
            if i > j:
                pl += p
            elif i == j:
                pe += p
            else:
                pv += p
    tot = pl + pe + pv
    if tot <= 0:
        return None
    return pl / tot, pe / tot, pv / tot


# =========================================================
# BASELINE (fórmula actual, con pesos parametrizables)
# =========================================================
def predecir_baseline(prior_l, prior_v, goles_liga_prev, w_temp, w_racha, w_loc):
    avg_liga = promedio(goles_liga_prev[-N_LIGA_GOLES:]) or 2.5
    if avg_liga == 0:
        avg_liga = 2.5

    temp_l, temp_v = prior_l[-N_TEMPORADA:], prior_v[-N_TEMPORADA:]
    gf_l = promedio([m["gf"] for m in temp_l])
    gc_l = promedio([m["gc"] for m in temp_l])
    gf_v = promedio([m["gf"] for m in temp_v])
    gc_v = promedio([m["gc"] for m in temp_v])

    r_l, r_v = prior_l[-N_RACHA:], prior_v[-N_RACHA:]
    rgf_l = promedio([m["gf"] for m in r_l])
    rgc_l = promedio([m["gc"] for m in r_l])
    rgf_v = promedio([m["gf"] for m in r_v])
    rgc_v = promedio([m["gc"] for m in r_v])

    home_l = [m for m in prior_l[-50:] if m["local"]][-N_LOCALIA:]
    away_v = [m for m in prior_v[-50:] if not m["local"]][-N_LOCALIA:]
    hgf_l = promedio([m["gf"] for m in home_l]) if home_l else gf_l
    hgc_l = promedio([m["gc"] for m in home_l]) if home_l else gc_l
    agf_v = promedio([m["gf"] for m in away_v]) if away_v else gf_v
    agc_v = promedio([m["gc"] for m in away_v]) if away_v else gc_v

    gf_l_f = gf_l * w_temp + rgf_l * w_racha + hgf_l * w_loc
    gc_l_f = gc_l * w_temp + rgc_l * w_racha + hgc_l * w_loc
    gf_v_f = gf_v * w_temp + rgf_v * w_racha + agf_v * w_loc
    gc_v_f = gc_v * w_temp + rgc_v * w_racha + agc_v * w_loc

    xg_l = max((gf_l_f * gc_v_f) / avg_liga, 0.05)
    xg_v = max((gf_v_f * gc_l_f) / avg_liga, 0.05)

    pl = pe = pv = 0.0
    for i in range(7):
        pi = poisson.pmf(i, xg_l)
        for j in range(7):
            p = pi * poisson.pmf(j, xg_v)
            if i > j:
                pl += p
            elif i == j:
                pe += p
            else:
                pv += p
    tot = pl + pe + pv
    if tot <= 0:
        return None
    return pl / tot, pe / tot, pv / tot


# =========================================================
# MÉTRICAS
# =========================================================
def metricas(pl, pe, pv, real):
    yl, ye, yv = (1, 0, 0) if real == "L" else (0, 1, 0) if real == "E" else (0, 0, 1)
    brier = (pl - yl) ** 2 + (pe - ye) ** 2 + (pv - yv) ** 2
    predicho = max([("L", pl), ("E", pe), ("V", pv)], key=lambda x: x[1])[0]
    probs = {"L": pl, "E": pe, "V": pv}
    logloss = -math.log(max(probs[real], 1e-12))
    return predicho, brier, logloss, max(pl, pe, pv) * 100


# =========================================================
# MOTOR PRINCIPAL
# =========================================================
def main():
    print("Cargando partidos...")
    df = cargar_partidos()

    print("Etiquetando ligas...")
    mapa = construir_mapa_equipo_liga()
    cache = {}

    def liga_de(nombre):
        if nombre not in cache:
            cache[nombre] = liga_de_equipo(nombre, mapa)
        return cache[nombre]

    partidos = []
    for row in df.itertuples(index=False):
        liga = liga_de(row.equipo_local) or liga_de(row.equipo_visita)
        partidos.append({
            "fecha": row.fecha_parseada,
            "local": row.equipo_local,
            "visita": row.equipo_visita,
            "gl": int(row.goles_local),
            "gv": int(row.goles_visita),
            "liga": liga,
        })
    print(f"Partidos listos: {len(partidos)}")

    team_history = {}
    liga_goles_history = {}
    liga_partidos = {}       # liga -> lista de partidos previos (para ajustar DC)
    modelos_dc = {}          # liga -> modelo ajustado
    contador_liga = {}       # liga -> cuántos partidos van desde el último reajuste

    filas = []
    ajustes_hechos = 0

    print("Corriendo backtest comparativo (esto tarda varios minutos)...")
    for n_p, p in enumerate(partidos, 1):
        if n_p % 2000 == 0:
            print(f"  ... {n_p}/{len(partidos)} partidos procesados ({ajustes_hechos} ajustes DC)")

        local, visita, gl, gv, liga = p["local"], p["visita"], p["gl"], p["gv"], p["liga"]
        prior_l = team_history.get(local, [])
        prior_v = team_history.get(visita, [])
        goles_liga_prev = liga_goles_history.get(liga, []) if liga else []

        puede_baseline = (
            liga is not None
            and len(prior_l) >= MIN_PARTIDOS_EQUIPO
            and len(prior_v) >= MIN_PARTIDOS_EQUIPO
            and len(goles_liga_prev) >= MIN_PARTIDOS_LIGA
        )

        if puede_baseline:
            real = "L" if gl > gv else ("E" if gl == gv else "V")
            fila = {"liga": liga, "fecha": p["fecha"], "local": local, "visita": visita, "real": real}

            # --- Modelo 1: baseline actual ---
            r1 = predecir_baseline(prior_l, prior_v, goles_liga_prev, 0.5, 0.2, 0.3)
            if r1:
                pred, br, ll, pmax = metricas(*r1, real)
                fila.update({"m1_pred": pred, "m1_brier": br, "m1_logloss": ll, "m1_pmax": pmax,
                             "m1_acierto": int(pred == real)})

            # --- Modelo 2: mejor combinación de pesos ---
            r2 = predecir_baseline(prior_l, prior_v, goles_liga_prev, 0.4, 0.1, 0.5)
            if r2:
                pred, br, ll, pmax = metricas(*r2, real)
                fila.update({"m2_pred": pred, "m2_brier": br, "m2_logloss": ll, "m2_pmax": pmax,
                             "m2_acierto": int(pred == real)})

            # --- Modelo 3: Dixon-Coles ---
            modelo = modelos_dc.get(liga)
            if modelo:
                r3 = predecir_dc(modelo, local, visita)
                if r3:
                    pred, br, ll, pmax = metricas(*r3, real)
                    fila.update({"m3_pred": pred, "m3_brier": br, "m3_logloss": ll, "m3_pmax": pmax,
                                 "m3_acierto": int(pred == real),
                                 "m3_pL": r3[0] * 100, "m3_pE": r3[1] * 100, "m3_pV": r3[2] * 100})

            filas.append(fila)

        # --- actualizar historiales DESPUÉS de predecir ---
        team_history.setdefault(local, []).append({"gf": gl, "gc": gv, "local": True})
        team_history.setdefault(visita, []).append({"gf": gv, "gc": gl, "local": False})
        if liga:
            liga_goles_history.setdefault(liga, []).append(gl + gv)
            liga_partidos.setdefault(liga, []).append(p)
            contador_liga[liga] = contador_liga.get(liga, 0) + 1

            # reajustar Dixon-Coles periódicamente
            hist = liga_partidos[liga]
            if len(hist) >= MIN_PARTIDOS_AJUSTE and contador_liga[liga] >= REAJUSTE_CADA:
                contador_liga[liga] = 0
                ventana = hist[-MAX_HISTORIA_AJUSTE:]
                fecha_ref = p["fecha"]
                historial_fmt = []
                for h in ventana:
                    dias = (fecha_ref - h["fecha"]).days
                    historial_fmt.append({
                        "local": h["local"], "visita": h["visita"],
                        "gl": h["gl"], "gv": h["gv"],
                        "dias_atras": max(dias, 0),
                    })
                m = ajustar_dixon_coles(historial_fmt)
                if m:
                    modelos_dc[liga] = m
                    ajustes_hechos += 1

    df_r = pd.DataFrame(filas)
    df_r.to_csv(OUT_CSV, index=False, encoding="utf-8")
    print(f"✅ Detalle guardado: {OUT_CSV}")

    # =====================================================
    # REPORTE
    # =====================================================
    lineas = []
    lineas.append("=" * 78)
    lineas.append("COMPARACIÓN DE MODELOS")
    lineas.append("=" * 78)
    lineas.append("M1 = Baseline actual (0.5/0.2/0.3)")
    lineas.append("M2 = Mejor combinación de pesos (0.4/0.1/0.5)")
    lineas.append("M3 = Dixon-Coles (ataque/defensa por equipo + localía estimada + rho + decaimiento)")
    lineas.append("")

    # comparación justa: solo partidos donde los 3 modelos predijeron
    df_comun = df_r.dropna(subset=["m1_brier", "m2_brier", "m3_brier"])
    lineas.append(f"Partidos evaluados por M1/M2: {df_r['m1_brier'].notna().sum()}")
    lineas.append(f"Partidos evaluados por M3 (Dixon-Coles): {df_r['m3_brier'].notna().sum()}")
    lineas.append(f"Partidos comunes a los 3 (base de comparación justa): {len(df_comun)}")
    lineas.append("")

    if df_comun.empty:
        lineas.append("⚠️ No hubo partidos comunes. Revisar el ajuste de Dixon-Coles.")
    else:
        lineas.append("-" * 78)
        lineas.append("MÉTRICAS GLOBALES (sobre los partidos comunes)")
        lineas.append("-" * 78)
        lineas.append(f"{'Modelo':<10}{'Acierto%':<12}{'Brier':<11}{'LogLoss':<11}{'N(60%+)':<11}{'Acierto 60%+':<12}")
        for m, nombre in [("m1", "M1"), ("m2", "M2"), ("m3", "M3 (DC)")]:
            acc = df_comun[f"{m}_acierto"].mean() * 100
            br = df_comun[f"{m}_brier"].mean()
            ll = df_comun[f"{m}_logloss"].mean()
            alta = df_comun[df_comun[f"{m}_pmax"] >= 60]
            n_alta = len(alta)
            acc_alta = alta[f"{m}_acierto"].mean() * 100 if n_alta > 0 else 0.0
            lineas.append(f"{nombre:<10}{acc:<12.2f}{br:<11.4f}{ll:<11.4f}{n_alta:<11}{acc_alta:<12.2f}")

        lineas.append("")
        lineas.append("Referencia: apostar SIEMPRE al local en estos mismos partidos ->")
        acc_local = (df_comun["real"] == "L").mean() * 100
        lineas.append(f"  Acierto = {acc_local:.2f}%   (este es el número a superar)")

        lineas.append("")
        lineas.append("-" * 78)
        lineas.append("CALIBRACIÓN DE DIXON-COLES")
        lineas.append("-" * 78)
        bins = [0, 40, 50, 60, 70, 80, 90, 100]
        etiquetas = ["<40%", "40-50%", "50-60%", "60-70%", "70-80%", "80-90%", "90-100%"]
        df_comun = df_comun.copy()
        df_comun["bucket"] = pd.cut(df_comun["m3_pmax"], bins=bins, labels=etiquetas, include_lowest=True)
        calib = df_comun.groupby("bucket", observed=True).agg(
            n=("m3_acierto", "size"), acierto=("m3_acierto", "mean"))
        for idx, r in calib.iterrows():
            lineas.append(f"  {idx:<10} N={int(r['n']):<7} Acierto real={r['acierto']*100:.1f}%")

        lineas.append("")
        lineas.append("-" * 78)
        lineas.append("POR LIGA (Brier: más bajo = mejor)")
        lineas.append("-" * 78)
        lineas.append(f"{'Liga':<20}{'N':<8}{'M1 Brier':<12}{'M2 Brier':<12}{'M3 Brier':<12}{'M3 Acierto%':<12}{'Gana':<8}")
        por_liga = df_comun.groupby("liga").agg(
            n=("m1_brier", "size"),
            m1=("m1_brier", "mean"),
            m2=("m2_brier", "mean"),
            m3=("m3_brier", "mean"),
            m3_acc=("m3_acierto", "mean"),
        ).sort_values("n", ascending=False)
        for liga, r in por_liga.iterrows():
            if r["n"] < 100:
                continue
            mejor = min([("M1", r["m1"]), ("M2", r["m2"]), ("M3", r["m3"])], key=lambda x: x[1])[0]
            lineas.append(
                f"{str(liga):<20}{int(r['n']):<8}{r['m1']:<12.4f}{r['m2']:<12.4f}"
                f"{r['m3']:<12.4f}{r['m3_acc']*100:<12.1f}{mejor:<8}"
            )

    with open(OUT_TXT, "w", encoding="utf-8") as f:
        f.write("\n".join(lineas))
    print(f"✅ Reporte guardado: {OUT_TXT}")
    print("\nSubí comparacion_modelos.txt para analizarlo.")


if __name__ == "__main__":
    main()
