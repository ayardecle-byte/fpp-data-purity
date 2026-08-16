import streamlit as st
import sqlite3
import pandas as pd
import json
import os
import unicodedata
import re
import requests
import datetime
from io import StringIO
from scipy.stats import poisson
import motor_v2
import picks_dia
import registro_predicciones as reg
import movil

# --- 1. CONFIGURACIÓN ---
st.set_page_config(page_title="FPP - Data Purity Pro", page_icon="🛡️", layout="wide")

# Ajustes automáticos para pantallas chicas (celular)
movil.configurar()
ES_MOVIL = movil.es_movil()

# MODO NUBE: si no existe la base de datos local, la app corre en
# Streamlit Cloud. Ahí solo se consulta; las apuestas y la billetera
# viven únicamente en tu computadora.
MODO_NUBE = not os.path.exists("database/football_data.db")

def cambiar_pagina(nombre_pagina):
    st.session_state.pagina = nombre_pagina

# --- 1B. BASE DE DATOS DE APUESTAS Y BILLETERA ---
def crear_tabla_apuestas():
    os.makedirs("database", exist_ok=True)

    conn = sqlite3.connect("database/football_data.db")
    cursor = conn.cursor()

    cursor.execute('''CREATE TABLE IF NOT EXISTS favoritos (
            id INTEGER PRIMARY KEY AUTOINCREMENT, liga TEXT, equipo_local TEXT, equipo_visita TEXT, UNIQUE(liga, equipo_local, equipo_visita))''')

    cursor.execute('''CREATE TABLE IF NOT EXISTS mis_apuestas (
            id INTEGER PRIMARY KEY AUTOINCREMENT, liga TEXT, equipo_local TEXT, equipo_visita TEXT, mercado TEXT, cuota REAL, probabilidad REAL, ev REAL, cuotas_json TEXT)''')

    try: cursor.execute('ALTER TABLE mis_apuestas ADD COLUMN picks TEXT DEFAULT "Simple"')
    except: pass
    try: cursor.execute('ALTER TABLE mis_apuestas ADD COLUMN inversion REAL DEFAULT 0.0')
    except: pass
    try: cursor.execute('ALTER TABLE mis_apuestas ADD COLUMN estado TEXT DEFAULT "Pendiente"')
    except: pass
    try: cursor.execute('ALTER TABLE mis_apuestas ADD COLUMN en_billetera INTEGER DEFAULT 0')
    except: pass
    try: cursor.execute('ALTER TABLE mis_apuestas ADD COLUMN fecha_apuesta TEXT DEFAULT ""')
    except: pass

    cursor.execute('''CREATE TABLE IF NOT EXISTS config_billetera (
            id INTEGER PRIMARY KEY CHECK (id = 1), bankroll_inicial REAL, meta REAL)''')
    cursor.execute('INSERT OR IGNORE INTO config_billetera (id, bankroll_inicial, meta) VALUES (1, 100.0, 1000.0)')

    try: cursor.execute('ALTER TABLE config_billetera ADD COLUMN api_key TEXT DEFAULT ""')
    except: pass

    conn.commit()
    conn.close()

def guardar_apuesta(liga, local, visita, mercado, cuota, prob, ev, cuotas_json):
    fecha_actual = datetime.datetime.now().strftime("%Y-%m-%d")
    conn = sqlite3.connect("database/football_data.db")
    cursor = conn.cursor()
    cursor.execute('''INSERT INTO mis_apuestas (liga, equipo_local, equipo_visita, mercado, cuota, probabilidad, ev, cuotas_json, picks, inversion, estado, en_billetera, fecha_apuesta) 
                     VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?)''', 
                   (liga, local, visita, mercado, cuota, prob, ev, cuotas_json, mercado, 0.0, "Pendiente", fecha_actual))
    conn.commit()
    conn.close()

def guardar_apuesta_manual(liga, local, visita, picks, inversion, cuota, stake, fecha):
    conn = sqlite3.connect("database/football_data.db")
    cursor = conn.cursor()
    prob_simulada = stake * 10.0
    cursor.execute('''INSERT INTO mis_apuestas (liga, equipo_local, equipo_visita, mercado, cuota, probabilidad, ev, cuotas_json, picks, inversion, estado, en_billetera, fecha_apuesta) 
                     VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)''', 
                   (liga, local, visita, picks, cuota, prob_simulada, 0.0, "{}", picks, inversion, "Pendiente", fecha))
    conn.commit()
    conn.close()

def pasar_a_billetera(id_apuesta):
    conn = sqlite3.connect("database/football_data.db")
    cursor = conn.cursor()
    cursor.execute('UPDATE mis_apuestas SET en_billetera = 1 WHERE id = ?', (id_apuesta,))
    conn.commit()
    conn.close()

def borrar_apuesta(id_apuesta):
    conn = sqlite3.connect("database/football_data.db")
    cursor = conn.cursor()
    cursor.execute('DELETE FROM mis_apuestas WHERE id = ?', (id_apuesta,))
    conn.commit()
    conn.close()

def obtener_config_billetera():
    conn = sqlite3.connect("database/football_data.db")
    cursor = conn.cursor()
    cursor.execute('SELECT bankroll_inicial, meta, api_key FROM config_billetera WHERE id = 1')
    res = cursor.fetchone()
    conn.close()
    return res if res else (100.0, 1000.0, "")

def actualizar_config_billetera(bankroll, meta, api_key=""):
    conn = sqlite3.connect("database/football_data.db")
    cursor = conn.cursor()
    cursor.execute('UPDATE config_billetera SET bankroll_inicial = ?, meta = ?, api_key = ? WHERE id = 1', (bankroll, meta, api_key))
    conn.commit()
    conn.close()

def actualizar_fila_billetera(id_apuesta, picks, inversion, cuota, estado, fecha_apuesta):
    conn = sqlite3.connect("database/football_data.db")
    cursor = conn.cursor()
    cursor.execute('UPDATE mis_apuestas SET picks = ?, inversion = ?, cuota = ?, estado = ?, fecha_apuesta = ? WHERE id = ?', 
                   (picks, inversion, cuota, estado, fecha_apuesta, id_apuesta))
    conn.commit()
    conn.close()

if not MODO_NUBE:
    crear_tabla_apuestas()

# --- FUNCIONES DE LIMPIEZA DE TEXTO ---
def normalize_text(text):
    if not isinstance(text, str): return ""
    nfkd_form = unicodedata.normalize('NFKD', text)
    return "".join([c for c in nfkd_form if not unicodedata.combining(c)]).lower().strip()

def normalize_db_name(name):
    name = normalize_text(name)
    name = re.sub(r'[\s\-]+(sc|ba|pr|go|rs|ce|pe|mt|rj|sp|mg|af|am|pa|rn|al|pb)$', '', name)
    mapa = {
        "athletico paranaense": "athletico", 
        "atletico paranaense": "athletico", 
        "atletico pr": "athletico",
        "atletico mineiro": "atletico mg", 
        "atletico goianiense": "atletico go", 
        "red bull bragantino": "bragantino",
        "vasco da gama": "vasco"
    }
    return mapa.get(name, name).strip()

# --- 2. CARGA DE DATOS (JSON) ---
def cargar_tabla_json(nombre_liga):
    mapa_archivos = {
        "Inglaterra - Premier League": "england", "España - La Liga": "spain", 
        "Italia - Serie A": "italy", "Francia - Ligue 1": "france", 
        "Argentina": "argentina", "Argentina - Primera Nacional": "primera_nacional", "Brasil": "brazil",
        "Brasil - Serie B": "serie_b_brasil",
        "Champions League": "champions", "Libertadores": "libertadores", 
        "Copa Sudamericana": "sudamericana", "Europa League": "europa",
        "Noruega - Eliteserien": "norway", "Estados Unidos - MLS": "mls", 
        "México - Liga MX": "mexico", "Bolivia - Div. Profesional": "bolivia",
        "Estonia - Meistriliiga": "estonia", "Islandia - 2da División": "iceland2",
        "Dinamarca - Superliga": "denmark", "China - Super League": "china",
        "Suecia - Allsvenskan": "sweden", "Islandia - 1ra División": "iceland",
        "Escocia - Premiership": "scotland",
        "Chequia - Fortuna Liga": "czechrepublic",
        "Turquía - Süper Lig": "turkey",
        "Ucrania - Premier League": "ukraine",
        "Finlandia - Veikkausliiga": "finland",
        "Japón - J1 League": "japan",
        "Suiza - Super League": "switzerland",
        "Países Bajos - Eerste Divisie": "netherlands2",
        "Portugal - Liga 2": "portugal2",
        "Alemania - 2. Bundesliga": "germany2",
    }
    limite_equipos = {
        "Inglaterra - Premier League": 20, "España - La Liga": 20, "Italia - Serie A": 20, "Francia - Ligue 1": 18,
        "Argentina": 30, "Argentina - Primera Nacional": 40, "Brasil": 20, "Brasil - Serie B": 20,
        "Champions League": 36, "Europa League": 36, "Libertadores": 32, 
        "Copa Sudamericana": 32, "Noruega - Eliteserien": 16, "Estados Unidos - MLS": 30, "México - Liga MX": 18,
        "Bolivia - Div. Profesional": 16, "Estonia - Meistriliiga": 10, "Islandia - 2da División": 12,
        "Dinamarca - Superliga": 12, "China - Super League": 16,
        "Suecia - Allsvenskan": 16, "Islandia - 1ra División": 12,
        "Escocia - Premiership": 12,
        "Chequia - Fortuna Liga": 16,
        "Turquía - Süper Lig": 19,
        "Ucrania - Premier League": 16,
        "Finlandia - Veikkausliiga": 12,
        "Japón - J1 League": 20,
        "Suiza - Super League": 12,
        "Países Bajos - Eerste Divisie": 20,
        "Portugal - Liga 2": 18,
        "Alemania - 2. Bundesliga": 18,
    }

    path = f"data_json/{mapa_archivos.get(nombre_liga)}.json"
    if not os.path.exists(path): return pd.DataFrame()
    with open(path, 'r', encoding='utf-8') as f: data = json.load(f)
    
    lista = []
    basura_keywords = ['team', 'total', 'average', 'points', 'matches', 'date', 'pos', 'pts', 'home', 'all', 'last', 'segments', 'offence', 'latest']
    
    for row in data.get("posiciones", []):
        if isinstance(row, list) and len(row) >= 10:
            club_name = str(row[1]).strip()
            try:
                float(club_name)
                continue
            except ValueError:
                pass
            if not club_name:
                continue

            club_lower = club_name.lower()

            # Excepción VIP para All Boys
            if club_lower != "all boys":
                palabras_club = club_lower.split()
                if any(basura in palabras_club for basura in basura_keywords): 
                    continue
            
            lista.append({"Club": club_name, "PJ": row[2], "G": row[3], "E": row[4], "P": row[5], "GF": row[6], "GC": row[7], "DG": row[8], "Pts": row[9]})
    
    df = pd.DataFrame(lista)
    if not df.empty:
        df = df.drop_duplicates(subset=['Club'], keep='first')
        
        cols_num = ['PJ', 'G', 'E', 'P', 'GF', 'GC', 'Pts', 'DG']
        for col in cols_num: 
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0)
            
        df = df.head(limite_equipos.get(nombre_liga, 30))

        if nombre_liga in ["Argentina", "Argentina - Primera Nacional", "Estados Unidos - MLS"]:
            if nombre_liga == "Argentina":
                def asignar_grupo(club):
                    c = str(club).lower().strip()
                    if "mendoza" in c or "esgrima" in c: return "A"
                    if c == "gimnasia": return "B"
                    if "rio cuarto" in c: return "B"
                    if c == "estudiantes" or "estudiantes l" in c: return "A"
                    grupo_a = ["velez", "independiente", "defensa", "riestra", "lanus", "instituto", "newell", "san lorenzo", "union", "platense", "talleres", "t. de cordoba", "central cordoba", "boca"]
                    if any(g in c for g in grupo_a): 
                        return "A"
                    return "B"
                
                df['Grupo'] = df['Club'].apply(asignar_grupo)
                df_zona_a = df[df['Grupo'] == "A"].copy().drop(columns=['Grupo'])
                df_zona_b = df[df['Grupo'] == "B"].copy().drop(columns=['Grupo'])
            else:
                mitad = len(df) // 2
                df_zona_a = df.iloc[:mitad].copy()
                df_zona_b = df.iloc[mitad:].copy()
                
            df_zona_a['Pts'] = pd.to_numeric(df_zona_a['Pts'], errors='coerce').fillna(0)
            df_zona_a['DG'] = pd.to_numeric(df_zona_a['DG'], errors='coerce').fillna(0)
            df_zona_b['Pts'] = pd.to_numeric(df_zona_b['Pts'], errors='coerce').fillna(0)
            df_zona_b['DG'] = pd.to_numeric(df_zona_b['DG'], errors='coerce').fillna(0)
            
            df_zona_a = df_zona_a.sort_values(by=['Pts', 'DG'], ascending=[False, False]).reset_index(drop=True)
            df_zona_b = df_zona_b.sort_values(by=['Pts', 'DG'], ascending=[False, False]).reset_index(drop=True)
            
            df_zona_a.insert(0, 'Pos', range(1, len(df_zona_a) + 1))
            df_zona_b.insert(0, 'Pos', range(1, len(df_zona_b) + 1))
            
            df = pd.concat([df_zona_a, df_zona_b]).reset_index(drop=True)
            return df
        else:
            df = df.sort_values(by=['Pts', 'DG'], ascending=[False, False]).reset_index(drop=True)
            df.index += 1
            df.insert(0, 'Pos', df.index)
            return df
            
    return pd.DataFrame()

# --- 3. MOTORES ESTADÍSTICOS Y SCRAPING ---

def obtener_stats_promediosinfo(nombre_equipo):
    eq_norm = normalize_text(nombre_equipo)
    diccionario = {
        "palmeiras": "palmeiras_121", "flamengo": "flamengo_127", "fluminense": "fluminense_124",
        "bragantino": "rb-bragantino_794", "paranaense": "atletico-paranaense_134", "bahia": "bahia_118",
        "coritiba": "coritiba_147", "sao paulo": "sao-paulo_126", "botafogo": "botafogo_120",
        "vitoria": "vitoria_136", "atletico mg": "atletico-mg_1062", "mineiro": "atletico-mg_1062",
        "corinthians": "corinthians_131", "cruzeiro": "cruzeiro_135", "internacional": "internacional_119",
        "santos": "santos_128", "gremio": "gremio_130", "vasco": "vasco-da-gama_133",
        "mirassol": "mirassol_7848", "remo": "remo_1198", "chapecoense": "chapecoense-sc_132"
    }
    sufijo = next((v for k, v in diccionario.items() if k in eq_norm), None)
    if not sufijo: return -1, -1, -1, -1, -1, -1
        
    url = f"https://promediosinfo.com/{sufijo}.html"
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8'
    }
    
    try:
        res = requests.get(url, headers=headers, timeout=10)
        if res.status_code != 200: return -1, -1, -1, -1, -1, -1
            
        tablas = pd.read_html(StringIO(res.text))
        if len(tablas) > 1:
            df = tablas[1] 
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(-1)
            df.columns = [str(c).strip() for c in df.columns]

            df = df.dropna(subset=['Res.'])
            df = df[df['Res.'].str.contains("-", na=False)]
            
            temp_split = df['Res.'].str.split('-', expand=True)
            if temp_split.shape[1] >= 2:
                df['GF_Real'] = pd.to_numeric(temp_split[0], errors='coerce')
                df['GC_Real'] = pd.to_numeric(temp_split[1], errors='coerce')
                df = df.dropna(subset=['GF_Real', 'GC_Real']) 
                
                ultimos_5 = df.tail(5)
                racha_gf = ultimos_5['GF_Real'].mean()
                racha_gc = ultimos_5['GC_Real'].mean()
                
                df_local = df[df['L/V'] == 'L'].tail(5)
                local_gf = df_local['GF_Real'].mean() if not df_local.empty else -1
                local_gc = df_local['GC_Real'].mean() if not df_local.empty else -1
                
                df_visita = df[df['L/V'] == 'V'].tail(5)
                visita_gf = df_visita['GF_Real'].mean() if not df_visita.empty else -1
                visita_gc = df_visita['GC_Real'].mean() if not df_visita.empty else -1
                
                return racha_gf, racha_gc, local_gf, local_gc, visita_gf, visita_gc
    except Exception:
        pass
    return -1, -1, -1, -1, -1, -1

def obtener_forma_visual_promediosinfo(nombre_equipo):
    # Función desactivada para unificar con el motor SoccerStats
    return []

def obtener_h2h_y_rachas(equipo_local, equipo_visita):
    try:
        conn = sqlite3.connect("database/football_data.db")
        query = "SELECT fecha, equipo_local, equipo_visita, goles_local, goles_visita, corners_local, corners_visita FROM partidos"
        df = pd.read_sql(query, conn)
        conn.close()
    except Exception:
        return pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), -1, -1, -1, -1

    if df.empty: return pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), -1, -1, -1, -1

    df['local_norm'] = df['equipo_local'].apply(normalize_db_name)
    df['visita_norm'] = df['equipo_visita'].apply(normalize_db_name)
    
    df['fecha_real'] = pd.to_datetime(df['fecha'], format='mixed', dayfirst=True, errors='coerce', utc=True)
    df = df.dropna(subset=['fecha_real'])
    df['fecha_str'] = df['fecha_real'].dt.strftime('%d-%m-%Y')
    df = df.drop_duplicates(subset=['fecha_real', 'local_norm', 'visita_norm'])

    l_norm = normalize_db_name(equipo_local)
    v_norm = normalize_db_name(equipo_visita)

    h2h = df[((df['local_norm'] == l_norm) & (df['visita_norm'] == v_norm)) |
             ((df['local_norm'] == v_norm) & (df['visita_norm'] == l_norm))].copy()
    h2h = h2h.sort_values(by='fecha_real', ascending=False).head(5)

    racha_l = df[(df['local_norm'] == l_norm) | (df['visita_norm'] == l_norm)].copy()
    racha_l = racha_l.sort_values(by='fecha_real', ascending=False).head(5)

    racha_v = df[(df['local_norm'] == v_norm) | (df['visita_norm'] == v_norm)].copy()
    racha_v = racha_v.sort_values(by='fecha_real', ascending=False).head(5)

    df_home = df[df['local_norm'] == l_norm].sort_values(by='fecha_real', ascending=False).head(10)
    df_away = df[df['visita_norm'] == v_norm].sort_values(by='fecha_real', ascending=False).head(10)

    gf_l_home = df_home['goles_local'].mean() if not df_home.empty else -1
    gc_l_home = df_home['goles_visita'].mean() if not df_home.empty else -1
    gf_v_away = df_away['goles_visita'].mean() if not df_away.empty else -1
    gc_v_away = df_away['goles_local'].mean() if not df_away.empty else -1

    return h2h, racha_l, racha_v, gf_l_home, gc_l_home, gf_v_away, gc_v_away

