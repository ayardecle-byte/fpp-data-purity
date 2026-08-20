"""
MOTOR V2 - Football Predictor Pro
==================================
Módulo que usa el dashboard para predecir.
Requiere haber corrido antes: python entrenar_motor.py

Funciones principales:
    predecir(liga_display, local, visita)   -> dict con probabilidades calibradas
    calidad_liga(liga_display)              -> si el modelo tiene ventaja ahí
    prob_combinada([probs])                 -> probabilidad real de una combinada
    calcular_ev(prob_pct, cuota)            -> valor esperado en %
    cuota_minima(prob_pct)                  -> cuota desde la cual hay valor
    kelly_fraccionado(prob, cuota, bank)    -> cuánto invertir
"""

import os
import math
import pickle
import unicodedata
import numpy as np
from scipy.stats import poisson

RUTA_PKL = os.path.join("modelos", "motor_v2.pkl")
MAX_GOLES = 8

# =========================================================
# CALIDAD DEL MODELO POR LIGA (backtest sobre ~19.000 partidos)
# =========================================================
CALIDAD_LIGAS = {
    # ============================================================
    # Validacion de agosto 2026 · 39.588 predicciones · test 19.794
    # Base de ~48.000 partidos
    # ============================================================

    # ===== ALTA: ventaja confirmada (skill > 0.010) =====
    "estonia":        {"nivel": "ALTA",  "skill": 0.0251, "n_test": 246},
    "portugal":       {"nivel": "ALTA",  "skill": 0.0240, "n_test": 442},
    "czechrepublic":  {"nivel": "ALTA",  "skill": 0.0240, "n_test": 382},
    "netherlands":    {"nivel": "ALTA",  "skill": 0.0202, "n_test": 442},
    "turkey":         {"nivel": "ALTA",  "skill": 0.0193, "n_test": 482},
    "italy":          {"nivel": "ALTA",  "skill": 0.0182, "n_test": 1060},
    "ukraine":        {"nivel": "ALTA",  "skill": 0.0178, "n_test": 357},
    "china":          {"nivel": "ALTA",  "skill": 0.0170, "n_test": 581},
    "mexico":         {"nivel": "ALTA",  "skill": 0.0162, "n_test": 161},
    "champions":      {"nivel": "ALTA",  "skill": 0.0132, "n_test": 959},
    "finland":        {"nivel": "ALTA",  "skill": 0.0122, "n_test": 449},
    "spain":          {"nivel": "ALTA",  "skill": 0.0115, "n_test": 1216},
    "norway":         {"nivel": "ALTA",  "skill": 0.0114, "n_test": 907},

    # ===== MEDIA: ventaja moderada (0.003 - 0.010) =====
    "netherlands2":   {"nivel": "MEDIA", "skill": 0.0100, "n_test": 525},
    "scotland":       {"nivel": "MEDIA", "skill": 0.0096, "n_test": 387},
    "sweden":         {"nivel": "MEDIA", "skill": 0.0093, "n_test": 496},
    "europa":         {"nivel": "MEDIA", "skill": 0.0087, "n_test": 448},
    "bolivia":        {"nivel": "MEDIA", "skill": 0.0081, "n_test": 251},
    "france":         {"nivel": "MEDIA", "skill": 0.0076, "n_test": 469},
    "england":        {"nivel": "MEDIA", "skill": 0.0072, "n_test": 576},
    "iceland":        {"nivel": "MEDIA", "skill": 0.0071, "n_test": 356},
    "germany2":       {"nivel": "MEDIA", "skill": 0.0067, "n_test": 428},
    "portugal2":      {"nivel": "MEDIA", "skill": 0.0054, "n_test": 424},
    "mls":            {"nivel": "MEDIA", "skill": 0.0048, "n_test": 1016},
    "japan":          {"nivel": "MEDIA", "skill": 0.0037, "n_test": 756},
    "spain2":         {"nivel": "MEDIA", "skill": 0.0036, "n_test": 641},
    "england3":       {"nivel": "MEDIA", "skill": 0.0036, "n_test": 170},
    "poland":         {"nivel": "MEDIA", "skill": 0.0035, "n_test": 444},
    "france2":        {"nivel": "MEDIA", "skill": 0.0030, "n_test": 455},

    # ===== BAJA: ventaja marginal (0 - 0.003) =====
    "argentina":      {"nivel": "BAJA",  "skill": 0.0026, "n_test": 1516},
    "brazil":         {"nivel": "BAJA",  "skill": 0.0025, "n_test": 1055},
    "switzerland":    {"nivel": "BAJA",  "skill": 0.0020, "n_test": 339},
    "southkorea":     {"nivel": "BAJA",  "skill": 0.0007, "n_test": 229},
    "serie_b_brasil": {"nivel": "BAJA",  "skill": 0.0005, "n_test": 211},

    # ===== NULA: sin ventaja demostrable =====
    "italy2":         {"nivel": "NULA",  "skill": -0.0003, "n_test": 288},
    "england2":       {"nivel": "NULA",  "skill": -0.0015, "n_test": 174},

    # ===== Sin validar: muestra insuficiente en el test =====
    "denmark":          {"nivel": "SIN_VALIDAR", "skill": 0.0, "n_test": 0},
    "austria":          {"nivel": "SIN_VALIDAR", "skill": 0.0, "n_test": 0},
    "belgium":          {"nivel": "SIN_VALIDAR", "skill": 0.0, "n_test": 0},
    "greece":           {"nivel": "SIN_VALIDAR", "skill": 0.0, "n_test": 0},
    "libertadores":     {"nivel": "SIN_VALIDAR", "skill": 0.0, "n_test": 0},
    "sudamericana":     {"nivel": "SIN_VALIDAR", "skill": 0.0, "n_test": 0},
    "primera_nacional": {"nivel": "SIN_VALIDAR", "skill": 0.0, "n_test": 0},
    "scotland2":        {"nivel": "SIN_VALIDAR", "skill": 0.0, "n_test": 0},
    "iceland2":         {"nivel": "SIN_VALIDAR", "skill": 0.0, "n_test": 0},
}

