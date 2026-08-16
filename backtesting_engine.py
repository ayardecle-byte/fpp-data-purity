"""
MOTOR DE BACKTESTING v1 - Football Predictor Pro (Beta)
==========================================================
Uso: colocar en la raíz del proyecto (junto a dashboard.py) y correr:
    python backtesting_engine.py

Qué hace:
  1. Carga los partidos históricos de database/football_data.db
  2. Parsea las fechas (soporta los 2 formatos mezclados que detectamos)
  3. Etiqueta cada partido con su liga, cruzando nombres de equipo
     contra los archivos data_json/*.json
  4. Recorre los partidos EN ORDEN CRONOLÓGICO, y para cada uno calcula
     la probabilidad del modelo usando SOLO datos de partidos anteriores
     (nunca usa información del futuro - esto es lo que hace que el
     backtest sea válido y no una trampa)
  5. Compara la probabilidad predicha contra el resultado real
  6. Genera dos archivos de salida:
       - backtest_resultados.csv  (detalle partido por partido)
       - backtest_resumen.txt     (métricas agregadas)

Los pesos del modelo (temporada/racha/localía) están al inicio del
archivo como variables — se pueden cambiar y volver a correr el
script para comparar configuraciones distintas.
"""

import sqlite3
import pandas as pd
import numpy as np
import json
import os
import re
import unicodedata
from scipy.stats import poisson

# =========================================================
# PARÁMETROS DEL MODELO (los mismos que usa dashboard.py hoy)
# Cambiar estos 3 valores y volver a correr para probar otras combinaciones
# =========================================================
W_TEMPORADA = 0.50
W_RACHA = 0.20
W_LOCALIA = 0.30

# Cuántos partidos previos considerar para cada componente
N_TEMPORADA = 30   # ventana de "temporada" (partidos recientes, cualquier condición)
N_RACHA = 5        # ventana de "racha" (forma reciente)
N_LOCALIA = 10      # ventana de partidos como local (para el local) / visita (para la visita)
N_LIGA_GOLES = 200  # ventana para el promedio de goles de la liga

# Mínimos de muestra para confiar en un partido (si no se cumplen, se descarta)
MIN_PARTIDOS_EQUIPO = 5
MIN_PARTIDOS_LIGA = 20

DB_PATH = "database/football_data.db"
JSON_DIR = "data_json"
OUTPUT_CSV = "backtest_resultados.csv"
OUTPUT_SUMMARY = "backtest_resumen.txt"


# =========================================================
# UTILIDADES
# =========================================================
def normalize_text(text):
    if not isinstance(text, str):
        return ""
    nfkd = unicodedata.normalize('NFKD', text)
    return "".join(c for c in nfkd if not unicodedata.combining(c)).lower().strip()


def parsear_fecha(fecha_str):
    """Soporta los 2 formatos detectados en la auditoría: ISO y DD/MM/YYYY"""
    if not fecha_str:
        return pd.NaT
    fecha_str = fecha_str.strip()
    if re.match(r"^\d{4}-\d{2}-\d{2}T", fecha_str):
        try:
            return pd.to_datetime(fecha_str, utc=True)
        except Exception:
            return pd.NaT
    if re.match(r"^\d{2}/\d{2}/\d{4}$", fecha_str):
        try:
            return pd.to_datetime(fecha_str, format="%d/%m/%Y", utc=True)
        except Exception:
            return pd.NaT
    try:
        return pd.to_datetime(fecha_str, utc=True, errors="coerce")
    except Exception:
        return pd.NaT


def construir_mapa_equipo_liga():
    """Lee todos los data_json/*.json y arma un diccionario equipo_normalizado -> liga"""
    mapa = {}
    if not os.path.isdir(JSON_DIR):
        print(f"⚠️ No se encontró la carpeta {JSON_DIR}")
        return mapa
    for archivo in os.listdir(JSON_DIR):
        if not archivo.endswith(".json"):
            continue
        ruta = os.path.join(JSON_DIR, archivo)
        liga_nombre = archivo.replace(".json", "")
        try:
            with open(ruta, "r", encoding="utf-8") as f:
                data = json.load(f)
            for row in data.get("posiciones", []):
                if isinstance(row, list) and len(row) >= 2:
                    equipo = str(row[1]).strip()
                    if equipo:
                        mapa[normalize_text(equipo)] = liga_nombre
        except Exception:
            continue
    return mapa


def liga_de_equipo(nombre_equipo, mapa):
    clave = normalize_text(nombre_equipo)
    if clave in mapa:
        return mapa[clave]
    for k, liga in mapa.items():
        if len(k) > 4 and (k in clave or clave in k):
            return liga
    return None


def promedio(lista_valores):
    return sum(lista_valores) / len(lista_valores) if lista_valores else None


# =========================================================
# CARGA Y PREPARACIÓN
# =========================================================
def cargar_partidos():
    conn = sqlite3.connect(DB_PATH)
    df = pd.read_sql("SELECT * FROM partidos", conn)
    conn.close()

    df["fecha_parseada"] = df["fecha"].apply(parsear_fecha)
    antes = len(df)
    df = df.dropna(subset=["fecha_parseada", "goles_local", "goles_visita"])
    despues = len(df)
    print(f"Partidos cargados: {antes} | Con fecha y goles válidos: {despues}")

    df = df.sort_values("fecha_parseada").reset_index(drop=True)
    return df


