"""
SCRAPER DE HISTORIAL - SoccerStats  (v2 CORREGIDA)
===================================================
Correcciones respecto de la v1:

  BUG 1 - El crawler se metía en otras temporadas.
          Causa: "league=england2" también coincide con "league=england2_2025".
          Arreglo: comparación EXACTA del código de temporada.

  BUG 2 - Años mal asignados en ligas de temporada única (Noruega, Suecia,
          China, MLS, Japón, etc.).
          Causa: se asumía "cruza de año" si había partidos en Ago-Dic y Ene-May,
          pero esas ligas juegan Mar-Dic sin cruzar año.
          Arreglo: una liga es de AÑO CALENDARIO si juega en junio Y julio.
          Si no juega en esos meses, es temporada europea que cruza el año.

  BUG 3 - Marcadores imposibles (14:0, 18:0) provenientes de tablas agregadas.
          Arreglo: se descartan y se reportan los marcadores > MAX_GOLES_VALIDO.

  EXTRA - Se descartan partidos con fecha futura (no pueden tener resultado).

Modos:
  python scraper_historial.py            -> PRUEBA (no toca la base de datos)
  python scraper_historial.py --guardar  -> inserta en la base de datos
"""

import os
import re
import sys
import time
import sqlite3
import requests
from datetime import datetime, date

try:
    from bs4 import BeautifulSoup
except ImportError:
    print("❌ Falta beautifulsoup4. Corré: pip install beautifulsoup4")
    sys.exit(1)

try:
    import pandas as pd
except ImportError:
    print("❌ Falta pandas. Corré: pip install pandas")
    sys.exit(1)

BASE = "https://www.soccerstats.com"
DB_PATH = "database/football_data.db"
CSV_SALIDA = "historial_descargado.csv"
TXT_SALIDA = "historial_reporte.txt"
CSV_RECHAZADOS = "historial_rechazados.csv"

PAUSA = 3
MAX_PAGINAS_TEMPORADA = 15
MAX_GOLES_VALIDO = 12      # un marcador mayor a esto es error de parseo
HOY = date.today()

# Se bajan 3 temporadas anteriores + la actual.
# Con el recorrido por meses cada temporada rinde mucho más que antes.
_TEMPS = ["", "_2025", "_2024", "_2023"]

OBJETIVOS = {
    # --- Ligas con ventaja ALTA ---
    "czechrepublic": ("czechrepublic", _TEMPS),
    "estonia":       ("estonia",       _TEMPS),
    "turkey":        ("turkey",        _TEMPS),
    "china":         ("china",         _TEMPS),
    "ukraine":       ("ukraine",       _TEMPS),
    "mexico":        ("mexico",        _TEMPS),
    "scotland":      ("scotland",      _TEMPS),
    "spain":         ("spain",         _TEMPS),
    "brazil":        ("brazil",        _TEMPS),
    "france":        ("france",        _TEMPS),
    "norway":        ("norway",        _TEMPS),
    "italy":         ("italy",         _TEMPS),
    "england":       ("england",       _TEMPS),

    # --- Ligas con ventaja MEDIA ---
    "finland":       ("finland",       _TEMPS),
    "iceland":       ("iceland",       _TEMPS),
    "netherlands2":  ("netherlands2",  _TEMPS),
    "usa":           ("mls",           _TEMPS),
    "japan":         ("japan",         _TEMPS),
    "portugal2":     ("portugal2",     _TEMPS),
    "germany2":      ("germany2",      _TEMPS),
    "switzerland":   ("switzerland",   _TEMPS),

    # --- Ligas de menor rendimiento, útiles igual para el backtest ---
    "poland":        ("poland",        _TEMPS),
    "italy2":        ("italy2",        _TEMPS),
    "france2":       ("france2",       _TEMPS),
    "spain2":        ("spain2",        _TEMPS),
    "england2":      ("england2",      _TEMPS),
    "england3":      ("england3",      _TEMPS),
    "belgium":       ("belgium",       _TEMPS),
    "austria":       ("austria",       _TEMPS),
    "greece":        ("greece",        _TEMPS),
    "denmark":       ("denmark",       _TEMPS),
    "sweden":        ("sweden",        _TEMPS),
    "southkorea":    ("southkorea",    _TEMPS),
}

MESES = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
         "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12}

sesion = requests.Session()
rechazados = []