# Muestra minima para considerar solida una validacion.
# Brasil parecia ALTA con 210 partidos y resulto BAJA con 1.055.
N_TEST_CONFIABLE = 300

# Mercados SIN ventaja estadística demostrada (backtest: skill <= 0)
MERCADOS_SIN_VENTAJA = {
    "Over05", "Over15", "Over25", "Under25", "Under35",
    "BTTS_Y", "BTTS_N",
}

# Nombre visible en el dashboard -> nombre interno (archivo json)
MAPA_LIGAS = {
    "Inglaterra - Premier League": "england",
    "España - La Liga": "spain",
    "Italia - Serie A": "italy",
    "Francia - Ligue 1": "france",
    "Argentina": "argentina",
    "Argentina - Primera Nacional": "primera_nacional",
    "Brasil": "brazil",
    "Brasil - Serie B": "serie_b_brasil",
    "Champions League": "champions",
    "Libertadores": "libertadores",
    "Copa Sudamericana": "sudamericana",
    "Europa League": "europa",
    "Noruega - Eliteserien": "norway",
    "Estados Unidos - MLS": "mls",
    "México - Liga MX": "mexico",
    "Bolivia - Div. Profesional": "bolivia",
    "Estonia - Meistriliiga": "estonia",
    "Islandia - 2da División": "iceland2",
    "Dinamarca - Superliga": "denmark",
    "China - Super League": "china",
    "Suecia - Allsvenskan": "sweden",
    "Islandia - 1ra División": "iceland",

    # --- Ligas agregadas tras la validación de agosto 2026 ---
    "Portugal - Primeira Liga": "portugal",
    "Países Bajos - Eredivisie": "netherlands",
    "Escocia - Premiership": "scotland",
    "Chequia - Fortuna Liga": "czechrepublic",
    "Turquía - Süper Lig": "turkey",
    "Ucrania - Premier League": "ukraine",
    "Finlandia - Veikkausliiga": "finland",
    "Japón - J1 League": "japan",
    "Suiza - Super League": "switzerland",
    "Polonia - Ekstraklasa": "poland",
    "Grecia - Super League": "greece",
    "Corea del Sur - K League": "southkorea",
    "Bélgica - Pro League": "belgium",
    "Austria - Bundesliga": "austria",
    "Alemania - 2. Bundesliga": "germany2",
    "España - La Liga 2": "spain2",
    "Italia - Serie B": "italy2",
    "Francia - Ligue 2": "france2",
    "Países Bajos - Eerste Divisie": "netherlands2",
    "Portugal - Liga 2": "portugal2",
    "Inglaterra - Championship": "england2",
    "Inglaterra - League One": "england3",
    "Escocia - Championship": "scotland2",
}

_PAQUETE = None


