"""
LIMPIEZA DE BASE DE DATOS
==========================
Ordena la base sin perder información.

Qué hace:
  1. BACKUP completo de la base antes de tocar nada.
  2. Normaliza las fechas viejas de 'partidos' a formato ISO (YYYY-MM-DD).
     Hoy conviven DD/MM/YYYY e ISO con hora, lo que obliga a adivinar
     el formato en cada consulta.
  3. Completa la columna 'liga' en los partidos que la tienen vacía,
     cruzando los nombres de equipo contra los data_json.
  4. Archiva las tablas muertas (las renombra con prefijo zz_archivo_,
     NO las borra) para que no confundan:
       historial_apuestas, capital_usuario, competitions,
       teams, seasons, matches
  5. Crea índices para que las consultas sean más rápidas.

Modos:
    python limpiar_base.py            -> revisa y reporta, no toca nada
    python limpiar_base.py --aplicar  -> ejecuta los cambios
"""

import os
import re
import sys
import shutil
import sqlite3
from datetime import datetime

DB_PATH = "database/football_data.db"
BACKUP_DIR = "database/backups"

TABLAS_MUERTAS = [
    "historial_apuestas",
    "capital_usuario",
    "competitions",
    "teams",
    "seasons",
    "matches",
]


def hacer_backup():
    os.makedirs(BACKUP_DIR, exist_ok=True)
    sello = datetime.now().strftime("%Y%m%d_%H%M%S")
    destino = os.path.join(BACKUP_DIR, f"football_data_{sello}.db")
    shutil.copy2(DB_PATH, destino)
    tam = os.path.getsize(destino) / (1024 * 1024)
    print(f"📦 Backup creado: {destino}  ({tam:.1f} MB)")
    return destino


def normalizar_fecha(valor):
    """Devuelve YYYY-MM-DD, o None si no se puede interpretar."""
    if valor is None:
        return None
    t = str(valor).strip()
    if not t:
        return None

    # Ya está en ISO
    if re.match(r"^\d{4}-\d{2}-\d{2}$", t):
        return t

    # ISO con hora / zona horaria
    m = re.match(r"^(\d{4}-\d{2}-\d{2})T", t)
    if m:
        return m.group(1)

    # DD/MM/YYYY
    m = re.match(r"^(\d{2})/(\d{2})/(\d{4})$", t)
    if m:
        d, mes, a = m.groups()
        try:
            datetime(int(a), int(mes), int(d))
            return f"{a}-{mes}-{d}"
        except ValueError:
            return None

    # DD-MM-YYYY
    m = re.match(r"^(\d{2})-(\d{2})-(\d{4})$", t)
    if m:
        d, mes, a = m.groups()
        try:
            datetime(int(a), int(mes), int(d))
            return f"{a}-{mes}-{d}"
        except ValueError:
            return None

    return None


def analizar(conn):
    cur = conn.cursor()
    print("\n" + "=" * 68)
    print("ANÁLISIS")
    print("=" * 68)

    cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tablas = [r[0] for r in cur.fetchall()]

    # --- Tablas muertas ---
    print("\n1. TABLAS A ARCHIVAR")
    presentes = []
    for t in TABLAS_MUERTAS:
        if t not in tablas:
            continue
        cur.execute(f"SELECT COUNT(*) FROM {t}")
        n = cur.fetchone()[0]
        print(f"   {t:<22} {n:>6} filas")
        presentes.append((t, n))
    if not presentes:
        print("   (ninguna presente)")

    # --- Fechas ---
    print("\n2. FORMATOS DE FECHA EN 'partidos'")
    cur.execute("SELECT fecha FROM partidos")
    fechas = [r[0] for r in cur.fetchall()]
    formatos = {"ISO": 0, "ISO_con_hora": 0, "DD/MM/YYYY": 0, "DD-MM-YYYY": 0, "OTRO": 0}
    a_convertir = 0
    for f in fechas:
        t = str(f).strip() if f else ""
        if re.match(r"^\d{4}-\d{2}-\d{2}$", t):
            formatos["ISO"] += 1
        elif re.match(r"^\d{4}-\d{2}-\d{2}T", t):
            formatos["ISO_con_hora"] += 1
            a_convertir += 1
        elif re.match(r"^\d{2}/\d{2}/\d{4}$", t):
            formatos["DD/MM/YYYY"] += 1
            a_convertir += 1
        elif re.match(r"^\d{2}-\d{2}-\d{4}$", t):
            formatos["DD-MM-YYYY"] += 1
            a_convertir += 1
        else:
            formatos["OTRO"] += 1
    for k, v in formatos.items():
        if v:
            print(f"   {k:<16} {v:>7}")
    print(f"   → A normalizar: {a_convertir}")

    # --- Liga vacía ---
    print("\n3. COLUMNA 'liga'")
    cur.execute("PRAGMA table_info(partidos)")
    cols = [c[1] for c in cur.fetchall()]
    if "liga" not in cols:
        print("   ⚠️ No existe la columna 'liga'")
    else:
        cur.execute("SELECT COUNT(*) FROM partidos WHERE liga IS NULL OR liga = ''")
        vacias = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM partidos")
        total = cur.fetchone()[0]
        print(f"   Con liga: {total - vacias} · Sin liga: {vacias}")

    # --- Índices ---
    print("\n4. ÍNDICES")
    cur.execute("SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='partidos'")
    idx = [r[0] for r in cur.fetchall() if not r[0].startswith("sqlite_")]
    print(f"   Existentes en 'partidos': {idx if idx else 'ninguno'}")

    return presentes, a_convertir