# =========================================================
# MOTOR PRINCIPAL (una sola pasada cronológica, sin fuga de datos)
# =========================================================
def correr_backtest(df, mapa_equipo_liga):
    team_history = {}     # equipo -> lista de dicts {fecha, gf, gc, local}
    liga_goles_history = {}  # liga -> lista de goles totales por partido

    resultados = []
    sin_liga = 0
    sin_muestra = 0

    for row in df.itertuples(index=False):
        local = row.equipo_local
        visita = row.equipo_visita
        gl = row.goles_local
        gv = row.goles_visita

        liga_l = liga_de_equipo(local, mapa_equipo_liga)
        liga_v = liga_de_equipo(visita, mapa_equipo_liga)
        liga = liga_l or liga_v  # si al menos uno matchea, asumimos esa liga

        prior_l = team_history.get(local, [])
        prior_v = team_history.get(visita, [])
        goles_liga_prev = liga_goles_history.get(liga, []) if liga else []

        puede_predecir = (
            liga is not None
            and len(prior_l) >= MIN_PARTIDOS_EQUIPO
            and len(prior_v) >= MIN_PARTIDOS_EQUIPO
            and len(goles_liga_prev) >= MIN_PARTIDOS_LIGA
        )

        if not puede_predecir:
            if liga is None:
                sin_liga += 1
            else:
                sin_muestra += 1
        else:
            avg_liga_goles = promedio(goles_liga_prev[-N_LIGA_GOLES:])
            if avg_liga_goles == 0:
                avg_liga_goles = 2.5

            # --- Temporada (ventana reciente, cualquier condición) ---
            temp_l = prior_l[-N_TEMPORADA:]
            temp_v = prior_v[-N_TEMPORADA:]
            gf_l_avg = promedio([m["gf"] for m in temp_l])
            gc_l_avg = promedio([m["gc"] for m in temp_l])
            gf_v_avg = promedio([m["gf"] for m in temp_v])
            gc_v_avg = promedio([m["gc"] for m in temp_v])

            # --- Racha (últimos N_RACHA) ---
            racha_l = prior_l[-N_RACHA:]
            racha_v = prior_v[-N_RACHA:]
            racha_gf_l = promedio([m["gf"] for m in racha_l])
            racha_gc_l = promedio([m["gc"] for m in racha_l])
            racha_gf_v = promedio([m["gf"] for m in racha_v])
            racha_gc_v = promedio([m["gc"] for m in racha_v])

            # --- Localía (solo partidos de local para el local, solo visita para la visita) ---
            home_l = [m for m in prior_l[-50:] if m["local"]][-N_LOCALIA:]
            away_v = [m for m in prior_v[-50:] if not m["local"]][-N_LOCALIA:]
            home_gf_l = promedio([m["gf"] for m in home_l]) if home_l else gf_l_avg
            home_gc_l = promedio([m["gc"] for m in home_l]) if home_l else gc_l_avg
            away_gf_v = promedio([m["gf"] for m in away_v]) if away_v else gf_v_avg
            away_gc_v = promedio([m["gc"] for m in away_v]) if away_v else gc_v_avg

            # --- Combinación ponderada (misma fórmula que dashboard.py) ---
            gf_l_final = gf_l_avg * W_TEMPORADA + racha_gf_l * W_RACHA + home_gf_l * W_LOCALIA
            gc_l_final = gc_l_avg * W_TEMPORADA + racha_gc_l * W_RACHA + home_gc_l * W_LOCALIA
            gf_v_final = gf_v_avg * W_TEMPORADA + racha_gf_v * W_RACHA + away_gf_v * W_LOCALIA
            gc_v_final = gc_v_avg * W_TEMPORADA + racha_gc_v * W_RACHA + away_gc_v * W_LOCALIA

            xg_local = (gf_l_final * gc_v_final) / avg_liga_goles
            xg_visita = (gf_v_final * gc_l_final) / avg_liga_goles
            xg_local = max(xg_local, 0.05)
            xg_visita = max(xg_visita, 0.05)

            # --- Poisson 1X2 ---
            prob_l, prob_e, prob_v = 0.0, 0.0, 0.0
            for i in range(7):
                for j in range(7):
                    p = poisson.pmf(i, xg_local) * poisson.pmf(j, xg_visita)
                    if i > j:
                        prob_l += p
                    elif i == j:
                        prob_e += p
                    else:
                        prob_v += p
            total = prob_l + prob_e + prob_v
            if total > 0:
                prob_l, prob_e, prob_v = prob_l / total, prob_e / total, prob_v / total

            # --- Resultado real ---
            if gl > gv:
                real = "L"
            elif gl == gv:
                real = "E"
            else:
                real = "V"

            predicho = max([("L", prob_l), ("E", prob_e), ("V", prob_v)], key=lambda x: x[1])[0]
            acierto = 1 if predicho == real else 0

            y_l, y_e, y_v = (1, 0, 0) if real == "L" else (0, 1, 0) if real == "E" else (0, 0, 1)
            brier = (prob_l - y_l) ** 2 + (prob_e - y_e) ** 2 + (prob_v - y_v) ** 2

            resultados.append({
                "fecha": row.fecha_parseada,
                "liga": liga,
                "local": local,
                "visita": visita,
                "goles_local": gl,
                "goles_visita": gv,
                "prob_L": round(prob_l * 100, 1),
                "prob_E": round(prob_e * 100, 1),
                "prob_V": round(prob_v * 100, 1),
                "predicho": predicho,
                "real": real,
                "acierto": acierto,
                "brier": round(brier, 4),
                "prob_predicho": round(max(prob_l, prob_e, prob_v) * 100, 1),
            })

        # --- Actualizar historiales DESPUÉS de predecir (evita fuga de datos) ---
        team_history.setdefault(local, []).append({"fecha": row.fecha_parseada, "gf": gl, "gc": gv, "local": True})
        team_history.setdefault(visita, []).append({"fecha": row.fecha_parseada, "gf": gv, "gc": gl, "local": False})
        if liga:
            liga_goles_history.setdefault(liga, []).append(gl + gv)

    return pd.DataFrame(resultados), sin_liga, sin_muestra