def _cargar():
    global _PAQUETE
    if _PAQUETE is None:
        if not os.path.exists(RUTA_PKL):
            return None
        try:
            with open(RUTA_PKL, "rb") as f:
                _PAQUETE = pickle.load(f)
        except Exception:
            return None
    return _PAQUETE


def motor_disponible():
    return _cargar() is not None


def ligas_disponibles():
    paq = _cargar()
    if not paq:
        return []
    return sorted(paq.get("modelos", {}).keys())


def _norm(t):
    if not isinstance(t, str):
        return ""
    n = unicodedata.normalize("NFKD", t)
    return "".join(c for c in n if not unicodedata.combining(c)).lower().strip()


def _buscar_equipo(nombre, equipos):
    if nombre in equipos:
        return nombre
    n = _norm(nombre)
    for e in equipos:
        if _norm(e) == n:
            return e
    for e in equipos:
        ne = _norm(e)
        if len(n) > 4 and len(ne) > 4 and (n in ne or ne in n):
            return e
    return None


def _tau(x, y, lam, mu, rho):
    if x == 0 and y == 0:
        return 1.0 - lam * mu * rho
    if x == 0 and y == 1:
        return 1.0 + lam * rho
    if x == 1 and y == 0:
        return 1.0 + mu * rho
    if x == 1 and y == 1:
        return 1.0 - rho
    return 1.0


def calidad_liga(liga_display):
    """Nivel de confianza del modelo en esa liga, segun el backtest."""
    interna = MAPA_LIGAS.get(liga_display, liga_display)
    info = CALIDAD_LIGAS.get(interna)

    if not info:
        return {
            "nivel": "DESCONOCIDA", "skill": 0.0, "n_test": 0, "provisional": True,
            "mensaje": "Liga sin validar. No sabemos si el modelo tiene ventaja aca.",
            "apostar": False,
        }

    n = info["nivel"]
    sk = info["skill"]
    nt = info.get("n_test", 0)
    provisional = (nt < N_TEST_CONFIABLE) and n in ("ALTA", "MEDIA")

    if n == "SIN_VALIDAR":
        msg = "Liga sin validar (muestra insuficiente en el backtest). Solo analisis."
    elif n == "NULA":
        msg = f"Sin ventaja demostrable (skill {sk:+.4f} sobre {nt} partidos). No recomendado para apostar."
    elif n == "BAJA":
        msg = f"Ventaja marginal (skill {sk:+.4f}). Probablemente no supere el margen de la casa."
    elif n == "MEDIA":
        msg = f"Ventaja moderada (skill {sk:+.4f} sobre {nt} partidos)."
    else:
        msg = f"Ventaja confirmada (skill {sk:+.4f} sobre {nt} partidos)."

    if provisional:
        msg += f" ATENCION: validada con solo {nt} partidos; tratala como provisional y usa stake reducido."

    return {
        "nivel": n,
        "skill": sk,
        "n_test": nt,
        "provisional": provisional,
        "mensaje": msg,
        "apostar": n in ("ALTA", "MEDIA"),
    }