def bajar(url):
    try:
        r = sesion.get(url, timeout=20)
        if r.status_code == 200:
            return r.text
        print(f"      HTTP {r.status_code}")
        return None
    except Exception as e:
        print(f"      Error de red: {e}")
        return None


def parsear_partidos(html, contexto):
    soup = BeautifulSoup(html, "html.parser")
    partidos = []

    re_fecha = re.compile(r"^[A-Za-z]{3}\s+\d{1,2}\s+[A-Za-z]{3}$")
    re_ft = re.compile(r"^(\d+)\s*:\s*(\d+)$")
    re_ht = re.compile(r"^\((\d+)\s*-\s*(\d+)\)$")

    for tr in soup.find_all("tr"):
        celdas = [td.get_text(" ", strip=True) for td in tr.find_all(["td", "th"])]
        if len(celdas) < 4:
            continue

        idx_ft = None
        for i, c in enumerate(celdas):
            if re_ft.match(c):
                idx_ft = i
                break
        if idx_ft is None or idx_ft < 2:
            continue

        fecha_txt = celdas[idx_ft - 2].strip()
        local = celdas[idx_ft - 1].strip()
        visita = celdas[idx_ft + 1].strip() if idx_ft + 1 < len(celdas) else ""

        if not re_fecha.match(fecha_txt) or not local or not visita:
            continue

        m = re_ft.match(celdas[idx_ft])
        gl, gv = int(m.group(1)), int(m.group(2))

        # --- BUG 3: descartar marcadores imposibles ---
        if gl > MAX_GOLES_VALIDO or gv > MAX_GOLES_VALIDO:
            rechazados.append({
                "motivo": "marcador imposible",
                "contexto": contexto,
                "fecha_txt": fecha_txt,
                "local": local, "visita": visita,
                "marcador": f"{gl}:{gv}",
            })
            continue

        if local.lower() == visita.lower():
            rechazados.append({
                "motivo": "equipos iguales",
                "contexto": contexto,
                "fecha_txt": fecha_txt,
                "local": local, "visita": visita,
                "marcador": f"{gl}:{gv}",
            })
            continue

        ht_l, ht_v = None, None
        for c in celdas[idx_ft + 1:]:
            mh = re_ht.match(c.strip())
            if mh:
                h1, h2 = int(mh.group(1)), int(mh.group(2))
                if h1 <= gl and h2 <= gv:   # el HT no puede superar al final
                    ht_l, ht_v = h1, h2
                break

        partes = fecha_txt.split()
        try:
            dia = int(partes[1])
            mes = MESES.get(partes[2][:3].lower())
        except Exception:
            continue
        if not mes:
            continue

        partidos.append({
            "fecha_txt": fecha_txt, "dia": dia, "mes": mes,
            "local": local, "visita": visita,
            "goles_local": gl, "goles_visita": gv,
            "ht_local": ht_l, "ht_visita": ht_v,
        })

    return partidos


def urls_de_temporada(codigo_exacto):
    """
    HALLAZGO CLAVE: SoccerStats parte la temporada en bloques y usa el
    parámetro 'pmtype' para moverse entre ellos.

        results.asp?league=X                    -> solo el primer mes (~47 partidos)
        results.asp?league=X&pmtype=bydate      -> un bloque de ~4 meses (~180)
        results.asp?league=X&pmtype=month1..12  -> un mes concreto

    Recorriendo los 12 meses se obtiene la temporada completa.
    """
    urls = [
        f"{BASE}/results.asp?league={codigo_exacto}",
        f"{BASE}/results.asp?league={codigo_exacto}&pmtype=bydate",
    ]
    for i in range(1, 13):
        urls.append(f"{BASE}/results.asp?league={codigo_exacto}&pmtype=month{i}")
    return urls


