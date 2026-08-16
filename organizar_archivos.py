"""
ORGANIZADOR DE ARCHIVOS DEL PROYECTO
=====================================
La raíz tiene 28 archivos .py y cuesta encontrar los que se usan a diario.
Este script los ordena en carpetas SIN BORRAR NADA.

  archivo/investigacion/   -> scripts que ya cumplieron su función
                              (auditorías, calibradores, inspectores)
  archivo/obsoletos/       -> versiones viejas reemplazadas por otras
  archivo/pruebas/         -> tests y scripts de depuración

Los que se usan quedan en la raíz.

Modos:
    python organizar_archivos.py            -> muestra el plan, no mueve nada
    python organizar_archivos.py --aplicar  -> mueve los archivos
    python organizar_archivos.py --deshacer -> los devuelve a la raíz
"""

import os
import sys
import shutil
import json

BASE_ARCHIVO = "archivo"
REGISTRO = os.path.join(BASE_ARCHIVO, "_movimientos.json")

# ---------------------------------------------------------
# Se QUEDAN en la raíz (uso habitual)
# ---------------------------------------------------------
SE_QUEDAN = {
    # App
    "dashboard.py",
    "motor_v2.py",
    "picks_dia.py",
    "registro_predicciones.py",
    # Entrenamiento
    "entrenar_motor.py",
    "modelo_dixon_coles.py",
    "backtesting_engine.py",
    # Datos del día a día
    "actualizar_todas_ligas.py",
    "scraper_historial.py",
    "reparar_historial.py",
    # Validación (se re-ejecuta cada vez que crece la base)
    "validar_ligas.py",
    # Mantenimiento
    "limpiar_base.py",
    "diagnostico_json.py",
    "organizar_archivos.py",
    # Scripts propios del usuario que puede seguir usando
    "ejecutar_scraping.py",
    "ejecutar_scraping_v2.py",
    "actualizar_todo.py",
}

# ---------------------------------------------------------
# Clasificación de lo que se mueve
# ---------------------------------------------------------
INVESTIGACION = {
    "auditoria_db.py": "Auditoría inicial de la base (ya ejecutada)",
    "auditoria_db_v2.py": "Segunda auditoría: fechas y cobertura",
    "auditoria_rendimiento.py": "Análisis del yield real del usuario",
    "calibrador_pesos.py": "Probó 20 combinaciones de pesos (resultado: no servían)",
    "recalibracion_mercados.py": "Calibración isotónica multi-mercado",
    "modelo_goles_empirico.py": "Modelos empíricos de goles (resultado: sin ventaja)",
    "inspector_soccerstats.py": "Descubrió cómo acceder a SoccerStats",
    "inspector_corners.py": "Confirmó que no hay córners por partido",
    "actualizar_ligas_nuevas.py": "Reemplazado por actualizar_todas_ligas.py",
}

PRUEBAS = {
    "debug_db.py": "Depuración de base",
    "diagnostico.py": "Diagnóstico suelto",
    "test_arg_primera.py": "Prueba de Argentina Primera",
    "test_multi_ligas.py": "Prueba de varias ligas",
}

OBSOLETOS = {
    "descargar_historial.py": "Reemplazado por scraper_historial.py",
}

DESTINOS = [
    ("investigacion", INVESTIGACION),
    ("pruebas", PRUEBAS),
    ("obsoletos", OBSOLETOS),
]


def deshacer():
    if not os.path.exists(REGISTRO):
        print("No hay registro de movimientos. Nada que deshacer.")
        return
    with open(REGISTRO, "r", encoding="utf-8") as f:
        movimientos = json.load(f)

    devueltos = 0
    for origen, destino in movimientos.items():
        if os.path.exists(destino):
            shutil.move(destino, origen)
            print(f"  ← {os.path.basename(origen)}")
            devueltos += 1
    os.remove(REGISTRO)
    print(f"\n✅ Devueltos a la raíz: {devueltos}")


