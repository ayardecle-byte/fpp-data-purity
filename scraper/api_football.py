import os
import sqlite3
import time
import requests
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

class DescargadorPaciente:
    def __init__(self):
        # Buscamos tu llave de API en el archivo .env
        self.api_key = os.getenv("API_FOOTBALL_KEY") or os.getenv("FOOTBALL_API_KEY") or os.getenv("API_KEY")
            
        self.base_url = "https://v3.football.api-sports.io"
        self.db_path = "database/football_data.db"
        
        # --- CORRECCIÓN 1: SOLO BOLIVIA (ID 233) ---
        self.ligas_interes = [233]
        
        # --- CORRECCIÓN 2: RANGO DE TEMPORADAS ACTUALES ---
        self.temporadas = [2024, 2025, 2026]
        
        self.peticiones_realizadas = 0
        self.limite_seguro = 80 

    def conectar_db(self):
        conexion = sqlite3.connect(self.db_path)
        cursor = conexion.cursor()
        
        # 1. Aseguramos la creación de la tabla base original
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS partidos (
                id_partido INTEGER PRIMARY KEY AUTOINCREMENT,
                fecha TEXT,
                equipo_local TEXT,
                equipo_visita TEXT,
                goles_local INTEGER,
                goles_visita INTEGER,
                ht_goles_local INTEGER,
                ht_goles_visita INTEGER,
                corners_local INTEGER DEFAULT 0,
                corners_visita INTEGER DEFAULT 0,
                amarillas_local INTEGER DEFAULT 0,
                amarillas_visita INTEGER DEFAULT 0,
                faltas_local INTEGER DEFAULT 0,
                faltas_visita INTEGER DEFAULT 0,
                stats_descargadas INTEGER DEFAULT 0,
                fixture_id INTEGER
            )
        ''')
        
        # 2. MIGRACIÓN SEGURA: Añadimos columnas nuevas si la tabla es antigua
        columnas_migracion = ['fixture_id', 'ht_goles_local', 'ht_goles_visita']
        for col in columnas_migracion:
            try:
                cursor.execute(f'ALTER TABLE partidos ADD COLUMN {col} INTEGER')
                conexion.commit()
                print(f"[⚙️] Columna '{col}' añadida con éxito a la base de datos existente.")
            except sqlite3.OperationalError:
                pass # Si lanza error es porque la columna ya existía, está perfecto.
            
        return conexion, cursor

    def realizar_peticion(self, endpoint, params=None):
        if self.peticiones_realizadas >= self.limite_seguro:
            print(f"[⚠️] Se ha alcanzado el límite seguro de {self.limite_seguro} peticiones por hoy.")
            return None

        headers = {
            'x-rapidapi-host': "v3.football.api-sports.io",
            'x-rapidapi-key': self.api_key
        }
        
        url = f"{self.base_url}/{endpoint}"
        
        try:
            self.peticiones_realizadas += 1
            response = requests.get(url, headers=headers, params=params)
            if response.status_code == 200:
                return response.json()
            else:
                print(f"[❌] Error en API ({response.status_code}): {response.text}")
                return None
        except Exception as e:
            print(f"[❌] Error de conexión: {e}")
            return None

    def descargar_calendario_y_partidos(self):
        print("\n📅 1. COMPROBANDO Y DESCARGANDO NUEVOS ENCUENTROS...")
        conexion, cursor = self.conectar_db()
        
        if not self.api_key:
            print("[❌] Error CRÍTICO: No se encontró tu llave de la API en el archivo .env.")
            print("Asegúrate de tener una línea como: API_KEY=tu_llave_aqui")
            conexion.close()
            return False

        partidos_nuevos = 0

        for liga in self.ligas_interes:
            for temp in self.temporadas:
                print(f"📡 Consultando Liga ID {liga} - Temporada {temp}...")
                
                params = {
                    'league': liga,
                    'season': temp
                }
                
                datos = self.realizar_peticion("fixtures", params)
                if not datos or 'response' not in datos:
                    continue
                    
                fixtures = datos['response']
                print(f"   -> Encontrados {len(fixtures)} partidos finalizados o programados.")
                
                for f in fixtures:
                    fixture_id = f['fixture']['id']
                    
                    # Formateo de Fecha (De YYYY-MM-DDTHH:MM a DD/MM/YYYY)
                    fecha_raw = f['fixture']['date']
                    try:
                        fecha_obj = datetime.strptime(fecha_raw[:10], "%Y-%m-%d")
                        fecha = fecha_obj.strftime("%d/%m/%Y")
                    except:
                        fecha = fecha_raw
                    
                    status = f['fixture']['status']['short']
                    if status not in ['FT', 'AET', 'PEN']:
                        continue
                        
                    local = f['teams']['home']['name']
                    visita = f['teams']['away']['name']
                    goles_L = f['goals']['home']
                    goles_V = f['goals']['away']
                    
                    # Nuevos datos de Medio Tiempo (HT)
                    ht_goles_L = f['score']['halftime']['home']
                    ht_goles_V = f['score']['halftime']['away']
                    
                    # 1. Comprobamos si el fixture_id ya existe en nuestra base de datos
                    cursor.execute('SELECT 1 FROM partidos WHERE fixture_id = ?', (fixture_id,))
                    if cursor.fetchone():
                        continue 

                    # 2. Comprobamos si ya existía por nombres de equipos y goles
                    cursor.execute('''
                        SELECT id_partido FROM partidos 
                        WHERE equipo_local=? AND equipo_visita=? AND goles_local=? AND goles_visita=?
                    ''', (local, visita, goles_L, goles_V))
                    
                    partido_existente = cursor.fetchone()
                    
                    if partido_existente:
                        id_partido_viejo = partido_existente[0]
                        cursor.execute('''
                            UPDATE partidos SET fixture_id = ?, ht_goles_local = ?, ht_goles_visita = ? WHERE id_partido = ?
                        ''', (fixture_id, ht_goles_L, ht_goles_V, id_partido_viejo))
                        conexion.commit()
                        continue
                    
                    # 3. Si es un partido completamente nuevo, lo insertamos
                    cursor.execute('''
                        INSERT INTO partidos (
                            fixture_id, fecha, equipo_local, equipo_visita, goles_local, goles_visita, ht_goles_local, ht_goles_visita, stats_descargadas
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0)
                    ''', (fixture_id, fecha, local, visita, goles_L, goles_V, ht_goles_L, ht_goles_V))
                    partidos_nuevos += 1
                    conexion.commit()
        
        conexion.close()
        print(f"[✅] Sincronización de calendario completada. Se agregaron {partidos_nuevos} partidos nuevos.")
        return True

    def descargar_estadisticas_detalladas(self):
        print("\n📊 2. DESCARGANDO ESTADÍSTICAS DETALLADAS (CÓRNERS, TARJETAS, FALTAS)...")
        conexion, cursor = self.conectar_db()
        
        cursor.execute('''
            SELECT id_partido, fixture_id, equipo_local, equipo_visita 
            FROM partidos 
            WHERE stats_descargadas = 0 AND fixture_id IS NOT NULL
            LIMIT ?
        ''', (self.limite_seguro - self.peticiones_realizadas,))
        
        partidos_pendientes = cursor.fetchall()
        
        if not partidos_pendientes:
            print("[✅] ¡No hay estadísticas detalladas pendientes para esta liga!")
            conexion.close()
            return

        print(f"[ℹ️] Se procesarán {len(partidos_pendientes)} partidos en esta ejecución.")
        
        for id_partido, fixture_id, local, visita in partidos_pendientes:
            if self.peticiones_realizadas >= self.limite_seguro:
                print(f"[⚠️] Límite de peticiones diarias alcanzado ({self.limite_seguro}). Deteniendo descargas.")
                break
                
            print(f" ⏳ [{self.peticiones_realizadas}/{self.limite_seguro}] Descargando estadísticas de: {local} vs {visita} (ID: {fixture_id})...")
            
            params = {'fixture': fixture_id}
            datos = self.realizar_peticion("fixtures/statistics", params)
            
            # --- CORRECCIÓN 3: PAUSA MÁS LARGA (6.5s) PARA EVITAR ERROR 429 ---
            time.sleep(6.5) 
            
            if not datos or 'response' not in datos or len(datos['response']) < 2:
                cursor.execute('UPDATE partidos SET stats_descargadas = 1 WHERE id_partido = ?', (id_partido,))
                conexion.commit()
                continue
            
            stats_local = datos['response'][0]['statistics']
            stats_visita = datos['response'][1]['statistics']
            
            def extraer_valor(stats_list, tipo_stat):
                for item in stats_list:
                    if item['type'] == tipo_stat:
                        val = item['value']
                        return int(val) if val is not None else 0
                return 0

            corners_L = extraer_valor(stats_local, "Corner Kicks")
            corners_V = extraer_valor(stats_visita, "Corner Kicks")
            
            tarj_L = extraer_valor(stats_local, "Yellow Cards")
            tarj_V = extraer_valor(stats_visita, "Yellow Cards")
            
            faltas_L = extraer_valor(stats_local, "Fouls")
            faltas_V = extraer_valor(stats_visita, "Fouls")
            
            cursor.execute('''
                UPDATE partidos 
                SET corners_local = ?, corners_visita = ?, 
                    amarillas_local = ?, amarillas_visita = ?, 
                    faltas_local = ?, faltas_visita = ?, 
                    stats_descargadas = 1
                WHERE id_partido = ?
            ''', (corners_L, corners_V, tarj_L, tarj_V, faltas_L, faltas_V, id_partido))
            
            conexion.commit()
            
        conexion.close()
        print(f"\n[✅] Ejecución finalizada. Peticiones consumidas hoy: {self.peticiones_realizadas}")

    def ejecutar(self):
        print("="*60)
        print("🚀 INICIANDO EL DESCARGADOR PACIENTE PARA BOLIVIA")
        print("="*60)
        
        if self.descargar_calendario_y_partidos():
            self.descargar_estadisticas_detalladas()
            
        print("="*60)

if __name__ == "__main__":
    descargador = DescargadorPaciente()
    descargador.ejecutar()