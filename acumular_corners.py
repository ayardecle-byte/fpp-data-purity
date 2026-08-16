"""
ACUMULADOR DE CÓRNERS
======================
SoccerStats NO publica córners partido a partido (lo confirmamos con
inspector_corners.py). Solo publica promedios por equipo y temporada.

Consecuencia: el mercado de córners no se puede backtestear con datos
históricos. La única salida es acumular hacia adelante: guardar una
"foto" de las estadísticas cada semana, y en unos meses tener suficiente
historial para validarlo con el mismo rigor que usamos en 1X2.

Este script guarda esa foto semanal en la tabla `corners_snapshots`.

Cómo usarlo:
    python acumular_corners.py            -> guarda la foto de hoy
    python acumular_corners.py --reporte  -> muestra qué se acumuló

Conviene correrlo una vez por semana, junto con el actualizador de ligas.
En unos 3-4 meses habrá muestra suficiente para intentar la validación.
"""

import os
import re
import sys
import time
import sqlite3
import requests
from datetime import date
from bs4 import BeautifulSoup

BASE = "https://www.soccerstats.com"
DB_PATH = "database/football_data.db"
PAUSA = 3

# Ligas con ventaja validada (ALTA y MEDIA) — las que vale la pena seguir
LIGAS = [
    ("czechrepublic", "ALTA"), ("estonia", "ALTA"), ("turkey", "ALTA"),
    ("china", "ALTA"), ("ukraine", "ALTA"), ("mexico", "ALTA"),
    ("scotland", "ALTA"), ("spain", "ALTA"), ("brazil", "ALTA"),
    ("france", "ALTA"), ("norway", "ALTA"), ("italy", "ALTA"),
    ("england", "ALTA"),
    ("finland", "MEDIA"), ("iceland", "MEDIA"), ("netherlands2", "MEDIA"),
    ("usa", "MEDIA"), ("japan", "MEDIA"), ("portugal2", "MEDIA"),
    ("germany2", "MEDIA"), ("switzerland", "MEDIA"),
]

sesion = requests.Session()