def procesar_forma_detallada(df_racha, equipo):
    if df_racha.empty: return ["No hay registros previos"], None
    eq_norm = normalize_db_name(equipo)
    detalles = []
    ultima_fecha = df_racha.iloc[0]['fecha_str']
    
    for _, r in df_racha.iterrows():
        try:
            gl, gv = int(r['goles_local']), int(r['goles_visita'])
            if r['local_norm'] == eq_norm:
                condicion = "🏠 L"
                rival = str(r['equipo_visita']).title()
                resultado = f"**{gl} - {gv}**"
                icono = '✅' if gl > gv else '➖' if gl == gv else '❌'
            else:
                condicion = "✈️ V"
                rival = str(r['equipo_local']).title()
                resultado = f"**{gv} - {gl}**" 
                icono = '✅' if gv > gl else '➖' if gv == gl else '❌'
            detalles.append(f"{icono} {resultado} | {condicion} vs {rival}")
        except Exception:
            pass
    return detalles, ultima_fecha


def calcular_prediccion_avanzada(tabla, local, visita, racha_l_df, racha_v_df, gf_lh_old, gc_lh_old, gf_va_old, gc_va_old, liga_sel=""):
    """MOTOR ANTIGUO (respaldo). Solo se usa si motor_v2 no puede predecir."""
    row_l = tabla[tabla['Club'] == local].iloc[0] if not tabla[tabla['Club'] == local].empty else pd.Series({'PJ':1, 'GF':0, 'GC':0})
    row_v = tabla[tabla['Club'] == visita].iloc[0] if not tabla[tabla['Club'] == visita].empty else pd.Series({'PJ':1, 'GF':0, 'GC':0})
    
    liga_gf_total = tabla['GF'].sum()
    liga_pj_total = tabla['PJ'].sum()
    avg_liga_goles = liga_gf_total / max(1, liga_pj_total)
    if avg_liga_goles == 0: avg_liga_goles = 2.0
    
    gf_l_avg = row_l['GF'] / max(1, row_l['PJ'])
    gc_l_avg = row_l['GC'] / max(1, row_l['PJ'])
    gf_v_avg = row_v['GF'] / max(1, row_v['PJ'])
    gc_v_avg = row_v['GC'] / max(1, row_v['PJ'])

    # Tabla anual acumulada (solo para las matemáticas)
    df_acumulado = tabla.copy()
    try:
        mapa_archivos = {"Inglaterra - Premier League": "england", "España - La Liga": "spain", "Italia - Serie A": "italy", "Francia - Ligue 1": "france", "Argentina": "argentina", "Argentina - Primera Nacional": "primera_nacional", "Brasil": "brazil", "Brasil - Serie B": "serie_b_brasil", "Estados Unidos - MLS": "mls", "México - Liga MX": "mexico", "Noruega - Eliteserien": "norway", "Dinamarca - Superliga": "denmark", "Suecia - Allsvenskan": "sweden", "China - Super League": "china", "Estonia - Meistriliiga": "estonia", "Islandia - 1ra División": "iceland", "Islandia - 2da División": "iceland2"}
        path = f"data_json/{mapa_archivos.get(liga_sel)}.json"
        if os.path.exists(path):
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
                lista_acum = []
                for row in data.get("posiciones", []):
                    if isinstance(row, list) and len(row) >= 10:
                        club = str(row[1]).strip()
                        if club.lower() == "all boys" or not any(b in club.lower() for b in ['team', 'total', 'average', 'points']):
                            try: float(club)
                            except ValueError:
                                lista_acum.append({"Club": club, "PJ": row[2], "GF": row[6], "GC": row[7]})
                if lista_acum:
                    df_json = pd.DataFrame(lista_acum)
                    for c in ['PJ', 'GF', 'GC']: df_json[c] = pd.to_numeric(df_json[c], errors='coerce').fillna(0)
                    df_acumulado = df_json.groupby('Club', as_index=False)[['PJ', 'GF', 'GC']].sum()
    except Exception:
        pass

    liga_gf_total = df_acumulado['GF'].sum()
    liga_pj_total = df_acumulado['PJ'].sum()
    avg_liga_goles = liga_gf_total / max(1, liga_pj_total)
    
    row_l_acum = df_acumulado[df_acumulado['Club'] == local]
    row_v_acum = df_acumulado[df_acumulado['Club'] == visita]
    
    if not row_l_acum.empty:
        gf_l_avg = row_l_acum.iloc[0]['GF'] / max(1, row_l_acum.iloc[0]['PJ'])
        gc_l_avg = row_l_acum.iloc[0]['GC'] / max(1, row_l_acum.iloc[0]['PJ'])
    else:
        if not tabla[tabla['Club'] == local].empty:
            row_l = tabla[tabla['Club'] == local].iloc[0]
            gf_l_avg = row_l['GF'] / max(1, row_l['PJ'])
            gc_l_avg = row_l['GC'] / max(1, row_l['PJ'])
        
    if not row_v_acum.empty:
        gf_v_avg = row_v_acum.iloc[0]['GF'] / max(1, row_v_acum.iloc[0]['PJ'])
        gc_v_avg = row_v_acum.iloc[0]['GC'] / max(1, row_v_acum.iloc[0]['PJ'])
    else:
        if not tabla[tabla['Club'] == visita].empty:
            row_v = tabla[tabla['Club'] == visita].iloc[0]
            gf_v_avg = row_v['GF'] / max(1, row_v['PJ'])
            gc_v_avg = row_v['GC'] / max(1, row_v['PJ'])

    # PromediosInfo desconectado: se usa el motor unificado de SoccerStats
    l_racha_gf, l_racha_gc, l_home_gf, l_home_gc = -1, -1, -1, -1
    v_racha_gf, v_racha_gc, v_away_gf, v_away_gc = -1, -1, -1, -1

    # Pesos calibrados por backtest (mejor combinación global hallada)
    w_temp, w_racha, w_loc = 0.40, 0.10, 0.50
    
    db_l_racha_gf, db_l_racha_gc = gf_l_avg, gc_l_avg
    if not racha_l_df.empty:
        eq_norm = normalize_db_name(local)
        gl_list, gc_list = [], []
        for _, r in racha_l_df.iterrows():
            try:
                if r['local_norm'] == eq_norm:
                    gl_list.append(float(r['goles_local'])); gc_list.append(float(r['goles_visita']))
                else:
                    gl_list.append(float(r['goles_visita'])); gc_list.append(float(r['goles_local']))
            except Exception:
                pass
        if gl_list:
            db_l_racha_gf = sum(gl_list)/len(gl_list)
            db_l_racha_gc = sum(gc_list)/len(gc_list)

    db_v_racha_gf, db_v_racha_gc = gf_v_avg, gc_v_avg
    if not racha_v_df.empty:
        eq_norm_v = normalize_db_name(visita)
        gl_list, gc_list = [], []
        for _, r in racha_v_df.iterrows():
            try:
                if r['local_norm'] == eq_norm_v:
                    gl_list.append(float(r['goles_local'])); gc_list.append(float(r['goles_visita']))
                else:
                    gl_list.append(float(r['goles_visita'])); gc_list.append(float(r['goles_local']))
            except Exception:
                pass
        if gl_list:
            db_v_racha_gf = sum(gl_list)/len(gl_list)
            db_v_racha_gc = sum(gc_list)/len(gc_list)

    fin_l_racha_gf = l_racha_gf if l_racha_gf != -1 else db_l_racha_gf
    fin_l_racha_gc = l_racha_gc if l_racha_gc != -1 else db_l_racha_gc
    fin_l_home_gf = l_home_gf if l_home_gf != -1 else (gf_lh_old if gf_lh_old != -1 else gf_l_avg)
    fin_l_home_gc = l_home_gc if l_home_gc != -1 else (gc_lh_old if gc_lh_old != -1 else gc_l_avg)
    
    fin_v_racha_gf = v_racha_gf if v_racha_gf != -1 else db_v_racha_gf
    fin_v_racha_gc = v_racha_gc if v_racha_gc != -1 else db_v_racha_gc
    fin_v_away_gf = v_away_gf if v_away_gf != -1 else (gf_va_old if gf_va_old != -1 else gf_v_avg)
    fin_v_away_gc = v_away_gc if v_away_gc != -1 else (gc_va_old if gc_va_old != -1 else gc_v_avg)

    gf_l_final = (gf_l_avg * w_temp) + (fin_l_racha_gf * w_racha) + (fin_l_home_gf * w_loc)
    gc_l_final = (gc_l_avg * w_temp) + (fin_l_racha_gc * w_racha) + (fin_l_home_gc * w_loc)

    gf_v_final = (gf_v_avg * w_temp) + (fin_v_racha_gf * w_racha) + (fin_v_away_gf * w_loc)
    gc_v_final = (gc_v_avg * w_temp) + (fin_v_racha_gc * w_racha) + (fin_v_away_gc * w_loc)

    if avg_liga_goles == 0: return {}
    
    xg_local = max((gf_l_final * gc_v_final) / avg_liga_goles, 0.05)
    xg_visita = max((gf_v_final * gc_l_final) / avg_liga_goles, 0.05)
    
    prob_l, prob_e, prob_v, prob_mas_05, prob_mas_15, prob_mas_25, prob_menos_35, prob_btts_yes = 0, 0, 0, 0, 0, 0, 0, 0
    prob_L_minus_15, prob_L_plus_15, prob_L_minus_25, prob_L_plus_25 = 0, 0, 0, 0
    prob_V_minus_15, prob_V_plus_15, prob_V_minus_25, prob_V_plus_25 = 0, 0, 0, 0
    marcadores = []

    for i in range(7): 
        for j in range(7): 
            p = poisson.pmf(i, xg_local) * poisson.pmf(j, xg_visita)
            marcadores.append((f"{i} - {j}", p * 100))
            if i > j: prob_l += p
            elif i == j: prob_e += p
            else: prob_v += p
            if i + j > 0.5: prob_mas_05 += p
            if i + j > 1.5: prob_mas_15 += p
            if i + j > 2.5: prob_mas_25 += p
            if i + j < 3.5: prob_menos_35 += p
            if i > 0 and j > 0: prob_btts_yes += p

            diff = i - j
            if diff >= 2: prob_L_minus_15 += p
            if diff >= -1: prob_L_plus_15 += p
            if diff >= 3: prob_L_minus_25 += p
            if diff >= -2: prob_L_plus_25 += p
            
            if diff <= -2: prob_V_minus_15 += p
            if diff <= 1: prob_V_plus_15 += p
            if diff <= -3: prob_V_minus_25 += p
            if diff <= 2: prob_V_plus_25 += p
            
    marcadores.sort(key=lambda x: x[1], reverse=True)
    top_marcadores = marcadores[:3]

    total_1x2 = prob_l + prob_e + prob_v
    if total_1x2 > 0:
        prob_l = (prob_l / total_1x2) * 100
        prob_e = (prob_e / total_1x2) * 100
        prob_v = (prob_v / total_1x2) * 100

    goles_ex_L = {str(k): poisson.pmf(k, xg_local) * 100 for k in range(3)}
    goles_ex_L['3+'] = (1 - sum(poisson.pmf(k, xg_local) for k in range(3))) * 100
    
    goles_ex_V = {str(k): poisson.pmf(k, xg_visita) * 100 for k in range(3)}
    goles_ex_V['3+'] = (1 - sum(poisson.pmf(k, xg_visita) for k in range(3))) * 100

    u05_L = poisson.pmf(0, xg_local) * 100
    o05_L = 100 - u05_L
    u15_L = u05_L + (poisson.pmf(1, xg_local) * 100)
    o15_L = 100 - u15_L
    u25_L = u15_L + (poisson.pmf(2, xg_local) * 100)
    o25_L = 100 - u25_L
    u35_L = u25_L + (poisson.pmf(3, xg_local) * 100)
    
    u05_V = poisson.pmf(0, xg_visita) * 100
    o05_V = 100 - u05_V
    u15_V = u05_V + (poisson.pmf(1, xg_visita) * 100)
    o15_V = 100 - u15_V
    u25_V = u15_V + (poisson.pmf(2, xg_visita) * 100)
    o25_V = 100 - u25_V
    u35_V = u25_V + (poisson.pmf(3, xg_visita) * 100)

    return {
        'xG_L': xg_local, 'xG_V': xg_visita,
        '1': prob_l, 'X': prob_e, '2': prob_v,
        '1X': prob_l + prob_e, 'X2': prob_e + prob_v, '12': prob_l + prob_v,
        'Over05': prob_mas_05 * 100, 'Over15': prob_mas_15 * 100, 'Over25': prob_mas_25 * 100,
        'Under25': (1 - prob_mas_25) * 100, 'Under35': prob_menos_35 * 100,
        'BTTS_Y': prob_btts_yes * 100, 'BTTS_N': (1 - prob_btts_yes) * 100,
        'Goles_L': goles_ex_L, 'Goles_V': goles_ex_V,
        'Top_Marcadores': top_marcadores,
        'Team_Totals_L': {'O05': o05_L, 'O15': o15_L, 'U15': u15_L, 'O25': o25_L, 'U25': u25_L, 'U35': u35_L},
        'Team_Totals_V': {'O05': o05_V, 'O15': o15_V, 'U15': u15_V, 'O25': o25_V, 'U25': u25_V, 'U35': u35_V},
        'Handicap_L': {'-1.5': prob_L_minus_15 * 100, '+1.5': prob_L_plus_15 * 100, '-2.5': prob_L_minus_25 * 100, '+2.5': prob_L_plus_25 * 100},
        'Handicap_V': {'-1.5': prob_V_minus_15 * 100, '+1.5': prob_V_plus_15 * 100, '-2.5': prob_V_minus_25 * 100, '+2.5': prob_V_plus_25 * 100},
        '_motor': 'v1'
    }

# --- CEREBRO DE FORTALEZA ---
def calcular_fortaleza(historial, equipo_buscado, es_local):
    if not historial: return 0, 0.0, 0
    pj, victorias, puntos = 0, 0, 0

    def es_partido_local(partido, eq):
        eq_clean = normalize_text(eq).replace('.', '').lower()
        loc_clean = normalize_text(partido["Local"]).replace('.', '').lower()
        vis_clean = normalize_text(partido["Visita"]).replace('.', '').lower()
        
        if eq_clean == loc_clean: return True
        if eq_clean == vis_clean: return False
        
        set_eq = set(eq_clean.split())
        set_loc = set(loc_clean.split())
        set_vis = set(vis_clean.split())
        
        match_loc = len(set_eq & set_loc)
        match_vis = len(set_eq & set_vis)
        
        if match_loc > match_vis: return True
        if match_vis > match_loc: return False
        return loc_clean in eq_clean

    for p in historial:
        try:
            somos_local = es_partido_local(p, equipo_buscado)
            if (es_local and somos_local) or (not es_local and not somos_local):
                g1, g2 = map(int, p["Res"].strip("[]").split(":"))
                gf = g1 if somos_local else g2
                gc = g2 if somos_local else g1
                pj += 1
                if gf > gc:
                    victorias += 1
                    puntos += 3
                elif gf == gc:
                    puntos += 1
        except Exception:
            pass
            
    if pj == 0: return 0, 0.0, 0
    win_rate = round((victorias / pj) * 100)
    ppp = round(puntos / pj, 2)
    return win_rate, ppp, pj

def extraer_promedios_corners(df_racha, equipo):
    if df_racha.empty: return -1, -1
    eq_norm = normalize_db_name(equipo)
    cor_a_favor, cor_en_contra, pj = 0, 0, 0
    if 'corners_local' not in df_racha.columns: return -1, -1
    for _, r in df_racha.iterrows():
        try:
            if pd.isna(r['corners_local']) or pd.isna(r['corners_visita']): continue
            cl, cv = float(r['corners_local']), float(r['corners_visita'])
            if r['local_norm'] == eq_norm:
                cor_a_favor += cl; cor_en_contra += cv
            else:
                cor_a_favor += cv; cor_en_contra += cl
            pj += 1
        except Exception:
            pass
    if pj == 0: return -1, -1
    return cor_a_favor / pj, cor_en_contra / pj

def calcular_prediccion_corners(racha_l, racha_v, local, visita):
    cf_l, cc_l = extraer_promedios_corners(racha_l, local)
    cf_v, cc_v = extraer_promedios_corners(racha_v, visita)
    if cf_l == -1 or cf_v == -1: return None 
    xc_local = (cf_l + cc_v) / 2
    xc_visita = (cf_v + cc_l) / 2
    prob_over_85, prob_over_95, prob_over_105 = 0, 0, 0
    for i in range(21):
        for j in range(21):
            p = poisson.pmf(i, xc_local) * poisson.pmf(j, xc_visita)
            total_c = i + j
            if total_c > 8.5: prob_over_85 += p
            if total_c > 9.5: prob_over_95 += p
            if total_c > 10.5: prob_over_105 += p
    return {'xC_L': xc_local, 'xC_V': xc_visita, 'Over85': prob_over_85 * 100, 'Over95': prob_over_95 * 100, 'Over105': prob_over_105 * 100}

# --- CARGA GLOBAL DE CONFIGURACIÓN ---
bankroll_ini, meta, saved_api_key = obtener_config_billetera()

# --- 4. INTERFAZ Y SIDEBAR ---
if 'pagina' not in st.session_state: st.session_state.pagina = 'Cartelera'

opciones_liga = [
    "Inglaterra - Premier League", "España - La Liga", "Italia - Serie A", 
    "Francia - Ligue 1", "Argentina", "Argentina - Primera Nacional", "Brasil", 
    "Brasil - Serie B", "Champions League", 
    "Libertadores", "Copa Sudamericana", "Europa League", 
    "Noruega - Eliteserien", "Estados Unidos - MLS", "México - Liga MX",
    "Bolivia - Div. Profesional", "Estonia - Meistriliiga", "Islandia - 2da División",
    "Dinamarca - Superliga", "China - Super League", "Suecia - Allsvenskan", "Islandia - 1ra División",
    # --- Ligas agregadas tras la validación (backtest agosto 2026) ---
    "Escocia - Premiership",
    "Chequia - Fortuna Liga",
    "Turquía - Süper Lig",
    "Ucrania - Premier League",
    "Finlandia - Veikkausliiga",
    "Japón - J1 League",
    "Suiza - Super League",
    "Países Bajos - Eerste Divisie",
    "Portugal - Liga 2",
    "Alemania - 2. Bundesliga",
]

