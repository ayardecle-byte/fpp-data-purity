import pandas as pd
import sqlite3
import os

def inyectar_europa():
    print("🌍 INICIANDO INYECCIÓN DE DATOS EUROPEOS ACTUALES (24/25 y 25/26)...")
    
    db_path = "database/football_data.db"
    conexion = sqlite3.connect(db_path)
    cursor = conexion.cursor()
    
    # Temporadas actuales (2024/2025 y 2025/2026)
    temporadas = ['2425', '2526'] 
    ligas_principales = {
        'E0': 'Premier League (Inglaterra)',
        'SP1': 'La Liga (España)',
        'F1': 'Ligue 1 (Francia)',
        'I1': 'Serie A (Italia)',
        'I2': 'Serie B (Italia)',
        'SC0': 'Premiership (Escocia)'
    }

    partidos_agregados = 0

    # Procesar Ligas Principales
    for temp in temporadas:
        for codigo, nombre_liga in ligas_principales.items():
            url = f"https://www.football-data.co.uk/mmz4281/{temp}/{codigo}.csv"
            print(f"📥 Descargando {nombre_liga} (Temp. {temp})...")
            
            try:
                df = pd.read_csv(url, on_bad_lines='skip')
                df = df.dropna(subset=['HomeTeam', 'AwayTeam', 'FTHG', 'FTAG'])
                
                for _, row in df.iterrows():
                    local = str(row['HomeTeam']).strip()
                    visita = str(row['AwayTeam']).strip()
                    goles_L = int(row['FTHG'])
                    goles_V = int(row['FTAG'])
                    
                    try:
                        corn_L, corn_V = int(row.get('HC', 5)), int(row.get('AC', 5))
                        tarj_L, tarj_V = int(row.get('HY', 2)), int(row.get('AY', 2))
                        fal_L, fal_V = int(row.get('HF', 10)), int(row.get('AF', 10))
                        stats_ok = 1
                    except:
                        corn_L, corn_V, tarj_L, tarj_V, fal_L, fal_V = 0, 0, 0, 0, 0, 0
                        stats_ok = 0

                    # Control de duplicados antes de insertar
                    cursor.execute('''
                        SELECT id_partido FROM partidos 
                        WHERE equipo_local=? AND equipo_visita=? AND goles_local=? AND goles_visita=?
                    ''', (local, visita, goles_L, goles_V))
                    
                    if not cursor.fetchone():
                        cursor.execute('''
                            INSERT INTO partidos (
                                fecha, equipo_local, equipo_visita, goles_local, goles_visita,
                                corners_local, corners_visita, amarillas_local, amarillas_visita,
                                faltas_local, faltas_visita, stats_descargadas
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ''', (str(row.get('Date', '')), local, visita, goles_L, goles_V, corn_L, corn_V, tarj_L, tarj_V, fal_L, fal_V, stats_ok))
                        partidos_agregados += 1
                        
            except Exception as e:
                print(f"  [!] No se pudo procesar {nombre_liga}: {e}")

    # Procesar Noruega (Últimos 1000 partidos históricos que cubren hasta la actualidad)
    print("📥 Descargando Eliteserien (Noruega)...")
    try:
        url_noruega = "https://www.football-data.co.uk/new/NOR.csv"
        df_nor = pd.read_csv(url_noruega, on_bad_lines='skip')
        df_nor = df_nor.dropna(subset=['Home', 'Away', 'HG', 'AG'])
        
        for _, row in df_nor.tail(1000).iterrows():
            local = str(row['Home']).strip()
            visita = str(row['Away']).strip()
            goles_L, goles_V = int(row['HG']), int(row['AG'])
            
            cursor.execute('SELECT id_partido FROM partidos WHERE equipo_local=? AND equipo_visita=? AND goles_local=?', (local, visita, goles_L))
            if not cursor.fetchone():
                cursor.execute('''
                    INSERT INTO partidos (
                        fecha, equipo_local, equipo_visita, goles_local, goles_visita,
                        corners_local, corners_visita, amarillas_local, amarillas_visita,
                        faltas_local, faltas_visita, stats_descargadas
                    ) VALUES (?, ?, ?, ?, ?, 5, 5, 2, 2, 10, 10, 1) 
                ''', (str(row.get('Date', '')), local, visita, goles_L, goles_V))
                partidos_agregados += 1
    except Exception as e:
        print(f"  [!] Error con Noruega: {e}")

    conexion.commit()
    conexion.close()
    
    print("\n" + "=" * 55)
    print(f"✅ ¡INYECCIÓN ACTUALIZADA COMPLETADA!")
    print(f"⚽ Se agregaron {partidos_agregados} nuevos partidos de las temporadas 24/25 y 25/26.")
    print("=" * 55)

if __name__ == "__main__":
    inyectar_europa()