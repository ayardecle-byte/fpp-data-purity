"""
REGISTRO DE PREDICCIONES
=========================
Guarda TODAS las probabilidades que calcula el modelo, se apueste o no,
y las compara con el resultado real cuando el partido se juega.

Por qué importa:
  - Hoy solo se guardan las apuestas realizadas. Eso mezcla el
    rendimiento del modelo con el criterio del usuario al elegir.
  - Todo lo que sabemos viene de backtest sobre datos históricos.
    Esto mide si el modelo SIGUE funcionando ahora.

Uso:
  Desde el dashboard (automático):
      import registro_predicciones as reg
      reg.guardar(liga, local, visita, stats, fecha_partido)

  Para actualizar resultados y ver la calibración en vivo:
      python registro_predicciones.py --actualizar
      python registro_predicciones.py --reporte
"""

import os
import sys
import sqlite3
from datetime import datetime, date

DB_PATH = "database/football_data.db"
VERSION_MOTOR = "v2-dixoncoles-calibrado"


# =========================================================
# CREACIÓN DE LA TABLA
# =========================================================
def crear_tabla():
    os.makedirs("database", exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS predicciones_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fecha_prediccion TEXT,
            fecha_partido TEXT,
            liga TEXT,
            calidad_liga TEXT,
            equipo_local TEXT,
            equipo_visita TEXT,
            prob_1 REAL, prob_X REAL, prob_2 REAL,
            prob_1X REAL, prob_X2 REAL, prob_12 REAL,
            xg_local REAL, xg_visita REAL,
            version_motor TEXT,
            goles_local INTEGER DEFAULT NULL,
            goles_visita INTEGER DEFAULT NULL,
            resultado TEXT DEFAULT NULL,
            UNIQUE(fecha_partido, equipo_local, equipo_visita, version_motor)
        )
    """)
    conn.commit()
    conn.close()


# =========================================================
# GUARDAR UNA PREDICCIÓN (lo llama el dashboard)
# =========================================================
def guardar(liga, local, visita, stats, fecha_partido=None):
    """
    stats: el diccionario que devuelve motor_v2.predecir()
    fecha_partido: 'YYYY-MM-DD'. Si no se pasa, se usa hoy.
    Devuelve True si guardó, False si ya existía o falló.
    """
    if not stats or "1" not in stats:
        return False

    try:
        crear_tabla()
        calidad = stats.get("_calidad", {})
        fecha_partido = fecha_partido or date.today().strftime("%Y-%m-%d")

        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute("""
            INSERT OR IGNORE INTO predicciones_log
                (fecha_prediccion, fecha_partido, liga, calidad_liga,
                 equipo_local, equipo_visita,
                 prob_1, prob_X, prob_2, prob_1X, prob_X2, prob_12,
                 xg_local, xg_visita, version_motor)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            fecha_partido, liga, calidad.get("nivel", "?"),
            local, visita,
            stats.get("1"), stats.get("X"), stats.get("2"),
            stats.get("1X"), stats.get("X2"), stats.get("12"),
            stats.get("xG_L"), stats.get("xG_V"),
            stats.get("_motor", VERSION_MOTOR),
        ))
        guardado = cur.rowcount > 0
        conn.commit()
        conn.close()
        return guardado
    except Exception:
        return False


# =========================================================
# ACTUALIZAR RESULTADOS
# =========================================================
def _norm(t):
    return str(t).strip().lower()


def actualizar_resultados(ventana_dias=12):
    """
    Cruza las predicciones pendientes contra la tabla de partidos.

    NOTA: al analizar en la Cartelera no siempre se conoce la fecha exacta
    del partido, así que se guarda la fecha en que se hizo la predicción.
    Por eso la búsqueda es por EQUIPOS dentro de una ventana de días
    alrededor de esa fecha, no por coincidencia exacta.
    """
    from datetime import datetime as _dt, timedelta as _td

    crear_tabla()
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    cur.execute("""
        SELECT id, fecha_partido, fecha_prediccion, equipo_local, equipo_visita
        FROM predicciones_log WHERE resultado IS NULL
    """)
    pendientes = cur.fetchall()
    if not pendientes:
        print("No hay predicciones pendientes de resultado.")
        conn.close()
        return 0

    print(f"Predicciones pendientes: {len(pendientes)}")

    # Índice de partidos jugados, agrupado por par de equipos
    cur.execute("""
        SELECT fecha, equipo_local, equipo_visita, goles_local, goles_visita
        FROM partidos
        WHERE goles_local IS NOT NULL AND goles_visita IS NOT NULL
    """)
    por_equipos = {}
    for f, l, v, gl, gv in cur.fetchall():
        clave = (_norm(l), _norm(v))
        por_equipos.setdefault(clave, []).append((str(f)[:10], gl, gv))

    def a_fecha(txt):
        try:
            return _dt.strptime(str(txt)[:10], "%Y-%m-%d")
        except Exception:
            return None

    actualizadas = 0
    sin_encontrar = []

    for pid, f_part, f_pred, local, visita in pendientes:
        candidatos = por_equipos.get((_norm(local), _norm(visita)), [])
        if not candidatos:
            sin_encontrar.append((local, visita, "el par de equipos no aparece jugado"))
            continue

        ref = a_fecha(f_part) or a_fecha(f_pred)
        if ref is None:
            sin_encontrar.append((local, visita, "fecha de referencia inválida"))
            continue

        # El partido más cercano dentro de la ventana, sin ir hacia atrás
        mejor = None
        for f_real, gl, gv in candidatos:
            d = a_fecha(f_real)
            if d is None:
                continue
            dias = (d - ref).days
            if -2 <= dias <= ventana_dias:
                if mejor is None or abs(dias) < abs(mejor[0]):
                    mejor = (dias, f_real, gl, gv)

        if mejor is None:
            sin_encontrar.append((local, visita, "sin partido jugado en la ventana de fechas"))
            continue

        _, f_real, gl, gv = mejor
        res = "1" if gl > gv else ("X" if gl == gv else "2")
        cur.execute("""
            UPDATE predicciones_log
            SET goles_local = ?, goles_visita = ?, resultado = ?, fecha_partido = ?
            WHERE id = ?
        """, (gl, gv, res, f_real, pid))
        actualizadas += 1

    conn.commit()
    conn.close()

    print(f"Resultados completados: {actualizadas}")
    if sin_encontrar:
        print(f"Siguen pendientes: {len(sin_encontrar)}")
        motivos = {}
        for _, _, m in sin_encontrar:
            motivos[m] = motivos.get(m, 0) + 1
        for m, n in motivos.items():
            print(f"   - {n}: {m}")
        print("\n   Lo normal es que sean partidos que todavía no se jugaron.")
        print("   Si persisten después de actualizar los datos, puede ser que")
        print("   el nombre del equipo difiera entre el fixture y la base.")
        for l, v, m in sin_encontrar[:5]:
            print(f"      · {l} vs {v}  ({m})")

    return actualizadas


