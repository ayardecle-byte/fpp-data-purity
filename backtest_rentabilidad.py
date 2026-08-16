"""
BACKTEST DE RENTABILIDAD
=========================
Hasta ahora medimos PRECISIÓN (si el modelo acierta más que el azar).
Este script mide RENTABILIDAD: si apostando con el modelo contra cuotas
reales se gana o se pierde dinero.

Son cosas distintas. Un modelo puede acertar más que la referencia y
perder plata igual, porque el margen de la casa (6.46% medido) se come
la ventaja.

Qué hace:
  1. Cruza los partidos de tu base con las cuotas de cierre descargadas.
  2. Recorre la historia en orden cronológico, entrenando el modelo solo
     con lo anterior a cada partido (sin fuga de información futura).
  3. Apuesta donde el modelo ve valor esperado positivo, con Kelly
     fraccionado.
  4. Reporta: yield, curva de bankroll, drawdown máximo y CLV.

Un problema a resolver: los nombres de equipo difieren entre fuentes
("Man United" vs "Manchester Utd"). El script los empareja y reporta
cuántos logró cruzar.

Uso:
    python backtest_rentabilidad.py               -> backtest completo
    python backtest_rentabilidad.py --solo-cruce  -> solo probar el cruce de nombres

Genera: rentabilidad_reporte.txt · rentabilidad_apuestas.csv
"""

import os
import re
import sys
import math
import sqlite3
import unicodedata
from difflib import SequenceMatcher

import numpy as np
import pandas as pd
from scipy.stats import poisson
from sklearn.isotonic import IsotonicRegression

try:
    from modelo_dixon_coles import tau, ajustar_dixon_coles, MAX_GOLES
except ImportError as e:
    print(f"❌ Falta modelo_dixon_coles.py: {e}")
    sys.exit(1)

DB_PATH = "database/football_data.db"
OUT_TXT = "rentabilidad_reporte.txt"
OUT_CSV = "rentabilidad_apuestas.csv"

# --- Parámetros del backtest ---
BANKROLL_INICIAL = 1000.0
KELLY_FRACCION = 0.25       # Kelly a un cuarto
STAKE_MAXIMO = 0.05         # tope del 5% del bankroll
EV_MINIMO = 0.02            # apostar solo si el EV supera el 2%
REAJUSTE_CADA = 40
MIN_PARTIDOS_AJUSTE = 60
MAX_HISTORIA = 500
UMBRAL_NOMBRE = 0.82
MAX_DIFERENCIA_MERCADO = 0.15   # 15 puntos: mas que eso es error del modelo        # similitud mínima para emparejar nombres

# Nivel de cada liga segun la validacion. Se apuesta en todas las que
# tienen cuotas, y despues el reporte separa por nivel: asi comprobamos
# si las ligas "buenas" rinden mejor que las demas.
NIVEL_LIGA = {
    "italy": "ALTA", "spain": "ALTA", "turkey": "ALTA",
    "france": "MEDIA", "england": "MEDIA", "germany2": "MEDIA",
    "scotland": "MEDIA", "spain2": "MEDIA", "france2": "MEDIA",
    "england3": "MEDIA",
    "italy2": "NULA", "england2": "NULA",
    "greece": "SIN_VALIDAR", "belgium": "SIN_VALIDAR",
    "scotland2": "SIN_VALIDAR",
}

SUFIJOS = [
    "fc", "cf", "ac", "sc", "afc", "cd", "ud", "sd", "rc", "as", "ss",
    "us", "aj", "sv", "vfl", "vfb", "fk", "if", "bk", "sk", "ik",
    "club", "de", "futbol", "football",
]


def norm(texto):
    """Normaliza un nombre de equipo para poder compararlo."""
    t = unicodedata.normalize("NFKD", str(texto))
    t = "".join(c for c in t if not unicodedata.combining(c))
    t = t.lower().strip()
    t = t.replace("'", "").replace("`", "")   # Nott'm -> nottm
    t = re.sub(r"[^a-z0-9 ]", " ", t)
    palabras = [p for p in t.split() if p and p not in SUFIJOS]
    return " ".join(palabras)


