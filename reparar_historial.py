"""
REPARADOR DE HISTORIAL
=======================
Trabaja sobre historial_descargado.csv (el que ya bajaste).
NO vuelve a scrapear nada: repara y valida lo que ya está.

Corrige:
  1. HORARIOS confundidos con marcadores.
     "0:30", "12:00" son horas de inicio, no resultados. Se detectan
     porque los minutos tienen 2 dígitos.
  2. AÑOS mal asignados en ligas de año calendario.
     Suecia, Noruega, MLS, China, Japón, etc. juegan de marzo a noviembre
     dentro del mismo año. La detección automática falló en algunas
     temporadas, así que ahora el tipo de liga está fijado a mano.
  3. Fechas futuras y duplicados.

Modos:
  python reparar_historial.py             -> revisa y reporta (no toca la BD)
  python reparar_historial.py --guardar   -> inserta en la base de datos
"""

import os
import sys
import sqlite3
from datetime import date

import pandas as pd

CSV_ENTRADA = "historial_descargado.csv"
CSV_LIMPIO = "historial_limpio.csv"
TXT_REPORTE = "reparacion_reporte.txt"
DB_PATH = "database/football_data.db"

HOY = date.today()

# =========================================================
# LIGAS DE AÑO CALENDARIO (juegan de marzo/febrero a noviembre,
# dentro del mismo año). El resto se asume temporada europea
# que cruza el año (agosto -> mayo).
# =========================================================
LIGAS_CALENDARIO = {
    "mls", "sweden", "norway", "finland", "iceland",
    "estonia", "china", "japan", "southkorea",
    "brazil", "serie_b_brasil", "argentina", "primera_nacional",
    "bolivia",
}

MAX_GOLES_VALIDO = 12


def es_horario(gl, gv, fila):
    """
    Un horario tiene minutos de 2 dígitos (0:30, 12:00, 19:45).
    Un marcador real casi nunca pasa de 9 goles por lado.
    """
    # Si alguno marca 13+ es imposible
    if gv > MAX_GOLES_VALIDO or gl > MAX_GOLES_VALIDO:
        return True
    # Minutos típicos de inicio de partido (16:00 -> 16:0)
    if gv in (0, 15, 30, 45) and gl >= 10:
        return True
    # Sin HT y con marcador alto: sospechoso
    if gl >= 10 and pd.isna(fila.get("ht_local")):
        return True
    # Horarios tempranos (8:00, 9:00) que pasarían el filtro de 12 goles.
    # Las ligas asiáticas en hora británica caen en ese rango.
    # Un 8-0 o 9-0 real casi siempre trae medio tiempo (el 98% lo tiene).
    if gv == 0 and gl >= 8 and pd.isna(fila.get("ht_local")):
        return True
    return False