def aplicar(conn):
    cur = conn.cursor()
    print("\n" + "=" * 68)
    print("APLICANDO CAMBIOS")
    print("=" * 68)

    # --- 1. Normalizar fechas ---
    print("\n[1/4] Normalizando fechas...")
    cur.execute("SELECT id_partido, fecha FROM partidos")
    filas = cur.fetchall()
    cambios, fallos = 0, []
    for pid, fecha in filas:
        nueva = normalizar_fecha(fecha)
        if nueva is None:
            if fecha:
                fallos.append((pid, fecha))
            continue
        if nueva != str(fecha).strip():
            cur.execute("UPDATE partidos SET fecha = ? WHERE id_partido = ?", (nueva, pid))
            cambios += 1
    conn.commit()
    print(f"      Fechas normalizadas: {cambios}")
    if fallos:
        print(f"      ⚠️ No se pudieron interpretar: {len(fallos)}")
        for pid, f in fallos[:5]:
            print(f"         id {pid}: '{f}'")

    # --- 2. Completar liga ---
    print("\n[2/4] Completando la columna 'liga'...")
    mapa = {}
    if os.path.isdir("data_json"):
        import json
        import unicodedata

        def norm(t):
            n = unicodedata.normalize("NFKD", str(t))
            return "".join(c for c in n if not unicodedata.combining(c)).lower().strip()

        for archivo in os.listdir("data_json"):
            if not archivo.endswith(".json"):
                continue
            liga = archivo[:-5]
            try:
                with open(f"data_json/{archivo}", "r", encoding="utf-8") as f:
                    data = json.load(f)
                for fila in data.get("posiciones", []):
                    if isinstance(fila, list) and len(fila) > 1:
                        eq = norm(fila[1])
                        if eq and eq not in mapa:
                            mapa[eq] = liga
            except Exception:
                continue

        cur.execute("""
            SELECT id_partido, equipo_local, equipo_visita FROM partidos
            WHERE liga IS NULL OR liga = ''
        """)
        pendientes = cur.fetchall()
        completadas = 0
        for pid, l, v in pendientes:
            liga = mapa.get(norm(l)) or mapa.get(norm(v))
            if liga:
                cur.execute("UPDATE partidos SET liga = ? WHERE id_partido = ?", (liga, pid))
                completadas += 1
        conn.commit()
        print(f"      Partidos etiquetados: {completadas} de {len(pendientes)}")
    else:
        print("      (no existe data_json/, se omite)")

    # --- 3. Archivar tablas muertas ---
    print("\n[3/4] Archivando tablas sin uso...")
    cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tablas = [r[0] for r in cur.fetchall()]
    for t in TABLAS_MUERTAS:
        if t not in tablas:
            continue
        nuevo = f"zz_archivo_{t}"
        if nuevo in tablas:
            print(f"      {t}: ya archivada")
            continue
        try:
            cur.execute(f"ALTER TABLE {t} RENAME TO {nuevo}")
            print(f"      {t} → {nuevo}")
        except Exception as e:
            print(f"      ⚠️ {t}: {e}")
    conn.commit()

    # --- 4. Índices ---
    print("\n[4/4] Creando índices...")
    indices = [
        ("idx_partidos_fecha", "partidos(fecha)"),
        ("idx_partidos_liga", "partidos(liga)"),
        ("idx_partidos_equipos", "partidos(equipo_local, equipo_visita)"),
        ("idx_apuestas_billetera", "mis_apuestas(en_billetera)"),
    ]
    for nombre, definicion in indices:
        try:
            cur.execute(f"CREATE INDEX IF NOT EXISTS {nombre} ON {definicion}")
            print(f"      ✓ {nombre}")
        except Exception as e:
            print(f"      ⚠️ {nombre}: {e}")
    conn.commit()

    print("\n[extra] Compactando la base (VACUUM)...")
    try:
        conn.isolation_level = None
        conn.execute("VACUUM")
        print("      ✓ Listo")
    except Exception as e:
        print(f"      ⚠️ {e}")


def main():
    if not os.path.exists(DB_PATH):
        print(f"❌ No se encontró {DB_PATH}")
        return

    aplicar_cambios = "--aplicar" in sys.argv
    print("=" * 68)
    print("LIMPIEZA DE BASE DE DATOS")
    print("MODO:", "APLICAR CAMBIOS" if aplicar_cambios else "SOLO REVISIÓN")
    print("=" * 68)

    conn = sqlite3.connect(DB_PATH)
    analizar(conn)

    if not aplicar_cambios:
        conn.close()
        print("\n⚠️  MODO REVISIÓN: no se modificó nada.")
        print("   Para aplicar: python limpiar_base.py --aplicar")
        print("   (se hace un backup automático antes de tocar la base)")
        return

    conn.close()
    hacer_backup()

    conn = sqlite3.connect(DB_PATH)
    aplicar(conn)

    # Verificación final
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM partidos")
    total = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM partidos WHERE fecha LIKE '____-__-__'")
    iso = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM partidos WHERE liga IS NOT NULL AND liga != ''")
    con_liga = cur.fetchone()[0]
    conn.close()

    print("\n" + "=" * 68)
    print("RESULTADO FINAL")
    print("=" * 68)
    print(f"Partidos totales: {total}")
    print(f"Con fecha ISO: {iso} ({iso/total*100:.1f}%)")
    print(f"Con liga: {con_liga} ({con_liga/total*100:.1f}%)")
    print(f"\nBackups guardados en: {BACKUP_DIR}/")


if __name__ == "__main__":
    main()
