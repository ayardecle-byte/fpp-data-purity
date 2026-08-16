"""
VALIDACIÓN DE LIGAS Y MERCADOS
===============================
Backtest cronológico (sin fuga de información futura) que responde:

  1. ¿En qué ligas el modelo tiene ventaja real?
  2. ¿Qué mercados sirven en cada liga?

Mercados evaluados:
  - 1X2:                1, X, 2
  - Doble oportunidad:  1X, X2, 12
  - Goles:              Over 1.5 / 2.5 / 3.5, BTTS
  - Medio tiempo (HT):  HT1, HTX, HT2, HT 1X, HT X2,
                        Over 0.5 HT, Over 1.5 HT

Método: para cada liga se ajustan DOS modelos Dixon-Coles en paralelo,
uno sobre el resultado final y otro sobre el marcador de medio tiempo.
Las probabilidades se calibran (isotónica) entrenando en la primera
mitad cronológica y midiendo en la segunda.

Métrica clave: SKILL = Brier(trivial) - Brier(modelo)
  Trivial = decir siempre la frecuencia histórica de esa liga.
  Skill > 0  -> el modelo aporta información.
  Skill <= 0 -> el modelo no sirve para ese mercado.

Uso:
    python validar_ligas.py

Salidas:
    validacion_resultado.txt   -> reporte legible
    validacion_detalle.csv     -> predicciones partido a partido
"""

import os
import sys
import math
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
        MAX_GOLES,
    )
except ImportError as e:
    print(f"❌ Falta un archivo requerido: {e}")
    sys.exit(1)

OUT_TXT = "validacion_resultado.txt"
OUT_CSV = "validacion_detalle.csv"

REAJUSTE_CADA = 40          # cada cuántos partidos se reajusta el modelo
MIN_PARTIDOS_AJUSTE = 60
MAX_HISTORIA = 500
MIN_TEST_LIGA = 120         # mínimo de partidos en test para reportar una liga

MERCADOS_FT = ["1", "X", "2", "1X", "X2", "12", "O15", "O25", "O35", "BTTS"]
MERCADOS_HT = ["HT1", "HTX", "HT2", "HT_1X", "HT_X2", "HT_O05", "HT_O15"]
TODOS = MERCADOS_FT + MERCADOS_HT

GRUPOS = {
    "1X2": ["1", "X", "2"],
    "Doble oportunidad": ["1X", "X2", "12"],
    "Goles": ["O15", "O25", "O35", "BTTS"],
    "Medio tiempo": MERCADOS_HT,
}


def matriz_dc(modelo, local, visita):
    idx = modelo["idx"]
    if local not in idx or visita not in idx:
        return None
    il, iv = idx[local], idx[visita]
    lam = min(max(math.exp(modelo["ataque"][il] - modelo["defensa"][iv] + modelo["home_adv"]), 0.03), 8.0)
    mu = min(max(math.exp(modelo["ataque"][iv] - modelo["defensa"][il]), 0.03), 8.0)
    rho = modelo["rho"]

    M = np.zeros((MAX_GOLES, MAX_GOLES))
    for i in range(MAX_GOLES):
        pi = poisson.pmf(i, lam)
        for j in range(MAX_GOLES):
            M[i, j] = max(pi * poisson.pmf(j, mu) * tau(i, j, lam, mu, rho), 0.0)
    s = M.sum()
    return M / s if s > 0 else None


def mercados_ft(M):
    ar = np.arange(MAX_GOLES)
    dif = ar[:, None] - ar[None, :]
    tot = ar[:, None] + ar[None, :]
    ambos = (ar[:, None] > 0) & (ar[None, :] > 0)
    p1 = float(M[dif > 0].sum())
    px = float(M[dif == 0].sum())
    p2 = float(M[dif < 0].sum())
    return {
        "1": p1, "X": px, "2": p2,
        "1X": p1 + px, "X2": px + p2, "12": p1 + p2,
        "O15": float(M[tot > 1.5].sum()),
        "O25": float(M[tot > 2.5].sum()),
        "O35": float(M[tot > 3.5].sum()),
        "BTTS": float(M[ambos].sum()),
    }


