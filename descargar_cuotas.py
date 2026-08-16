"""
DESCARGADOR DE CUOTAS HISTÓRICAS
=================================
Baja los CSV gratuitos de football-data.co.uk, que traen resultados
Y CUOTAS DE CIERRE de las principales ligas europeas.

Por qué importa:
  Hasta ahora medimos PRECISIÓN (si el modelo acierta más que el azar).
  Nunca medimos RENTABILIDAD (si ganaría dinero contra cuotas reales).
  Son cosas distintas: un modelo puede acertar más que la referencia
  y perder plata igual, si el margen de la casa se come la ventaja.

  Con estos datos vamos a poder simular: "siguiendo este modelo con
  Kelly fraccionado durante 4 temporadas, el bankroll hubiera hecho X".

Qué guarda:
  Tabla `cuotas_historicas` con fecha, equipos, resultado y las cuotas
  de varias casas (incluida la de cierre, que es la más informativa).

Uso:
    python descargar_cuotas.py            -> descarga y guarda
    python descargar_cuotas.py --reporte  -> muestra qué hay guardado
"""

import io
import os
import sys
import time
import sqlite3
import requests
import pandas as pd

BASE = "https://www.football-data.co.uk/mmz4281"
DB_PATH = "database/football_data.db"
PAUSA = 2

# Temporadas en el formato del sitio: 2223 = 2022/23
TEMPORADAS = ["2223", "2324", "2425", "2526"]

# Código del sitio : nombre interno que usamos nosotros
LIGAS = {
    "E0":  "england",        # Premier League
    "E1":  "england2",       # Championship
    "E2":  "england3",       # League One
    "SP1": "spain",          # La Liga
    "SP2": "spain2",         # La Liga 2
    "I1":  "italy",          # Serie A
    "I2":  "italy2",         # Serie B
    "F1":  "france",         # Ligue 1
    "F2":  "france2",        # Ligue 2
    "D2":  "germany2",       # 2. Bundesliga
    "N1":  "netherlands",    # Eredivisie
    "P1":  "portugal",       # Primeira Liga
    "T1":  "turkey",         # Süper Lig
    "B1":  "belgium",        # Pro League
    "G1":  "greece",         # Super League
    "SC0": "scotland",       # Premiership
    "SC1": "scotland2",      # Championship
}

# Columnas de cuotas que nos interesan.
# Las que terminan en C son las de CIERRE (closing), las más informativas:
# reflejan toda la información disponible justo antes del partido.
COLUMNAS_CUOTAS = {
    "B365CH": "cierre_1", "B365CD": "cierre_X", "B365CA": "cierre_2",
    "B365H": "b365_1", "B365D": "b365_X", "B365A": "b365_2",
    "PSCH": "pinnacle_1", "PSCD": "pinnacle_X", "PSCA": "pinnacle_2",
    "MaxCH": "max_1", "MaxCD": "max_X", "MaxCA": "max_2",
    "AvgCH": "prom_1", "AvgCD": "prom_X", "AvgCA": "prom_2",
}