# =========================================================
# REPORTE DE CALIBRACIÓN EN VIVO
# =========================================================
def reporte():
    crear_tabla()
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    cur.execute("SELECT COUNT(*) FROM predicciones_log")
    total = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM predicciones_log WHERE resultado IS NOT NULL")
    con_res = cur.fetchone()[0]

    print("=" * 70)
    print("CALIBRACIÓN EN VIVO")
    print("=" * 70)
    print(f"Predicciones registradas: {total}")
    print(f"Con resultado conocido: {con_res}")

    if con_res < 30:
        print(f"\n⚠️  Con {con_res} partidos todavía no se puede sacar conclusiones.")
        print("   Se necesitan al menos 100-200 para que la calibración")
        print("   diga algo confiable. Seguí usando el sistema con normalidad.")
        conn.close()
        return

    cur.execute("""
        SELECT prob_1, prob_X, prob_2, resultado, liga, calidad_liga
        FROM predicciones_log WHERE resultado IS NOT NULL
    """)
    filas = cur.fetchall()
    conn.close()

    # --- Acierto general ---
    aciertos = 0
    for p1, px, p2, real, _, _ in filas:
        pred = max([("1", p1 or 0), ("X", px or 0), ("2", p2 or 0)], key=lambda x: x[1])[0]
        if pred == real:
            aciertos += 1
    print(f"\nAcierto general (1X2): {aciertos / len(filas) * 100:.1f}%")
    n_local = sum(1 for f in filas if f[3] == "1")
    print(f"Referencia — apostar siempre al local: {n_local / len(filas) * 100:.1f}%")

    # --- Calibración por rango ---
    print("\n" + "-" * 70)
    print("¿Las probabilidades dicen la verdad?")
    print("-" * 70)
    print(f"{'Rango':<14}{'N':<8}{'Predicho':<12}{'Real':<10}{'Desvío'}")

    rangos = [(0, 40), (40, 50), (50, 60), (60, 70), (70, 80), (80, 101)]
    for lo, hi in rangos:
        sel = []
        for p1, px, p2, real, _, _ in filas:
            opciones = [("1", p1 or 0), ("X", px or 0), ("2", p2 or 0)]
            pred, prob = max(opciones, key=lambda x: x[1])
            if lo <= prob < hi:
                sel.append((prob, 1 if pred == real else 0))
        if len(sel) < 5:
            continue
        pred_medio = sum(s[0] for s in sel) / len(sel)
        real_medio = sum(s[1] for s in sel) / len(sel) * 100
        desvio = real_medio - pred_medio
        marca = "  ✅" if abs(desvio) <= 7 else "  ⚠️"
        etiqueta = f"{lo}-{hi if hi <= 100 else 100}%"
        print(f"{etiqueta:<14}{len(sel):<8}{pred_medio:<12.1f}{real_medio:<10.1f}{desvio:+.1f}{marca}")

    print("\n  Desvío = acierto real menos lo que decía el modelo.")
    print("  Cerca de cero es lo ideal. Positivo = el modelo se queda corto.")
    print("  Negativo = el modelo promete de más (peligroso para el EV).")

    # --- Por liga ---
    print("\n" + "-" * 70)
    print("POR LIGA (mínimo 15 partidos)")
    print("-" * 70)
    ligas = {}
    for p1, px, p2, real, liga, cal in filas:
        pred = max([("1", p1 or 0), ("X", px or 0), ("2", p2 or 0)], key=lambda x: x[1])[0]
        d = ligas.setdefault(liga, {"n": 0, "ok": 0, "cal": cal})
        d["n"] += 1
        d["ok"] += 1 if pred == real else 0

    for liga, d in sorted(ligas.items(), key=lambda x: -x[1]["n"]):
        if d["n"] < 15:
            continue
        print(f"  {liga:<28} {d['ok']}/{d['n']}  ({d['ok']/d['n']*100:.1f}%)  [{d['cal']}]")


def main():
    if "--actualizar" in sys.argv:
        actualizar_resultados()
    elif "--reporte" in sys.argv:
        reporte()
    else:
        crear_tabla()
        print("Tabla 'predicciones_log' lista.")
        print("\nComandos:")
        print("  python registro_predicciones.py --actualizar   (completar resultados)")
        print("  python registro_predicciones.py --reporte      (ver calibración)")


if __name__ == "__main__":
    main()