def mercados_ht(M):
    ar = np.arange(MAX_GOLES)
    dif = ar[:, None] - ar[None, :]
    tot = ar[:, None] + ar[None, :]
    p1 = float(M[dif > 0].sum())
    px = float(M[dif == 0].sum())
    p2 = float(M[dif < 0].sum())
    return {
        "HT1": p1, "HTX": px, "HT2": p2,
        "HT_1X": p1 + px, "HT_X2": px + p2,
        "HT_O05": float(M[tot > 0.5].sum()),
        "HT_O15": float(M[tot > 1.5].sum()),
    }


def reales_ft(gl, gv):
    tot = gl + gv
    return {
        "1": int(gl > gv), "X": int(gl == gv), "2": int(gl < gv),
        "1X": int(gl >= gv), "X2": int(gl <= gv), "12": int(gl != gv),
        "O15": int(tot > 1.5), "O25": int(tot > 2.5), "O35": int(tot > 3.5),
        "BTTS": int(gl > 0 and gv > 0),
    }


def reales_ht(hl, hv):
    tot = hl + hv
    return {
        "HT1": int(hl > hv), "HTX": int(hl == hv), "HT2": int(hl < hv),
        "HT_1X": int(hl >= hv), "HT_X2": int(hl <= hv),
        "HT_O05": int(tot > 0.5), "HT_O15": int(tot > 1.5),
    }


def brier(p, y):
    p = np.asarray(p, dtype=float)
    y = np.asarray(y, dtype=float)
    m = ~(np.isnan(p) | np.isnan(y))
    if m.sum() == 0:
        return np.nan
    return float(np.mean((p[m] - y[m]) ** 2))