with st.sidebar:
    st.title("💎 FPP - Data Purity")
    st.button("⚽ Cartelera", on_click=cambiar_pagina, args=('Cartelera',), width="stretch")
    st.button("🎯 Picks del Día", on_click=cambiar_pagina, args=('Picks',), width="stretch")
    st.button("📅 Calendario Global", on_click=cambiar_pagina, args=('Calendario',), width="stretch")
    if not MODO_NUBE:
        st.button("⭐ Mis Apuestas (Radar)", on_click=cambiar_pagina, args=('Favoritos',), width="stretch")
        st.button("💼 Billetera", on_click=cambiar_pagina, args=('Billetera',), width="stretch")
        st.button("📈 Calibración en Vivo", on_click=cambiar_pagina, args=('Calibracion',), width="stretch")
    else:
        st.caption(
            "☁️ Versión en línea: solo consulta. "
            "Las apuestas y la billetera están en la app de tu computadora."
        )
    movil.selector_vista()
    st.markdown("---")

    liga_sel = st.selectbox("Seleccionar Liga:", opciones_liga, key="sel_liga")

    # Estado del motor
    if motor_v2.motor_disponible():
        st.caption("🟢 Motor V2 activo (Dixon-Coles calibrado)")
    else:
        st.caption("🔴 Motor V2 no entrenado — corré `python entrenar_motor.py`")

    st.markdown("---")
    st.markdown("🤖 **Conexión IA**")
    api_key_input = st.text_input("🔑 API Key de Gemini:", value=saved_api_key, type="password", help="Se guardará automáticamente.")
    if api_key_input != saved_api_key:
        actualizar_config_billetera(bankroll_ini, meta, api_key_input)
        st.success("API Key guardada!")
        st.rerun()