def main():
    guardar = "--guardar" in sys.argv

    if not os.path.exists(CSV_ENTRADA):
        print(f"❌ No se encontró {CSV_ENTRADA}")
        print("   Corré primero: python scraper_historial.py")
        return

    df = pd.read_csv(CSV_ENTRADA)
    total_inicial = len(df)
    print(f"Cargados {total_inicial} partidos de {CSV_ENTRADA}")

    L = []
    L.append("=" * 78)
    L.append("REPORTE DE REPARACIÓN DE HISTORIAL")
    L.append("=" * 78)
    L.append(f"Partidos en el CSV original: {total_inicial}")
    L.append("")

    # ---------- 1. Filtrar horarios ----------
    mask_horario = df.apply(
        lambda r: es_horario(int(r["goles_local"]), int(r["goles_visita"]), r), axis=1
    )
    n_horarios = int(mask_horario.sum())
    if n_horarios:
        L.append(f"Descartados por parecer HORARIO y no marcador: {n_horarios}")
        for r in df[mask_horario].head(10).itertuples(index=False):
            L.append(f"   {r.liga} · {r.fecha_txt} · {r.local} "
                     f"{r.goles_local}:{r.goles_visita} {r.visita}")
    df = df[~mask_horario].copy()

    # ---------- 2. Recalcular años ----------
    L.append("")
    L.append("-" * 78)
    L.append("RECÁLCULO DE AÑOS")
    L.append("-" * 78)

    cambios = 0
    filas_ok = []

    for r in df.itertuples(index=False):
        liga = r.liga
        etiqueta = str(r.temporada)
        mes, dia = int(r.mes), int(r.dia)

        es_calendario = liga in LIGAS_CALENDARIO

        if etiqueta in ("actual", "nan", ""):
            anio_base = HOY.year
            if es_calendario:
                anio = anio_base
            else:
                if HOY.month >= 7:
                    anio = HOY.year if mes >= 7 else HOY.year + 1
                else:
                    anio = HOY.year - 1 if mes >= 7 else HOY.year
        else:
            anio_etq = int(str(etiqueta).replace("_", ""))
            if es_calendario:
                anio = anio_etq
            else:
                # Julio ya es arranque de temporada en varias ligas
                # (Polonia, Chequia, Suiza, Alemania 2, México)
                anio = anio_etq - 1 if mes >= 7 else anio_etq

        try:
            f = date(anio, mes, dia)
        except ValueError:
            continue

        if f > HOY:
            continue

        nueva_iso = f.strftime("%Y-%m-%d")
        if nueva_iso != str(r.fecha_iso):
            cambios += 1

        filas_ok.append({
            "fecha_iso": nueva_iso,
            "liga": liga,
            "temporada": etiqueta,
            "local": r.local,
            "visita": r.visita,
            "goles_local": int(r.goles_local),
            "goles_visita": int(r.goles_visita),
            "ht_local": r.ht_local if pd.notna(r.ht_local) else None,
            "ht_visita": r.ht_visita if pd.notna(r.ht_visita) else None,
            "tipo": "calendario" if es_calendario else "cruza-año",
        })

    limpio = pd.DataFrame(filas_ok)
    L.append(f"Fechas corregidas: {cambios}")
    L.append(f"Partidos tras filtrar fechas inválidas/futuras: {len(limpio)}")

    # ---------- 3. Duplicados ----------
    antes = len(limpio)
    limpio["_k"] = (limpio["fecha_iso"] + "|" +
                    limpio["local"].str.lower().str.strip() + "|" +
                    limpio["visita"].str.lower().str.strip())
    limpio = limpio.drop_duplicates(subset="_k").drop(columns="_k")
    L.append(f"Duplicados eliminados: {antes - len(limpio)}")

    # ---------- Reporte por liga ----------
    L.append("")
    L.append("-" * 78)
    L.append("RESULTADO POR LIGA")
    L.append("-" * 78)
    L.append(f"{'Liga':<18}{'Tipo':<14}{'Partidos':<11}{'Con HT':<9}{'Desde':<12}{'Hasta':<12}")

    apto_modelo = []
    for liga, sub in limpio.groupby("liga"):
        tipo = sub["tipo"].iloc[0]
        con_ht = int(sub["ht_local"].notna().sum())
        L.append(f"{liga:<18}{tipo:<14}{len(sub):<11}{con_ht:<9}"
                 f"{sub['fecha_iso'].min():<12}{sub['fecha_iso'].max():<12}")
        if len(sub) >= 200:
            apto_modelo.append((liga, len(sub)))

    L.append("")
    L.append("-" * 78)
    L.append("LIGAS CON MUESTRA SUFICIENTE PARA ENTRENAR (>= 200 partidos)")
    L.append("-" * 78)
    for liga, n in sorted(apto_modelo, key=lambda x: -x[1]):
        L.append(f"  {liga:<18} {n:>5} partidos")
    L.append("")
    L.append(f"Total de ligas aptas: {len(apto_modelo)} de {limpio['liga'].nunique()}")

    # ---------- Verificación de coherencia ----------
    L.append("")
    L.append("-" * 78)
    L.append("VERIFICACIÓN DE COHERENCIA (meses en que juega cada liga)")
    L.append("-" * 78)
    limpio["_mes"] = pd.to_datetime(limpio["fecha_iso"]).dt.month
    for liga, sub in limpio.groupby("liga"):
        meses = sorted(int(m) for m in sub["_mes"].unique())
        tipo = sub["tipo"].iloc[0]
        alerta = ""
        if tipo == "calendario" and 1 in meses:
            alerta = "  ⚠️ revisar: enero en liga de calendario"
        if tipo == "cruza-año" and 6 in meses and 7 in meses and 8 in meses:
            alerta = "  ⚠️ revisar: parece de año calendario"
        L.append(f"  {liga:<18} {tipo:<12} meses: {meses}{alerta}")
    limpio = limpio.drop(columns="_mes")

    L.append("")
    L.append("MUESTRA (12 al azar):")
    for r in limpio.sample(min(12, len(limpio))).itertuples(index=False):
        ht = f"HT {int(r.ht_local)}-{int(r.ht_visita)}" if pd.notna(r.ht_local) else "sin HT"
        L.append(f"  {r.fecha_iso} [{r.liga}] {r.local} "
                 f"{r.goles_local}:{r.goles_visita} {r.visita} ({ht})")

    limpio.to_csv(CSV_LIMPIO, index=False, encoding="utf-8")
    with open(TXT_REPORTE, "w", encoding="utf-8") as f:
        f.write("\n".join(L))

    print(f"✅ CSV limpio: {CSV_LIMPIO} ({len(limpio)} partidos)")
    print(f"✅ Reporte: {TXT_REPORTE}")

    if not guardar:
        print("\n⚠️  MODO REVISIÓN: no se guardó nada en la base de datos.")
        print("   Si el reporte está bien: python reparar_historial.py --guardar")
        return

    # ---------- Guardar en la BD ----------
    if not os.path.exists(DB_PATH):
        print(f"❌ No se encontró {DB_PATH}")
        return

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    cur.execute("PRAGMA table_info(partidos)")
    cols = [c[1] for c in cur.fetchall()]
    if "liga" not in cols:
        print("Agregando columna 'liga' a partidos...")
        cur.execute("ALTER TABLE partidos ADD COLUMN liga TEXT DEFAULT ''")
        conn.commit()

    cur.execute("SELECT fecha, equipo_local, equipo_visita FROM partidos")
    existentes = {(str(f)[:10], str(l).strip().lower(), str(v).strip().lower())
                  for f, l, v in cur.fetchall()}

    nuevos, saltados = 0, 0
    for r in limpio.itertuples(index=False):
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
        """, (r.fecha_iso, r.local, r.visita,
              int(r.goles_local), int(r.goles_visita),
              int(r.ht_local) if pd.notna(r.ht_local) else None,
              int(r.ht_visita) if pd.notna(r.ht_visita) else None,
              r.liga))
        existentes.add(clave)
        nuevos += 1

    conn.commit()

    cur.execute("SELECT COUNT(*) FROM partidos")
    total = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM partidos WHERE ht_goles_local IS NOT NULL")
    con_ht = cur.fetchone()[0]
    conn.close()

    print(f"\n✅ Insertados: {nuevos} · Ya existían: {saltados}")
    print(f"   La tabla 'partidos' ahora tiene {total} filas")
    print(f"   Con medio tiempo (HT): {con_ht}")


if __name__ == "__main__":
    main()