def main():
    if "--deshacer" in sys.argv:
        deshacer()
        return

    aplicar = "--aplicar" in sys.argv

    print("=" * 70)
    print("ORGANIZADOR DE ARCHIVOS")
    print("MODO:", "APLICAR" if aplicar else "SOLO MOSTRAR EL PLAN")
    print("=" * 70)

    en_raiz = {f for f in os.listdir(".") if f.endswith(".py")}

    # --- Los que se quedan ---
    quedan = sorted(en_raiz & SE_QUEDAN)
    print(f"\n📌 SE QUEDAN EN LA RAÍZ ({len(quedan)})")
    for f in quedan:
        print(f"   {f}")

    # --- Los que se mueven ---
    movimientos = {}
    total_mover = 0
    for carpeta, grupo in DESTINOS:
        presentes = {f: d for f, d in grupo.items() if f in en_raiz}
        if not presentes:
            continue
        print(f"\n📦 → archivo/{carpeta}/  ({len(presentes)})")
        for f, desc in sorted(presentes.items()):
            print(f"   {f:<32} {desc}")
            movimientos[os.path.abspath(f)] = os.path.abspath(
                os.path.join(BASE_ARCHIVO, carpeta, f))
            total_mover += 1

    # --- Sin clasificar ---
    clasificados = SE_QUEDAN | set(INVESTIGACION) | set(PRUEBAS) | set(OBSOLETOS)
    sin_clasificar = sorted(en_raiz - clasificados)
    if sin_clasificar:
        print(f"\n❓ SIN CLASIFICAR ({len(sin_clasificar)}) — se quedan donde están")
        for f in sin_clasificar:
            print(f"   {f}")

    print(f"\nResumen: {len(quedan)} se quedan · {total_mover} se mueven · "
          f"{len(sin_clasificar)} sin clasificar")

    if not aplicar:
        print("\n⚠️  No se movió nada.")
        print("   Para aplicar:  python organizar_archivos.py --aplicar")
        print("   Para revertir: python organizar_archivos.py --deshacer")
        return

    # --- Mover ---
    print("\nMoviendo archivos...")
    for carpeta, _ in DESTINOS:
        os.makedirs(os.path.join(BASE_ARCHIVO, carpeta), exist_ok=True)

    movidos = 0
    for origen, destino in movimientos.items():
        try:
            shutil.move(origen, destino)
            print(f"   ✓ {os.path.basename(origen)}")
            movidos += 1
        except Exception as e:
            print(f"   ⚠️ {os.path.basename(origen)}: {e}")

    with open(REGISTRO, "w", encoding="utf-8") as f:
        json.dump(movimientos, f, indent=2, ensure_ascii=False)

    # --- Nota explicativa en cada carpeta ---
    notas = {
        "investigacion": (
            "Scripts que ya cumplieron su función.\n"
            "Documentan CÓMO se llegó a las conclusiones del proyecto:\n"
            "qué mercados sirven, qué ligas tienen ventaja, cómo acceder a "
            "SoccerStats.\n"
            "No se ejecutan a diario, pero conviene conservarlos.\n"
        ),
        "pruebas": "Scripts de prueba y depuración de etapas anteriores.\n",
        "obsoletos": "Versiones reemplazadas por scripts más nuevos.\n",
    }
    for carpeta, texto in notas.items():
        ruta = os.path.join(BASE_ARCHIVO, carpeta, "LEEME.txt")
        if os.path.isdir(os.path.dirname(ruta)):
            with open(ruta, "w", encoding="utf-8") as f:
                f.write(texto)

    print(f"\n✅ Archivos movidos: {movidos}")
    print(f"   La raíz quedó con {len(quedan) + len(sin_clasificar)} scripts.")
    print(f"   Si algo hace falta: python organizar_archivos.py --deshacer")


if __name__ == "__main__":
    main()