def predecir(liga_display, local, visita):
    """Probabilidades CALIBRADAS en %. None si no se puede predecir."""
    paq = _cargar()
    if not paq:
        return None

    interna = MAPA_LIGAS.get(liga_display, liga_display)
    modelo = paq["modelos"].get(interna)
    if not modelo:
        return None

    equipos = list(modelo["idx"].keys())
    el = _buscar_equipo(local, equipos)
    ev = _buscar_equipo(visita, equipos)
    if not el or not ev or el == ev:
        return None

    il, iv = modelo["idx"][el], modelo["idx"][ev]
    lam = min(max(math.exp(modelo["ataque"][il] - modelo["defensa"][iv] + modelo["home_adv"]), 0.05), 8.0)
    mu = min(max(math.exp(modelo["ataque"][iv] - modelo["defensa"][il]), 0.05), 8.0)
    rho = modelo["rho"]

    M = np.zeros((MAX_GOLES, MAX_GOLES))
    for i in range(MAX_GOLES):
        pi = poisson.pmf(i, lam)
        for j in range(MAX_GOLES):
            M[i, j] = max(pi * poisson.pmf(j, mu) * _tau(i, j, lam, mu, rho), 0.0)
    s = M.sum()
    if s > 0:
        M /= s

    ar = np.arange(MAX_GOLES)
    dif = ar[:, None] - ar[None, :]
    tot = ar[:, None] + ar[None, :]
    ambos = (ar[:, None] > 0) & (ar[None, :] > 0)

    crudo = {
        "1": float(M[dif > 0].sum()),
        "X": float(M[dif == 0].sum()),
        "2": float(M[dif < 0].sum()),
    }
    crudo["1X"] = crudo["1"] + crudo["X"]
    crudo["X2"] = crudo["X"] + crudo["2"]
    crudo["12"] = crudo["1"] + crudo["2"]

    cal = paq.get("calibradores", {})
    out = {}
    for mkt, val in crudo.items():
        if mkt in cal:
            try:
                out[mkt] = float(cal[mkt].predict([val])[0]) * 100
            except Exception:
                out[mkt] = val * 100
        else:
            out[mkt] = val * 100

    s3 = out["1"] + out["X"] + out["2"]
    if s3 > 0:
        f = 100.0 / s3
        out["1"] *= f
        out["X"] *= f
        out["2"] *= f
        out["1X"] = out["1"] + out["X"]
        out["X2"] = out["X"] + out["2"]
        out["12"] = out["1"] + out["2"]

    out["xG_L"] = lam
    out["xG_V"] = mu

    # Mercados de goles (NO validados, solo referencia)
    out["Over05"] = float(M[tot > 0.5].sum()) * 100
    out["Over15"] = float(M[tot > 1.5].sum()) * 100
    out["Over25"] = float(M[tot > 2.5].sum()) * 100
    out["Under25"] = float(M[tot < 2.5].sum()) * 100
    out["Under35"] = float(M[tot < 3.5].sum()) * 100
    out["BTTS_Y"] = float(M[ambos].sum()) * 100
    out["BTTS_N"] = float(M[~ambos].sum()) * 100

    # Goles exactos y totales por equipo (marginales de la matriz)
    marg_L = M.sum(axis=1)
    marg_V = M.sum(axis=0)

    def _goles_exactos(marg):
        d = {str(k): float(marg[k]) * 100 for k in range(3)}
        d["3+"] = float(marg[3:].sum()) * 100
        return d

    out["Goles_L"] = _goles_exactos(marg_L)
    out["Goles_V"] = _goles_exactos(marg_V)

    def _team_totals(marg):
        p0, p1, p2, p3 = (float(marg[0]), float(marg[1]), float(marg[2]), float(marg[3]))
        u15 = p0 + p1
        u25 = u15 + p2
        u35 = u25 + p3
        return {
            "O05": (1.0 - p0) * 100,
            "O15": (1.0 - u15) * 100,
            "U15": u15 * 100,
            "O25": (1.0 - u25) * 100,
            "U25": u25 * 100,
            "U35": u35 * 100,
        }

    out["Team_Totals_L"] = _team_totals(marg_L)
    out["Team_Totals_V"] = _team_totals(marg_V)

    # Hándicap asiático. Ojo: ±0.5 equivale a 1/1X/2/X2 (validados).
    # Las líneas anchas (±1.5, ±2.5) NO están validadas.
    out["Handicap_L"] = {
        "-1.5": float(M[dif >= 2].sum()) * 100,
        "+1.5": float(M[dif >= -1].sum()) * 100,
        "-2.5": float(M[dif >= 3].sum()) * 100,
        "+2.5": float(M[dif >= -2].sum()) * 100,
    }
    out["Handicap_V"] = {
        "-1.5": float(M[dif <= -2].sum()) * 100,
        "+1.5": float(M[dif <= 1].sum()) * 100,
        "-2.5": float(M[dif <= -3].sum()) * 100,
        "+2.5": float(M[dif <= 2].sum()) * 100,
    }

    marcadores = []
    for i in range(min(6, MAX_GOLES)):
        for j in range(min(6, MAX_GOLES)):
            marcadores.append((f"{i} - {j}", float(M[i, j]) * 100))
    marcadores.sort(key=lambda x: x[1], reverse=True)
    out["Top_Marcadores"] = marcadores[:3]

    out["_equipo_local_modelo"] = el
    out["_equipo_visita_modelo"] = ev
    out["_calidad"] = calidad_liga(liga_display)
    out["_motor"] = "v2"
    return out