# ==========================================
# PESTAÑA 1: CARTELERA
# ==========================================
if st.session_state.pagina == 'Cartelera':
    st.header(f"🏆 {liga_sel}")
    tabla = cargar_tabla_json(liga_sel)
    
    if not tabla.empty:
        lista = tabla['Club'].tolist()

        def encontrar_indice_seguro(nombre_buscado, lista_opciones, default_idx):
            if nombre_buscado in lista_opciones: return lista_opciones.index(nombre_buscado)
            n_buscado = normalize_text(nombre_buscado)
            for i, opc in enumerate(lista_opciones):
                n_opc = normalize_text(opc)
                if n_buscado in n_opc or n_opc in n_buscado: return i
            return default_idx

        if liga_sel in ["Argentina", "Argentina - Primera Nacional", "Estados Unidos - MLS"]:
            mitad = len(tabla) // 2
            col_za, col_zb = st.columns(2)

            if liga_sel == "Estados Unidos - MLS":
                titulo_a = "🔵 Conferencia Este (Eastern)"
                titulo_b = "🔴 Conferencia Oeste (Western)"
            else:
                titulo_a = "🔵 Zona A"
                titulo_b = "🔴 Zona B"

            orden_columnas = ['Pos', 'Club', 'Pts', 'PJ', 'G', 'E', 'P']
            
            with col_za:
                st.markdown(f"#### {titulo_a}")
                st.dataframe(tabla.iloc[:mitad] if not movil.es_movil() else tabla,
                             width="stretch", hide_index=True,
                             column_order=movil.columnas_tabla() if movil.es_movil() else orden_columnas)
            with col_zb:
                st.markdown(f"#### {titulo_b}")
                st.dataframe(tabla.iloc[mitad:], width="stretch", hide_index=True, column_order=orden_columnas)

            st.markdown("---")
            st.markdown("#### 📅 Fixture (Próximos Partidos)")
            try:
                if liga_sel == "Argentina": archivo_arg = 'data_json/argentina.json'
                elif liga_sel == "Argentina - Primera Nacional": archivo_arg = 'data_json/primera_nacional.json'
                else: archivo_arg = 'data_json/mls.json'
                
                with open(archivo_arg, 'r', encoding='utf-8') as f:
                    data_arg = json.load(f)
                
                fixture_data = data_arg.get("fixture", [])
                
                hoy = datetime.datetime.now() - datetime.timedelta(hours=5)
                manana = hoy + datetime.timedelta(days=1)
                meses = {1: 'Jan', 2: 'Feb', 3: 'Mar', 4: 'Apr', 5: 'May', 6: 'Jun', 7: 'Jul', 8: 'Aug', 9: 'Sep', 10: 'Oct', 11: 'Nov', 12: 'Dec'}
                dias_inv = {0: 'Mon', 1: 'Tue', 2: 'Wed', 3: 'Thu', 4: 'Fri', 5: 'Sat', 6: 'Sun'}
                
                fecha_hoy_str = f"{dias_inv[hoy.weekday()]} {hoy.day} {meses[hoy.month]}"
                fecha_manana_str = f"{dias_inv[manana.weekday()]} {manana.day} {meses[manana.month]}"
                
                fixture_filtrado = [p for p in fixture_data if p['Fecha'] in [fecha_hoy_str, fecha_manana_str]]
                
                if not fixture_filtrado and fixture_data:
                    fixture_filtrado = fixture_data[:5]
                    st.info("ℹ️ No hay partidos hoy ni mañana. Mostrando los próximos disponibles.")
                elif fixture_filtrado:
                    st.info("💡 **TIP:** Partidos de HOY y MAÑANA. Clic en **'📥 Cargar'**.")

                if fixture_filtrado:
                    c_f, c_h, c_l, c_v, c_b = st.columns([1.5, 1, 3, 3, 1.5])
                    c_f.markdown("**Fecha**")
                    c_h.markdown("**Hora**")
                    c_l.markdown("**Local**")
                    c_v.markdown("**Visita**")
                    c_b.markdown("**Acción**")
                    st.markdown("<hr style='margin: 0px; padding: 0px;'>", unsafe_allow_html=True)
                    
                    for idx, p in enumerate(fixture_filtrado):
                        col_f, col_h, col_l, col_v, col_btn = st.columns([1.5, 1, 3, 3, 1.5])
                        
                        fecha_display = "🚨 **HOY**" if p['Fecha'] == fecha_hoy_str else p['Fecha']
                        
                        col_f.write(fecha_display)
                        col_h.write(p['Hora'])
                        col_l.write(p['Local'])
                        col_v.write(p['Visita'])
                        
                        if col_btn.button("📥 Cargar", key=f"btn_fix_arg_{idx}", width="stretch"):
                            local_seguro = lista[encontrar_indice_seguro(p['Local'], lista, 0)]
                            visita_segura = lista[encontrar_indice_seguro(p['Visita'], lista, 1 if len(lista)>1 else 0)]
                            st.session_state.res_l = local_seguro
                            st.session_state.res_v = visita_segura
                            st.session_state.last_l = local_seguro
                            st.session_state.last_v = visita_segura
                            st.session_state.analizar = True
                            st.rerun()
                        st.markdown("<hr style='margin: 0px; padding: 0px; border-color: #333;'>", unsafe_allow_html=True)
            except Exception:
                st.warning("⚠️ No se pudo cargar el archivo correspondiente. Ejecuta el scraper.")
                
        elif liga_sel in [
            "Brasil", "Brasil - Serie B", "Noruega - Eliteserien", 
            "México - Liga MX", 
            "Estonia - Meistriliiga", "Islandia - 2da División", 
            "Dinamarca - Superliga", "China - Super League", 
            "Suecia - Allsvenskan", "Islandia - 1ra División",
            "Escocia - Premiership",
            "Chequia - Fortuna Liga",
            "Turquía - Süper Lig",
            "Ucrania - Premier League",
            "Finlandia - Veikkausliiga",
            "Japón - J1 League",
            "Suiza - Super League",
            "Países Bajos - Eerste Divisie",
            "Portugal - Liga 2",
            "Alemania - 2. Bundesliga",
            # --- Las cinco grandes: antes solo mostraban la tabla ---
            "Inglaterra - Premier League",
            "España - La Liga",
            "Italia - Serie A",
            "Francia - Ligue 1",
            "Bolivia - Div. Profesional",
        ]:
            col_tabla, col_fixture = st.columns([1.3, 1])
            
            with col_tabla:
                st.markdown("#### 🏆 Tabla de Posiciones")
                st.dataframe(tabla, width="stretch", hide_index=True, column_order=movil.columnas_tabla())
                
            with col_fixture:
                st.markdown("#### 📅 Fixture (Próximos Partidos)")
                archivo_fix = None
                try:
                    mapa_archivos_fix = {
                        "Brasil": 'data_json/brazil.json',
                        "Argentina": 'data_json/argentina.json',
                        "Brasil - Serie B": 'data_json/serie_b_brasil.json',
                        "Noruega - Eliteserien": 'data_json/norway.json',
                        "Estados Unidos - MLS": 'data_json/mls.json',
                        "México - Liga MX": 'data_json/mexico.json',
                        "Estonia - Meistriliiga": 'data_json/estonia.json',
                        "Islandia - 2da División": 'data_json/iceland2.json',
                        "Dinamarca - Superliga": 'data_json/denmark.json',
                        "China - Super League": 'data_json/china.json',
                        "Suecia - Allsvenskan": 'data_json/sweden.json',
                        "Islandia - 1ra División": 'data_json/iceland.json',
                        "Inglaterra - Premier League": 'data_json/england.json',
                        "España - La Liga": 'data_json/spain.json',
                        "Italia - Serie A": 'data_json/italy.json',
                        "Francia - Ligue 1": 'data_json/france.json',
                        "Bolivia - Div. Profesional": 'data_json/bolivia.json',
                        "Escocia - Premiership": 'data_json/scotland.json',
                        "Chequia - Fortuna Liga": 'data_json/czechrepublic.json',
                        "Turquía - Süper Lig": 'data_json/turkey.json',
                        "Ucrania - Premier League": 'data_json/ukraine.json',
                        "Finlandia - Veikkausliiga": 'data_json/finland.json',
                        "Japón - J1 League": 'data_json/japan.json',
                        "Suiza - Super League": 'data_json/switzerland.json',
                        "Países Bajos - Eerste Divisie": 'data_json/netherlands2.json',
                        "Portugal - Liga 2": 'data_json/portugal2.json',
                        "Alemania - 2. Bundesliga": 'data_json/germany2.json',
                    }
                    archivo_fix = mapa_archivos_fix.get(liga_sel)
                    with open(archivo_fix, 'r', encoding='utf-8') as f:
                        data_fix = json.load(f)
                    fixture_data = data_fix.get("fixture", [])
                    
                    hoy = datetime.datetime.now() - datetime.timedelta(hours=5)
                    manana = hoy + datetime.timedelta(days=1)
                    meses = {1: 'Jan', 2: 'Feb', 3: 'Mar', 4: 'Apr', 5: 'May', 6: 'Jun', 7: 'Jul', 8: 'Aug', 9: 'Sep', 10: 'Oct', 11: 'Nov', 12: 'Dec'}
                    dias_inv = {0: 'Mon', 1: 'Tue', 2: 'Wed', 3: 'Thu', 4: 'Fri', 5: 'Sat', 6: 'Sun'}
                    
                    fecha_hoy_str = f"{dias_inv[hoy.weekday()]} {hoy.day} {meses[hoy.month]}"
                    fecha_manana_str = f"{dias_inv[manana.weekday()]} {manana.day} {meses[manana.month]}"
                    
                    fixture_filtrado = [p for p in fixture_data if p['Fecha'] in [fecha_hoy_str, fecha_manana_str]]
                    
                    if not fixture_filtrado and fixture_data:
                        fixture_filtrado = fixture_data[:5]
                        st.info("ℹ️ No hay partidos hoy ni mañana. Próximos 5:")
                    elif fixture_filtrado:
                        st.info("💡 **TIP:** Partidos de HOY y MAÑANA. Clic en **'📥 Cargar'**.")
                        
                    if fixture_filtrado:
                        cf_fh, cf_p, cf_b = st.columns([2.5, 4, 2])
                        cf_fh.markdown("**Fecha / Hora**")
                        cf_p.markdown("**Partido**")
                        cf_b.markdown("**Acción**")
                        st.markdown("<hr style='margin: 0px; padding: 0px;'>", unsafe_allow_html=True)
                        
                        for idx, p in enumerate(fixture_filtrado):
                            cf1, cf2, cf3 = st.columns([2.5, 4, 2])
                            
                            fecha_display = "🚨 HOY" if p['Fecha'] == fecha_hoy_str else p['Fecha']
                            
                            cf1.caption(f"{fecha_display} | {p['Hora']}")
                            cf2.write(f"{p['Local']} vs {p['Visita']}")
                            if cf3.button("📥 Cargar", key=f"btn_fix_oth_{idx}", width="stretch"):
                                local_seguro = lista[encontrar_indice_seguro(p['Local'], lista, 0)]
                                visita_segura = lista[encontrar_indice_seguro(p['Visita'], lista, 1 if len(lista)>1 else 0)]
                                st.session_state.res_l = local_seguro
                                st.session_state.res_v = visita_segura
                                st.session_state.last_l = local_seguro
                                st.session_state.last_v = visita_segura
                                st.session_state.analizar = True
                                st.rerun()
                            st.markdown("<hr style='margin: 0px; padding: 0px; border-color: #333;'>", unsafe_allow_html=True)
                    else:
                        st.info("No hay partidos programados en el fixture en este momento.")
                except Exception:
                    st.warning(f"⚠️ No se pudo cargar el archivo {archivo_fix}. Asegúrate de ejecutar el scraper.")

        else: 
            st.dataframe(tabla, width="stretch", hide_index=True, column_order=movil.columnas_tabla())
        
        st.markdown("---")
        with st.expander("📅 Ver Cronograma de Partidos y Resultados (Google Live)", expanded=False):
            mapa_busquedas = {
                "Inglaterra - Premier League": "partidos+premier+league",
                "España - La Liga": "partidos+liga+española",
                "Italia - Serie A": "partidos+serie+a+italia",
                "Francia - Ligue 1": "partidos+ligue+1+francia",
                "Argentina": "partidos+liga+profesional+argentina",
                "Argentina - Primera Nacional": "partidos+primera+nacional+argentina",
                "Brasil": "partidos+brasileirao+serie+a",
                "Brasil - Serie B": "partidos+brasileirao+serie+b",
                "Champions League": "partidos+champions+league",
                "Libertadores": "partidos+copa+libertadores",
                "Copa Sudamericana": "partidos+copa+sudamericana",
                "Europa League": "partidos+europa+league",
                "Estados Unidos - MLS": "partidos+mls",
                "México - Liga MX": "partidos+liga+mx",
                "Bolivia - Div. Profesional": "partidos+liga+boliviana",
                "Estonia - Meistriliiga": "partidos+liga+estonia",
                "Islandia - 2da División": "partidos+liga+islandesa",
                "Dinamarca - Superliga": "partidos+liga+dinamarca",
                "China - Super League": "partidos+liga+china",
                "Suecia - Allsvenskan": "partidos+liga+suecia",
                "Islandia - 1ra División": "partidos+liga+islandesa",
                "Escocia - Premiership": "partidos+premiership+escocia",
                "Chequia - Fortuna Liga": "partidos+liga+chequia",
                "Turquía - Süper Lig": "partidos+super+lig+turquia",
                "Ucrania - Premier League": "partidos+liga+ucrania",
                "Finlandia - Veikkausliiga": "partidos+veikkausliiga",
                "Japón - J1 League": "partidos+j1+league+japon",
                "Suiza - Super League": "partidos+super+league+suiza",
                "Países Bajos - Eerste Divisie": "partidos+eerste+divisie",
                "Portugal - Liga 2": "partidos+liga+portugal+2",
                "Alemania - 2. Bundesliga": "partidos+2+bundesliga",
            }
            query = mapa_busquedas.get(liga_sel, "partidos+de+futbol")
            url_widget = f"https://www.google.com/search?igu=1&q={query}"
            st.info("💡 Desliza hacia abajo dentro del cuadro para ver fechas pasadas o futuras. La hora se ajusta automáticamente a tu país.")
            # st.iframe (nuevo) no acepta 'scrolling'; el de components sí.
            # Se prueban las variantes en orden hasta que una funcione.
            try:
                st.iframe(url_widget, height=650)
            except (AttributeError, TypeError):
                try:
                    st.components.v1.iframe(url_widget, height=650, scrolling=True)
                except Exception as _e:
                    st.warning(f"No se pudo cargar el cronograma en línea: {_e}")

        st.markdown("---")
        st.markdown(f"### 🔬 Radiografía Matemática: {liga_sel}")
        st.caption("ℹ️ Datos descriptivos de la liga. **No son recomendaciones de apuesta**: el backtest mostró que los mercados de goles no tienen ventaja estadística demostrable.")
        
        total_pj = tabla['PJ'].sum()
        if total_pj > 0:
            promedio_goles_liga = tabla['GF'].sum() / (total_pj / 2)
            
            if promedio_goles_liga >= 2.8:
                perfil_liga = "🔥 **Liga sumamente ofensiva**\n(muchos goles por partido)"
            elif promedio_goles_liga <= 2.3:
                perfil_liga = "🛡️ **Liga cerrada y táctica**\n(pocos goles por partido)"
            elif promedio_goles_liga > 2.5:
                perfil_liga = "⚽ **Liga con tendencia leve al Over**"
            else:
                perfil_liga = "⚖️ **Liga equilibrada**"
            
            tabla_goles = tabla.copy()
            tabla_goles['Goles_Partido'] = (tabla_goles['GF'] + tabla_goles['GC']) / tabla_goles['PJ'].replace(0, 1)
            top_3_goles = tabla_goles.sort_values(by='Goles_Partido', ascending=False).head(3)
            
            col_i1, col_i2, col_i3 = st.columns(3)
            with col_i1:
                st.info(f"📊 **Promedio de la Liga**\n### {promedio_goles_liga:.2f}\n*goles por partido*")
            with col_i2:
                st.success(f"📈 **Perfil de la liga**\n\n{perfil_liga}")
            with col_i3:
                texto_equipos = "\n".join([f"- **{r['Club']}** ({r['Goles_Partido']:.1f} g/p)" for _, r in top_3_goles.iterrows()])
                st.warning(f"💥 **Equipos con más goles (Top 3)**\n{texto_equipos}")
                
        st.markdown("---")

        # --- BLOQUE RADAR ---
        ligas_radar = ["Brasil", "Brasil - Serie B", "Noruega - Eliteserien", "Argentina", "Argentina - Primera Nacional", "Estados Unidos - MLS", "México - Liga MX", "Estonia - Meistriliiga", "Islandia - 2da División", "Dinamarca - Superliga", "China - Super League", "Suecia - Allsvenskan", "Islandia - 1ra División"] + [
            "Escocia - Premiership",
            "Chequia - Fortuna Liga",
            "Turquía - Süper Lig",
            "Ucrania - Premier League",
            "Finlandia - Veikkausliiga",
            "Japón - J1 League",
            "Suiza - Super League",
            "Países Bajos - Eerste Divisie",
            "Portugal - Liga 2",
            "Alemania - 2. Bundesliga",
        ]
        
        if liga_sel in ligas_radar:
            with st.expander("🏆 Radar Automático (Filtro de Oro de la Jornada)", expanded=True):
                try:
                    mapa_archivos_avanzados = {
                        "Brasil - Serie B": 'data_json/serie_b_brasil.json',
                        "Argentina - Primera Nacional": 'data_json/primera_nacional.json',
                        "Argentina": 'data_json/argentina.json',
                        "Noruega - Eliteserien": 'data_json/norway.json',
                        "Brasil": 'data_json/brazil.json',
                        "Estados Unidos - MLS": 'data_json/mls.json',
                        "México - Liga MX": 'data_json/mexico.json',
                        "Estonia - Meistriliiga": 'data_json/estonia.json',
                        "Islandia - 2da División": 'data_json/iceland2.json',
                        "Dinamarca - Superliga": 'data_json/denmark.json',
                        "China - Super League": 'data_json/china.json',
                        "Suecia - Allsvenskan": 'data_json/sweden.json',
                        "Islandia - 1ra División": 'data_json/iceland.json',
                        "Inglaterra - Premier League": 'data_json/england.json',
                        "España - La Liga": 'data_json/spain.json',
                        "Italia - Serie A": 'data_json/italy.json',
                        "Francia - Ligue 1": 'data_json/france.json',
                        "Bolivia - Div. Profesional": 'data_json/bolivia.json',
                        "Escocia - Premiership": 'data_json/scotland.json',
                        "Chequia - Fortuna Liga": 'data_json/czechrepublic.json',
                        "Turquía - Süper Lig": 'data_json/turkey.json',
                        "Ucrania - Premier League": 'data_json/ukraine.json',
                        "Finlandia - Veikkausliiga": 'data_json/finland.json',
                        "Japón - J1 League": 'data_json/japan.json',
                        "Suiza - Super League": 'data_json/switzerland.json',
                        "Países Bajos - Eerste Divisie": 'data_json/netherlands2.json',
                        "Portugal - Liga 2": 'data_json/portugal2.json',
                        "Alemania - 2. Bundesliga": 'data_json/germany2.json',
                    }
                    archivo_json = mapa_archivos_avanzados.get(liga_sel)
                    with open(archivo_json, 'r', encoding='utf-8') as f:
                        data_avanzada_radar = json.load(f)
                    
                    mejores_locales = []
                    peores_visitantes = []
                    locales_vistos = set()
                    visitantes_vistos = set()
                    
                    hoy = datetime.datetime.now() - datetime.timedelta(hours=5)
                    manana = hoy + datetime.timedelta(days=1)
                    meses = {1: 'Jan', 2: 'Feb', 3: 'Mar', 4: 'Apr', 5: 'May', 6: 'Jun', 7: 'Jul', 8: 'Aug', 9: 'Sep', 10: 'Oct', 11: 'Nov', 12: 'Dec'}
                    dias_inv = {0: 'Mon', 1: 'Tue', 2: 'Wed', 3: 'Thu', 4: 'Fri', 5: 'Sat', 6: 'Sun'}
                    
                    fecha_hoy_str = f"{dias_inv[hoy.weekday()]} {hoy.day} {meses[hoy.month]}"
                    fecha_manana_str = f"{dias_inv[manana.weekday()]} {manana.day} {meses[manana.month]}"
                    
                    fixture_completo = data_avanzada_radar.get("fixture", [])
                    fixture_radar = [p for p in fixture_completo if p.get('Fecha') in [fecha_hoy_str, fecha_manana_str]]
                    
                    if not fixture_radar and fixture_completo:
                        fixture_radar = fixture_completo[:8]
                    
                    estadisticas_radar = data_avanzada_radar.get("estadisticas_avanzadas", {})
                    
                    for partido in fixture_radar:
                        loc_r = partido["Local"]
                        vis_r = partido["Visita"]
                        fecha_r = partido.get("Fecha", "Próximamente")

                        h_loc = estadisticas_radar.get(loc_r, {}).get("historial", [])
                        if not h_loc:
                            for k in estadisticas_radar.keys():
                                if normalize_db_name(loc_r) == normalize_db_name(k) or normalize_db_name(loc_r) in normalize_db_name(k) or normalize_db_name(k) in normalize_db_name(loc_r):
                                    h_loc = estadisticas_radar[k].get("historial", [])
                                    break
                                    
                        h_vis = estadisticas_radar.get(vis_r, {}).get("historial", [])
                        if not h_vis:
                            for k in estadisticas_radar.keys():
                                if normalize_db_name(vis_r) == normalize_db_name(k) or normalize_db_name(vis_r) in normalize_db_name(k) or normalize_db_name(k) in normalize_db_name(vis_r):
                                    h_vis = estadisticas_radar[k].get("historial", [])
                                    break
                        
                        if h_loc and h_vis:
                            w_loc, p_loc, pj_l = calcular_fortaleza(h_loc, loc_r, True)
                            w_vis, p_vis, pj_v = calcular_fortaleza(h_vis, vis_r, False)
                            
                            if loc_r not in locales_vistos:
                                locales_vistos.add(loc_r)
                                if pj_l >= 4 and p_loc >= 2.0:
                                    mejores_locales.append((loc_r, p_loc, vis_r, fecha_r))
                                    
                            if vis_r not in visitantes_vistos:
                                visitantes_vistos.add(vis_r)
                                if pj_v >= 4 and p_vis <= 0.7:
                                    peores_visitantes.append((vis_r, p_vis, loc_r, fecha_r))
                    
                    c1_rad, c2_rad = st.columns(2)
                    with c1_rad:
                        st.error("🏰 **Fortalezas (Mejores Locales)**")
                        st.caption("Promedian +2.0 pts en casa. Dato descriptivo: contrastalo con la probabilidad del modelo.")
                        if mejores_locales:
                            mejores_locales_ord = sorted(mejores_locales, key=lambda x: (1 if x[3] == fecha_hoy_str else (2 if x[3] == fecha_manana_str else 3), -x[1]))
                            for m in mejores_locales_ord:
                                if m[3] == fecha_hoy_str:
                                    st.write(f"🚨 **HOY** ➔ ✅ **{m[0]}** ({m[1]} ppp) vs {m[2]}")
                                else:
                                    st.write(f"✅ **{m[0]}** ({m[1]} ppp) vs {m[2]}  *(📅 {m[3]})*")
                        else:
                            st.info("No hay fortalezas destacadas para hoy o mañana.")
                    
                    with c2_rad:
                        st.success("🧳 **Cenicientas (Peores Visitantes)**")
                        st.caption("Promedian 0.7 pts o menos de visita. Dato descriptivo: contrastalo con la probabilidad del modelo.")
                        if peores_visitantes:
                            peores_visitantes_ord = sorted(peores_visitantes, key=lambda x: (1 if x[3] == fecha_hoy_str else (2 if x[3] == fecha_manana_str else 3), x[1]))
                            for m in peores_visitantes_ord:
                                if m[3] == fecha_hoy_str:
                                    st.write(f"🚨 **HOY** ➔ ❌ **{m[0]}** ({m[1]} ppp) jugando vs {m[2]}")
                                else:
                                    st.write(f"❌ **{m[0]}** ({m[1]} ppp) jugando vs {m[2]}  *(📅 {m[3]})*")
                        else:
                            st.info("No hay visitantes desastrosos para hoy o mañana.")
                except Exception:
                    pass
            st.markdown("---")

        saved_l = st.session_state.get('last_l', lista[0] if len(lista) > 0 else "")
        saved_v = st.session_state.get('last_v', lista[1] if len(lista) > 1 else (lista[0] if len(lista) > 0 else ""))
        
        idx_l = encontrar_indice_seguro(saved_l, lista, 0)
        idx_v = encontrar_indice_seguro(saved_v, lista, 1 if len(lista) > 1 else 0)

        # Si el valor guardado ya no pertenece a la liga actual, se reinicia
        if st.session_state.get("res_l") not in lista:
            st.session_state.res_l = lista[idx_l]
        if st.session_state.get("res_v") not in lista:
            st.session_state.res_v = lista[idx_v]

        c1, c2 = st.columns(2)
        l = c1.selectbox("🏠 Local", lista, key="res_l")
        v = c2.selectbox("✈️ Visita", lista, key="res_v")
        
        if st.session_state.get('last_l') != l or st.session_state.get('last_v') != v or st.session_state.get('last_liga') != liga_sel:
            st.session_state.last_l = l
            st.session_state.last_v = v
            st.session_state.last_liga = liga_sel
            st.session_state.analizar = False
            st.session_state.cuotas_restauradas = {}
        
        if st.button("📊 Analizar y Buscar Valor", width="stretch"):
            st.session_state.analizar = True
        
        if st.session_state.get('analizar'):
            st.markdown("### 🥊 Cara a Cara (Tale of the Tape)")
            
            stats_L = tabla[tabla['Club'] == l].iloc[0]
            stats_V = tabla[tabla['Club'] == v].iloc[0]
            
            _, racha_l_df, racha_v_df, gf_lh, gc_lh, gf_va, gc_va = obtener_h2h_y_rachas(l, v)
            
            detalles_forma_L, _ = procesar_forma_detallada(racha_l_df, l)
            detalles_forma_V, _ = procesar_forma_detallada(racha_v_df, v)

            hist_L = []
            hist_V = []
            data_avanzada = {}

            ligas_json_avanzadas = [
                "Inglaterra - Premier League", "España - La Liga", "Italia - Serie A", "Francia - Ligue 1",
                "Brasil", "Brasil - Serie B", "Noruega - Eliteserien", "Argentina", "Argentina - Primera Nacional", 
                "Estados Unidos - MLS", "México - Liga MX", "Estonia - Meistriliiga", "Islandia - 2da División", 
                "Dinamarca - Superliga", "China - Super League", "Suecia - Allsvenskan", "Islandia - 1ra División",
                "Escocia - Premiership",
                "Chequia - Fortuna Liga",
                "Turquía - Süper Lig",
                "Ucrania - Premier League",
                "Finlandia - Veikkausliiga",
                "Japón - J1 League",
                "Suiza - Super League",
                "Países Bajos - Eerste Divisie",
                "Portugal - Liga 2",
                "Alemania - 2. Bundesliga",
            ]
            
            if liga_sel in ligas_json_avanzadas:
                try:
                    mapa_archivos_avanzados = {
                        "Inglaterra - Premier League": 'data_json/england.json',
                        "España - La Liga": 'data_json/spain.json',
                        "Italia - Serie A": 'data_json/italy.json',
                        "Francia - Ligue 1": 'data_json/france.json',
                        "Brasil - Serie B": 'data_json/serie_b_brasil.json',
                        "Argentina - Primera Nacional": 'data_json/primera_nacional.json',
                        "Argentina": 'data_json/argentina.json',
                        "Noruega - Eliteserien": 'data_json/norway.json',
                        "Brasil": 'data_json/brazil.json',
                        "Estados Unidos - MLS": 'data_json/mls.json',
                        "México - Liga MX": 'data_json/mexico.json',
                        "Estonia - Meistriliiga": 'data_json/estonia.json',
                        "Islandia - 2da División": 'data_json/iceland2.json',
                        "Dinamarca - Superliga": 'data_json/denmark.json',
                        "China - Super League": 'data_json/china.json',
                        "Suecia - Allsvenskan": 'data_json/sweden.json',
                        "Islandia - 1ra División": 'data_json/iceland.json',
                        "Inglaterra - Premier League": 'data_json/england.json',
                        "España - La Liga": 'data_json/spain.json',
                        "Italia - Serie A": 'data_json/italy.json',
                        "Francia - Ligue 1": 'data_json/france.json',
                        "Bolivia - Div. Profesional": 'data_json/bolivia.json',
                        "Escocia - Premiership": 'data_json/scotland.json',
                        "Chequia - Fortuna Liga": 'data_json/czechrepublic.json',
                        "Turquía - Süper Lig": 'data_json/turkey.json',
                        "Ucrania - Premier League": 'data_json/ukraine.json',
                        "Finlandia - Veikkausliiga": 'data_json/finland.json',
                        "Japón - J1 League": 'data_json/japan.json',
                        "Suiza - Super League": 'data_json/switzerland.json',
                        "Países Bajos - Eerste Divisie": 'data_json/netherlands2.json',
                        "Portugal - Liga 2": 'data_json/portugal2.json',
                        "Alemania - 2. Bundesliga": 'data_json/germany2.json',
                    }
                    archivo_json = mapa_archivos_avanzados.get(liga_sel)
                    with open(archivo_json, 'r', encoding='utf-8') as f:
                        data_avanzada = json.load(f)
                    
                    estadisticas = data_avanzada.get("estadisticas_avanzadas", {})

                    for k in estadisticas.keys():
                        nl = normalize_db_name(l)
                        nk = normalize_db_name(k)
                        if nl == nk:
                            hist_L.extend(estadisticas[k].get("historial", []))
                        elif (nl in nk or nk in nl) and len(nl) > 4 and len(nk) > 4:
                            hist_L.extend(estadisticas[k].get("historial", []))
                    
                    for k in estadisticas.keys():
                        nv = normalize_db_name(v)
                        nk = normalize_db_name(k)
                        if nv == nk:
                            hist_V.extend(estadisticas[k].get("historial", []))
                        elif (nv in nk or nk in nv) and len(nv) > 4 and len(nk) > 4:
                            hist_V.extend(estadisticas[k].get("historial", []))

                    def ordenar_historial(hist):
                        if not hist: return hist
                        meses_map = {'Jan': 1, 'Feb': 2, 'Mar': 3, 'Apr': 4, 'May': 5, 'Jun': 6, 'Jul': 7, 'Aug': 8, 'Sep': 9, 'Oct': 10, 'Nov': 11, 'Dec': 12}
                        
                        meses_presentes = set()
                        for item in hist:
                            try:
                                p = str(item.get("Fecha", "")).replace(",", "").split()
                                m_str = p[-1][:3] if len(p) >= 1 else ""
                                if m_str not in meses_map and len(p) > 1: m_str = p[1][:3]
                                if m_str in meses_map: meses_presentes.add(meses_map[m_str])
                            except Exception:
                                pass
                        
                        cruza_ano = (12 in meses_presentes) and (1 in meses_presentes)
                        
                        def fecha_val(item):
                            try:
                                p = str(item.get("Fecha", "")).replace(",", "").split()
                                dia = int(p[-2]) if len(p) >= 2 and p[-2].isdigit() else int(p[0]) if p and p[0].isdigit() else 0
                                m_str = p[-1][:3] if len(p) >= 1 else ""
                                if m_str not in meses_map and len(p) > 1: m_str = p[1][:3]
                                mes = meses_map.get(m_str, 0)
                                if cruza_ano and mes < 7: mes += 12 
                                return mes * 100 + dia
                            except Exception:
                                return 0
                            
                        hist_limpio = []
                        vistos = set()
                        for p in hist:
                            id_partido = f"{p.get('Local','').strip()}_{p.get('Visita','').strip()}_{p.get('Res','')}"
                            if id_partido not in vistos:
                                vistos.add(id_partido)
                                hist_limpio.append(p)
                                
                        return sorted(hist_limpio, key=fecha_val)

                    hist_L_ord = ordenar_historial(hist_L)
                    hist_V_ord = ordenar_historial(hist_V)
                    
                    def armar_forma_json(equipo, hist_ordenado):
                        if not hist_ordenado: return ["No hay registros previos en SoccerStats"]
                        detalles = []
                        for r in reversed(hist_ordenado[-5:]): 
                            try:
                                gl, gv = map(int, r["Res"].strip("[]").split(":"))
                                es_loc = normalize_text(equipo) in normalize_text(r["Local"])
                                cond = "🏠 L" if es_loc else "✈️ V"
                                rival = r["Visita"] if es_loc else r["Local"]
                                res_str = f"**{gl} - {gv}**" if es_loc else f"**{gv} - {gl}**"
                                icono = '✅' if r["Status"] == "🟢" else '➖' if r["Status"] == "🟡" else '❌'
                                detalles.append(f"{icono} {res_str} | {cond} vs {rival.title()}")
                            except Exception:
                                pass
                        return detalles
                    
                    if hist_L_ord: detalles_forma_L = armar_forma_json(l, hist_L_ord)
                    if hist_V_ord: detalles_forma_V = armar_forma_json(v, hist_V_ord)
                    
                    def json_to_df(hist_ordenado):
                        if not hist_ordenado: return pd.DataFrame()
                        filas = []
                        for p in hist_ordenado:
                            try:
                                gl, gv = map(int, p["Res"].strip("[]").split(":"))
                                filas.append({'local_norm': normalize_db_name(p["Local"]), 'visita_norm': normalize_db_name(p["Visita"]), 'goles_local': gl, 'goles_visita': gv})
                            except Exception:
                                pass
                        return pd.DataFrame(filas)

                    if hist_L_ord: racha_l_df = json_to_df(hist_L_ord[-5:])
                    if hist_V_ord: racha_v_df = json_to_df(hist_V_ord[-5:])

                    if hist_L:
                        df_full_L = json_to_df(hist_L)
                        if not df_full_L.empty:
                            df_home = df_full_L[df_full_L['local_norm'] == normalize_db_name(l)].tail(10)
                            if not df_home.empty:
                                gf_lh = df_home['goles_local'].mean()
                                gc_lh = df_home['goles_visita'].mean()

                    if hist_V:
                        df_full_V = json_to_df(hist_V)
                        if not df_full_V.empty:
                            df_away = df_full_V[df_full_V['visita_norm'] == normalize_db_name(v)].tail(10)
                            if not df_away.empty:
                                gf_va = df_away['goles_visita'].mean()
                                gc_va = df_away['goles_local'].mean()
                            
                except Exception:
                    pass

            panel_A, panel_B, panel_C = st.columns([2, 1, 2])
            with panel_A:
                st.markdown(f"<h3 style='text-align: center; color: #4CAF50;'>{l}</h3>", unsafe_allow_html=True)
                st.markdown(f"**Posición:** {stats_L['Pos']} | **Puntos:** {stats_L['Pts']}")
                st.markdown(f"**Global:** {stats_L['GF']} GF | {stats_L['GC']} GC")
                st.markdown("**Forma Reciente (Últimos 5):**")
                for partido in detalles_forma_L: st.caption(partido)

            with panel_B:
                st.markdown("<h2 style='text-align: center; margin-top: 50px;'>VS</h2>", unsafe_allow_html=True)

            with panel_C:
                st.markdown(f"<h3 style='text-align: center; color: #F44336;'>{v}</h3>", unsafe_allow_html=True)
                st.markdown(f"**Posición:** {stats_V['Pos']} | **Puntos:** {stats_V['Pts']}")
                st.markdown(f"**Global:** {stats_V['GF']} GF | {stats_V['GC']} GC")
                st.markdown("**Forma Reciente (Últimos 5):**")
                for partido in detalles_forma_V: st.caption(partido)
            
            st.divider()

            # --- SEMÁFORO VISUAL ---
            if liga_sel in ligas_json_avanzadas:
                st.markdown("#### 🚦 Semáforo de Fortaleza (Condición Real)")
                st.caption("ℹ️ Calculado con datos de **TODA la temporada actual**. Filtro estricto: Local en casa 🏠 / Visita fuera ✈️")
                
                win_loc, ppp_loc, pj_loc = calcular_fortaleza(hist_L, l, es_local=True)
                win_vis, ppp_vis, pj_vis = calcular_fortaleza(hist_V, v, es_local=False)
                
                col1_sem, col2_sem, col3_sem = st.columns(3)
                
                with col1_sem:
                    st.info(f"🏠 **{l} (En Casa)**")
                    st.metric("Puntos Por Partido (PPP)", f"{ppp_loc} pts", f"{pj_loc} jugados de local")
                    color_loc = "🟢" if win_loc >= 50 else "🟡" if win_loc >= 30 else "🔴"
                    st.write(f"{color_loc} **Win Rate:** {win_loc}%")
                
                with col2_sem:
                    st.markdown("<h2 style='text-align: center; color: gray;'>VS</h2>", unsafe_allow_html=True)
                    if ppp_loc > 1.8 and ppp_vis < 1.0:
                        st.success("📌 **Lectura:** Local muy superior en su condición")
                    elif ppp_vis > 1.8 and ppp_loc < 1.0:
                        st.success("📌 **Lectura:** Visita muy superior en su condición")
                    else:
                        st.warning("⚖️ **Lectura:** Partido parejo en la condición")
                    st.caption("Dato descriptivo. La decisión sale del modelo + la cuota.")
                
                with col3_sem:
                    st.info(f"✈️ **{v} (De Visita)**")
                    st.metric("Puntos Por Partido (PPP)", f"{ppp_vis} pts", f"{pj_vis} jugados de visita")
                    color_vis = "🟢" if win_vis >= 50 else "🟡" if win_vis >= 30 else "🔴"
                    st.write(f"{color_vis} **Win Rate:** {win_vis}%")
                st.divider()

            if liga_sel in ligas_json_avanzadas:
                try:
                    def calc_ht_estricto(equipo_nombre, historial, es_local):
                        if not historial: return 0, 0, 0, 0
                        
                        historial_filtrado = []
                        
                        def es_partido_local(partido, eq):
                            eq_clean = normalize_text(eq).replace('.', '').lower()
                            loc_clean = normalize_text(partido["Local"]).replace('.', '').lower()
                            vis_clean = normalize_text(partido["Visita"]).replace('.', '').lower()
                            if eq_clean == loc_clean: return True
                            if eq_clean == vis_clean: return False
                            set_eq = set(eq_clean.split())
                            set_loc = set(loc_clean.split())
                            set_vis = set(vis_clean.split())
                            if len(set_eq & set_loc) > len(set_eq & set_vis): return True
                            if len(set_eq & set_vis) > len(set_eq & set_loc): return False
                            return loc_clean in eq_clean

                        for p in reversed(historial):
                            try:
                                jugo_local = es_partido_local(p, equipo_nombre)
                                if (es_local and jugo_local) or (not es_local and not jugo_local):
                                    historial_filtrado.append(p)
                                if len(historial_filtrado) == 10: break
                            except Exception:
                                pass
                            
                        pj = len(historial_filtrado)
                        if pj == 0: return 0, 0, 0, 0
                        
                        o05, o15, gana = 0, 0, 0
                        for partido in historial_filtrado:
                            ht_score = partido.get("HT", "-")
                            if "-" in ht_score and ht_score.replace("-", "").isdigit():
                                g1, g2 = map(int, ht_score.split("-"))
                                tot = g1 + g2
                                if tot >= 1: o05 += 1
                                if tot >= 2: o15 += 1
                                
                                gf = g1 if es_local else g2
                                gc = g2 if es_local else g1
                                if gf > gc: gana += 1
                                
                        return round((o05/pj)*100, 1), round((o15/pj)*100, 1), round((gana/pj)*100, 1), pj

                    st.markdown("#### ⏱️ Radar de Medio Tiempo (HT) - Condición Estricta")
                    
                    o05_L, o15_L, gana_L, pj_L = calc_ht_estricto(l, hist_L, es_local=True)
                    o05_V, o15_V, gana_V, pj_V = calc_ht_estricto(v, hist_V, es_local=False)
                    
                    ht_c1, ht_c2 = st.columns(2)
                    with ht_c1:
                        st.markdown(f"🟢 **{l} (Local)** - *(Muestra: {pj_L} partidos de local)*")
                        r_col1, r_col2, r_col3 = st.columns(3)
                        r_col1.metric("Over 0.5 HT", f"{o05_L}%")
                        r_col2.metric("Over 1.5 HT", f"{o15_L}%")
                        r_col3.metric("Gana al Descanso", f"{gana_L}%")
                        
                    with ht_c2:
                        st.markdown(f"🔴 **{v} (Visita)** - *(Muestra: {pj_V} partidos de visita)*")
                        v_col1, v_col2, v_col3 = st.columns(3)
                        v_col1.metric("Over 0.5 HT", f"{o05_V}%")
                        v_col2.metric("Over 1.5 HT", f"{o15_V}%")
                        v_col3.metric("Gana al Descanso", f"{gana_V}%")
                    
                    st.caption("ℹ️ Datos históricos descriptivos, tomando exclusivamente los encuentros en su condición real (🏠/✈️).")
                    st.divider()

                    st.markdown("#### 🌡️ Termómetro de Tendencias (Goles y Rachas Reales)")
                    st.caption("Filtro Estricto: rendimiento del Local en casa (🏠) y de la Visita fuera (✈️).")
                    
                    def calcular_tasas_condicion(historial, equipo_buscado, es_local):
                        vacio = {"btts": 0, "gf_05": 0, "gc_05": 0, "o15": 0, "o25": 0, "pj": 0}
                        if not historial: return vacio
                        u_condicion = []
                        
                        def es_partido_local(partido, eq):
                            eq_clean = normalize_text(eq).replace('.', '').lower()
                            loc_clean = normalize_text(partido["Local"]).replace('.', '').lower()
                            vis_clean = normalize_text(partido["Visita"]).replace('.', '').lower()
                            if eq_clean == loc_clean: return True
                            if eq_clean == vis_clean: return False
                            set_eq = set(eq_clean.split())
                            set_loc = set(loc_clean.split())
                            set_vis = set(vis_clean.split())
                            if len(set_eq & set_loc) > len(set_eq & set_vis): return True
                            if len(set_eq & set_vis) > len(set_eq & set_loc): return False
                            return loc_clean in eq_clean

                        for p in reversed(historial):
                            try:
                                jugo_local = es_partido_local(p, equipo_buscado)
                                if (es_local and jugo_local) or (not es_local and not jugo_local):
                                    u_condicion.append(p)
                                if len(u_condicion) == 10: break
                            except Exception:
                                pass
                        
                        pj = len(u_condicion)
                        if pj == 0: return vacio
                        
                        btts, gf_05, gc_05, o15, o25 = 0, 0, 0, 0, 0
                        for p in u_condicion:
                            try:
                                g1, g2 = map(int, p["Res"].strip("[]").split(":"))
                                jugo_local = es_partido_local(p, equipo_buscado)
                                gf = g1 if jugo_local else g2
                                gc = g2 if jugo_local else g1
                                
                                if gf > 0 and gc > 0: btts += 1
                                if gf > 0: gf_05 += 1
                                if gc > 0: gc_05 += 1
                                if (gf + gc) > 1.5: o15 += 1
                                if (gf + gc) > 2.5: o25 += 1
                            except Exception:
                                pass
                            
                        return {
                            "btts": round((btts/pj)*100), 
                            "gf_05": round((gf_05/pj)*100), 
                            "gc_05": round((gc_05/pj)*100), 
                            "o15": round((o15/pj)*100), 
                            "o25": round((o25/pj)*100),
                            "pj": pj
                        }

                    stats_L_casa = calcular_tasas_condicion(hist_L, l, True)
                    stats_V_fuera = calcular_tasas_condicion(hist_V, v, False)

                    df_tendencias = pd.DataFrame({
                        "Métrica a Evaluar": [
                            "📊 Partidos Analizados (Muestra)",
                            "🤝 Ambos Anotan (BTTS)", 
                            "⚽ Anota +0.5 Goles (Puntuación)", 
                            "🥅 Recibe +0.5 Goles (Concesión)", 
                            "🔥 Partido +1.5 Goles", 
                            "🌋 Partido +2.5 Goles"
                        ],
                        f"{l} (En Casa 🏠)": [
                            f"{stats_L_casa['pj']} partidos",
                            f"{stats_L_casa['btts']}%", 
                            f"{stats_L_casa['gf_05']}%", 
                            f"{stats_L_casa['gc_05']}%", 
                            f"{stats_L_casa['o15']}%", 
                            f"{stats_L_casa['o25']}%"
                        ],
                        f"{v} (De Visita ✈️)": [
                            f"{stats_V_fuera['pj']} partidos",
                            f"{stats_V_fuera['btts']}%", 
                            f"{stats_V_fuera['gf_05']}%", 
                            f"{stats_V_fuera['gc_05']}%", 
                            f"{stats_V_fuera['o15']}%", 
                            f"{stats_V_fuera['o25']}%"
                        ]
                    })
                    
                    st.dataframe(df_tendencias, width="stretch", hide_index=True)
                    st.divider()
                except Exception:
                    pass

            # ==========================================
            # MOTOR DE PREDICCIÓN
            # ==========================================
            stats = None
            usando_v2 = False
            if motor_v2.motor_disponible():
                stats = motor_v2.predecir(liga_sel, l, v)
                if stats:
                    usando_v2 = True

            if not stats:
                stats = calcular_prediccion_avanzada(tabla, l, v, racha_l_df, racha_v_df, gf_lh, gc_lh, gf_va, gc_va, liga_sel)

            # Registrar la predicción para medir calibración en vivo
            # (solo en la app local: en la nube no hay base de datos)
            if usando_v2 and not MODO_NUBE:
                try:
                    # Buscar la fecha real del partido en el fixture de la liga
                    fecha_real_partido = None
                    try:
                        _meses_r = {'Jan': 1, 'Feb': 2, 'Mar': 3, 'Apr': 4, 'May': 5, 'Jun': 6,
                                    'Jul': 7, 'Aug': 8, 'Sep': 9, 'Oct': 10, 'Nov': 11, 'Dec': 12}
                        _hoy_r = datetime.datetime.now()
                        for _p in (data_avanzada.get("fixture", []) if data_avanzada else []):
                            if (normalize_text(_p.get("Local", "")) == normalize_text(l)
                                    and normalize_text(_p.get("Visita", "")) == normalize_text(v)):
                                _partes = str(_p.get("Fecha", "")).split()
                                if len(_partes) >= 3:
                                    _d = int(_partes[1])
                                    _m = _meses_r.get(_partes[2][:3])
                                    if _m:
                                        _a = _hoy_r.year
                                        if _hoy_r.month >= 10 and _m <= 3:
                                            _a += 1
                                        elif _hoy_r.month <= 3 and _m >= 10:
                                            _a -= 1
                                        fecha_real_partido = f"{_a:04d}-{_m:02d}-{_d:02d}"
                                break
                    except Exception:
                        fecha_real_partido = None

                    reg.guardar(liga_sel, l, v, stats, fecha_real_partido)
                except Exception:
                    pass

            calidad = motor_v2.calidad_liga(liga_sel)
            if usando_v2:
                if calidad["nivel"] == "ALTA":
                    st.success(f"🟢 **Motor V2 (Dixon-Coles calibrado)** · {calidad['mensaje']}")
                elif calidad["nivel"] == "MEDIA":
                    st.warning(f"🟡 **Motor V2 (Dixon-Coles calibrado)** · {calidad['mensaje']}")
                else:
                    st.error(f"🔴 **Motor V2 (Dixon-Coles calibrado)** · {calidad['mensaje']}")
            else:
                st.warning("⚠️ Motor V2 no disponible para este partido (liga sin modelo o equipos no encontrados). Usando el motor anterior: probabilidades **sin calibrar**, tratalas con mucha cautela.")

            # Respaldo de datos detrás de la predicción
            if usando_v2:
                muestra = motor_v2.info_muestra(liga_sel)
                if muestra:
                    txt = (f"📊 Basado en **{muestra['n_partidos']} partidos** de esta liga "
                           f"({muestra['n_equipos']} equipos · "
                           f"{muestra['partidos_por_equipo']} partidos por equipo) · "
                           f"último dato: {muestra['ultima_fecha']}")
                    if muestra["nivel_datos"] == "MUY_POCOS":
                        st.error(txt + "  ⚠️ **Muy pocos datos**: las fuerzas de los equipos "
                                       "están mal estimadas. Tomá estas probabilidades como orientativas.")
                    elif muestra["nivel_datos"] == "POCOS":
                        st.warning(txt + "  ⚠️ Muestra limitada.")
                    else:
                        st.caption(txt)
            
            if stats:
                st.markdown("#### ⚽ Probabilidades de Partido (1X2)")
                st.caption("✅ Mercado validado por backtest. Junto a cada probabilidad, la **cuota mínima** para que la apuesta tenga valor.")
                m1, m2, m3 = st.columns(3)
                cm_1 = motor_v2.cuota_minima(stats['1'])
                cm_x = motor_v2.cuota_minima(stats['X'])
                cm_2 = motor_v2.cuota_minima(stats['2'])
                m1.metric(f"Victoria {l}", f"{stats['1']:.1f}%", f"xG: {stats['xG_L']:.2f}", delta_color="off")
                m1.caption(f"Cuota mínima: **{cm_1:.2f}**" if cm_1 else "—")
                m2.metric("Empate", f"{stats['X']:.1f}%", None)
                m2.caption(f"Cuota mínima: **{cm_x:.2f}**" if cm_x else "—")
                m3.metric(f"Victoria {v}", f"{stats['2']:.1f}%", f"xG: {stats['xG_V']:.2f}", delta_color="off")
                m3.caption(f"Cuota mínima: **{cm_2:.2f}**" if cm_2 else "—")
                
                st.divider()

                st.markdown("#### 🎯 Posibles Marcadores Exactos (Top 3)")
                sm1, sm2, sm3 = st.columns(3)
                try:
                    sm1.metric("1ra Opción", stats['Top_Marcadores'][0][0], f"{stats['Top_Marcadores'][0][1]:.1f}% prob", delta_color="off")
                    sm2.metric("2da Opción", stats['Top_Marcadores'][1][0], f"{stats['Top_Marcadores'][1][1]:.1f}% prob", delta_color="off")
                    sm3.metric("3ra Opción", stats['Top_Marcadores'][2][0], f"{stats['Top_Marcadores'][2][1]:.1f}% prob", delta_color="off")
                except Exception:
                    st.caption("Sin datos de marcadores exactos.")

                st.divider()
                
                col_doble, col_btts, col_goles = st.columns(3)

                with col_doble:
                    st.markdown("#### 🛡️ Doble Oportunidad")
                    st.caption("✅ Mercado validado por backtest.")
                    cm_1x = motor_v2.cuota_minima(stats['1X'])
                    cm_x2 = motor_v2.cuota_minima(stats['X2'])
                    cm_12 = motor_v2.cuota_minima(stats['12'])
                    st.info(f"**1X ({l} o Empate):** {stats['1X']:.1f}%  ·  cuota mín. **{cm_1x:.2f}**" if cm_1x else f"**1X:** {stats['1X']:.1f}%")
                    st.info(f"**X2 (Empate o {v}):** {stats['X2']:.1f}%  ·  cuota mín. **{cm_x2:.2f}**" if cm_x2 else f"**X2:** {stats['X2']:.1f}%")
                    st.info(f"**12 (Cualquiera gana):** {stats['12']:.1f}%  ·  cuota mín. **{cm_12:.2f}**" if cm_12 else f"**12:** {stats['12']:.1f}%")

                with col_btts:
                    st.markdown("#### 🤝 Ambos Anotan (BTTS)")
                    st.caption("⚠️ Sin ventaja estadística validada (backtest: skill ≤ 0). Solo referencia.")
                    st.info(f"**SÍ:** {stats['BTTS_Y']:.1f}%")
                    st.info(f"**NO:** {stats['BTTS_N']:.1f}%")

                with col_goles:
                    st.markdown("#### 🎯 Mercado Totales")
                    st.caption("⚠️ Mercados de goles: sin ventaja validada. Solo referencia.")
                    st.info(f"**Más de 1.5:** {stats['Over15']:.1f}%")
                    st.info(f"**Más de 2.5:** {stats['Over25']:.1f}%")
                    st.info(f"**Menos de 2.5:** {stats['Under25']:.1f}%")
                    st.info(f"**Menos de 3.5:** {stats['Under35']:.1f}%")
                    
                st.divider()

                st.markdown("#### 📊 Análisis de Goles por Equipo (Líneas Asiáticas y Goles Exactos)")
                st.caption("⚠️ Sección informativa: los mercados de goles por equipo no fueron validados.")
                cg1, cg2 = st.columns(2)
                with cg1:
                    st.markdown(f"🟢 **{l} (Local)**")
                    st.write(f"- **Más de 0.5 Goles (+0.5):** {stats['Team_Totals_L']['O05']:.1f}%")
                    st.write(f"- **Más de 1.5 Goles (+1.5):** {stats['Team_Totals_L']['O15']:.1f}%")
                    st.write(f"- **Menos de 2.5 Goles (-2.5):** {stats['Team_Totals_L']['U25']:.1f}%")
                    st.write(f"- **Menos de 3.5 Goles (-3.5):** {stats['Team_Totals_L']['U35']:.1f}%")
                    st.caption("Goles Exactos:")
                    st.caption(f"0: {stats['Goles_L']['0']:.1f}% | 1: {stats['Goles_L']['1']:.1f}% | 2: {stats['Goles_L']['2']:.1f}% | 3+: {stats['Goles_L']['3+']:.1f}%")
                
                with cg2:
                    st.markdown(f"🔴 **{v} (Visita)**")
                    st.write(f"- **Más de 0.5 Goles (+0.5):** {stats['Team_Totals_V']['O05']:.1f}%")
                    st.write(f"- **Más de 1.5 Goles (+1.5):** {stats['Team_Totals_V']['O15']:.1f}%")
                    st.write(f"- **Menos de 2.5 Goles (-2.5):** {stats['Team_Totals_V']['U25']:.1f}%")
                    st.write(f"- **Menos de 3.5 Goles (-3.5):** {stats['Team_Totals_V']['U35']:.1f}%")
                    st.caption("Goles Exactos:")
                    st.caption(f"0: {stats['Goles_V']['0']:.1f}% | 1: {stats['Goles_V']['1']:.1f}% | 2: {stats['Goles_V']['2']:.1f}% | 3+: {stats['Goles_V']['3+']:.1f}%")
            
            st.markdown("---")
            
            st.markdown("### 🚩 Mercado de Córners (Contexto Estricto Local/Visita)")
            st.caption("⚠️ Mercado no validado por backtest. Datos históricos de referencia.")

            corners_data = {}
            try:
                corners_data = data_avanzada.get("corners", {})
            except Exception:
                corners_data = {}

            c_loc_full = corners_data.get(l, {}) if corners_data else {}
            c_vis_full = corners_data.get(v, {}) if corners_data else {}
            
            if not c_loc_full and corners_data:
                for k in corners_data.keys():
                    if normalize_db_name(l) in normalize_db_name(k) or normalize_db_name(k) in normalize_db_name(l):
                        c_loc_full = corners_data[k]
                        break
            if not c_vis_full and corners_data:
                for k in corners_data.keys():
                    if normalize_db_name(v) in normalize_db_name(k) or normalize_db_name(k) in normalize_db_name(v):
                        c_vis_full = corners_data[k]
                        break

            if c_loc_full and c_vis_full:
                c_loc = c_loc_full.get('Local', c_loc_full.get('Total', {}))
                c_vis = c_vis_full.get('Visita', c_vis_full.get('Total', {}))
                
                colC1, colC2, colC3 = st.columns(3)
                
                colC1.metric(f"🏠 {l} (De Local)", f"{c_loc.get('Favor', 0)} a favor")
                colC1.caption(f"Córners recibidos: {c_loc.get('Contra', 0)}")
                
                try:
                    promedio_partido = round((float(c_loc.get('Total', 0)) + float(c_vis.get('Total', 0))) / 2, 2)
                except Exception:
                    promedio_partido = 0
                colC2.metric("Promedio Esperado del Partido", f"{promedio_partido}")
                colC2.caption("Cruce: Local vs Visita")
                
                colC3.metric(f"✈️ {v} (De Visita)", f"{c_vis.get('Favor', 0)} a favor")
                colC3.caption(f"Córners recibidos: {c_vis.get('Contra', 0)}")
                
                st.markdown("**📊 Frecuencia histórica de superar la línea (contexto):**")
                cO1, cO2 = st.columns(2)
                cO1.info(f"🏠 **{l}:** +8.5 ({c_loc.get('+8.5', '0%')}) | +9.5 ({c_loc.get('+9.5', '0%')}) | +10.5 ({c_loc.get('+10.5', '0%')})")
                cO2.info(f"✈️ **{v}:** +8.5 ({c_vis.get('+8.5', '0%')}) | +9.5 ({c_vis.get('+9.5', '0%')}) | +10.5 ({c_vis.get('+10.5', '0%')})")
            else:
                st.info("ℹ️ Datos estadísticos de córners no disponibles para esta liga en el archivo JSON.")
            
            st.markdown("---")

            # ==========================================
            # ESCÁNER DE VALOR
            # ==========================================
            st.markdown("### 💰 Escáner de Valor (Ingresa tus cuotas reales)")
            st.caption("Solo tiene sentido apostar si la cuota supera la **cuota mínima** del mercado. Los mercados marcados con ⚠️ no tienen ventaja validada.")
            c_mem = st.session_state.get('cuotas_restauradas', {})

            todas_ops = []
            ops_valor = []

            ligas_manuales = ["Bolivia - Div. Profesional", "Argentina", "Argentina - Primera Nacional", "Brasil", "Brasil - Serie B", "Libertadores", "Copa Sudamericana", "México - Liga MX", "Estados Unidos - MLS", "Estonia - Meistriliiga", "Islandia - 2da División", "Noruega - Eliteserien", "Dinamarca - Superliga", "China - Super League", "Suecia - Allsvenskan", "Islandia - 1ra División", "Inglaterra - Premier League", "España - La Liga", "Italia - Serie A", "Francia - Ligue 1", "Champions League", "Europa League"] + [
                "Escocia - Premiership",
                "Chequia - Fortuna Liga",
                "Turquía - Süper Lig",
                "Ucrania - Premier League",
                "Finlandia - Veikkausliiga",
                "Japón - J1 League",
                "Suiza - Super League",
                "Países Bajos - Eerste Divisie",
                "Portugal - Liga 2",
                "Alemania - 2. Bundesliga",
            ]

            if stats and liga_sel in ligas_manuales:
                t1, t2, t3, t4, t5, t6 = st.tabs(["Ganador ✅", "Doble Op ✅", "Ambos Anotan ⚠️", "Goles Totales ⚠️", "Goles Equipo ⚠️", "Handicap Asiático"])
                with t1: 
                    st.caption("✅ Mercado validado por backtest.")
                    c1t, c2t, c3t = st.columns(3)
                    v1 = c1t.number_input("Win L", 1.0, 15.0, float(c_mem.get('in_1', 1.0)), 0.01, key="in_1")
                    vx = c2t.number_input("Empate", 1.0, 15.0, float(c_mem.get('in_x', 1.0)), 0.01, key="in_x")
                    v2 = c3t.number_input("Win V", 1.0, 15.0, float(c_mem.get('in_2', 1.0)), 0.01, key="in_2")
                with t2:
                    st.caption("✅ Mercado validado por backtest.")
                    c4, c5, c6 = st.columns(3)
                    v1x = c4.number_input("1X", 1.0, 15.0, float(c_mem.get('in_1x', 1.0)), 0.01, key="in_1x")
                    vx2 = c5.number_input("X2", 1.0, 15.0, float(c_mem.get('in_x2', 1.0)), 0.01, key="in_x2")
                    v12 = c6.number_input("12", 1.0, 15.0, float(c_mem.get('in_12', 1.0)), 0.01, key="in_12")
                with t3:
                    st.caption("⚠️ Sin ventaja validada. Solo referencia.")
                    c7, c8 = st.columns(2)
                    vbtts_y = c7.number_input("SÍ Anotan", 1.0, 15.0, float(c_mem.get('in_btts_y', 1.0)), 0.01, key="in_btts_y")
                    vbtts_n = c8.number_input("NO Anotan", 1.0, 15.0, float(c_mem.get('in_btts_n', 1.0)), 0.01, key="in_btts_n")
                with t4:
                    st.caption("⚠️ Sin ventaja validada. Solo referencia.")
                    c9, c10, c11, c11a, c11b = st.columns(5)
                    vo05 = c9.number_input("+0.5 Goles", 1.0, 15.0, float(c_mem.get('in_o05', 1.0)), 0.01, key="in_o05")
                    vo15 = c10.number_input("+1.5 Goles", 1.0, 15.0, float(c_mem.get('in_o15', 1.0)), 0.01, key="in_o15")
                    vo25 = c11.number_input("+2.5 Goles", 1.0, 15.0, float(c_mem.get('in_o25', 1.0)), 0.01, key="in_o25")
                    vu25 = c11a.number_input("-2.5 Goles", 1.0, 15.0, float(c_mem.get('in_u25', 1.0)), 0.01, key="in_u25")
                    vu35 = c11b.number_input("-3.5 Goles", 1.0, 15.0, float(c_mem.get('in_u35', 1.0)), 0.01, key="in_u35")
                with t5:
                    st.caption("⚠️ Sin ventaja validada. Solo referencia.")
                    c12, c13 = st.columns(2)
                    with c12:
                        st.markdown(f"**{l}**")
                        l_o05 = st.number_input(f"{l} +0.5", 1.0, 15.0, float(c_mem.get('in_l_o05', 1.0)), 0.01, key="in_l_o05")
                        l_o15 = st.number_input(f"{l} +1.5", 1.0, 15.0, float(c_mem.get('in_l_o15', 1.0)), 0.01, key="in_l_o15")
                        l_u25 = st.number_input(f"{l} -2.5", 1.0, 15.0, float(c_mem.get('in_l_u25', 1.0)), 0.01, key="in_l_u25")
                        l_u35 = st.number_input(f"{l} -3.5", 1.0, 15.0, float(c_mem.get('in_l_u35', 1.0)), 0.01, key="in_l_u35")
                    with c13:
                        st.markdown(f"**{v}**")
                        v_o05 = st.number_input(f"{v} +0.5", 1.0, 15.0, float(c_mem.get('in_v_o05', 1.0)), 0.01, key="in_v_o05")
                        v_o15 = st.number_input(f"{v} +1.5", 1.0, 15.0, float(c_mem.get('in_v_o15', 1.0)), 0.01, key="in_v_o15")
                        v_u25 = st.number_input(f"{v} -2.5", 1.0, 15.0, float(c_mem.get('in_v_u25', 1.0)), 0.01, key="in_v_u25")
                        v_u35 = st.number_input(f"{v} -3.5", 1.0, 15.0, float(c_mem.get('in_v_u35', 1.0)), 0.01, key="in_v_u35")
                with t6:
                    st.caption("ℹ️ Los hándicaps **±0.5 equivalen a 1X2 / Doble Oportunidad** (validados). Las líneas ±1.5 y ±2.5 **no** están validadas.")
                    c14, c15 = st.columns(2)
                    with c14:
                        st.markdown(f"**🏠 {l} (Handicap)**")
                        hl_m05 = st.number_input(f"{l} -0.5", 1.0, 25.0, float(c_mem.get('in_hl_m05', 1.0)), 0.01, key="in_hl_m05")
                        hl_p05 = st.number_input(f"{l} +0.5", 1.0, 25.0, float(c_mem.get('in_hl_p05', 1.0)), 0.01, key="in_hl_p05")
                        hl_m15 = st.number_input(f"{l} -1.5", 1.0, 25.0, float(c_mem.get('in_hl_m15', 1.0)), 0.01, key="in_hl_m15")
                        hl_p15 = st.number_input(f"{l} +1.5", 1.0, 25.0, float(c_mem.get('in_hl_p15', 1.0)), 0.01, key="in_hl_p15")
                        hl_m25 = st.number_input(f"{l} -2.5", 1.0, 25.0, float(c_mem.get('in_hl_m25', 1.0)), 0.01, key="in_hl_m25")
                        hl_p25 = st.number_input(f"{l} +2.5", 1.0, 25.0, float(c_mem.get('in_hl_p25', 1.0)), 0.01, key="in_hl_p25")
                    with c15:
                        st.markdown(f"**✈️ {v} (Handicap)**")
                        hv_m05 = st.number_input(f"{v} -0.5", 1.0, 25.0, float(c_mem.get('in_hv_m05', 1.0)), 0.01, key="in_hv_m05")
                        hv_p05 = st.number_input(f"{v} +0.5", 1.0, 25.0, float(c_mem.get('in_hv_p05', 1.0)), 0.01, key="in_hv_p05")
                        hv_m15 = st.number_input(f"{v} -1.5", 1.0, 25.0, float(c_mem.get('in_hv_m15', 1.0)), 0.01, key="in_hv_m15")
                        hv_p15 = st.number_input(f"{v} +1.5", 1.0, 25.0, float(c_mem.get('in_hv_p15', 1.0)), 0.01, key="in_hv_p15")
                        hv_m25 = st.number_input(f"{v} -2.5", 1.0, 25.0, float(c_mem.get('in_hv_m25', 1.0)), 0.01, key="in_hv_m25")
                        hv_p25 = st.number_input(f"{v} +2.5", 1.0, 25.0, float(c_mem.get('in_hv_p25', 1.0)), 0.01, key="in_hv_p25")

                st.session_state.cuotas_restauradas = {
                    'in_1': v1, 'in_x': vx, 'in_2': v2, 'in_1x': v1x, 'in_x2': vx2, 'in_12': v12, 
                    'in_btts_y': vbtts_y, 'in_btts_n': vbtts_n, 'in_o05': vo05, 'in_o15': vo15, 'in_o25': vo25, 'in_u25': vu25, 'in_u35': vu35, 
                    'in_l_o05': l_o05, 'in_l_o15': l_o15, 'in_l_u25': l_u25, 'in_l_u35': l_u35,
                    'in_v_o05': v_o05, 'in_v_o15': v_o15, 'in_v_u25': v_u25, 'in_v_u35': v_u35,
                    'in_hl_m05': hl_m05, 'in_hl_p05': hl_p05, 'in_hl_m15': hl_m15, 'in_hl_p15': hl_p15, 'in_hl_m25': hl_m25, 'in_hl_p25': hl_p25,
                    'in_hv_m05': hv_m05, 'in_hv_p05': hv_p05, 'in_hv_m15': hv_m15, 'in_hv_p15': hv_p15, 'in_hv_m25': hv_m25, 'in_hv_p25': hv_p25
                }

                alertas_discrepancia = []

                def eval_val(prob, cuota, nombre, validado=True):
                    if cuota and cuota > 1.01:
                        ev = motor_v2.calcular_ev(prob, cuota)
                        if ev is None:
                            return
                        cmin = motor_v2.cuota_minima(prob)
                        # Diferencia sospechosa entre el modelo y la cuota
                        try:
                            disc = motor_v2.revisar_discrepancia(prob, cuota)
                        except Exception:
                            disc = None

                        item = {
                            "Mercado": ("" if validado else "⚠️ ") + nombre,
                            "Prob": prob,
                            "Cuota": cuota,
                            "Cuota Mín.": round(cmin, 2) if cmin else None,
                            "EV": ev,
                            "Validado": "Sí" if validado else "No",
                            "Alerta": ("🚨" if disc and disc["nivel"] == "GRAVE"
                                       else ("⚠️" if disc else "")),
                        }
                        todas_ops.append(item)
                        if disc:
                            alertas_discrepancia.append((nombre, disc))
                        # Una diferencia grave no se ofrece como oportunidad
                        if ev > 1.0 and validado and not (disc and disc["nivel"] == "GRAVE"):
                            ops_valor.append(item)

                # Mercados VALIDADOS
                eval_val(stats['1'], v1, f"Victoria {l}")
                eval_val(stats['X'], vx, "Empate")
                eval_val(stats['2'], v2, f"Victoria {v}")
                eval_val(stats['1X'], v1x, "Doble 1X")
                eval_val(stats['X2'], vx2, "Doble X2")
                eval_val(stats['12'], v12, "Doble 12")
                # Hándicaps ±0.5 = equivalentes a 1X2 / doble oportunidad (validados)
                eval_val(stats['1'], hl_m05, f"{l} HA -0.5")
                eval_val(stats['1X'], hl_p05, f"{l} HA +0.5")
                eval_val(stats['2'], hv_m05, f"{v} HA -0.5")
                eval_val(stats['X2'], hv_p05, f"{v} HA +0.5")

                # Mercados NO validados
                eval_val(stats['BTTS_Y'], vbtts_y, "Ambos Anotan (Sí)", validado=False)
                eval_val(stats['BTTS_N'], vbtts_n, "Ambos Anotan (No)", validado=False)
                eval_val(stats['Over05'], vo05, "+0.5 Goles", validado=False)
                eval_val(stats['Over15'], vo15, "+1.5 Goles", validado=False)
                eval_val(stats['Over25'], vo25, "+2.5 Goles", validado=False)
                eval_val(stats['Under25'], vu25, "-2.5 Goles", validado=False)
                eval_val(stats['Under35'], vu35, "-3.5 Goles", validado=False)
                eval_val(stats['Team_Totals_L']['O05'], l_o05, f"{l} +0.5 Goles", validado=False)
                eval_val(stats['Team_Totals_L']['O15'], l_o15, f"{l} +1.5 Goles", validado=False)
                eval_val(stats['Team_Totals_L']['U25'], l_u25, f"{l} -2.5 Goles", validado=False)
                eval_val(stats['Team_Totals_L']['U35'], l_u35, f"{l} -3.5 Goles", validado=False)
                eval_val(stats['Team_Totals_V']['O05'], v_o05, f"{v} +0.5 Goles", validado=False)
                eval_val(stats['Team_Totals_V']['O15'], v_o15, f"{v} +1.5 Goles", validado=False)
                eval_val(stats['Team_Totals_V']['U25'], v_u25, f"{v} -2.5 Goles", validado=False)
                eval_val(stats['Team_Totals_V']['U35'], v_u35, f"{v} -3.5 Goles", validado=False)
                eval_val(stats['Handicap_L']['-1.5'], hl_m15, f"{l} HA -1.5", validado=False)
                eval_val(stats['Handicap_L']['+1.5'], hl_p15, f"{l} HA +1.5", validado=False)
                eval_val(stats['Handicap_L']['-2.5'], hl_m25, f"{l} HA -2.5", validado=False)
                eval_val(stats['Handicap_L']['+2.5'], hl_p25, f"{l} HA +2.5", validado=False)
                eval_val(stats['Handicap_V']['-1.5'], hv_m15, f"{v} HA -1.5", validado=False)
                eval_val(stats['Handicap_V']['+1.5'], hv_p15, f"{v} HA +1.5", validado=False)
                eval_val(stats['Handicap_V']['-2.5'], hv_m25, f"{v} HA -2.5", validado=False)
                eval_val(stats['Handicap_V']['+2.5'], hv_p25, f"{v} HA +2.5", validado=False)

                data_json = json.dumps(st.session_state.cuotas_restauradas)

                if todas_ops:
                    # ---- Aviso de diferencias sospechosas con la cuota ----
                    graves = [(n, d) for n, d in alertas_discrepancia if d["nivel"] == "GRAVE"]
                    if graves:
                        st.error(
                            "🚨 **Diferencia sospechosa entre el modelo y la casa**\n\n"
                            + graves[0][1]["mensaje"]
                            + "\n\nCuando el modelo y el mercado difieren tanto, lo más probable "
                            "es que el modelo esté equivocado: un equipo recién ascendido sin "
                            "historial en su categoría, datos mezclados entre divisiones, o bajas "
                            "que el modelo no conoce. Las casas ajustan precios con información "
                            "que el modelo no tiene."
                        )
                        if len(graves) > 1:
                            st.caption(f"Hay {len(graves)} mercados con esta señal: "
                                       + ", ".join(n for n, _ in graves[:5]))
                    elif alertas_discrepancia:
                        st.warning("⚠️ " + alertas_discrepancia[0][1]["mensaje"])

                    if ops_valor:
                        df_ops = pd.DataFrame(ops_valor).sort_values(by=["EV"], ascending=False).reset_index(drop=True)
                        mejor = df_ops.iloc[0]

                        if not calidad.get("apostar", False):
                            st.error(
                                f"🔴 **Cuidado:** en {liga_sel} el modelo no tiene ventaja demostrada "
                                f"({calidad['mensaje']}). Aunque el cálculo marque valor, ese valor puede ser ilusorio."
                            )
                        elif mejor['EV'] >= 5.0:
                            st.success(
                                f"💎 **Valor encontrado:** {mejor['Mercado']} · Probabilidad {mejor['Prob']:.1f}% · "
                                f"Cuota {mejor['Cuota']:.2f} (mínima {mejor['Cuota Mín.']:.2f}) · **EV {mejor['EV']:+.1f}%**"
                            )
                        else:
                            st.warning(
                                f"⚠️ Hay valor matemático pero es pequeño (mejor EV: {mejor['EV']:+.1f}%). "
                                "Con márgenes de casa altos, un EV bajo puede desaparecer. Stake conservador o pasar."
                            )

                        st.dataframe(
                            df_ops.style.format({'Prob': '{:.1f}%', 'Cuota': '{:.2f}', 'Cuota Mín.': '{:.2f}', 'EV': '{:+.1f}%'}),
                            width="stretch"
                        )
                    else:
                        st.info("ℹ️ Con las cuotas ingresadas, **ningún mercado validado tiene valor esperado positivo**. Lo correcto acá es NO apostar.")

                    with st.expander("Ver todos los mercados evaluados (incluidos los no validados)"):
                        df_todas = pd.DataFrame(todas_ops).sort_values(by=["EV"], ascending=False).reset_index(drop=True)
                        st.dataframe(
                            df_todas.style.format({'Prob': '{:.1f}%', 'Cuota': '{:.2f}', 'Cuota Mín.': '{:.2f}', 'EV': '{:+.1f}%'}),
                            width="stretch"
                        )
                    
                    st.markdown("#### 🎯 Arma tu Radar Personalizado")
                    opciones_multiselect = [f"{r['Mercado']} | Prob {r['Prob']:.1f}% | Cuota {r['Cuota']:.2f} | EV {r['EV']:+.1f}%" for r in todas_ops]
                    selecciones = st.multiselect("Elige las apuestas que quieres guardar en tu radar (puedes seleccionar varias):", opciones_multiselect)
                    
                    if st.button("⭐ Guardar Seleccionadas en el Radar", width="stretch"):
                        if selecciones:
                            for sel in selecciones:
                                idx = opciones_multiselect.index(sel)
                                fila = todas_ops[idx]
                                guardar_apuesta(liga_sel, l, v, fila['Mercado'], fila['Cuota'], fila['Prob'], fila['EV'], data_json)
                            st.success(f"✅ ¡{len(selecciones)} apuestas guardadas en tus Favoritos! Podrás pasarlas a la Billetera cuando te decidas.")
                        else:
                            st.warning("Selecciona al menos una apuesta de la lista arriba.")
                else:
                    st.info("Ingresa cuotas para buscar valor.")
            elif stats:
                st.info("Esta liga no tiene panel de cuotas manuales configurado.")

            # ==========================================
            # ANÁLISIS CONTEXTUAL (Gemini)
            # ==========================================
            st.markdown("---")
            st.markdown("### 🧠 Análisis Contextual Avanzado (Gemini IA)")
            st.caption("Combina la estadística de Data Purity con el contexto real (lesiones, noticias, motivación).")

            fecha_hoy = datetime.datetime.now().strftime("%d de %B de %Y")

            cuotas_str = "\n".join([f"- {op['Mercado']}: Cuota {op['Cuota']:.2f}" for op in todas_ops if op.get('Cuota', 0) > 1.01])
            if not cuotas_str: 
                cuotas_str = "No se ingresaron cuotas de casas de apuestas para evaluar."

            prompt = f"""
¡ATENCIÓN! Hoy es {fecha_hoy}. Asume el rol de un Analista de Datos de Élite y Experto Profesional en Apuestas Deportivas. Tu reputación se basa en la precisión y la objetividad implacable.

Tu misión es auditar el partido entre {l} (Local) y {v} (Visita) de la liga {liga_sel}.

CONTEXTO IMPORTANTE SOBRE MI MODELO:
Mi modelo estadístico fue validado con un backtest sobre ~19.000 partidos. Los resultados fueron:
- Mercados 1X2 y Doble Oportunidad: SÍ tienen ventaja estadística.
- Mercados de goles (Over/Under) y Ambos Anotan: NO tienen ventaja demostrable.
- Nivel de confianza en esta liga: {calidad['nivel']} ({calidad['mensaje']})

REGLAS ESTRICTAS:
1. NO inventes ni alucines información. Si no encuentras datos confirmados sobre lesiones o bajas, di explícitamente "Información no disponible".
2. Utiliza exclusivamente fuentes confiables (Flashscore, OneFootball, Sofascore, FootyStats, SoccerStats).
3. Eres un auditor crítico: NO estés de acuerdo con mi modelo por complacencia. Defiende tus datos con argumentos sólidos.

Escribe un informe estructurado usando estas viñetas exactas:

🔍 PASO 1: INVESTIGACIÓN DE CAMPO (Contexto Actualizado)
- Motivación y Contexto: (Peleas por título, descenso, rivalidades, cansancio por copas).
- Dinámica Reciente: (OBLIGATORIO: cómo les fue en sus ÚLTIMOS 5 PARTIDOS exactos).
- Rendimiento de Condición: (Cómo juega {l} en casa y {v} de visita).
- Bajas y Rotaciones: (Lesiones, suspensiones críticas actualizadas).
- Estilo de Juego: (Posesión, defensas adelantadas, contragolpes).

🧮 PASO 2: TUS PROPIAS PROBABILIDADES INDEPENDIENTES
Basado en tu investigación, calcula TUS probabilidades para Ganador (1X2) y Doble Oportunidad.

⚔️ PASO 3: CONTRASTE CRÍTICO (Tú vs Data Purity)
Mi modelo calibrado (Dixon-Coles) arrojó:
- 1X2: Local {stats.get('1', 0):.1f}% | Empate {stats.get('X', 0):.1f}% | Visita {stats.get('2', 0):.1f}%
- Doble Oportunidad: 1X {stats.get('1X', 0):.1f}% | X2 {stats.get('X2', 0):.1f}% | 12 {stats.get('12', 0):.1f}%
>> DEBATE: ¿Mi modelo está sobrevalorando a un equipo, ignorando una lesión clave o un cambio de contexto? Valida o refuta con argumentos reales.

⚖️ PASO 4: EVALUACIÓN DE CUOTAS Y VEREDICTO FINAL
Cuotas introducidas:
{cuotas_str}
- Riesgos: ¿Cuál es el mayor peligro de apostar aquí?
- VEREDICTO FINAL: recomienda solo mercados de 1X2 o Doble Oportunidad donde la cuota supere claramente la probabilidad estimada. Si no hay valor claro, tu obligación profesional es ordenarme "NO APOSTAR EN ESTE PARTIDO (NO BET)".
"""

            if not saved_api_key:
                st.error("⚠️ Por favor, ingresa tu API Key en el menú lateral.")
            else:
                if st.button("⚡ Solicitar Análisis Contextual", width="stretch", key=f"btn_unico_{l}_{v}"):
                    modelo_fijo = "gemini-robotics-er-1.6-preview"
                    
                    with st.spinner("⏳ Analizando datos actuales en internet... Por favor espera un momento..."):
                        url_generar = f"https://generativelanguage.googleapis.com/v1beta/models/{modelo_fijo}:generateContent?key={saved_api_key}"
                        headers = {'Content-Type': 'application/json'}
                        
                        payload = {
                            "contents": [{"parts": [{"text": prompt}]}],
                            "tools": [{"googleSearch": {}}],
                            "generationConfig": {"maxOutputTokens": 8192}
                        }
                        
                        try:
                            respuesta = requests.post(url_generar, headers=headers, json=payload, timeout=60)
                            
                            if respuesta.status_code == 200:
                                texto = respuesta.json()['candidates'][0]['content']['parts'][0]['text']
                                texto = texto.replace("```markdown", "").replace("```", "")
                                st.success("✅ Análisis completado:")
                                st.markdown(texto)
                            else:
                                st.error(f"❌ Error del servidor. Código: {respuesta.status_code}")
                                with st.expander("Ver detalles técnicos"):
                                    st.write(respuesta.text)
                                    
                        except requests.exceptions.Timeout:
                            st.error("⏳ ¡Tiempo agotado! El servidor tardó demasiado en responder. Intenta de nuevo.")
                        except Exception as e:
                            st.error(f"❌ Error de red crítico: {str(e)}")