def asignar_anios(partidos, etiqueta, nombre_liga):
    """
    BUG 2 CORREGIDO.
    Regla: si la liga juega en JUNIO y JULIO, es de año calendario
    (Noruega, Suecia, MLS, China, Japón...). Si no juega en esos meses,
    es temporada europea que cruza el año (Ago -> May).
    """
    if not partidos:
        return []

    meses = {p["mes"] for p in partidos}
    es_calendario = (6 in meses and 7 in meses)

    if etiqueta:
        anio_etiqueta = int(etiqueta.replace("_", ""))
    else:
        anio_etiqueta = HOY.year

    resultado = []
    for p in partidos:
        if es_calendario:
            anio = anio_etiqueta
        else:
            # Temporada que cruza el año. La etiqueta es el año de CIERRE.
            if etiqueta:
                anio = anio_etiqueta - 1 if p["mes"] >= 8 else anio_etiqueta
            else:
                # Temporada actual en curso
                if HOY.month >= 8:
                    anio = HOY.year if p["mes"] >= 8 else HOY.year + 1
                else:
                    anio = HOY.year - 1 if p["mes"] >= 8 else HOY.year

        try:
            f = date(anio, p["mes"], p["dia"])
        except ValueError:
            continue

        # Descartar fechas futuras: no pueden tener resultado
        if f > HOY:
            rechazados.append({
                "motivo": "fecha futura",
                "contexto": f"{nombre_liga}{etiqueta}",
                "fecha_txt": p["fecha_txt"],
                "local": p["local"], "visita": p["visita"],
                "marcador": f"{p['goles_local']}:{p['goles_visita']}",
            })
            continue

        p["anio"] = anio
        p["fecha_iso"] = f.strftime("%Y-%m-%d")
        p["tipo_temporada"] = "calendario" if es_calendario else "cruza-año"
        resultado.append(p)

    return resultado


def scrapear_temporada(codigo_base, etiqueta, nombre_liga):
    codigo = f"{codigo_base}{etiqueta}"
    url_inicial = f"{BASE}/results.asp?league={codigo}"
    print(f"  → {codigo}")

    todos = []
    paginas_ok = 0

    for i, url in enumerate(urls_de_temporada(codigo)):
        if i > 0:
            time.sleep(PAUSA)
        h = bajar(url)
        if not h:
            continue
        encontrados = parsear_partidos(h, codigo)
        if encontrados:
            paginas_ok += 1
            todos.extend(encontrados)

    todos = asignar_anios(todos, etiqueta, nombre_liga)

    vistos = set()
    unicos = []
    for p in todos:
        clave = (p["fecha_iso"], p["local"].lower(), p["visita"].lower())
        if clave in vistos:
            continue
        vistos.add(clave)
        p["liga"] = nombre_liga
        p["temporada"] = etiqueta or "actual"
        unicos.append(p)

    tipo = unicos[0]["tipo_temporada"] if unicos else "?"
    print(f"     {len(unicos)} partidos · {paginas_ok}/14 páginas con datos · temporada {tipo}")
    return unicos


def asegurar_columna_liga(conn):
    cur = conn.cursor()
    cur.execute("PRAGMA table_info(partidos)")
    cols = [c[1] for c in cur.fetchall()]
    if "liga" not in cols:
        print("Agregando columna 'liga' a la tabla partidos...")
        cur.execute("ALTER TABLE partidos ADD COLUMN liga TEXT DEFAULT ''")
        conn.commit()


def guardar_en_db(df):
    if not os.path.exists(DB_PATH):
        print(f"❌ No se encontró {DB_PATH}")
        return

    conn = sqlite3.connect(DB_PATH)
    asegurar_columna_liga(conn)
    cur = conn.cursor()

    cur.execute("SELECT fecha, equipo_local, equipo_visita FROM partidos")
    existentes = set()
    for f, l, v in cur.fetchall():
        existentes.add((str(f)[:10], str(l).strip().lower(), str(v).strip().lower()))

    nuevos, saltados = 0, 0
    for r in df.itertuples(index=False):
        clave = (r.fecha_iso, r.local.strip().lower(), r.visita.strip().lower())
        if clave in existentes:
            saltados += 1
            continue
        cur.execute("""
            INSERT INTO partidos
                (fecha, estado, equipo_local, equipo_visita,
                 goles_local, goles_visita, stats_descargadas,
                 ht_goles_local, ht_goles_visita, liga)
            VALUES (?, 'Match Finished', ?, ?, ?, ?, 0, ?, ?, ?)
        """, (r.fecha_iso, r.local, r.visita, int(r.goles_local), int(r.goles_visita),
              int(r.ht_local) if pd.notna(r.ht_local) else None,
              int(r.ht_visita) if pd.notna(r.ht_visita) else None,
              r.liga))
        existentes.add(clave)
        nuevos += 1

    conn.commit()
    conn.close()
    print(f"\n✅ Insertados: {nuevos} · Ya existían: {saltados}")


