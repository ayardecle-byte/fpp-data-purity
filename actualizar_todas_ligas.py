"""
ACTUALIZADOR GENERAL DE LIGAS
==============================
Vuelve a descargar TODAS las ligas con el parseo corregido.

PROTECCIÓN IMPORTANTE: antes de sobrescribir un archivo compara la
calidad de lo nuevo contra lo que ya tenías. Si lo nuevo es peor,
NO lo pisa y te avisa. Así no se pierde información buena por un
código de liga equivocado o una caída momentánea del sitio.

Qué corrige:
  - Ligas grandes (Inglaterra, España, Italia, Francia) con historial vacío
  - Copas (Libertadores, Sudamericana, Champions, Europa) vacías
  - Tablas de posiciones contaminadas con filas basura
    (México tenía "0 pt", "31 min.", "segments table" como si fueran equipos)

Uso:
    python actualizar_todas_ligas.py                 -> todas
    python actualizar_todas_ligas.py --solo-rotas    -> solo las que están mal
    python actualizar_todas_ligas.py --forzar        -> sobrescribe aunque sea peor
"""

import os
import re
import sys
import json
import time
import shutil
import requests
from datetime import datetime, timedelta
from bs4 import BeautifulSoup

BASE = "https://www.soccerstats.com"
CARPETA = "data_json"
BACKUP = "data_json_backup"
PAUSA = 3
MAX_PAGINAS_EXTRA = 12
HORAS_AJUSTE = 5