# ==========================================
# PESTAÑA: CALIBRACIÓN EN VIVO
# ==========================================
elif st.session_state.pagina in ('Favoritos', 'Billetera', 'Calibracion') and MODO_NUBE:
    st.header("☁️ Versión en línea")
    st.info(
        "Esta sección necesita la base de datos con tus apuestas, que vive "
        "únicamente en tu computadora.\n\n"
        "Acá podés consultar partidos, probabilidades, tablas y el calendario."
    )
    if st.button("← Volver a la Cartelera", width="stretch"):
        st.session_state.pagina = 'Cartelera'
        st.rerun()

elif st.session_state.pagina == 'Calibracion':
    st.header("📈 Calibración en Vivo")
    st.markdown(
        "Cada vez que analizás un partido, el sistema guarda la probabilidad que calculó. "
        "Cuando el partido se juega, la compara con el resultado real. "
        "Esto mide si el modelo **sigue funcionando hoy**, no solo en el backtest histórico."
    )

    try:
        reg.crear_tabla()
        conn_cal = sqlite3.connect("database/football_data.db")
        df_log = pd.read_sql("SELECT * FROM predicciones_log", conn_cal)
        conn_cal.close()
    except Exception as e:
        st.error(f"No se pudo leer el registro: {e}")
        df_log = pd.DataFrame()

    if df_log.empty:
        st.info(
            "Todavía no hay predicciones registradas. Se van guardando solas "
            "cada vez que analizás un partido en la Cartelera."
        )
    else:
        con_res = df_log["resultado"].notna().sum()
        c_a, c_b, c_c = st.columns(3)
        c_a.metric("Predicciones guardadas", len(df_log))
        c_b.metric("Con resultado", int(con_res))
        c_c.metric("Pendientes", int(len(df_log) - con_res))

        if st.button("🔄 Buscar resultados de los partidos ya jugados", width="stretch"):
            with st.spinner("Cruzando con la base de partidos..."):
                reg.actualizar_resultados()
            st.success("Listo. Recargando...")
            st.rerun()

        st.caption(
            "ℹ️ Los resultados se completan cruzando con la tabla de partidos. "
            "Actualizá los datos con el scraper y después tocá el botón de arriba."
        )

        if con_res < 30:
            st.warning(
                f"Con {int(con_res)} partidos resueltos todavía no se pueden sacar "
                "conclusiones. Hacen falta 100-200 para que la calibración sea confiable. "
                "Seguí usando el sistema normalmente."
            )
        else:
            df_ok = df_log[df_log["resultado"].notna()].copy()

            def pred_y_prob(fila):
                ops = [("1", fila["prob_1"] or 0), ("X", fila["prob_X"] or 0), ("2", fila["prob_2"] or 0)]
                return max(ops, key=lambda x: x[1])

            df_ok["pred"] = df_ok.apply(lambda r: pred_y_prob(r)[0], axis=1)
            df_ok["prob_max"] = df_ok.apply(lambda r: pred_y_prob(r)[1], axis=1)
            df_ok["acierto"] = (df_ok["pred"] == df_ok["resultado"]).astype(int)

            acierto = df_ok["acierto"].mean() * 100
            base_local = (df_ok["resultado"] == "1").mean() * 100

            m1c, m2c = st.columns(2)
            m1c.metric("Acierto del modelo", f"{acierto:.1f}%")
            m2c.metric("Apostar siempre al local", f"{base_local:.1f}%",
                       f"{acierto - base_local:+.1f} pts", delta_color="off")

            st.divider()
            st.markdown("#### ¿Las probabilidades dicen la verdad?")

            bins = [0, 40, 50, 60, 70, 80, 101]
            etiquetas = ["<40%", "40-50%", "50-60%", "60-70%", "70-80%", "80%+"]
            df_ok["rango"] = pd.cut(df_ok["prob_max"], bins=bins,
                                    labels=etiquetas, include_lowest=True)
            tabla_cal = df_ok.groupby("rango", observed=True).agg(
                N=("acierto", "size"),
                Predicho=("prob_max", "mean"),
                Real=("acierto", lambda x: x.mean() * 100),
            ).reset_index()
            tabla_cal = tabla_cal[tabla_cal["N"] >= 5]
            tabla_cal["Desvío"] = tabla_cal["Real"] - tabla_cal["Predicho"]

            st.dataframe(
                tabla_cal.style.format({"Predicho": "{:.1f}%", "Real": "{:.1f}%", "Desvío": "{:+.1f}"}),
                width="stretch", hide_index=True
            )
            st.caption(
                "Desvío cerca de cero = el modelo es honesto. "
                "Negativo = promete de más (peligroso: infla el valor esperado). "
                "Positivo = se queda corto."
            )

            st.divider()
            st.markdown("#### Rendimiento por liga")
            por_liga = df_ok.groupby("liga").agg(
                N=("acierto", "size"),
                Acierto=("acierto", lambda x: x.mean() * 100),
                Calidad=("calidad_liga", "first"),
            ).reset_index().sort_values("N", ascending=False)
            st.dataframe(
                por_liga[por_liga["N"] >= 10].style.format({"Acierto": "{:.1f}%"}),
                width="stretch", hide_index=True
            )

        with st.expander("Ver registro completo"):
            st.dataframe(df_log.sort_values("id", ascending=False),
                         width="stretch", hide_index=True)