# football-data.co.uk usa abreviaturas. Estas equivalencias las resuelven.
SINONIMOS = {
    "utd": "united", "man": "manchester", "ath": "athletic",
    "atl": "atletico", "sp": "sporting", "nottm": "nottingham",
    "wolves": "wolverhampton", "west brom": "west bromwich",
    "sheffield weds": "sheffield wednesday", "qpr": "queens park rangers",
    "psg": "paris saint germain", "hertha": "hertha berlin",
    "ein frankfurt": "eintracht frankfurt", "bayern munich": "bayern munchen",
    "vallecano": "rayo vallecano", "betis": "real betis",
    "sociedad": "real sociedad", "espanol": "espanyol",
    "celta": "celta vigo", "la coruna": "deportivo la coruna",
    "gimnastic": "gimnastic tarragona", "inter": "inter milan",
    "milan": "ac milan", "roma": "as roma", "spal": "spal ferrara",
    "st etienne": "saint etienne", "paris sg": "paris saint germain",
    "ajaccio gfco": "gazelec ajaccio", "az alkmaar": "az",
    "for sittard": "fortuna sittard", "waalwijk": "rkc waalwijk",
    "den haag": "ado den haag", "sp lisbon": "sporting lisbon",
    "sp braga": "braga", "pacos ferreira": "pacos de ferreira",
    "st truiden": "sint truiden", "st gilloise": "union saint gilloise",
    "aek": "aek athens", "paok": "paok salonika",
    "qpr": "qp rangers",
    "oud heverlee leuven": "oh leuven",
    "st gilloise": "royale union sg",
    "beerschot va": "beerschot",
    "waregem": "zulte waregem",
    "sint truiden": "st truiden",
    "cercle brugge": "cercle brugge",
    "club brugge": "club brugge",
    "afc wimbledon": "wimbledon",
    "peterboro": "peterborough",
    "shrewsbury": "shrewsbury town",
    "crewe": "crewe alexandra",
    "wycombe": "wycombe wanderers",
}


def aplicar_sinonimos(texto):
    """Expande abreviaturas conocidas antes de comparar."""
    t = texto
    for corto, largo in SINONIMOS.items():
        if t == corto:
            return largo
    palabras = []
    for p in t.split():
        palabras.append(SINONIMOS.get(p, p))
    return " ".join(palabras)


def similitud(a, b):
    """
    Compara nombres de equipo tolerando abreviaturas.
    Combina similitud textual con coincidencia palabra por palabra,
    donde una palabra cuenta si es prefijo de la otra
    ("man" cuenta como "manchester").
    """
    a2, b2 = aplicar_sinonimos(a), aplicar_sinonimos(b)
    if a2 == b2:
        return 1.0

    base = SequenceMatcher(None, a2, b2).ratio()

    pa, pb = a2.split(), b2.split()
    if not pa or not pb:
        return base

    coincidencias = 0
    usadas = set()
    for x in pa:
        for i, y in enumerate(pb):
            if i in usadas:
                continue
            if x == y or (len(x) >= 3 and y.startswith(x)) or (len(y) >= 3 and x.startswith(y)):
                coincidencias += 1
                usadas.add(i)
                break

    corto = min(len(pa), len(pb))

    # Si el nombre corto está contenido entero en el largo, es el mismo
    # equipo: "Cardiff" dentro de "Cardiff City", "Luton" en "Luton Town".
    if coincidencias == corto and corto >= 1:
        return 0.90

    por_palabra = coincidencias / max(len(pa), len(pb))
    return max(base, por_palabra * 0.95)