def main():
    print("[1/4] Cargando partidos...")
    df = cargar_partidos()
    tiene_liga = "liga" in df.columns

    print("[2/4] Etiquetando ligas...")
    mapa = construir_mapa_equipo_liga()
    cache = {}

    def por_nombre(n):
        if n not in cache:
            cache[n] = liga_de_equipo(n, mapa)
        return cache[n]

    partidos = []
    for row in df.itertuples(index=False):
        liga = None
        if tiene_liga:
            val = getattr(row, "liga", None)
            if isinstance(val, str) and val.strip():
                liga = val.strip()
        if not liga:
            liga = por_nombre(row.equipo_local) or por_nombre(row.equipo_visita)
        if not liga:
            continue

        hl = getattr(row, "ht_goles_local", None)
        hv = getattr(row, "ht_goles_visita", None)
        try:
            hl = int(hl) if pd.notna(hl) else None
            hv = int(hv) if pd.notna(hv) else None
        except (TypeError, ValueError):
            hl, hv = None, None

        partidos.append({
            "fecha": row.fecha_parseada,
            "liga": liga,
            "local": row.equipo_local,
            "visita": row.equipo_visita,
            "gl": int(row.goles_local),
            "gv": int(row.goles_visita),
            "hl": hl, "hv": hv,
        })

    print(f"      Partidos con liga: {len(partidos)}")
    con_ht = sum(1 for p in partidos if p["hl"] is not None)
    print(f"      Con medio tiempo: {con_ht}")

    print("[3/4] Backtest cronológico (esto tarda; puede superar los 30 min)...")
    hist_ft, hist_ht = {}, {}
    modelo_ft, modelo_ht = {}, {}
    cont_ft, cont_ht = {}, {}
    filas = []

    for n, p in enumerate(partidos, 1):
        if n % 5000 == 0:
            print(f"      ... {n}/{len(partidos)}")

        liga = p["liga"]
        fila = {"fecha": p["fecha"], "liga": liga,
                "local": p["local"], "visita": p["visita"]}
        util = False

        # ---- Resultado final ----
        m = modelo_ft.get(liga)
        if m:
            M = matriz_dc(m, p["local"], p["visita"])
            if M is not None:
                probs = mercados_ft(M)
                reales = reales_ft(p["gl"], p["gv"])
                for k in MERCADOS_FT:
                    fila[f"p_{k}"] = probs[k]
                    fila[f"r_{k}"] = reales[k]
                util = True

        # ---- Medio tiempo ----
        mh = modelo_ht.get(liga)
        if mh and p["hl"] is not None:
            Mh = matriz_dc(mh, p["local"], p["visita"])
            if Mh is not None:
                probs = mercados_ht(Mh)
                reales = reales_ht(p["hl"], p["hv"])
                for k in MERCADOS_HT:
                    fila[f"p_{k}"] = probs[k]
                    fila[f"r_{k}"] = reales[k]
                util = True

        if util:
            filas.append(fila)

        # ---- Actualizar historial DESPUÉS de predecir ----
        hist_ft.setdefault(liga, []).append(p)
        cont_ft[liga] = cont_ft.get(liga, 0) + 1
        if len(hist_ft[liga]) >= MIN_PARTIDOS_AJUSTE and cont_ft[liga] >= REAJUSTE_CADA:
            cont_ft[liga] = 0
            ventana = hist_ft[liga][-MAX_HISTORIA:]
            ref = p["fecha"]
            fmt = [{"local": x["local"], "visita": x["visita"],
                    "gl": x["gl"], "gv": x["gv"],
                    "dias_atras": max((ref - x["fecha"]).days, 0)} for x in ventana]
            nuevo = ajustar_dixon_coles(fmt)
            if nuevo:
                modelo_ft[liga] = nuevo

        if p["hl"] is not None:
            hist_ht.setdefault(liga, []).append(p)
            cont_ht[liga] = cont_ht.get(liga, 0) + 1
            if len(hist_ht[liga]) >= MIN_PARTIDOS_AJUSTE and cont_ht[liga] >= REAJUSTE_CADA:
                cont_ht[liga] = 0
                ventana = hist_ht[liga][-MAX_HISTORIA:]
                ref = p["fecha"]
                fmt = [{"local": x["local"], "visita": x["visita"],
                        "gl": x["hl"], "gv": x["hv"],
                        "dias_atras": max((ref - x["fecha"]).days, 0)} for x in ventana]
                nuevo = ajustar_dixon_coles(fmt)
                if nuevo:
                    modelo_ht[liga] = nuevo

    if not filas:
        print("❌ No se generaron predicciones.")
        return

    res = pd.DataFrame(filas).sort_values("fecha").reset_index(drop=True)
    res.to_csv(OUT_CSV, index=False, encoding="utf-8")
    print(f"      Predicciones generadas: {len(res)}")

    print("[4/4] Calibrando y midiendo...")
    corte = len(res) // 2
    train, test = res.iloc[:corte], res.iloc[corte:].copy()

    for mkt in TODOS:
        pc, rc = f"p_{mkt}", f"r_{mkt}"
        if pc not in train.columns:
            continue
        sub = train.dropna(subset=[pc, rc])
        if len(sub) < 200 or sub[rc].nunique() < 2:
            continue
        iso = IsotonicRegression(y_min=0, y_max=1, out_of_bounds="clip")
        iso.fit(sub[pc].values, sub[rc].values)
        mask = test[pc].notna()
        test.loc[mask, f"cal_{mkt}"] = iso.predict(test.loc[mask, pc].values)

    # ================= REPORTE =================
    L = []
    L.append("=" * 84)
    L.append("VALIDACIÓN DE LIGAS Y MERCADOS")
    L.append("=" * 84)
    L.append(f"Predicciones totales: {len(res)}  |  Train: {len(train)}  Test: {len(test)}")
    L.append("Todo se mide sobre el Test (segunda mitad cronológica), tras calibrar.")
    L.append("")
    L.append("SKILL = Brier(trivial) - Brier(modelo).  Trivial = frecuencia histórica.")
    L.append("  > 0.010  fuerte    0.003-0.010  moderado")
    L.append("  0-0.003  marginal  <= 0  no sirve")
    L.append("")

    # ---- Global por mercado ----
    L.append("-" * 84)
    L.append("RESULTADO GLOBAL POR MERCADO")
    L.append("-" * 84)
    L.append(f"{'Mercado':<12}{'N':<8}{'Base%':<9}{'Brier mod.':<13}{'Brier triv.':<13}{'Skill':<12}{'Veredicto'}")

    resumen_global = {}
    for grupo, mercados in GRUPOS.items():
        L.append(f"\n  [{grupo}]")
        for mkt in mercados:
            cc, rc = f"cal_{mkt}", f"r_{mkt}"
            if cc not in test.columns:
                L.append(f"  {mkt:<12}(sin datos suficientes)")
                continue
            sub = test.dropna(subset=[cc, rc])
            if len(sub) < 100:
                L.append(f"  {mkt:<12}(muestra insuficiente: {len(sub)})")
                continue
            y = sub[rc].values
            base = float(np.mean(y))
            b_mod = brier(sub[cc].values, y)
            b_tri = brier(np.full(len(y), base), y)
            skill = b_tri - b_mod
            resumen_global[mkt] = skill

            if skill > 0.010:
                v = "SÍ (fuerte)"
            elif skill > 0.003:
                v = "Sí (moderado)"
            elif skill > 0:
                v = "Marginal"
            else:
                v = "NO sirve"
            L.append(f"  {mkt:<12}{len(sub):<8}{base*100:<9.1f}{b_mod:<13.4f}{b_tri:<13.4f}{skill:<+12.4f}{v}")

    # ---- Ranking de mercados ----
    L.append("")
    L.append("-" * 84)
    L.append("RANKING DE MERCADOS (mejor a peor)")
    L.append("-" * 84)
    for mkt, sk in sorted(resumen_global.items(), key=lambda x: -x[1]):
        marca = "✅" if sk > 0.010 else ("🟡" if sk > 0.003 else ("·" if sk > 0 else "❌"))
        L.append(f"  {marca} {mkt:<12} skill = {sk:+.4f}")

    # ---- Por liga ----
    L.append("")
    L.append("-" * 84)
    L.append(f"POR LIGA (solo ligas con >= {MIN_TEST_LIGA} partidos en el test)")
    L.append("-" * 84)

    resumen_ligas = []
    for liga, sub_liga in test.groupby("liga"):
        if len(sub_liga) < MIN_TEST_LIGA:
            continue
        skills = {}
        for mkt in TODOS:
            cc, rc = f"cal_{mkt}", f"r_{mkt}"
            if cc not in sub_liga.columns:
                continue
            s = sub_liga.dropna(subset=[cc, rc])
            if len(s) < 80:
                continue
            y = s[rc].values
            base = float(np.mean(y))
            if base in (0.0, 1.0):
                continue
            b_mod = brier(s[cc].values, y)
            b_tri = brier(np.full(len(y), base), y)
            skills[mkt] = b_tri - b_mod
        if not skills:
            continue

        skill_1x2 = np.mean([skills.get(m, 0) for m in ["1", "X", "2"] if m in skills]) if any(m in skills for m in ["1", "X", "2"]) else 0
        mejores = sorted(skills.items(), key=lambda x: -x[1])[:4]
        resumen_ligas.append((liga, len(sub_liga), skill_1x2, mejores))

    for liga, n, sk1x2, mejores in sorted(resumen_ligas, key=lambda x: -x[2]):
        if sk1x2 > 0.010:
            nivel = "🟢 ALTA"
        elif sk1x2 > 0.003:
            nivel = "🟡 MEDIA"
        elif sk1x2 > 0:
            nivel = "🔴 BAJA"
        else:
            nivel = "⛔ NULA"
        L.append(f"\n  {liga.upper():<20} N={n:<6} 1X2 skill={sk1x2:+.4f}  {nivel}")
        L.append("     Mejores mercados en esta liga:")
        for mkt, sk in mejores:
            marca = "✅" if sk > 0.010 else ("🟡" if sk > 0.003 else "·")
            L.append(f"       {marca} {mkt:<10} {sk:+.4f}")

    # ---- Sugerencia de configuración ----
    L.append("")
    L.append("-" * 84)
    L.append("SUGERENCIA PARA CALIDAD_LIGAS en motor_v2.py")
    L.append("-" * 84)
    for liga, n, sk1x2, _ in sorted(resumen_ligas, key=lambda x: -x[2]):
        if sk1x2 > 0.010:
            nivel = "ALTA"
        elif sk1x2 > 0.003:
            nivel = "MEDIA"
        elif sk1x2 > 0:
            nivel = "BAJA"
        else:
            nivel = "NULA"
        L.append(f'    "{liga}": {{"nivel": "{nivel}", "skill": {sk1x2:.4f}, "acierto": 0.0}},')

    with open(OUT_TXT, "w", encoding="utf-8") as f:
        f.write("\n".join(L))

    print(f"\n✅ Reporte: {OUT_TXT}")
    print(f"✅ Detalle: {OUT_CSV}")


if __name__ == "__main__":
    main()