def revisar_discrepancia(prob_modelo, cuota_casa):
    """
    Compara la probabilidad del modelo contra la que implica la cuota.

    Cuando la diferencia es muy grande, lo mas probable NO es haber
    encontrado una oportunidad, sino que el modelo esta equivocado:
    un equipo recien ascendido sin historial, datos mezclados entre
    divisiones, o una alineacion que el modelo no conoce.

    Las casas mueven millones y ajustan precios con informacion que
    el modelo no tiene.

    Devuelve None si todo esta en rango normal, o un dict con el aviso.
    """
    if not prob_modelo or not cuota_casa or cuota_casa <= 1:
        return None

    prob_casa = 1.0 / cuota_casa
    diferencia = (prob_modelo - prob_casa) * 100  # en puntos porcentuales

    if diferencia < 12:
        return None

    if diferencia >= 25:
        return {
            "nivel": "GRAVE",
            "diferencia": diferencia,
            "prob_casa": prob_casa * 100,
            "mensaje": (
                f"El modelo dice {prob_modelo*100:.1f}% y la cuota implica "
                f"{prob_casa*100:.1f}%: una diferencia de {diferencia:.0f} puntos. "
                "Una brecha asi casi nunca es una oportunidad real. "
                "Revisa si algun equipo ascendio hace poco, si la liga mezcla "
                "divisiones, o si hay bajas importantes. NO apostar."
            ),
        }

    return {
        "nivel": "REVISAR",
        "diferencia": diferencia,
        "prob_casa": prob_casa * 100,
        "mensaje": (
            f"El modelo dice {prob_modelo*100:.1f}% y la cuota implica "
            f"{prob_casa*100:.1f}% ({diferencia:.0f} puntos de diferencia). "
            "Es mas de lo habitual. Verifica el contexto del partido "
            "antes de apostar."
        ),
    }


def info_muestra(liga_display, local=None, visita=None):
    """
    Devuelve cuántos datos respaldan la predicción de esa liga/equipos.
    Sirve para avisar cuando el modelo está estimando con poca información.
    """
    paq = _cargar()
    if not paq:
        return None

    interna = MAPA_LIGAS.get(liga_display, liga_display)
    info = paq.get("info_ligas", {}).get(interna)
    if not info:
        return None

    out = {
        "n_partidos": info.get("n_partidos", 0),
        "n_equipos": len(info.get("equipos", [])),
        "ultima_fecha": info.get("ultima_fecha", "?"),
    }

    # Partidos por equipo: si es bajo, las fuerzas están mal estimadas
    if out["n_equipos"] > 0:
        out["partidos_por_equipo"] = round(out["n_partidos"] * 2 / out["n_equipos"], 1)
    else:
        out["partidos_por_equipo"] = 0

    if out["n_partidos"] < 150:
        out["nivel_datos"] = "MUY_POCOS"
    elif out["n_partidos"] < 400:
        out["nivel_datos"] = "POCOS"
    else:
        out["nivel_datos"] = "SUFICIENTES"

    return out


def mercado_validado(nombre_mercado):
    return nombre_mercado not in MERCADOS_SIN_VENTAJA


def prob_combinada(lista_probabilidades_pct):
    """Probabilidad real de que acierten TODAS las selecciones."""
    if not lista_probabilidades_pct:
        return 0.0
    p = 1.0
    usados = 0
    for x in lista_probabilidades_pct:
        try:
            v = float(x) / 100.0
        except (TypeError, ValueError):
            continue
        p *= max(min(v, 1.0), 0.0)
        usados += 1
    return p * 100 if usados else 0.0


def calcular_ev(prob_pct, cuota):
    try:
        cuota = float(cuota)
        prob_pct = float(prob_pct)
    except (TypeError, ValueError):
        return None
    if cuota <= 1.0:
        return None
    return (((prob_pct / 100.0) * cuota) - 1.0) * 100


def cuota_minima(prob_pct):
    """Cuota a partir de la cual la apuesta tiene EV positivo."""
    try:
        prob_pct = float(prob_pct)
    except (TypeError, ValueError):
        return None
    if prob_pct <= 0:
        return None
    return 100.0 / prob_pct


def kelly_fraccionado(prob_pct, cuota, bankroll, fraccion=0.25, tope_pct=5.0):
    """Kelly fraccionado (1/4 por defecto) con tope del 5% del bankroll."""
    try:
        cuota = float(cuota)
        prob_pct = float(prob_pct)
        bankroll = float(bankroll)
    except (TypeError, ValueError):
        return 0.0
    if cuota <= 1.0 or bankroll <= 0:
        return 0.0
    p = max(min(prob_pct / 100.0, 1.0), 0.0)
    b = cuota - 1.0
    q = 1.0 - p
    if b <= 0:
        return 0.0
    k = (b * p - q) / b
    if k <= 0:
        return 0.0
    monto = bankroll * k * fraccion
    tope = bankroll * (tope_pct / 100.0)
    return round(min(monto, tope), 2)