def emparejar_nombres(nombres_db, nombres_odds):
    """
    Empareja los nombres de equipo de las dos fuentes.
    Devuelve: dict {nombre_odds: nombre_db} y lista de no emparejados.
    """
    norm_db = {norm(n): n for n in nombres_db}
    mapa = {}
    sin_par = []

    for n_odds in nombres_odds:
        clave = norm(n_odds)

        # Coincidencia exacta tras normalizar
        if clave in norm_db:
            mapa[n_odds] = norm_db[clave]
            continue

        # Uno contiene al otro (ej: "man united" vs "manchester united")
        candidatos = [(k, v) for k, v in norm_db.items()
                      if clave and (clave in k or k in clave)]
        if len(candidatos) == 1:
            mapa[n_odds] = candidatos[0][1]
            continue

        # Similitud textual
        mejor, mejor_sim = None, 0.0
        for k, v in norm_db.items():
            s = similitud(clave, k)
            if s > mejor_sim:
                mejor, mejor_sim = v, s
        if mejor_sim >= UMBRAL_NOMBRE:
            mapa[n_odds] = mejor
        else:
            sin_par.append((n_odds, mejor, round(mejor_sim, 2)))

    return mapa, sin_par


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


def probs_de_matriz(M):
    ar = np.arange(MAX_GOLES)
    dif = ar[:, None] - ar[None, :]
    p1 = float(M[dif > 0].sum())
    px = float(M[dif == 0].sum())
    p2 = float(M[dif < 0].sum())
    return {"1": p1, "X": px, "2": p2,
            "1X": p1 + px, "X2": px + p2, "12": p1 + p2}


def quitar_margen(c1, cx, c2):
    """
    Convierte las cuotas en probabilidades reales de la casa, quitando
    el margen. Es la mejor estimación disponible de la probabilidad
    verdadera del partido.
    """
    if not all([c1, cx, c2]) or min(c1, cx, c2) <= 1:
        return None
    inv = [1.0 / c1, 1.0 / cx, 1.0 / c2]
    total = sum(inv)
    return {"1": inv[0]/total, "X": inv[1]/total, "2": inv[2]/total}