# ==========================================
# PESTAÑA: PICKS DEL DÍA
# ==========================================
elif st.session_state.pagina == 'Picks':
    picks_dia.renderizar_pestana()

# ==========================================
# PESTAÑA: CALENDARIO GLOBAL
# ==========================================
elif st.session_state.pagina == 'Calendario':
    st.header("📅 Calendario Global de Partidos")

    # (nombre visible, archivo json, bandera)
    LIGAS_CALENDARIO = [
        ("Inglaterra - Premier League", "england", "🏴"),
        ("España - La Liga", "spain", "🇪🇸"),
        ("Italia - Serie A", "italy", "🇮🇹"),
        ("Francia - Ligue 1", "france", "🇫🇷"),
        ("Brasil", "brazil", "🇧🇷"),
        ("Brasil - Serie B", "serie_b_brasil", "🇧🇷"),
        ("Argentina", "argentina", "🇦🇷"),
        ("Argentina - Primera Nacional", "primera_nacional", "🇦🇷"),
        ("Estados Unidos - MLS", "mls", "🇺🇸"),
        ("México - Liga MX", "mexico", "🇲🇽"),
        ("Bolivia - Div. Profesional", "bolivia", "🇧🇴"),
        ("Noruega - Eliteserien", "norway", "🇳🇴"),
        ("Suecia - Allsvenskan", "sweden", "🇸🇪"),
        ("Dinamarca - Superliga", "denmark", "🇩🇰"),
        ("Estonia - Meistriliiga", "estonia", "🇪🇪"),
        ("Islandia - 1ra División", "iceland", "🇮🇸"),
        ("Islandia - 2da División", "iceland2", "🇮🇸"),
        ("China - Super League", "china", "🇨🇳"),
        # --- Ligas agregadas tras la validación ---
        ("Escocia - Premiership", "scotland", "🏴"),
        ("Chequia - Fortuna Liga", "czechrepublic", "🇨🇿"),
        ("Turquía - Süper Lig", "turkey", "🇹🇷"),
        ("Ucrania - Premier League", "ukraine", "🇺🇦"),
        ("Finlandia - Veikkausliiga", "finland", "🇫🇮"),
        ("Japón - J1 League", "japan", "🇯🇵"),
        ("Suiza - Super League", "switzerland", "🇨🇭"),
        ("Países Bajos - Eerste Divisie", "netherlands2", "🇳🇱"),
        ("Portugal - Liga 2", "portugal2", "🇵🇹"),
        ("Alemania - 2. Bundesliga", "germany2", "🇩🇪"),
    ]

    ICONO_CAL = {"ALTA": "🟢", "MEDIA": "🟡", "BAJA": "🔴", "NULA": "⛔"}

    _meses_cal = {1: 'Jan', 2: 'Feb', 3: 'Mar', 4: 'Apr', 5: 'May', 6: 'Jun',
                  7: 'Jul', 8: 'Aug', 9: 'Sep', 10: 'Oct', 11: 'Nov', 12: 'Dec'}
    _dias_cal = {0: 'Mon', 1: 'Tue', 2: 'Wed', 3: 'Thu', 4: 'Fri', 5: 'Sat', 6: 'Sun'}
    _meses_num = {v: k for k, v in _meses_cal.items()}

    _hoy = datetime.datetime.now() - datetime.timedelta(hours=5)
    _manana = _hoy + datetime.timedelta(days=1)
    F_HOY = f"{_dias_cal[_hoy.weekday()]} {_hoy.day} {_meses_cal[_hoy.month]}"
    F_MANANA = f"{_dias_cal[_manana.weekday()]} {_manana.day} {_meses_cal[_manana.month]}"

    @st.cache_data(ttl=300)
    def cargar_fixture_liga(archivo):
        ruta = f"data_json/{archivo}.json"
        if not os.path.exists(ruta):
            return []
        try:
            with open(ruta, "r", encoding="utf-8") as f:
                return json.load(f).get("fixture", [])
        except Exception:
            return []

    def filtrar_futuro(lista):
        if not lista:
            return []
        futuros = []
        for p in lista:
            partes = str(p.get("Fecha", "")).split()
            if len(partes) >= 3:
                try:
                    dia = int(partes[1])
                    mes = _meses_num.get(partes[2][:3], _hoy.month)
                    anio = _hoy.year
                    if _hoy.month == 12 and mes == 1:
                        anio += 1
                    elif _hoy.month == 1 and mes == 12:
                        anio -= 1
                    if datetime.datetime(anio, mes, dia).date() >= _hoy.date():
                        futuros.append(p)
                except Exception:
                    futuros.append(p)
            else:
                futuros.append(p)
        return futuros

    def clave_hora(p):
        h = str(p.get("Hora", "99:99"))
        try:
            hh, mm = h.split(":")
            return int(hh) * 60 + int(mm)
        except Exception:
            return 9999

    # =====================================================
    # PARTIDOS DE HOY Y MAÑANA (todas las ligas juntas)
    # =====================================================
    de_hoy, de_manana = [], []
    for display, archivo, bandera in LIGAS_CALENDARIO:
        cal = motor_v2.calidad_liga(display)
        for p in cargar_fixture_liga(archivo):
            fecha = str(p.get("Fecha", ""))
            item = {
                "Liga": display, "Archivo": archivo, "Bandera": bandera,
                "Nivel": cal.get("nivel", "?"), "Apostar": cal.get("apostar", False),
                "Hora": p.get("Hora", "--:--"),
                "Local": p.get("Local", ""), "Visita": p.get("Visita", ""),
            }
            if fecha == F_HOY:
                de_hoy.append(item)
            elif fecha == F_MANANA:
                de_manana.append(item)

    de_hoy.sort(key=clave_hora)
    de_manana.sort(key=clave_hora)

    st.markdown(f"### 🚨 Partidos de hoy y mañana")
    st.caption(
        f"Hoy: {F_HOY} · Mañana: {F_MANANA} · "
        "El color indica si el modelo tiene ventaja validada en esa liga."
    )

    solo_buenas = st.checkbox(
        "Mostrar solo ligas con ventaja validada (🟢 ALTA y 🟡 MEDIA)",
        value=False, key="cal_solo_buenas"
    )

    def mostrar_lista(lista, etiqueta):
        datos = [x for x in lista if (not solo_buenas or x["Apostar"])]
        if not datos:
            st.info(f"No hay partidos {etiqueta} con los filtros actuales.")
            return
        filas = []
        for x in datos:
            filas.append({
                "": ICONO_CAL.get(x["Nivel"], "⚪"),
                "Hora": x["Hora"],
                "Liga": f"{x['Bandera']} {x['Liga']}",
                "Partido": f"{x['Local']} vs {x['Visita']}",
            })
        st.dataframe(pd.DataFrame(filas), width="stretch", hide_index=True)
        st.caption(f"{len(datos)} partidos")

    tab_hoy, tab_man = st.tabs([f"🔴 HOY ({len(de_hoy)})", f"🔵 MAÑANA ({len(de_manana)})"])
    with tab_hoy:
        mostrar_lista(de_hoy, "hoy")
    with tab_man:
        mostrar_lista(de_manana, "mañana")

    st.divider()

    # =====================================================
    # CALENDARIO COMPLETO POR LIGA
    # =====================================================
    st.markdown("### 📋 Calendario completo por liga")
    st.caption("Próximos partidos de cada liga. Las que no tienen datos necesitan que corras el actualizador.")

    col_a, col_b = st.columns(2)
    mitad_cal = (len(LIGAS_CALENDARIO) + 1) // 2

    def bloque_liga(display, archivo, bandera):
        cal = motor_v2.calidad_liga(display)
        icono = ICONO_CAL.get(cal.get("nivel", "?"), "⚪")
        fix = filtrar_futuro(cargar_fixture_liga(archivo))
        titulo = f"{icono} {bandera} {display}"
        if fix:
            titulo += f"  ·  {len(fix)} partidos"
        with st.expander(titulo, expanded=False):
            if not fix:
                st.info("Sin partidos próximos. Corré `python actualizar_todas_ligas.py` para actualizar.")
                return
            df_fix = pd.DataFrame(fix)[["Fecha", "Hora", "Local", "Visita"]]
            st.dataframe(df_fix.head(40), width="stretch", hide_index=True)
            if len(fix) > 40:
                st.caption(f"Mostrando 40 de {len(fix)} partidos.")

    with col_a:
        for display, archivo, bandera in LIGAS_CALENDARIO[:mitad_cal]:
            bloque_liga(display, archivo, bandera)
    with col_b:
        for display, archivo, bandera in LIGAS_CALENDARIO[mitad_cal:]:
            bloque_liga(display, archivo, bandera)