# =========================================================
# REPORTE
# =========================================================
def generar_reporte(df_res, sin_liga, sin_muestra, total_partidos):
    lineas = []
    lineas.append("=" * 70)
    lineas.append("RESUMEN DE BACKTESTING")
    lineas.append("=" * 70)
    lineas.append(f"Pesos usados: temporada={W_TEMPORADA} | racha={W_RACHA} | localía={W_LOCALIA}")
    lineas.append(f"\nTotal de partidos históricos válidos: {total_partidos}")
    lineas.append(f"Partidos descartados por liga no identificada: {sin_liga}")
    lineas.append(f"Partidos descartados por muestra insuficiente: {sin_muestra}")
    lineas.append(f"Partidos evaluados por el modelo: {len(df_res)}")

    if df_res.empty:
        lineas.append("\n⚠️ No hubo suficientes partidos evaluables. Revisa la cobertura de ligas.")
    else:
        acierto_global = df_res["acierto"].mean() * 100
        brier_global = df_res["brier"].mean()
        lineas.append(f"\nAcierto global (1X2): {acierto_global:.1f}%")
        lineas.append(f"Brier Score global (más bajo = mejor; referencia típica en fútbol ~0.60-0.65): {brier_global:.4f}")

        lineas.append("\n" + "-" * 70)
        lineas.append("CALIBRACIÓN (probabilidad del resultado predicho vs. acierto real)")
        lineas.append("-" * 70)
        bins = [0, 40, 50, 60, 70, 80, 90, 100]
        etiquetas = ["<40%", "40-50%", "50-60%", "60-70%", "70-80%", "80-90%", "90-100%"]
        df_res["bucket"] = pd.cut(df_res["prob_predicho"], bins=bins, labels=etiquetas, include_lowest=True)
        tabla_calib = df_res.groupby("bucket", observed=True).agg(
            n=("acierto", "size"),
            acierto_real=("acierto", "mean")
        )
        for idx, r in tabla_calib.iterrows():
            lineas.append(f"  {idx:<10} N={int(r['n']):<6} Acierto real={r['acierto_real']*100:.1f}%")

        lineas.append("\n" + "-" * 70)
        lineas.append("RENDIMIENTO POR LIGA (top 20 por volumen)")
        lineas.append("-" * 70)
        tabla_liga = df_res.groupby("liga").agg(
            n=("acierto", "size"),
            acierto=("acierto", "mean"),
            brier=("brier", "mean")
        ).sort_values("n", ascending=False).head(20)
        for idx, r in tabla_liga.iterrows():
            lineas.append(f"  {idx:<25} N={int(r['n']):<6} Acierto={r['acierto']*100:.1f}%  Brier={r['brier']:.4f}")

    with open(OUTPUT_SUMMARY, "w", encoding="utf-8") as f:
        f.write("\n".join(lineas))
    print(f"✅ Resumen guardado en {OUTPUT_SUMMARY}")


def main():
    if not os.path.exists(DB_PATH):
        print(f"❌ No se encontró {DB_PATH}")
        return

    print("Cargando partidos...")
    df = cargar_partidos()

    print("Construyendo mapa equipo -> liga desde data_json/...")
    mapa = construir_mapa_equipo_liga()
    print(f"Equipos identificados en el mapa: {len(mapa)}")

    print("Corriendo backtest cronológico (puede tardar 1-3 minutos)...")
    df_res, sin_liga, sin_muestra = correr_backtest(df, mapa)

    df_res.to_csv(OUTPUT_CSV, index=False, encoding="utf-8")
    print(f"✅ Detalle guardado en {OUTPUT_CSV}")

    generar_reporte(df_res, sin_liga, sin_muestra, len(df))


if __name__ == "__main__":
    main()