def crear_tabla():
    os.makedirs("database", exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS corners_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fecha_captura TEXT,
            liga TEXT,
            equipo TEXT,
            condicion TEXT,            -- 'total', 'local' o 'visita'
            partidos INTEGER,
            corners_favor REAL,
            corners_contra REAL,
            corners_total REAL,
            promedio_partido REAL,
            UNIQUE(fecha_captura, liga, equipo, condicion)
        )
    """)
    cur.execute("""CREATE INDEX IF NOT EXISTS idx_corners_liga
                   ON corners_snapshots(liga, equipo)""")
    conn.commit()
    conn.close()


def bajar(url):
    try:
        r = sesion.get(url, timeout=20)
        return BeautifulSoup(r.content, "html.parser") if r.status_code == 200 else None
    except Exception:
        return None


def num(txt):
    """Convierte '6.2' o '12' a float; None si no es número."""
    t = str(txt).strip().replace(",", ".")
    if not re.match(r"^-?\d+(\.\d+)?$", t):
        return None
    return float(t)


def extraer_corners(soup):
    """
    Busca la tabla de córners. Estructura típica:
      Equipo | PJ | A favor | En contra | Total | Promedio
    """
    filas_out = []
    if not soup:
        return filas_out

    for tabla in soup.find_all("table"):
        filas = tabla.find_all("tr")
        if len(filas) < 5:
            continue

        cabecera = " ".join(c.get_text(" ", strip=True).lower()
                            for c in filas[0].find_all(["td", "th"]))
        if "corner" not in cabecera and "for" not in cabecera:
            continue

        candidata = []
        for fila in filas[1:]:
            celdas = [c.get_text(" ", strip=True) for c in fila.find_all(["td", "th"])]
            if len(celdas) < 4:
                continue

            equipo = celdas[1].strip() if len(celdas) > 1 else ""
            if not equipo or not any(ch.isalpha() for ch in equipo):
                equipo = celdas[0].strip()
            if not equipo or not any(ch.isalpha() for ch in equipo):
                continue
            if len(equipo) > 35:
                continue
            if any(b in equipo.lower() for b in
                   ("average", "total", "team", "points", "matches")):
                continue

            numeros = [num(c) for c in celdas]
            numeros = [n for n in numeros if n is not None]
            if len(numeros) < 3:
                continue

            candidata.append({
                "equipo": equipo,
                "partidos": int(numeros[0]) if numeros[0] and numeros[0] < 100 else None,
                "favor": numeros[1] if len(numeros) > 1 else None,
                "contra": numeros[2] if len(numeros) > 2 else None,
                "total": numeros[3] if len(numeros) > 3 else None,
                "promedio": numeros[4] if len(numeros) > 4 else None,
            })

        if 4 <= len(candidata) <= 40:
            return candidata
        if len(candidata) > len(filas_out):
            filas_out = candidata

    return filas_out


def capturar():
    crear_tabla()
    hoy = date.today().strftime("%Y-%m-%d")

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    print("=" * 66)
    print(f"CAPTURA DE CÓRNERS — {hoy}")
    print("=" * 66)

    total_guardado = 0
    for liga, nivel in LIGAS:
        print(f"\n[{nivel}] {liga}")
        # tid=cr es la vista de córners
        soup = bajar(f"{BASE}/table.asp?league={liga}&tid=cr")
        datos = extraer_corners(soup)

        if not datos:
            print("      Sin datos de córners")
            time.sleep(PAUSA)
            continue

        guardados = 0
        for d in datos:
            try:
                cur.execute("""
                    INSERT OR IGNORE INTO corners_snapshots
                        (fecha_captura, liga, equipo, condicion, partidos,
                         corners_favor, corners_contra, corners_total, promedio_partido)
                    VALUES (?,?,?,?,?,?,?,?,?)
                """, (hoy, liga, d["equipo"], "total", d["partidos"],
                      d["favor"], d["contra"], d["total"], d["promedio"]))
                guardados += cur.rowcount
            except Exception:
                pass

        conn.commit()
        print(f"      {guardados} equipos guardados")
        total_guardado += guardados
        time.sleep(PAUSA)

    conn.close()

    print("\n" + "=" * 66)
    print(f"Total guardado: {total_guardado} registros")
    print("\nCorré este script una vez por semana. En 3-4 meses habrá")
    print("muestra suficiente para intentar validar el mercado de córners.")


def reporte():
    if not os.path.exists(DB_PATH):
        print(f"❌ No se encontró {DB_PATH}")
        return
    crear_tabla()
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    cur.execute("SELECT COUNT(*) FROM corners_snapshots")
    total = cur.fetchone()[0]
    if total == 0:
        print("Todavía no hay capturas. Corré: python acumular_corners.py")
        conn.close()
        return

    cur.execute("SELECT COUNT(DISTINCT fecha_captura) FROM corners_snapshots")
    n_fechas = cur.fetchone()[0]
    cur.execute("SELECT MIN(fecha_captura), MAX(fecha_captura) FROM corners_snapshots")
    desde, hasta = cur.fetchone()

    print("=" * 66)
    print("ACUMULACIÓN DE CÓRNERS")
    print("=" * 66)
    print(f"Registros: {total}")
    print(f"Capturas realizadas: {n_fechas}")
    print(f"Desde {desde} hasta {hasta}")

    cur.execute("""
        SELECT liga, COUNT(DISTINCT equipo), COUNT(DISTINCT fecha_captura), COUNT(*)
        FROM corners_snapshots GROUP BY liga ORDER BY COUNT(*) DESC
    """)
    print(f"\n{'Liga':<18}{'Equipos':<10}{'Capturas':<11}{'Registros'}")
    for liga, eq, fechas, reg in cur.fetchall():
        print(f"{liga:<18}{eq:<10}{fechas:<11}{reg}")

    print("\n" + "-" * 66)
    if n_fechas < 8:
        print(f"Llevás {n_fechas} capturas. Con menos de 8-12 semanas todavía no")
        print("se puede intentar la validación. Seguí acumulando.")
    else:
        print(f"Ya hay {n_fechas} capturas. Empieza a haber muestra para probar")
        print("un modelo de córners y validarlo contra los resultados reales.")

    conn.close()


if __name__ == "__main__":
    if "--reporte" in sys.argv:
        reporte()
    else:
        capturar()