# ==========================================
# PESTAÑA 2: FAVORITOS (RADAR)
# ==========================================
elif st.session_state.pagina == 'Favoritos':
    st.header("⭐ Radar de Apuestas (Watchlist)")
    st.markdown("Tickets guardados que aún no pasaste a la Billetera. Podés pasarlos de forma **Simple** o combinar varios en un **Parlay**.")
    
    conn = sqlite3.connect("database/football_data.db")
    df = pd.read_sql("SELECT * FROM mis_apuestas WHERE en_billetera = 0 ORDER BY id DESC", conn)
    conn.close()
    
    def viajar(liga, eq_l, eq_v, cuotas_str):
        st.session_state.pagina = 'Cartelera'
        st.session_state.sel_liga = liga
        st.session_state.res_l = eq_l
        st.session_state.res_v = eq_v
        st.session_state.last_liga = liga
        st.session_state.last_l = eq_l
        st.session_state.last_v = eq_v
        st.session_state.analizar = True
        if cuotas_str: 
            try:
                st.session_state.cuotas_restauradas = json.loads(cuotas_str)
            except Exception:
                st.session_state.cuotas_restauradas = {}
        else:
            st.session_state.cuotas_restauradas = {}

    if df.empty: 
        st.info("No hay tickets en tu radar.")
    else:
        seleccionados = []
        for _, row in df.iterrows():
            c_chk, c1, c2, c3 = st.columns([0.5, 3.5, 1.2, 1.2])
            with c_chk:
                if st.checkbox("➕", key=f"chk_{row['id']}", help="Seleccionar para Combinada"):
                    seleccionados.append(row.to_dict())
            with c1:
                st.write(f"🏆 {row['liga']} | **{row['equipo_local']}** vs **{row['equipo_visita']}**")
                st.caption(f"Mercado: {row['mercado']} | Prob: **{row['probabilidad']:.1f}%** | Cuota: {row['cuota']} | EV: {row['ev']:+.1f}%")
            
            with c2:
                if st.button("💼 Pasar Simple", key=f"p_{row['id']}"):
                    pasar_a_billetera(row['id'])
                    st.rerun()
                    
            with c3:
                st.button("🔍 Revisar", on_click=viajar, args=(row['liga'], row['equipo_local'], row['equipo_visita'], row['cuotas_json']), key=f"v_{row['id']}")
                if st.button("🗑️ Borrar", key=f"d_{row['id']}"):
                    borrar_apuesta(row['id']); st.rerun()
            st.divider()

        if len(seleccionados) > 1:
            st.markdown("### 🔗 Armador de Apuestas Combinadas (Parlay)")
            st.info(f"Has seleccionado **{len(seleccionados)}** apuestas para combinar.")

            # Aviso: selecciones del mismo partido no son independientes
            partidos_set = set()
            hay_repetido = False
            for r in seleccionados:
                clave = f"{r['equipo_local']}|{r['equipo_visita']}"
                if clave in partidos_set:
                    hay_repetido = True
                partidos_set.add(clave)
            if hay_repetido:
                st.error(
                    "⚠️ **Atención:** seleccionaste dos o más apuestas del MISMO partido. "
                    "No son eventos independientes, así que la probabilidad combinada calculada abajo **no es correcta**. "
                    "La mayoría de las casas ni siquiera permiten combinarlas."
                )

            picks_combinados = " + ".join([f"{r['equipo_local']} ({r['mercado']})" for r in seleccionados])

            cuota_referencia = 1.0
            probs = []
            for r in seleccionados:
                try:
                    cuota_referencia *= float(r['cuota'])
                except Exception:
                    pass
                probs.append(r.get('probabilidad', 0))

            prob_conjunta = motor_v2.prob_combinada(probs)
            cuota_min_comb = motor_v2.cuota_minima(prob_conjunta)

            st.write(f"**Picks Seleccionados:** {picks_combinados}")

            col_cmb1, col_cmb2, col_cmb3 = st.columns(3)
            with col_cmb1:
                st.metric("Cuota Base (Multiplicada)", f"{cuota_referencia:.2f}")
            with col_cmb2:
                st.metric("Probabilidad Real Combinada", f"{prob_conjunta:.2f}%")
                st.caption("Multiplicación de las probabilidades del modelo")
            with col_cmb3:
                st.metric("Cuota Mínima Necesaria", f"{cuota_min_comb:.2f}" if cuota_min_comb else "—")
                st.caption("Por debajo de esto, no hay valor")

            cuota_manual = st.number_input(
                "Cuota Final (la que realmente ofrece tu casa de apuestas)",
                min_value=1.01, value=float(round(cuota_referencia, 2)), step=0.01
            )

            ev_comb = motor_v2.calcular_ev(prob_conjunta, cuota_manual)
            if ev_comb is not None:
                if ev_comb > 1.0:
                    st.success(f"✅ EV de la combinada: **{ev_comb:+.1f}%** · La cuota supera la mínima necesaria.")
                else:
                    st.error(
                        f"❌ EV de la combinada: **{ev_comb:+.1f}%** · **NO APOSTAR.** "
                        f"Necesitarías una cuota de al menos {cuota_min_comb:.2f} para que tenga valor."
                    )

            st.caption(
                "ℹ️ Recordá: cada selección extra multiplica el margen de la casa. "
                "Las combinadas necesitan MÁS ventaja que las simples para ser rentables."
            )
                
            if st.button("🚀 Crear Combinada y Enviar a Billetera", type="primary", width="stretch"):
                fecha_actual = datetime.datetime.now().strftime("%Y-%m-%d")
                liga_comb = "Apuesta Combinada"
                local_comb = "Varios"
                visita_comb = "Varios"
                mercado_comb = f"Parlay ({len(seleccionados)} selecciones)"
                
                conn = sqlite3.connect("database/football_data.db")
                cursor = conn.cursor()
                
                cursor.execute('''INSERT INTO mis_apuestas (liga, equipo_local, equipo_visita, mercado, cuota, probabilidad, ev, cuotas_json, picks, inversion, estado, en_billetera, fecha_apuesta) 
                                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)''', 
                               (liga_comb, local_comb, visita_comb, mercado_comb, cuota_manual,
                                prob_conjunta, ev_comb if ev_comb is not None else 0.0,
                                "{}", picks_combinados, 0.0, "Pendiente", fecha_actual))
                
                for r in seleccionados:
                    cursor.execute('DELETE FROM mis_apuestas WHERE id = ?', (r['id'],))
                    
                conn.commit()
                conn.close()
                
                st.success("✅ ¡Combinada creada! Ve a tu Billetera para gestionar la inversión.")
                st.rerun()

