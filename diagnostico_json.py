"""
DIAGNÓSTICO DE ARCHIVOS data_json
==================================
Revisa dos sospechas:

  1. El fixture de México podría estar mezclando Liga MX con Liga de
     Expansión (apareció "Atlante vs Toluca", y Atlante no juega en
     Primera División).

  2. Las 5 ligas grandes (Brasil, Inglaterra, España, Italia, Francia)
     podrían tener "estadisticas_avanzadas" vacío, por el mismo bug de
     parseo que encontramos: SoccerStats muestra el marcador como "1:0"
     y el scraper viejo lo interpretaba como hora de inicio.
     Si están vacías, el Semáforo de Fortaleza, el Radar HT y el
     Termómetro de Tendencias no muestran nada real en esas ligas.

NO modifica nada. Solo mira y reporta.

Uso:
    python diagnostico_json.py

Genera: diagnostico_json.txt
"""

import os
import json
from datetime import datetime

CARPETA = "data_json"
SALIDA = "diagnostico_json.txt"


def cargar(nombre):
    ruta = os.path.join(CARPETA, f"{nombre}.json")
    if not os.path.exists(ruta):
        return None
    try:
        with open(ruta, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        return {"_error": str(e)}


def antiguedad(nombre):
    ruta = os.path.join(CARPETA, f"{nombre}.json")
    if not os.path.exists(ruta):
        return "-"
    ts = os.path.getmtime(ruta)
    dias = (datetime.now() - datetime.fromtimestamp(ts)).days
    return f"{datetime.fromtimestamp(ts).strftime('%Y-%m-%d')} ({dias}d)"


def main():
    L = []
    L.append("=" * 78)
    L.append("DIAGNÓSTICO DE ARCHIVOS data_json")
    L.append("=" * 78)

    if not os.path.isdir(CARPETA):
        L.append(f"❌ No existe la carpeta {CARPETA}")
        print("\n".join(L))
        return

    archivos = sorted(f[:-5] for f in os.listdir(CARPETA) if f.endswith(".json"))
    L.append(f"Archivos encontrados: {len(archivos)}")
    L.append("")

    # =====================================================
    # 1. PANORAMA GENERAL
    # =====================================================
    L.append("-" * 78)
    L.append("PANORAMA GENERAL")
    L.append("-" * 78)
    L.append(f"{'Archivo':<18}{'Posic.':<9}{'Fixture':<10}{'Equipos':<10}{'Partidos':<11}{'Actualizado':<18}{'Estado'}")

    vacias = []
    for nombre in archivos:
        d = cargar(nombre)
        if d is None or "_error" in d:
            L.append(f"{nombre:<18}ERROR al leer")
            continue

        pos = len(d.get("posiciones", []))
        fix = len(d.get("fixture", []))
        adv = d.get("estadisticas_avanzadas", {})
        eq = len(adv)
        part = sum(len(v.get("historial", [])) for v in adv.values()) // 2 if eq else 0

        if eq == 0:
            estado = "❌ SIN HISTORIAL"
            vacias.append(nombre)
        elif part < 20:
            estado = "⚠️ historial escaso"
        else:
            estado = "OK"

        L.append(f"{nombre:<18}{pos:<9}{fix:<10}{eq:<10}{part:<11}{antiguedad(nombre):<18}{estado}")

    # =====================================================
    # 2. SOSPECHA A: ligas grandes con historial vacío
    # =====================================================
    L.append("")
    L.append("-" * 78)
    L.append("SOSPECHA A — Ligas grandes sin historial (bug de parseo '1:0')")
    L.append("-" * 78)

    grandes = ["brazil", "england", "spain", "italy", "france",
               "argentina", "primera_nacional", "serie_b_brasil",
               "libertadores", "sudamericana", "champions", "europa"]

    afectadas = []
    for nombre in grandes:
        d = cargar(nombre)
        if d is None:
            L.append(f"  {nombre:<20} (archivo no existe)")
            continue
        adv = d.get("estadisticas_avanzadas", {})
        eq = len(adv)
        part = sum(len(v.get("historial", [])) for v in adv.values()) // 2 if eq else 0

        # Revisar si el fixture tiene partidos ya jugados mal clasificados
        raros = 0
        for p in d.get("fixture", []):
            hora = str(p.get("Hora", ""))
            if ":" in hora:
                mm = hora.split(":")[-1]
                if len(mm) == 1:   # "1:0" -> marcador disfrazado de hora
                    raros += 1

        marca = "❌" if eq == 0 else ("⚠️" if part < 20 else "✅")
        extra = f" · {raros} entradas del fixture parecen marcadores" if raros else ""
        L.append(f"  {marca} {nombre:<20} equipos: {eq:<5} partidos: {part:<6}{extra}")
        if eq == 0 or raros > 0:
            afectadas.append(nombre)

    if afectadas:
        L.append("")
        L.append(f"  → Afectadas por el bug: {', '.join(afectadas)}")
        L.append("     En esas ligas, el Semáforo de Fortaleza, el Radar de Medio")
        L.append("     Tiempo y el Termómetro de Tendencias no tienen datos reales.")
    else:
        L.append("")
        L.append("  → Ninguna liga grande parece afectada.")

    # =====================================================
    # 3. SOSPECHA B: México mezclando divisiones
    # =====================================================
    L.append("")
    L.append("-" * 78)
    L.append("SOSPECHA B — Fixture de México con equipos de otra división")
    L.append("-" * 78)

    d_mex = cargar("mexico")
    if not d_mex:
        L.append("  (no existe data_json/mexico.json)")
    else:
        # Equipos que aparecen en la tabla de posiciones = Liga MX real
        equipos_tabla = set()
        for fila in d_mex.get("posiciones", []):
            if isinstance(fila, list) and len(fila) > 1:
                club = str(fila[1]).strip()
                if club and any(c.isalpha() for c in club):
                    equipos_tabla.add(club.lower())

        L.append(f"  Equipos en la tabla de posiciones: {len(equipos_tabla)}")
        if equipos_tabla:
            L.append(f"  {sorted(equipos_tabla)}")

        fuera = []
        for p in d_mex.get("fixture", []):
            for lado in ("Local", "Visita"):
                eq = str(p.get(lado, "")).strip()
                if eq and eq.lower() not in equipos_tabla:
                    fuera.append(eq)

        unicos = sorted(set(fuera))
        L.append("")
        L.append(f"  Equipos del fixture que NO están en la tabla: {len(unicos)}")
        for e in unicos[:30]:
            L.append(f"    · {e}")

        if unicos:
            L.append("")
            L.append("  → Si estos nombres no son de Liga MX, el fixture está")
            L.append("     mezclando divisiones y esos partidos no deberían")
            L.append("     analizarse con el modelo de Liga MX.")
        else:
            L.append("  → Todos los equipos del fixture están en la tabla. Sin mezcla.")

    # =====================================================
    # 4. Chequeo general de mezcla en todas las ligas
    # =====================================================
    L.append("")
    L.append("-" * 78)
    L.append("CHEQUEO GENERAL — equipos del fixture ausentes de la tabla")
    L.append("-" * 78)
    L.append("(Un número alto sugiere mezcla de divisiones o nombres inconsistentes)")
    L.append("")

    for nombre in archivos:
        d = cargar(nombre)
        if not d or "_error" in d:
            continue
        equipos_tabla = set()
        for fila in d.get("posiciones", []):
            if isinstance(fila, list) and len(fila) > 1:
                club = str(fila[1]).strip().lower()
                if club and any(c.isalpha() for c in club):
                    equipos_tabla.add(club)
        if not equipos_tabla:
            continue

        fuera = set()
        total = 0
        for p in d.get("fixture", []):
            for lado in ("Local", "Visita"):
                eq = str(p.get(lado, "")).strip()
                if not eq:
                    continue
                total += 1
                if eq.lower() not in equipos_tabla:
                    fuera.add(eq)

        if total == 0:
            continue
        pct = len(fuera) / max(len(equipos_tabla), 1) * 100
        marca = "❌" if pct > 50 else ("⚠️" if pct > 20 else "✅")
        L.append(f"  {marca} {nombre:<18} tabla: {len(equipos_tabla):<4} "
                 f"ausentes del fixture: {len(fuera)}")

    # =====================================================
    if vacias:
        L.append("")
        L.append("=" * 78)
        L.append("RESUMEN DE ACCIONES SUGERIDAS")
        L.append("=" * 78)
        L.append(f"Ligas sin historial ({len(vacias)}): {', '.join(vacias)}")
        L.append("Hay que volver a scrapearlas con el parseo corregido.")

    with open(SALIDA, "w", encoding="utf-8") as f:
        f.write("\n".join(L))

    print("\n".join(L[:40]))
    print(f"\n... reporte completo en: {SALIDA}")


if __name__ == "__main__":
    main()