def crear_tabla():
    os.makedirs("database", exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS cuotas_historicas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fecha TEXT,
            liga TEXT,
            temporada TEXT,
            equipo_local TEXT,
            equipo_visita TEXT,
            goles_local INTEGER,
            goles_visita INTEGER,
            resultado TEXT,
            cierre_1 REAL, cierre_X REAL, cierre_2 REAL,
            b365_1 REAL, b365_X REAL, b365_2 REAL,
            pinnacle_1 REAL, pinnacle_X REAL, pinnacle_2 REAL,
            max_1 REAL, max_X REAL, max_2 REAL,
            prom_1 REAL, prom_X REAL, prom_2 REAL,
            UNIQUE(fecha, equipo_local, equipo_visita)
        )
    """)
    cur.execute("""CREATE INDEX IF NOT EXISTS idx_cuotas_liga
                   ON cuotas_historicas(liga, fecha)""")
    conn.commit()
    conn.close()


def normalizar_fecha(valor):
    """El sitio usa DD/MM/YY o DD/MM/YYYY."""
    t = str(valor).strip()
    for fmt in ("%d/%m/%Y", "%d/%m/%y"):
        try:
            return pd.to_datetime(t, format=fmt).strftime("%Y-%m-%d")
        except Exception:
            continue
    return None


def bajar_csv(temporada, codigo):
    url = f"{BASE}/{temporada}/{codigo}.csv"
    try:
        r = requests.get(url, timeout=30)
        if r.status_code != 200:
            return None, f"HTTP {r.status_code}"
        df = pd.read_csv(io.StringIO(r.content.decode("latin-1")),
                         on_bad_lines="skip")
        return df, None
    except Exception as e:
        return None, str(e)


def procesar(df, liga, temporada):
    """Extrae las columnas que nos sirven y las normaliza."""
    if df is None or df.empty:
        return []

    columnas = {c.strip(): c for c in df.columns}

    def col(nombre):
        return columnas.get(nombre)

    c_fecha = col("Date")
    c_local = col("HomeTeam")
    c_visita = col("AwayTeam")
    c_gl = col("FTHG")
    c_gv = col("FTAG")

    if not all([c_fecha, c_local, c_visita, c_gl, c_gv]):
        return []

    filas = []
    for r in df.itertuples(index=False):
        d = dict(zip(df.columns, r))

        fecha = normalizar_fecha(d.get(c_fecha))
        local = str(d.get(c_local, "")).strip()
        visita = str(d.get(c_visita, "")).strip()
        if not fecha or not local or not visita:
            continue

        try:
            gl = int(float(d.get(c_gl)))
            gv = int(float(d.get(c_gv)))
        except (TypeError, ValueError):
            continue

        fila = {
            "fecha": fecha, "liga": liga, "temporada": temporada,
            "equipo_local": local, "equipo_visita": visita,
            "goles_local": gl, "goles_visita": gv,
            "resultado": "1" if gl > gv else ("X" if gl == gv else "2"),
        }

        for origen, destino in COLUMNAS_CUOTAS.items():
            c = col(origen)
            valor = None
            if c is not None:
                try:
                    v = float(d.get(c))
                    if 1.0 < v < 1000:
                        valor = v
                except (TypeError, ValueError):
                    valor = None
            fila[destino] = valor

        # Sin ninguna cuota, la fila no aporta nada
        if not any(fila.get(x) for x in COLUMNAS_CUOTAS.values()):
            continue

        filas.append(fila)

    return filas


def guardar(filas):
    if not filas:
        return 0, 0
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    campos = ["fecha", "liga", "temporada", "equipo_local", "equipo_visita",
              "goles_local", "goles_visita", "resultado"] + list(COLUMNAS_CUOTAS.values())
    marcadores = ",".join("?" * len(campos))
    sql = f"INSERT OR IGNORE INTO cuotas_historicas ({','.join(campos)}) VALUES ({marcadores})"

    nuevos = 0
    for f in filas:
        cur.execute(sql, tuple(f.get(c) for c in campos))
        nuevos += cur.rowcount
    conn.commit()
    conn.close()
    return nuevos, len(filas) - nuevos


def descargar():
    crear_tabla()
    print("=" * 70)
    print("DESCARGA DE CUOTAS HISTÓRICAS — football-data.co.uk")
    print(f"Ligas: {len(LIGAS)} · Temporadas: {len(TEMPORADAS)}")
    print("=" * 70)

    total_nuevos = 0
    resumen = []

    for temporada in TEMPORADAS:
        print(f"\n--- Temporada {temporada[:2]}/{temporada[2:]} ---")
        for codigo, liga in LIGAS.items():
            df, error = bajar_csv(temporada, codigo)
            if error:
                print(f"  {liga:<14} {codigo:<5} ✗ {error}")
                time.sleep(PAUSA)
                continue

            filas = procesar(df, liga, temporada)
            nuevos, repetidos = guardar(filas)
            total_nuevos += nuevos

            con_cierre = sum(1 for f in filas if f.get("cierre_1"))
            print(f"  {liga:<14} {codigo:<5} {len(filas):>4} partidos · "
                  f"{nuevos:>4} nuevos · {con_cierre:>4} con cuota de cierre")
            resumen.append((liga, temporada, len(filas), nuevos, con_cierre))
            time.sleep(PAUSA)

    print("\n" + "=" * 70)
    print(f"Partidos nuevos guardados: {total_nuevos}")
    reporte()


def reporte():
    if not os.path.exists(DB_PATH):
        print(f"❌ No se encontró {DB_PATH}")
        return
    crear_tabla()
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    cur.execute("SELECT COUNT(*) FROM cuotas_historicas")
    total = cur.fetchone()[0]
    if total == 0:
        print("No hay cuotas guardadas. Corré: python descargar_cuotas.py")
        conn.close()
        return

    cur.execute("SELECT MIN(fecha), MAX(fecha) FROM cuotas_historicas")
    desde, hasta = cur.fetchone()

    print("\n" + "=" * 70)
    print("CUOTAS HISTÓRICAS GUARDADAS")
    print("=" * 70)
    print(f"Partidos con cuotas: {total}")
    print(f"Rango: {desde} a {hasta}")

    cur.execute("""
        SELECT liga, COUNT(*),
               SUM(CASE WHEN cierre_1 IS NOT NULL THEN 1 ELSE 0 END),
               MIN(fecha), MAX(fecha)
        FROM cuotas_historicas GROUP BY liga ORDER BY COUNT(*) DESC
    """)
    print(f"\n{'Liga':<16}{'Partidos':<11}{'Con cierre':<13}{'Desde':<12}{'Hasta'}")
    for liga, n, cierre, d, h in cur.fetchall():
        print(f"{liga:<16}{n:<11}{cierre or 0:<13}{d:<12}{h}")

    # Margen medio de la casa: cuánto se queda por partido
    cur.execute("""
        SELECT AVG(1.0/cierre_1 + 1.0/cierre_X + 1.0/cierre_2)
        FROM cuotas_historicas
        WHERE cierre_1 IS NOT NULL AND cierre_X IS NOT NULL AND cierre_2 IS NOT NULL
    """)
    m = cur.fetchone()[0]
    if m:
        print(f"\nMargen medio de la casa (cuotas de cierre): {(m-1)*100:.2f}%")
        print("  Es lo que la casa se queda en cada partido. Tu ventaja")
        print("  tiene que superar ese número para que haya ganancia real.")

    conn.close()


if __name__ == "__main__":
    if "--reporte" in sys.argv:
        reporte()
    else:
        descargar()