# ==========================================
# PESTAÑA 3: BILLETERA (GESTIÓN)
# ==========================================
elif st.session_state.pagina == 'Billetera':
    st.header("💼 Gestión de Bankroll y Billetera")
    st.markdown("Tu centro de inversiones. Controla tu progreso y registra las ganancias y pérdidas reales.")
    
    with st.expander("➕ Añadir Apuesta Manualmente a la Billetera", expanded=False):
        with st.form("form_manual"):
            st.write("Registra un pick de otra liga, deporte o tipster externo:")
            m_c1, m_c2 = st.columns(2)
            opciones_liga_manual = opciones_liga + ["Otra (escribir abajo)"]
            m_liga_sel = m_c1.selectbox("Liga / Torneo", opciones_liga_manual)
            m_local = m_c2.text_input("Equipo Local")
            m_visita = m_c1.text_input("Equipo Visitante")
            m_picks = m_c2.text_input("Picks / Mercado (Ej: 1X, Victoria Local)")
            m_liga_otra = st.text_input("Si elegiste 'Otra', escribí el nombre acá:", "")
            
            m_c3, m_c4, m_c5, m_c6 = st.columns(4)
            m_fecha = m_c3.date_input("Fecha", datetime.date.today())
            m_cuota = m_c4.number_input("Cuota", min_value=1.01, step=0.01)
            m_stake = m_c5.number_input("Stake (Nivel de confianza 1-10)", min_value=1, max_value=10, value=5, step=1)
            m_inv = m_c6.number_input("Inversión Real ($)", min_value=0.0, step=1.0)
            
            if st.form_submit_button("✅ Guardar Directo en Billetera"):
                m_liga_final = m_liga_otra.strip() if m_liga_sel == "Otra (escribir abajo)" else m_liga_sel
                if m_local and m_visita and m_picks and m_liga_final:
                    guardar_apuesta_manual(m_liga_final, m_local, m_visita, m_picks, m_inv, m_cuota, m_stake, m_fecha.strftime("%Y-%m-%d"))
                    st.success("¡Apuesta registrada exitosamente!")
                    st.rerun()
                else:
                    st.error("Por favor completa todos los campos (incluida la liga).")

    conn = sqlite3.connect("database/football_data.db")
    try:
        df_apuestas = pd.read_sql("SELECT * FROM mis_apuestas WHERE en_billetera = 1 ORDER BY id DESC", conn)
    except Exception:
        df_apuestas = pd.DataFrame()
    conn.close()
    
    inv_total = 0.0
    ganancia_neta_total = 0.0
    ganadas, perdidas, nulas = 0, 0, 0
    df_display = []
    
    for _, row in df_apuestas.iterrows():
        inv = float(row['inversion'])
        cuota = float(row['cuota'])
        estado = row['estado']
        
        stake_calc = round(cuota / 1)
        
        ganancia_bruta = 0.0
        neta = 0.0
        
        if estado == "Ganada":
            ganancia_bruta = inv * cuota
            neta = ganancia_bruta - inv
            ganadas += 1
            inv_total += inv
            ganancia_neta_total += neta
        elif estado == "Perdida":
            ganancia_bruta = 0.0
            neta = -inv
            perdidas += 1
            inv_total += inv
            ganancia_neta_total += neta
        elif estado == "Nula":
            ganancia_bruta = inv
            neta = 0.0
            nulas += 1
            inv_total += inv

        df_display.append({
            "ID": row['id'], 
            "Fecha": str(row.get('fecha_apuesta', '')),
            "Local": row['equipo_local'],
            "VS": "VS",
            "Visitante": row['equipo_visita'],
            "Picks": row['picks'],
            "Stake": stake_calc,
            "Inversión": inv,
            "Cuota": cuota,
            "Estado": estado,
            "Ganancia": ganancia_bruta,
            "Ganancia Neta": neta,
            "🗑️ Eliminar": False
        })
        
    df_billetera = pd.DataFrame(df_display)
    
    balance_total = bankroll_ini + ganancia_neta_total
    acierto = (ganadas / (ganadas + perdidas) * 100) if (ganadas + perdidas) > 0 else 0.0
    yield_pct = (ganancia_neta_total / inv_total * 100) if inv_total > 0 else 0.0
    progreso_meta = min(1.0, max(0.0, balance_total / meta)) if meta > 0 else 0.0

    st.markdown("""
        <style>
        .metric-box { background-color: #1e1e1e; padding: 15px; border-radius: 10px; border-left: 5px solid #00C853; box-shadow: 2px 2px 10px rgba(0,0,0,0.5);}
        .metric-title { font-size: 14px; color: #888; margin-bottom: 5px; }
        .metric-val { font-size: 24px; font-weight: bold; color: white; }
        .metric-val.green { color: #00C853; }
        </style>
    """, unsafe_allow_html=True)

    colA, colB, colC, colD = st.columns([2, 3, 2, 2])
    
    with colA:
        new_bank = st.number_input("Bankroll Inicial ($)", value=float(bankroll_ini), step=10.0)
        st.markdown(f"<div class='metric-box'><div class='metric-title'>Balance Total</div><div class='metric-val green'>$ {balance_total:.2f}</div></div>", unsafe_allow_html=True)
        new_meta = st.number_input("Meta A Cumplir ($)", value=float(meta), step=50.0)
        
        if new_bank != bankroll_ini or new_meta != meta:
            actualizar_config_billetera(new_bank, new_meta, saved_api_key)
            st.rerun()

    with colB:
        st.markdown("### 🧮 Calculadora de Inversión (Kelly)")
        st.caption("Kelly fraccionado (1/4), con tope del 5% del bankroll.")
        calc_cuota = st.number_input("Cuota que ofrece tu casa:", min_value=1.01, value=1.39, step=0.01)

        prob_implicita = (1 / calc_cuota) * 100

        calc_prob_modelo = st.number_input(
            "Probabilidad de tu modelo (%) para esta apuesta:",
            min_value=1.0, max_value=99.0, value=float(round(prob_implicita, 1)), step=0.5,
            help="Copiá acá la probabilidad que te dio el análisis. Si es igual o menor a la implícita, no hay valor."
        )
        sim_inv = motor_v2.kelly_fraccionado(calc_prob_modelo, calc_cuota, balance_total)
        sim_ev = motor_v2.calcular_ev(calc_prob_modelo, calc_cuota)
        sim_ev = sim_ev if sim_ev is not None else 0.0

        if sim_inv > 0:
            st.success(
                f"Cuota implícita: **{prob_implicita:.1f}%** · Tu modelo: **{calc_prob_modelo:.1f}%** · "
                f"EV: **{sim_ev:+.1f}%**\n\n➔ Kelly sugiere invertir: **$ {sim_inv:.2f}**"
            )
        else:
            st.error(
                f"Cuota implícita: **{prob_implicita:.1f}%** · Tu modelo: **{calc_prob_modelo:.1f}%** · "
                f"EV: **{sim_ev:+.1f}%**\n\n➔ **NO APOSTAR** (sin valor esperado positivo)"
            )

    with colC:
        st.markdown(f"<div class='metric-box'><div class='metric-title'>Inversión Total</div><div class='metric-val'>$ {inv_total:.2f}</div></div><br>", unsafe_allow_html=True)
        color_neta = "green" if ganancia_neta_total >= 0 else ""
        st.markdown(f"<div class='metric-box'><div class='metric-title'>Ganancia Neta</div><div class='metric-val {color_neta}'>$ {ganancia_neta_total:.2f}</div></div>", unsafe_allow_html=True)

    with colD:
        st.markdown(f"✅ Ganadas: **{ganadas}**")
        st.markdown(f"❌ Perdidas: **{perdidas}**")
        st.markdown(f"➖ Nulas: **{nulas}**")
        st.divider()
        st.markdown(f"🎯 Acierto: **{acierto:.1f}%**")
        st.markdown(f"📈 Yield: **{yield_pct:.1f}%**")

    st.write("")
    st.markdown(f"**Progreso hacia la Meta:** {progreso_meta*100:.1f}%")
    st.progress(progreso_meta)

    if (ganadas + perdidas) < 100:
        st.caption(
            f"ℹ️ Llevás {ganadas + perdidas} apuestas cerradas. "
            "Con menos de ~100-200 apuestas, el Yield todavía está dominado por la varianza y no dice si tenés ventaja real."
        )
    
    st.markdown("---")
    st.subheader("📋 Tabla de Apuestas en Billetera (Editor Interactivo)")
    st.caption("Haz doble clic en 'Picks', 'Inversión', 'Cuota' o 'Estado' para editar. Marca '🗑️ Eliminar' para borrar un registro.")
    
    if not df_billetera.empty:
        edited_df = st.data_editor(
            df_billetera,
            column_config={
                "ID": None,
                "Fecha": st.column_config.TextColumn("Fecha"),
                "Local": st.column_config.TextColumn(disabled=True),
                "VS": st.column_config.TextColumn(disabled=True),
                "Visitante": st.column_config.TextColumn(disabled=True),
                "Stake": st.column_config.NumberColumn(disabled=True),
                "Ganancia": st.column_config.NumberColumn(disabled=True, format="$ %.2f"),
                "Ganancia Neta": st.column_config.NumberColumn(disabled=True, format="$ %.2f"),
                "Inversión": st.column_config.NumberColumn(format="$ %.2f", min_value=0.0),
                "Cuota": st.column_config.NumberColumn(format="%.2f", min_value=1.01),
                "Estado": st.column_config.SelectboxColumn(options=["Pendiente", "Ganada", "Perdida", "Nula"]),
                "🗑️ Eliminar": st.column_config.CheckboxColumn("🗑️ Eliminar", default=False)
            },
            hide_index=True,
            width="stretch",
            key="editor_billetera"
        )
        
        if st.button("💾 Guardar Cambios en la Billetera", type="primary"):
            for i in range(len(edited_df)):
                id_ap = int(edited_df.iloc[i]['ID'])
                eliminar = bool(edited_df.iloc[i].get('🗑️ Eliminar', False))
                
                if eliminar:
                    borrar_apuesta(id_ap)
                else:
                    picks = str(edited_df.iloc[i]['Picks'])
                    inver = float(edited_df.iloc[i]['Inversión'])
                    cuota_nueva = float(edited_df.iloc[i]['Cuota'])
                    est = str(edited_df.iloc[i]['Estado'])
                    fecha_nueva = str(edited_df.iloc[i].get('Fecha', ''))
                    
                    actualizar_fila_billetera(id_ap, picks, inver, cuota_nueva, est, fecha_nueva)
            
            st.success("✅ ¡Billetera actualizada correctamente! Recargando...")
            st.rerun()

    else:
        st.info("Aún no has enviado ninguna apuesta a tu billetera. Ve a tu Radar o añade una manualmente arriba.")