def main():
    guardar = "--guardar" in sys.argv
    print("=" * 60)
    print("SCRAPER DE HISTORIAL v2 (corregida)")
    print("MODO:", "GUARDAR EN BD" if guardar else "PRUEBA (no toca la BD)")
    print("=" * 60)

    todos, reporte = [], []

    for codigo_base, (nombre_liga, temporadas) in OBJETIVOS.items():
        print(f"\n[{nombre_liga}]")
        for etiqueta in temporadas:
            partidos = scrapear_temporada(codigo_base, etiqueta, nombre_liga)
            todos.extend(partidos)
            reporte.append({
                "liga": nombre_liga,
                "temporada": etiqueta or "actual",
                "partidos": len(partidos),
                "con_ht": sum(1 for p in partidos if p["ht_local"] is not None),
                "desde": min((p["fecha_iso"] for p in partidos), default="-"),
                "hasta": max((p["fecha_iso"] for p in partidos), default="-"),
            })
            time.sleep(PAUSA)

    if not todos:
        print("\n❌ No se descargó ningún partido.")
        return

    df = pd.DataFrame(todos)
    df.to_csv(CSV_SALIDA, index=False, encoding="utf-8")

    if rechazados:
        pd.DataFrame(rechazados).to_csv(CSV_RECHAZADOS, index=False, encoding="utf-8")

    L = []
    L.append("=" * 78)
    L.append("REPORTE DE DESCARGA DE HISTORIAL (v2 corregida)")
    L.append("=" * 78)
    L.append(f"Partidos válidos: {len(df)}")
    L.append(f"Con medio tiempo (HT): {df['ht_local'].notna().sum()}")
    L.append(f"Rango de fechas: {df['fecha_iso'].min()} a {df['fecha_iso'].max()}")
    L.append(f"Descartados por control de calidad: {len(rechazados)}")
    L.append("")
    L.append("CONTROLES: sin marcadores > 12 goles · sin fechas futuras · "
             "sin equipos repetidos · HT nunca mayor al resultado final")
    L.append("")
    L.append(f"{'Liga':<16}{'Temp.':<9}{'Part.':<8}{'HT':<7}{'Desde':<12}{'Hasta':<12}")
    L.append("-" * 78)
    for r in reporte:
        L.append(f"{r['liga']:<16}{r['temporada']:<9}{r['partidos']:<8}"
                 f"{r['con_ht']:<7}{r['desde']:<12}{r['hasta']:<12}")

    L.append("")
    L.append("TOTAL POR LIGA:")
    for liga, sub in df.groupby("liga"):
        tipo = sub["tipo_temporada"].mode()[0] if not sub.empty else "?"
        L.append(f"  {liga:<16} {len(sub):>5} partidos · {sub['fecha_iso'].min()} "
                 f"a {sub['fecha_iso'].max()} · {tipo}")

    if rechazados:
        L.append("")
        L.append(f"DESCARTADOS ({len(rechazados)}) — primeros 20:")
        for r in rechazados[:20]:
            L.append(f"  [{r['motivo']}] {r['contexto']} · {r['fecha_txt']} · "
                     f"{r['local']} {r['marcador']} {r['visita']}")

    L.append("")
    L.append("MUESTRA (12 partidos al azar):")
    for r in df.sample(min(12, len(df))).itertuples(index=False):
        ht = f"HT {int(r.ht_local)}-{int(r.ht_visita)}" if pd.notna(r.ht_local) else "sin HT"
        L.append(f"  {r.fecha_iso} [{r.liga}] {r.local} {r.goles_local}:{r.goles_visita} {r.visita} ({ht})")

    with open(TXT_SALIDA, "w", encoding="utf-8") as f:
        f.write("\n".join(L))

    print(f"\n✅ CSV: {CSV_SALIDA}")
    print(f"✅ Reporte: {TXT_SALIDA}")
    if rechazados:
        print(f"⚠️  Descartados: {CSV_RECHAZADOS} ({len(rechazados)} filas)")

    if guardar:
        guardar_en_db(df)
    else:
        print("\n⚠️  MODO PRUEBA: no se guardó nada.")
        print("   Si el reporte está bien: python scraper_historial.py --guardar")


if __name__ == "__main__":
    main()