def main():
    solo_cruce = "--solo-cruce" in sys.argv

    if not os.path.exists(DB_PATH):
        print(f"❌ No se encontró {DB_PATH}")
        return

    print("[1/5] Cargando datos...")
    conn = sqlite3.connect(DB_PATH)
    partidos = pd.read_sql("""
        SELECT fecha, liga, equipo_local, equipo_visita, goles_local, goles_visita
        FROM partidos
        WHERE goles_local IS NOT NULL AND goles_visita IS NOT NULL
    """, conn)
    odds = pd.read_sql("""
        SELECT fecha, liga, equipo_local, equipo_visita,
               goles_local, goles_visita, resultado,
               cierre_1, cierre_X, cierre_2, max_1, max_X, max_2,
               prom_1, prom_X, prom_2
        FROM cuotas_historicas
        WHERE cierre_1 IS NOT NULL
    """, conn)
    conn.close()

    if odds.empty:
        print("❌ No hay cuotas. Corré primero: python descargar_cuotas.py")
        return

    partidos["fecha_dt"] = pd.to_datetime(partidos["fecha"], errors="coerce", format="mixed")
    odds["fecha_dt"] = pd.to_datetime(odds["fecha"], errors="coerce")
    partidos = partidos.dropna(subset=["fecha_dt"])
    odds = odds.dropna(subset=["fecha_dt"])

    print(f"      Partidos en la base: {len(partidos)}")
    print(f"      Partidos con cuotas: {len(odds)}")

    # =====================================================
    # 2. Emparejar nombres de equipo por liga
    # =====================================================
    print("[2/5] Emparejando nombres de equipo entre las dos fuentes...")
    L = []
    L.append("=" * 78)
    L.append("BACKTEST DE RENTABILIDAD")
    L.append("=" * 78)
    L.append("")
    L.append("-" * 78)
    L.append("CRUCE DE NOMBRES ENTRE FUENTES")
    L.append("-" * 78)
    L.append(f"{'Liga':<16}{'Equipos odds':<15}{'Emparejados':<14}{'Sin par':<10}{'%'}")

    mapa_global = {}
    problemas = []

    for liga in sorted(set(odds["liga"]) & set(partidos["liga"])):
        eq_db = set(partidos[partidos["liga"] == liga]["equipo_local"]) | \
                set(partidos[partidos["liga"] == liga]["equipo_visita"])
        eq_od = set(odds[odds["liga"] == liga]["equipo_local"]) | \
                set(odds[odds["liga"] == liga]["equipo_visita"])
        if not eq_db or not eq_od:
            continue

        mapa, sin_par = emparejar_nombres(eq_db, eq_od)
        for k, v in mapa.items():
            mapa_global[(liga, k)] = v

        pct = len(mapa) / len(eq_od) * 100 if eq_od else 0
        L.append(f"{liga:<16}{len(eq_od):<15}{len(mapa):<14}{len(sin_par):<10}{pct:.0f}%")
        for n, cand, sim in sin_par[:6]:
            problemas.append((liga, n, cand, sim))

    if problemas:
        L.append("")
        L.append("Nombres que no se pudieron emparejar (muestra):")
        for liga, n, cand, sim in problemas[:25]:
            L.append(f"    [{liga}] '{n}' → mejor candidato: '{cand}' (similitud {sim})")

    # =====================================================
    # 3. Unir partidos con cuotas
    # =====================================================
    print("[3/5] Uniendo partidos con sus cuotas...")
    indice_odds = {}
    for r in odds.itertuples(index=False):
        loc = mapa_global.get((r.liga, r.equipo_local))
        vis = mapa_global.get((r.liga, r.equipo_visita))
        if not loc or not vis:
            continue
        clave = (r.fecha_dt.date(), norm(loc), norm(vis))
        indice_odds[clave] = r

    emparejados = 0
    partidos = partidos.sort_values("fecha_dt").reset_index(drop=True)
    lista = []
    for r in partidos.itertuples(index=False):
        fila = {
            "fecha": r.fecha_dt, "liga": r.liga,
            "local": r.equipo_local, "visita": r.equipo_visita,
            "gl": int(r.goles_local), "gv": int(r.goles_visita),
            "odds": None,
        }
        # Buscar cuotas en un margen de un día
        for delta in (0, 1, -1):
            clave = (r.fecha_dt.date() + pd.Timedelta(days=delta),
                     norm(r.equipo_local), norm(r.equipo_visita))
            if clave in indice_odds:
                fila["odds"] = indice_odds[clave]
                emparejados += 1
                break
        lista.append(fila)

    L.append("")
    L.append(f"Partidos con cuotas emparejadas: {emparejados} de {len(odds)} disponibles")
    pct_cruce = emparejados / len(odds) * 100 if len(odds) else 0
    L.append(f"Tasa de cruce: {pct_cruce:.1f}%")

    if solo_cruce or emparejados < 500:
        if emparejados < 500:
            L.append("")
            L.append("⚠️ Se emparejaron muy pocos partidos para un backtest confiable.")
            L.append("   Revisá los nombres sin emparejar de arriba.")
        with open(OUT_TXT, "w", encoding="utf-8") as f:
            f.write("\n".join(L))
        print("\n".join(L))
        print(f"\nReporte: {OUT_TXT}")
        return

    # =====================================================
    # 4. Generar predicciones (sin apostar todavía)
    # =====================================================
    print("[4/5] Generando predicciones cronológicas...")
    historial = {}
    modelos = {}
    contador = {}
    registros = []

    for n, p in enumerate(lista, 1):
        if n % 5000 == 0:
            print(f"      ... {n}/{len(lista)}")

        liga = p["liga"]
        o = p["odds"]

        if o is not None and liga in NIVEL_LIGA:
            m = modelos.get(liga)
            if m:
                M = matriz_dc(m, p["local"], p["visita"])
                if M is not None:
                    pr = probs_de_matriz(M)
                    real = "1" if p["gl"] > p["gv"] else ("X" if p["gl"] == p["gv"] else "2")
                    registros.append({
                        "fecha": p["fecha"], "liga": liga,
                        "nivel": NIVEL_LIGA.get(liga, "?"),
                        "partido": f"{p['local']} vs {p['visita']}",
                        "p1": pr["1"], "pX": pr["X"], "p2": pr["2"],
                        "c1": o.cierre_1, "cX": o.cierre_X, "c2": o.cierre_2,
                        "real": real,
                    })

        historial.setdefault(liga, []).append(p)
        contador[liga] = contador.get(liga, 0) + 1
        if len(historial[liga]) >= MIN_PARTIDOS_AJUSTE and contador[liga] >= REAJUSTE_CADA:
            contador[liga] = 0
            ventana = historial[liga][-MAX_HISTORIA:]
            ref = p["fecha"]
            fmt = [{"local": x["local"], "visita": x["visita"],
                    "gl": x["gl"], "gv": x["gv"],
                    "dias_atras": max((ref - x["fecha"]).days, 0)} for x in ventana]
            nuevo_m = ajustar_dixon_coles(fmt)
            if nuevo_m:
                modelos[liga] = nuevo_m

    if len(registros) < 500:
        L.append("")
        L.append(f"Solo se generaron {len(registros)} predicciones. Muestra insuficiente.")
        with open(OUT_TXT, "w", encoding="utf-8") as f:
            f.write("\n".join(L))
        print("\n".join(L[-5:]))
        return

    reg = pd.DataFrame(registros).sort_values("fecha").reset_index(drop=True)
    print(f"      Predicciones generadas: {len(reg)}")

    # =====================================================
    # 5. CALIBRAR y recién después simular apuestas
    # =====================================================
    print("[5/5] Calibrando probabilidades y simulando...")
    corte = len(reg) // 2
    train, test = reg.iloc[:corte].copy(), reg.iloc[corte:].copy()

    # La calibración isotónica corrige la sobreconfianza del modelo crudo.
    # Se entrena SOLO con la primera mitad y se aplica a la segunda.
    calibradores = {}
    for mkt, col in (("1", "p1"), ("X", "pX"), ("2", "p2")):
        y = (train["real"] == mkt).astype(int)
        if y.nunique() < 2:
            continue
        iso = IsotonicRegression(y_min=0.001, y_max=0.999, out_of_bounds="clip")
        iso.fit(train[col].values, y.values)
        calibradores[mkt] = iso

    for mkt, col in (("1", "p1"), ("X", "pX"), ("2", "p2")):
        if mkt in calibradores:
            test[f"cal_{mkt}"] = calibradores[mkt].predict(test[col].values)
        else:
            test[f"cal_{mkt}"] = test[col]

    # Renormalizar para que las tres sumen 1
    suma = test["cal_1"] + test["cal_X"] + test["cal_2"]
    for mkt in ("1", "X", "2"):
        test[f"cal_{mkt}"] = test[f"cal_{mkt}"] / suma

    L.append("")
    L.append("-" * 78)
    L.append("EFECTO DE LA CALIBRACIÓN")
    L.append("-" * 78)
    L.append(f"Predicciones: {len(reg)} · Entrenamiento: {len(train)} · Prueba: {len(test)}")
    for mkt, col in (("1", "p1"), ("X", "pX"), ("2", "p2")):
        real_pct = (test["real"] == mkt).mean() * 100
        crudo = test[col].mean() * 100
        calib = test[f"cal_{mkt}"].mean() * 100
        L.append(f"  Mercado {mkt}: real {real_pct:.1f}% · modelo crudo {crudo:.1f}% · calibrado {calib:.1f}%")
    L.append("")
    L.append("  Si el modelo crudo se aleja mucho del real, estaba sobreconfiado.")
    L.append("  La calibración lo corrige antes de calcular el valor.")

    # --- Simulación de apuestas ---
    apuestas = []
    bankroll = BANKROLL_INICIAL
    pico = bankroll
    drawdown_max = 0.0
    descartadas_discrepancia = 0

    for r in test.itertuples(index=False):
        justas = quitar_margen(r.c1, r.cX, r.c2)
        if not justas:
            continue
        cuotas = {"1": r.c1, "X": r.cX, "2": r.c2}
        probs = {"1": r.cal_1, "X": r.cal_X, "2": r.cal_2}

        mejor = None
        for mkt in ("1", "X", "2"):
            cuota = cuotas[mkt]
            if not cuota or cuota <= 1.01:
                continue
            prob = probs[mkt]
            ev = prob * cuota - 1.0
            if ev <= EV_MINIMO:
                continue
            # Guardia de discrepancia: una diferencia enorme con el mercado
            # casi nunca es una oportunidad, es un error del modelo.
            if (prob - justas[mkt]) > MAX_DIFERENCIA_MERCADO:
                descartadas_discrepancia += 1
                continue
            if mejor is None or ev > mejor["ev"]:
                mejor = {"mercado": mkt, "prob": prob, "cuota": cuota,
                         "ev": ev, "prob_casa": justas[mkt]}

        if not mejor:
            continue

        gano = (mejor["mercado"] == r.real)

        # --- Stake plano: 1 unidad por apuesta (métrica principal) ---
        gan_plano = (mejor["cuota"] - 1) if gano else -1.0

        # --- Kelly fraccionado sobre el bankroll ---
        b = mejor["cuota"] - 1.0
        q = 1.0 - mejor["prob"]
        kelly = (b * mejor["prob"] - q) / b if b > 0 else 0
        fraccion = max(0.0, min(kelly * KELLY_FRACCION, STAKE_MAXIMO))
        stake_k = bankroll * fraccion
        gan_kelly = stake_k * (mejor["cuota"] - 1) if gano else -stake_k
        bankroll = max(bankroll + gan_kelly, 1.0)
        pico = max(pico, bankroll)
        drawdown_max = max(drawdown_max, (pico - bankroll) / pico if pico > 0 else 0)

        apuestas.append({
            "fecha": r.fecha, "liga": r.liga, "nivel": r.nivel,
            "partido": r.partido, "mercado": mejor["mercado"],
            "prob_modelo": mejor["prob"], "prob_casa": mejor["prob_casa"],
            "cuota": mejor["cuota"], "ev": mejor["ev"],
            "resultado": r.real, "gano": gano,
            "gan_plano": gan_plano,
            "stake_kelly": stake_k, "gan_kelly": gan_kelly,
            "bankroll": bankroll,
        })

    if not apuestas:
        L.append("")
        L.append("No se generó ninguna apuesta con los criterios actuales.")
        L.append(f"(EV mínimo {EV_MINIMO*100:.0f}% · diferencia máxima con el mercado "
                 f"{MAX_DIFERENCIA_MERCADO*100:.0f} puntos)")
        L.append("Que el modelo calibrado no encuentre valor contra las cuotas de")
        L.append("cierre es un resultado válido: significa que el mercado ya")
        L.append("incorpora lo que el modelo sabe.")
        with open(OUT_TXT, "w", encoding="utf-8") as f:
            f.write("\n".join(L))
        print("\n".join(L[-10:]))
        return

    df = pd.DataFrame(apuestas)
    df.to_csv(OUT_CSV, index=False, encoding="utf-8")

    n = len(df)
    aciertos = int(df["gano"].sum())
    yield_plano = df["gan_plano"].sum() / n * 100

    L.append("")
    L.append("=" * 78)
    L.append("RESULTADO: ¿EL MODELO GANA DINERO?")
    L.append("=" * 78)
    L.append("Medido sobre la SEGUNDA MITAD cronológica, con probabilidades")
    L.append("calibradas usando solo la primera mitad.")
    L.append("")
    L.append(f"Apuestas: {n} · Aciertos: {aciertos} ({aciertos/n*100:.1f}%)")
    L.append(f"Descartadas por diferencia excesiva con el mercado: {descartadas_discrepancia}")
    L.append("")
    L.append("--- STAKE PLANO (1 unidad por apuesta) — métrica principal ---")
    L.append(f"  Unidades apostadas: {n}")
    L.append(f"  Resultado neto:     {df['gan_plano'].sum():+.2f} unidades")
    L.append(f"  YIELD:              {yield_plano:+.2f}%")
    L.append("")
    L.append("--- KELLY FRACCIONADO (1/4, tope 5%) ---")
    L.append(f"  Bankroll inicial:   {BANKROLL_INICIAL:,.2f}")
    L.append(f"  Bankroll final:     {bankroll:,.2f}  ({(bankroll/BANKROLL_INICIAL-1)*100:+.1f}%)")
    L.append(f"  Drawdown máximo:    {drawdown_max*100:.1f}%")
    L.append("")
    L.append("  El YIELD con stake plano es la medida más limpia: no depende")
    L.append("  del tamaño del bankroll ni del orden de los resultados.")
    L.append("  Los apostadores profesionales rondan entre +1% y +5%.")

    def bloque(titulo, columna, nota=None):
        L.append("")
        L.append("-" * 78)
        L.append(titulo)
        L.append("-" * 78)
        L.append(f"{'Grupo':<18}{'Apuestas':<11}{'Acierto':<11}{'Neto':<12}{'Yield'}")
        for g, sub in df.groupby(columna):
            neto = sub["gan_plano"].sum()
            L.append(f"{str(g):<18}{len(sub):<11}{sub['gano'].mean()*100:<11.1f}"
                     f"{neto:<+12.1f}{neto/len(sub)*100:+.2f}%")
        if nota:
            L.append("")
            L.append(nota)

    bloque("POR NIVEL DE LIGA — ¿rinden más las ligas validadas?", "nivel",
           "  Esta es la prueba de fuego de la validación: si las ligas ALTA no\n"
           "  rinden mejor que las NULA, el sistema de niveles no sirve.")
    bloque("POR LIGA", "liga")

    df["franja_ev"] = pd.cut(df["ev"], [EV_MINIMO, 0.05, 0.10, 0.15, 0.25, 99],
                             labels=["2-5%", "5-10%", "10-15%", "15-25%", "25%+"])
    bloque("POR FRANJA DE VALOR ESPERADO", "franja_ev",
           "  Si el yield CAE cuando sube el EV declarado, el modelo se equivoca\n"
           "  justo donde cree ver más valor.")

    df["anio"] = pd.to_datetime(df["fecha"]).dt.year
    bloque("EVOLUCIÓN POR AÑO", "anio",
           "  Un yield positivo en un solo año puede ser suerte. Importa si se\n"
           "  sostiene en el tiempo.")

    # --- CLV ---
    L.append("")
    L.append("-" * 78)
    L.append("CLV — ¿El modelo ve algo que el mercado no ve?")
    L.append("-" * 78)
    dif = (df["prob_modelo"] - df["prob_casa"]) * 100
    L.append(f"Diferencia media con el mercado: {dif.mean():+.2f} puntos")
    L.append(f"Diferencia mediana:              {dif.median():+.2f} puntos")
    L.append(f"Apuestas donde el modelo daba MÁS probabilidad: "
             f"{(dif > 0).sum()} de {len(df)} ({(dif>0).mean()*100:.0f}%)")
    L.append("")
    L.append("  Un modelo honesto debería ver MENOS probabilidad que el mercado")
    L.append("  parte del tiempo. Si siempre ve más, sigue sobreconfiado.")

    with open(OUT_TXT, "w", encoding="utf-8") as f:
        f.write("\n".join(L))

    print("\n" + "\n".join(L[-45:]))
    print(f"\nReporte: {OUT_TXT}")
    print(f"Detalle: {OUT_CSV}")


if __name__ == "__main__":
    main()
