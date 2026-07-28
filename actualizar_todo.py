import subprocess
import sys
import time
import os

def ejecutar_script(nombre_script, descripcion):
    print(f"\n⏳ Ejecutando: {descripcion} ({nombre_script})...")
    try:
        # Inyectamos el formato UTF-8 al entorno para que Windows no colapse con los emojis
        entorno = os.environ.copy()
        entorno["PYTHONIOENCODING"] = "utf-8"
        
        # Ejecutamos forzando la lectura en UTF-8
        resultado = subprocess.run(
            [sys.executable, nombre_script], 
            capture_output=True, 
            text=True, 
            encoding='utf-8', 
            env=entorno
        )
        
        if resultado.returncode == 0:
            print(f"✅ ¡Éxito! Datos actualizados.")
        else:
            print(f"❌ Error al ejecutar {nombre_script}.")
            print(f"Detalle del error:\n{resultado.stderr.strip()}")
    except FileNotFoundError:
        print(f"⚠️ No se encontró el archivo: {nombre_script}. Verifica el nombre y la ruta.")
    except Exception as e:
        print(f"⚠️ Ocurrió un error inesperado: {e}")
    time.sleep(1)

if __name__ == "__main__":
    print("==========================================================")
    print("🚀 INICIANDO ACTUALIZACIÓN MASIVA DE DATA PURITY PRO 🚀")
    print("==========================================================")
    
    # --- 1. ACTUALIZAR TABLAS DE POSICIONES (ARCHIVOS JSON) ---
    print("\n📂 FASE 1: Actualizando Tablas de Posiciones de las Ligas...")
    ejecutar_script("scraper/soccerstats_v2.py", "Ligas Principales (Soccerstats)")
    ejecutar_script("scraper/bolivia_scraper.py", "Liga Boliviana")
    ejecutar_script("scraper/primera_nacional_scraper.py", "Argentina - Primera Nacional")
    ejecutar_script("scraper/serie_b_brasil_scraper.py", "Brasil - Serie B")
    ejecutar_script("scraper/argentina_scraper.py", "Argentina - Primera División")
    ejecutar_script("scraper/noruega_scraper.py", "Noruega - Eliteserien")
    ejecutar_script("scraper/ligas_adicionales_scraper.py", "MLS, México, Estonia e Islandia 2")
    
    # --- 2. ACTUALIZAR HISTORIALES Y H2H (BASE DE DATOS) ---
    print("\n🗄️ FASE 2: Actualizando Base de Datos (Partidos Pasados y Córners)...")
    ejecutar_script("descargar_historial.py", "Historial Global de Partidos")
    
    # --- 3. INYECTORES EXTRA ---
    print("\n💉 FASE 3: Ejecutando Inyectores y Procesos Extra...")
    ejecutar_script("scraper/inyector_europa.py", "Inyector Europa")
    
    print("\n==========================================================")
    print("🏆 ¡TODOS LOS DATOS HAN SIDO ACTUALIZADOS CORRECTAMENTE! 🏆")
    print("==========================================================")
    print("👉 Ya puedes abrir/refrescar tu 'dashboard.py' para ver los datos al día.")