MESES = {"Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "Jun": 6,
         "Jul": 7, "Aug": 8, "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12}
MESES_INV = {v: k for k, v in MESES.items()}
DIAS_INV = {0: "Mon", 1: "Tue", 2: "Wed", 3: "Thu", 4: "Fri", 5: "Sat", 6: "Sun"}

RE_FECHA = re.compile(r"^[A-Za-z]{3}\s+\d{1,2}\s+[A-Za-z]{3}$")
RE_HORA = re.compile(r"^\d{1,2}:\d{2}$")
RE_MARCADOR = re.compile(r"^(\d{1,2}):(\d)$")          # formato "1:0"
RE_MARCADOR_GUION = re.compile(r"^(\d{1,2})\s*-\s*(\d{1,2})$")  # formato "1-0"
RE_HT = re.compile(r"^\((\d+)\s*-\s*(\d+)\)$")

# archivo : (codigo_soccerstats, es_copa)
LIGAS = {
    # --- Ligas grandes (historial roto: 0 partidos) ---
    "england":          ("england", False),
    "spain":            ("spain", False),
    "italy":            ("italy", False),
    "france":           ("france", False),
    "brazil":           ("brazil", False),

    # --- Copas (completamente vacías) ---
    "champions":        ("cleague", True),
    "europa":           ("uefa", True),
    "libertadores":     ("copalibertadores", True),
    "sudamericana":     ("copasudamericana", True),

    # --- Ligas con tabla contaminada ---
    "mexico":           ("mexico", False),
    "mls":              ("usa", False),
    "norway":           ("norway", False),
    "sweden":           ("sweden", False),
    "denmark":          ("denmark", False),
    "china":            ("china", False),
    "estonia":          ("estonia", False),
    "iceland":          ("iceland", False),
    "bolivia":          ("bolivia", False),

    # --- Ligas nuevas (refresco) ---
    "czechrepublic":    ("czechrepublic", False),
    "turkey":           ("turkey", False),
    "ukraine":          ("ukraine", False),
    "scotland":         ("scotland", False),
    "finland":          ("finland", False),
    "japan":            ("japan", False),
    "switzerland":      ("switzerland", False),
    "netherlands2":     ("netherlands2", False),
    "portugal2":        ("portugal2", False),
    "germany2":         ("germany2", False),

    # --- Validadas en agosto 2026 (ambas nivel ALTA) ---
    "portugal":         ("portugal", False),
    "netherlands":      ("netherlands", False),
}

sesion = requests.Session()


def bajar(url):
    try:
        r = sesion.get(url, timeout=20)
        if r.status_code == 200:
            return BeautifulSoup(r.content, "html.parser")
        return None
    except Exception:
        return None


def horas_a_restar(mes, dia):
    """
    SoccerStats publica los horarios en hora de Londres.
    Londres usa horario de verano (UTC+1) desde el último domingo de
    marzo hasta el último domingo de octubre; el resto del año va en UTC.
    Bolivia está siempre en UTC-4.
        Verano de Londres  -> restar 5 horas
        Invierno de Londres -> restar 4 horas
    """
    if 4 <= mes <= 9:
        return 5          # abril a septiembre: seguro es verano
    if mes in (11, 12, 1, 2):
        return 4          # noviembre a febrero: seguro es invierno
    if mes == 3:
        return 5 if dia >= 25 else 4    # cambia el último domingo
    if mes == 10:
        return 5 if dia < 25 else 4
    return 5


def ajustar_hora(fecha_txt, hora_txt):
    try:
        partes = fecha_txt.split()
        dia = int(partes[1])
        mes = MESES.get(partes[2][:3], 1)
        h, m = map(int, hora_txt.split(":"))
        anio = datetime.now().year
        if mes < datetime.now().month - 6:
            anio += 1
        dt = datetime(anio, mes, dia, h, m) - timedelta(hours=horas_a_restar(mes, dia))
        return f"{DIAS_INV[dt.weekday()]} {dt.day} {MESES_INV[dt.month]}", dt.strftime("%H:%M")
    except Exception:
        return fecha_txt, hora_txt


def extraer_posiciones(soup):
    """Devuelve solo la tabla de posiciones real, sin filas basura."""
    basura = ("points", "matches", "segments", "latest", "offence", "min.",
              "scored first", "average", "total", "home team", "away team", "pt")
    mejor = []

    for tabla in soup.find_all("table"):
        filas = tabla.find_all("tr")
        if len(filas) < 6:
            continue
        cabecera = " ".join(c.get_text(" ", strip=True).lower()
                            for c in filas[0].find_all(["td", "th"]))
        if not any(k in cabecera for k in ("pts", "points")):
            continue

        candidata = []
        for fila in filas[1:]:
            celdas = [c.get_text(" ", strip=True) for c in fila.find_all(["td", "th"])]
            if len(celdas) < 8:
                continue
            club = celdas[1].strip() if len(celdas) > 1 else ""
            cl = club.lower()
            if not club or club.replace(".", "").isdigit():
                continue
            if not any(ch.isalpha() for ch in club):
                continue
            if any(b in cl for b in basura):
                continue
            if len(club) > 35:
                continue
            numeros = sum(1 for c in celdas[2:]
                          if c.replace("-", "").replace("+", "").isdigit())
            if numeros < 5:
                continue
            candidata.append(celdas)

        if 4 <= len(candidata) <= 40:
            return candidata
        if len(candidata) > len(mejor):
            mejor = candidata
    return mejor


def clasificar_fila(celdas):
    idx_marc = idx_hora = None
    ht = None
    for i, c in enumerate(celdas):
        t = c.strip()
        if RE_HT.match(t):
            m = RE_HT.match(t)
            ht = f"{m.group(1)}-{m.group(2)}"
        elif RE_MARCADOR.match(t) and idx_marc is None:
            idx_marc = i
        elif RE_MARCADOR_GUION.match(t) and idx_marc is None:
            # SoccerStats usa "1:0" en unas paginas y "1-0" en otras
            idx_marc = i
        elif RE_HORA.match(t) and idx_hora is None:
            idx_hora = i

    if idx_marc is not None:
        if idx_marc < 1 or idx_marc + 1 >= len(celdas):
            return None, None
        celda = celdas[idx_marc].strip()
        m = RE_MARCADOR.match(celda) or RE_MARCADOR_GUION.match(celda)
        gh, ga = int(m.group(1)), int(m.group(2))
        if gh > 15 or ga > 15:
            return None, None
        local, visita = celdas[idx_marc - 1].strip(), celdas[idx_marc + 1].strip()
        if not local or not visita or local == visita:
            return None, None
        return "resultado", {"local": local, "visita": visita, "gh": gh, "ga": ga, "ht": ht or "-"}

    if idx_hora is not None:
        if idx_hora < 1 or idx_hora + 1 >= len(celdas):
            return None, None
        local, visita = celdas[idx_hora - 1].strip(), celdas[idx_hora + 1].strip()
        if not local or not visita or local == visita:
            return None, None
        return "fixture", {"local": local, "visita": visita, "hora": celdas[idx_hora].strip()}

    return None, None


def procesar_resultados(soup, data, vistos):
    fecha_actual = "Próximamente"
    for fila in soup.find_all("tr"):
        celdas = [c.get_text(" ", strip=True) for c in fila.find_all(["td", "th"])]
        if len(celdas) < 3:
            continue
        for c in celdas[:2]:
            if RE_FECHA.match(c.strip()):
                fecha_actual = c.strip()
                break

        tipo, d = clasificar_fila(celdas)
        if tipo is None:
            continue

        if tipo == "fixture":
            clave = ("F", fecha_actual, d["local"], d["visita"])
            if clave in vistos:
                continue
            vistos.add(clave)
            f_bo, h_bo = ajustar_hora(fecha_actual, d["hora"])
            data["fixture"].append({"Fecha": f_bo, "Hora": h_bo,
                                    "Local": d["local"], "Visita": d["visita"]})
        else:
            clave = ("R", fecha_actual, d["local"], d["visita"])
            if clave in vistos:
                continue
            vistos.add(clave)
            gh, ga = d["gh"], d["ga"]
            res = f"[{gh}:{ga}]"
            st_h = "🟢" if gh > ga else ("🟡" if gh == ga else "🔴")
            st_a = "🟢" if ga > gh else ("🟡" if gh == ga else "🔴")
            for equipo, status in ((d["local"], st_h), (d["visita"], st_a)):
                data["estadisticas_avanzadas"].setdefault(equipo, {"historial": []})
                data["estadisticas_avanzadas"][equipo]["historial"].append({
                    "Fecha": fecha_actual, "Res": res,
                    "Local": d["local"], "Visita": d["visita"],
                    "HT": d["ht"], "Status": status,
                })


def paginas_extra(soup, codigo):
    patron = re.compile(r"league=" + re.escape(codigo) + r"(?:&|$)")
    urls = set()
    for a in soup.find_all("a"):
        href = a.get("href", "")
        if "results.asp" in href and patron.search(href):
            urls.add(href if href.startswith("http") else f"{BASE}/{href.lstrip('/')}")
    return urls


def scrapear(codigo, es_copa):
    data = {"posiciones": [], "goles": [], "corners": {},
            "fixture": [], "estadisticas_avanzadas": {}}

    url_pos = (f"{BASE}/leagueview.asp?league={codigo}" if es_copa
               else f"{BASE}/latest.asp?league={codigo}")
    soup = bajar(url_pos)
    if soup:
        data["posiciones"] = extraer_posiciones(soup)
    time.sleep(PAUSA)

    url_res = f"{BASE}/results.asp?league={codigo}"
    soup_res = bajar(url_res)
    if not soup_res:
        return data

    vistos = set()
    procesar_resultados(soup_res, data, vistos)

    for url in list(paginas_extra(soup_res, codigo) - {url_res})[:MAX_PAGINAS_EXTRA]:
        time.sleep(PAUSA)
        s = bajar(url)
        if s:
            procesar_resultados(s, data, vistos)

    return data


def calidad(data):
    """Puntaje simple para comparar versiones de un mismo archivo."""
    if not data:
        return (0, 0, 0, 0)
    adv = data.get("estadisticas_avanzadas", {})
    partidos = sum(len(v.get("historial", [])) for v in adv.values()) // 2
    return (partidos, len(adv), len(data.get("fixture", [])),
            len(data.get("posiciones", [])))


def cargar_existente(nombre):
    ruta = os.path.join(CARPETA, f"{nombre}.json")
    if not os.path.exists(ruta):
        return None
    try:
        with open(ruta, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def guardar(nombre, data):
    os.makedirs(CARPETA, exist_ok=True)
    with open(os.path.join(CARPETA, f"{nombre}.json"), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)


def hacer_backup():
    if os.path.isdir(CARPETA) and not os.path.isdir(BACKUP):
        shutil.copytree(CARPETA, BACKUP)
        print(f"📦 Copia de seguridad creada en {BACKUP}/")


def esta_rota(data):
    if not data:
        return True
    adv = data.get("estadisticas_avanzadas", {})
    partidos = sum(len(v.get("historial", [])) for v in adv.values()) // 2
    return partidos < 5


def main():
    solo_rotas = "--solo-rotas" in sys.argv
    forzar = "--forzar" in sys.argv

    print("=" * 74)
    print("ACTUALIZADOR GENERAL DE LIGAS")
    print("Modo:", "SOLO ROTAS" if solo_rotas else "TODAS",
          "· FORZAR SOBRESCRITURA" if forzar else "· protección activada")
    print("=" * 74)

    hacer_backup()

    resumen = []
    for nombre, (codigo, es_copa) in LIGAS.items():
        viejo = cargar_existente(nombre)

        if solo_rotas and not esta_rota(viejo):
            resumen.append((nombre, "saltada", calidad(viejo), calidad(viejo)))
            continue

        tipo = "copa" if es_copa else "liga"
        print(f"\n[{tipo}] {nombre}  ({codigo})")

        nuevo = scrapear(codigo, es_copa)
        q_viejo, q_nuevo = calidad(viejo), calidad(nuevo)

        print(f"      antes: {q_viejo[0]} partidos / {q_viejo[3]} en tabla / {q_viejo[2]} fixture")
        print(f"      ahora: {q_nuevo[0]} partidos / {q_nuevo[3]} en tabla / {q_nuevo[2]} fixture")

        # Se guarda solo si no se pierde historial NI la tabla de posiciones
        pierde_partidos = q_nuevo[0] < q_viejo[0]
        pierde_tabla = q_viejo[3] >= 8 and q_nuevo[3] < 8
        if forzar or viejo is None or not (pierde_partidos or pierde_tabla):
            guardar(nombre, nuevo)
            accion = "actualizada"
            print("      ✅ Guardada")
        else:
            accion = "CONSERVADA (lo nuevo era peor)"
            print("      ⚠️  No se sobrescribió: la versión anterior tenía más datos")

        resumen.append((nombre, accion, q_viejo, q_nuevo))
        time.sleep(PAUSA)

    print("\n" + "=" * 74)
    print("RESUMEN")
    print("=" * 74)
    print(f"{'Liga':<16}{'Antes':<22}{'Ahora':<22}{'Resultado'}")
    for nombre, accion, qv, qn in resumen:
        antes = f"{qv[0]}p/{qv[3]}t/{qv[2]}f"
        ahora = f"{qn[0]}p/{qn[3]}t/{qn[2]}f"
        print(f"{nombre:<16}{antes:<22}{ahora:<22}{accion}")

    mejoradas = [r[0] for r in resumen if r[1] == "actualizada" and r[3][0] > r[2][0]]
    conservadas = [r[0] for r in resumen if "CONSERVADA" in r[1]]

    print(f"\nMejoraron: {len(mejoradas)}")
    if mejoradas:
        print(f"  {', '.join(mejoradas)}")
    if conservadas:
        print(f"\nNo se tocaron (lo nuevo era peor): {', '.join(conservadas)}")
        print("  Puede ser un código de liga equivocado o una caída del sitio.")

    print(f"\nSi algo salió mal, tu copia original está en {BACKUP}/")


if __name__ == "__main__":
    main()
