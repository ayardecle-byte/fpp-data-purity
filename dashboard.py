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

# --- 1. CONFIGURACIÓN ---
st.set_page_config(page_title="FPP - Data Purity Pro", page_icon="🛡️", layout="wide")

def cambiar_pagina(nombre_pagina):
    st.session_state.pagina = nombre_pagina

# --- 1B. BASE DE DATOS DE APUESTAS Y BILLETERA ---
def crear_tabla_apuestas():
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

crear_tabla_apuestas()

# --- FUNCIONES DE LIMPIEZA DE TEXTO ---
def normalize_text(text):
    if not isinstance(text, str): return ""
    nfkd_form = unicodedata.normalize('NFKD', text)
    return "".join([c for c in nfkd_form if not unicodedata.combining(c)]).lower().strip()

def normalize_db_name(name):
    name = normalize_text(name)
    # Quitamos sufijos de estados brasileños incluso si tienen guion (ej: chapecoense-sc -> chapecoense)
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
        "Estonia - Meistriliiga": "estonia", "Islandia - 2da División": "iceland2"
    }
    limite_equipos = {
        "Inglaterra - Premier League": 20, "España - La Liga": 20, "Italia - Serie A": 20, "Francia - Ligue 1": 18,
        "Argentina": 30, "Argentina - Primera Nacional": 40, "Brasil": 20, "Brasil - Serie B": 20,
        "Champions League": 36, "Europa League": 36, "Libertadores": 32, 
        "Copa Sudamericana": 32, "Noruega - Eliteserien": 16, "Estados Unidos - MLS": 30, "México - Liga MX": 18,
        "Bolivia - Div. Profesional": 16, "Estonia - Meistriliiga": 10, "Islandia - 2da División": 12
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
            if not club_name or any(word in club_name.lower() for word in basura_keywords): 
                continue
            lista.append({"Club": club_name, "PJ": row[2], "G": row[3], "E": row[4], "P": row[5], "GF": row[6], "GC": row[7], "DG": row[8], "Pts": row[9]})
    
    df = pd.DataFrame(lista)
    if not df.empty:
        df = df.drop_duplicates(subset=['Club'])
        cols_num = ['PJ', 'G', 'E', 'P', 'GF', 'GC', 'Pts', 'DG']
        for col in cols_num: 
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0)
            
        df = df.head(limite_equipos.get(nombre_liga, 20))
        
        if nombre_liga in ["Argentina", "Argentina - Primera Nacional"]:
            mitad = len(df) // 2
            df_zona_a = df.iloc[:mitad].copy()
            df_zona_b = df.iloc[mitad:].copy()
            
            if nombre_liga == "Argentina":
                if 'Instituto' in df_zona_b['Club'].values:
                    instituto_row = df_zona_b[df_zona_b['Club'] == 'Instituto']
                    df_zona_b = df_zona_b[df_zona_b['Club'] != 'Instituto']
                    df_zona_a = pd.concat([df_zona_a, instituto_row], ignore_index=True)
                
            df_zona_a = df_zona_a.sort_values(by=['Pts', 'DG'], ascending=[False, False]).reset_index(drop=True)
            df_zona_b = df_zona_b.sort_values(by=['Pts', 'DG'], ascending=[False, False]).reset_index(drop=True)
            
            df_zona_a.insert(0, 'Pos', range(1, len(df_zona_a) + 1))
            df_zona_b.insert(0, 'Pos', range(1, len(df_zona_b) + 1))
            
            df = pd.concat([df_zona_a, df_zona_b]).reset_index(drop=True)
            return df
        else:
            df.reset_index(drop=True, inplace=True)
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
    except: pass
    return -1, -1, -1, -1, -1, -1

def obtener_forma_visual_promediosinfo(nombre_equipo):
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
    if not sufijo: return [] 
        
    url = f"https://promediosinfo.com/{sufijo}.html"
    headers = {
        'User-Agent': 'Mozilla/5.0',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8'
    }
    
    try:
        res = requests.get(url, headers=headers, timeout=10)
        if res.status_code != 200: return []
            
        tablas = pd.read_html(StringIO(res.text))
        if len(tablas) > 1:
            df = tablas[1]
            if isinstance(df.columns, pd.MultiIndex): df.columns = df.columns.get_level_values(-1)
            df.columns = [str(c).strip() for c in df.columns]

            df = df.dropna(subset=['Res.'])
            df = df[df['Res.'].str.contains("-", na=False)]
            
            temp_split = df['Res.'].str.split('-', expand=True)
            if temp_split.shape[1] >= 2:
                df['GF_Temp'] = pd.to_numeric(temp_split[0], errors='coerce')
                df['GC_Temp'] = pd.to_numeric(temp_split[1], errors='coerce')
                df = df.dropna(subset=['GF_Temp', 'GC_Temp']) 
                
                ultimos_5 = df.tail(5).iloc[::-1] 
                
                detalles = []
                for _, r in ultimos_5.iterrows():
                    res_str = str(r['Res.']).strip()
                    rival = str(r['Rival']).strip().title()
                    cond = "🏠 L" if str(r['L/V']).strip().upper() == 'L' else "✈️ V"
                    
                    gf, gc = int(r['GF_Temp']), int(r['GC_Temp'])
                    if gf > gc: icono = '✅'
                    elif gf < gc: icono = '❌'
                    else: icono = '➖'
                        
                    detalles.append(f"{icono} {res_str} | {cond} vs {rival}")
                return detalles
    except: pass
    return []

def obtener_h2h_y_rachas(equipo_local, equipo_visita):
    try:
        conn = sqlite3.connect("database/football_data.db")
        query = "SELECT fecha, equipo_local, equipo_visita, goles_local, goles_visita, corners_local, corners_visita FROM partidos"
        df = pd.read_sql(query, conn)
        conn.close()
    except: return pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), -1, -1, -1, -1

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
        except: pass
    return detalles, ultima_fecha

def calcular_prediccion_avanzada(tabla, local, visita, racha_l_df, racha_v_df, gf_lh_old, gc_lh_old, gf_va_old, gc_va_old):
    row_l = tabla[tabla['Club'] == local].iloc[0]
    row_v = tabla[tabla['Club'] == visita].iloc[0]
    
    liga_gf_total = tabla['GF'].sum()
    liga_pj_total = tabla['PJ'].sum()
    avg_liga_goles = liga_gf_total / max(1, liga_pj_total)
    
    gf_l_avg = row_l['GF'] / max(1, row_l['PJ'])
    gc_l_avg = row_l['GC'] / max(1, row_l['PJ'])
    gf_v_avg = row_v['GF'] / max(1, row_v['PJ'])
    gc_v_avg = row_v['GC'] / max(1, row_v['PJ'])
    
    l_racha_gf, l_racha_gc, l_home_gf, l_home_gc, _, _ = obtener_stats_promediosinfo(local)
    v_racha_gf, v_racha_gc, _, _, v_away_gf, v_away_gc = obtener_stats_promediosinfo(visita)
    
    w_temp, w_racha, w_loc = 0.30, 0.40, 0.30  
    
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
            except: pass
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
            except: pass
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
    
    xg_local = (gf_l_final * gc_v_final) / avg_liga_goles
    xg_visita = (gf_v_final * gc_l_final) / avg_liga_goles
    
    prob_l, prob_e, prob_v, prob_mas_05, prob_mas_15, prob_mas_25, prob_menos_35, prob_btts_yes = 0, 0, 0, 0, 0, 0, 0, 0
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
        'Team_Totals_V': {'O05': o05_V, 'O15': o15_V, 'U15': u15_V, 'O25': o25_V, 'U25': u25_V, 'U35': u35_V}
    }

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
        except: pass
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
with st.sidebar:
    st.title("💎 FPP - Data Purity")
    st.button("⚽ Cartelera", on_click=cambiar_pagina, args=('Cartelera',), use_container_width=True)
    st.button("📅 Calendario Global", on_click=cambiar_pagina, args=('Calendario',), use_container_width=True)
    st.button("⭐ Mis Apuestas (Radar)", on_click=cambiar_pagina, args=('Favoritos',), use_container_width=True)
    st.button("💼 Billetera", on_click=cambiar_pagina, args=('Billetera',), use_container_width=True)
    st.markdown("---")
    
    opciones_liga = [
        "Inglaterra - Premier League", "España - La Liga", "Italia - Serie A", 
        "Francia - Ligue 1", "Argentina", "Argentina - Primera Nacional", "Brasil", 
        "Brasil - Serie B", "Champions League", 
        "Libertadores", "Copa Sudamericana", "Europa League", 
        "Noruega - Eliteserien", "Estados Unidos - MLS", "México - Liga MX",
        "Bolivia - Div. Profesional", "Estonia - Meistriliiga", "Islandia - 2da División"
    ]
    liga_sel = st.selectbox("Seleccionar Liga:", opciones_liga, key="sel_liga")
    
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
        
        if liga_sel in ["Argentina", "Argentina - Primera Nacional"]:
            mitad = len(tabla) // 2
            col_za, col_zb = st.columns(2)
            with col_za:
                st.markdown("#### 🔵 Zona A")
                st.dataframe(tabla.iloc[:mitad], use_container_width=True, hide_index=True)
            with col_zb:
                st.markdown("#### 🔴 Zona B")
                st.dataframe(tabla.iloc[mitad:], use_container_width=True, hide_index=True)
                
            if liga_sel in ["Argentina", "Argentina - Primera Nacional"]:
                st.markdown("---")
                st.markdown("#### 📅 Fixture (Próximos Partidos)")
                try:
                    archivo_arg = 'data_json/argentina.json' if liga_sel == "Argentina" else 'data_json/primera_nacional.json'
                    with open(archivo_arg, 'r', encoding='utf-8') as f:
                        data_arg = json.load(f)
                    fixture_data = data_arg.get("fixture", [])
                    if fixture_data:
                        st.dataframe(pd.DataFrame(fixture_data), use_container_width=True, hide_index=True)
                    else:
                        st.info("No hay partidos programados en el fixture en este momento.")
                except:
                    st.warning("⚠️ No se pudo cargar el archivo correspondiente. Ejecuta el scraper.")
                
        elif liga_sel in ["Brasil", "Brasil - Serie B", "Noruega - Eliteserien", "Estados Unidos - MLS", "México - Liga MX", "Estonia - Meistriliiga", "Islandia - 2da División"]:
            col_tabla, col_fixture = st.columns([1.3, 1])
            
            with col_tabla:
                st.markdown("#### 🏆 Tabla de Posiciones")
                st.dataframe(tabla, use_container_width=True, hide_index=True)
                
            with col_fixture:
                st.markdown("#### 📅 Fixture (Próximos Partidos)")
                try:
                    mapa_archivos_fix = {
                        "Brasil": 'data_json/brazil.json',
                        "Brasil - Serie B": 'data_json/serie_b_brasil.json',
                        "Noruega - Eliteserien": 'data_json/norway.json',
                        "Estados Unidos - MLS": 'data_json/mls.json',
                        "México - Liga MX": 'data_json/mexico.json',
                        "Estonia - Meistriliiga": 'data_json/estonia.json',
                        "Islandia - 2da División": 'data_json/iceland2.json'
                    }
                    archivo_fix = mapa_archivos_fix.get(liga_sel)
                    with open(archivo_fix, 'r', encoding='utf-8') as f:
                        data_fix = json.load(f)
                    fixture_data = data_fix.get("fixture", [])
                    if fixture_data:
                        st.dataframe(pd.DataFrame(fixture_data), use_container_width=True, hide_index=True)
                    else:
                        st.info("No hay partidos programados en el fixture en este momento.")
                except:
                    st.warning(f"⚠️ No se pudo cargar el archivo {archivo_fix}. Asegúrate de ejecutar el scraper.")

        else: 
            st.dataframe(tabla, use_container_width=True, hide_index=True)
        
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
                "Islandia - 2da División": "partidos+liga+islandesa"
            }
            query = mapa_busquedas.get(liga_sel, "partidos+de+futbol")
            url_widget = f"https://www.google.com/search?igu=1&q={query}"
            st.info("💡 Desliza hacia abajo dentro del cuadro para ver fechas pasadas o futuras. La hora se ajusta automáticamente a tu país.")
            st.components.v1.iframe(url_widget, height=650, scrolling=True)

        st.markdown("---")
        st.markdown(f"### 🔬 Radiografía Matemática: {liga_sel}")
        
        total_pj = tabla['PJ'].sum()
        if total_pj > 0:
            promedio_goles_liga = tabla['GF'].sum() / (total_pj / 2)
            
            if promedio_goles_liga >= 2.8: mejor_mercado = "🔥 **Más de 2.5 Goles / Ambos Anotan**\n(Liga sumamente ofensiva)"
            elif promedio_goles_liga <= 2.3: mejor_mercado = "🛡️ **Menos de 2.5 Goles / Ambos Anotan NO**\n(Liga muy cerrada y táctica)"
            elif promedio_goles_liga > 2.5: mejor_mercado = "⚽ **Más de 2.5 Goles**\n(Tendencia leve al Over)"
            else: mejor_mercado = "⚖️ **1X2 Localista / Menos de 2.5 Goles**\n(Liga equilibrada)"
            
            tabla_goles = tabla.copy()
            tabla_goles['Goles_Partido'] = (tabla_goles['GF'] + tabla_goles['GC']) / tabla_goles['PJ'].replace(0, 1)
            top_3_goles = tabla_goles.sort_values(by='Goles_Partido', ascending=False).head(3)
            
            col_i1, col_i2, col_i3 = st.columns(3)
            with col_i1:
                st.info(f"📊 **Promedio de la Liga**\n### {promedio_goles_liga:.2f}\n*goles por partido*")
            with col_i2:
                st.success(f"🎯 **Mejor Mercado (Sugerido)**\n\n{mejor_mercado}")
            with col_i3:
                texto_equipos = "\n".join([f"- **{r['Club']}** ({r['Goles_Partido']:.1f} g/p)" for _, r in top_3_goles.iterrows()])
                st.warning(f"💥 **Reyes del Over (Mejores 3)**\n{texto_equipos}")
                
        st.markdown("---")

        saved_l = st.session_state.get('last_l', lista[0] if len(lista) > 0 else "")
        saved_v = st.session_state.get('last_v', lista[1] if len(lista) > 1 else (lista[0] if len(lista) > 0 else ""))
        
        idx_l = lista.index(saved_l) if saved_l in lista else 0
        idx_v = lista.index(saved_v) if saved_v in lista else (1 if len(lista) > 1 else 0)

        c1, c2 = st.columns(2)
        l = c1.selectbox("🏠 Local", lista, index=idx_l, key="res_l")
        v = c2.selectbox("✈️ Visita", lista, index=idx_v, key="res_v")
        
        if st.session_state.get('last_l') != l or st.session_state.get('last_v') != v or st.session_state.get('last_liga') != liga_sel:
            st.session_state.last_l = l
            st.session_state.last_v = v
            st.session_state.last_liga = liga_sel
            st.session_state.analizar = False
            st.session_state.cuotas_restauradas = {}
        
        if st.button("📊 Analizar y Buscar Valor", use_container_width=True):
            st.session_state.analizar = True
        
        if st.session_state.get('analizar'):
            st.markdown("### 🥊 Cara a Cara (Tale of the Tape)")
            
            stats_L = tabla[tabla['Club'] == l].iloc[0]
            stats_V = tabla[tabla['Club'] == v].iloc[0]
            
            _, racha_l_df, racha_v_df, gf_lh, gc_lh, gf_va, gc_va = obtener_h2h_y_rachas(l, v)
            
            detalles_forma_L, _ = procesar_forma_detallada(racha_l_df, l)
            detalles_forma_V, _ = procesar_forma_detallada(racha_v_df, v)

            ligas_json_avanzadas = ["Brasil", "Brasil - Serie B", "Noruega - Eliteserien", "Argentina", "Argentina - Primera Nacional", "Estados Unidos - MLS", "México - Liga MX", "Estonia - Meistriliiga", "Islandia - 2da División"]
            if liga_sel in ligas_json_avanzadas:
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
                        "Islandia - 2da División": 'data_json/iceland2.json'
                    }
                    archivo_json = mapa_archivos_avanzados.get(liga_sel)
                    with open(archivo_json, 'r', encoding='utf-8') as f:
                        data_avanzada = json.load(f)
                    estadisticas = data_avanzada.get("estadisticas_avanzadas", {})
                    
                    hist_L = estadisticas.get(l, {}).get("historial", [])
                    hist_V = estadisticas.get(v, {}).get("historial", [])
                    
                    def armar_forma_json(equipo, hist):
                        if not hist: return ["No hay registros previos en SoccerStats"]
                        detalles = []
                        for r in reversed(hist[-5:]): 
                            gl, gv = map(int, r["Res"].strip("[]").split(":"))
                            es_loc = normalize_text(equipo) in normalize_text(r["Local"])
                            cond = "🏠 L" if es_loc else "✈️ V"
                            rival = r["Visita"] if es_loc else r["Local"]
                            res_str = f"**{gl} - {gv}**" if es_loc else f"**{gv} - {gl}**"
                            icono = '✅' if r["Status"]=="🟢" else '➖' if r["Status"]=="🟡" else '❌'
                            detalles.append(f"{icono} {res_str} | {cond} vs {rival.title()}")
                        return detalles
                    
                    if hist_L: detalles_forma_L = armar_forma_json(l, hist_L)
                    if hist_V: detalles_forma_V = armar_forma_json(v, hist_V)
                    
                    def json_to_df(hist):
                        if not hist: return pd.DataFrame()
                        filas = []
                        for p in hist:
                            try:
                                gl, gv = map(int, p["Res"].strip("[]").split(":"))
                                filas.append({
                                    'local_norm': normalize_db_name(p["Local"]),
                                    'visita_norm': normalize_db_name(p["Visita"]),
                                    'goles_local': gl,
                                    'goles_visita': gv
                                })
                            except Exception: 
                                pass
                        return pd.DataFrame(filas)

                    if hist_L: racha_l_df = json_to_df(hist_L[-5:])
                    if hist_V: racha_v_df = json_to_df(hist_V[-5:])

                    if hist_L:
                        df_full_L = json_to_df(hist_L)
                        df_home = df_full_L[df_full_L['local_norm'] == normalize_db_name(l)].tail(10)
                        if not df_home.empty:
                            gf_lh = df_home['goles_local'].mean()
                            gc_lh = df_home['goles_visita'].mean()

                    if hist_V:
                        df_full_V = json_to_df(hist_V)
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

            if liga_sel in ligas_json_avanzadas:
                try:
                    def calc_ht_10(equipo_nombre, historial):
                        if not historial: return 0, 0, 0, 0
                        u10 = historial[-10:] 
                        pj = len(u10)
                        if pj == 0: return 0, 0, 0, 0
                        
                        o05, o15, gana = 0, 0, 0
                        for partido in u10:
                            ht_score = partido.get("HT", "-")
                            if "-" in ht_score and ht_score.replace("-", "").isdigit():
                                g1, g2 = map(int, ht_score.split("-"))
                                tot = g1 + g2
                                if tot >= 1: o05 += 1
                                if tot >= 2: o15 += 1
                                
                                es_local = normalize_text(equipo_nombre) in normalize_text(partido["Local"])
                                gf = g1 if es_local else g2
                                gc = g2 if es_local else g1
                                if gf > gc: gana += 1
                                
                        return round((o05/pj)*100, 1), round((o15/pj)*100, 1), round((gana/pj)*100, 1), pj

                    if 'hist_L' in locals() and 'hist_V' in locals():
                        st.markdown("#### ⏱️ Radar de Medio Tiempo (HT) - Últimos 10 Partidos")
                        
                        o05_L, o15_L, gana_L, pj_L = calc_ht_10(l, hist_L)
                        o05_V, o15_V, gana_V, pj_V = calc_ht_10(v, hist_V)
                        
                        ht_c1, ht_c2 = st.columns(2)
                        with ht_c1:
                            st.markdown(f"🟢 **{l} (Local)** - *(Muestra: {pj_L} partidos)*")
                            r_col1, r_col2, r_col3 = st.columns(3)
                            r_col1.metric("Over 0.5 HT", f"{o05_L}%")
                            r_col2.metric("Over 1.5 HT", f"{o15_L}%")
                            r_col3.metric("Gana al Descanso", f"{gana_L}%")
                            
                        with ht_c2:
                            st.markdown(f"🔴 **{v} (Visita)** - *(Muestra: {pj_V} partidos)*")
                            v_col1, v_col2, v_col3 = st.columns(3)
                            v_col1.metric("Over 0.5 HT", f"{o05_V}%")
                            v_col2.metric("Over 1.5 HT", f"{o15_V}%")
                            v_col3.metric("Gana al Descanso", f"{gana_V}%")
                        
                        st.caption("ℹ️ Datos estadísticos calculados de forma dinámica tomando **exclusivamente** los 10 encuentros más recientes.")
                        st.divider()
        # 👇 NUEVO: TERMÓMETRO DE TENDENCIAS EMPÍRICAS (Solo Localía y Visita) 👇
                        st.markdown("#### 🌡️ Termómetro de Tendencias (Goles y Rachas Reales)")
                        st.caption("Filtro Estricto: Muestra el rendimiento exacto del Local jugando en casa (🏠) y de la Visita jugando fuera (✈️).")
                        
                        def calcular_tasas_condicion(historial, equipo_norm, es_local):
                            if not historial: return {"btts": 0, "gf_05": 0, "gc_05": 0, "o15": 0, "o25": 0, "pj": 0}
                            
                            u_condicion = []
                            # Filtrar solo los partidos en su condición actual (Local o Visita)
                            for p in reversed(historial):
                                p_es_local = equipo_norm in normalize_text(p["Local"])
                                if (es_local and p_es_local) or (not es_local and not p_es_local):
                                    u_condicion.append(p)
                                if len(u_condicion) == 10: break # Tomamos hasta 10 como muestra ideal
                            
                            pj = len(u_condicion)
                            if pj == 0: return {"btts": 0, "gf_05": 0, "gc_05": 0, "o15": 0, "o25": 0, "pj": 0}
                            
                            btts, gf_05, gc_05, o15, o25 = 0, 0, 0, 0, 0
                            for p in u_condicion:
                                try:
                                    g1, g2 = map(int, p["Res"].strip("[]").split(":"))
                                    p_es_loc = equipo_norm in normalize_text(p["Local"])
                                    gf = g1 if p_es_loc else g2
                                    gc = g2 if p_es_loc else g1
                                    
                                    if gf > 0 and gc > 0: btts += 1
                                    if gf > 0: gf_05 += 1
                                    if gc > 0: gc_05 += 1
                                    if (gf + gc) > 1.5: o15 += 1
                                    if (gf + gc) > 2.5: o25 += 1
                                except: pass
                                
                            return {
                                "btts": round((btts/pj)*100), 
                                "gf_05": round((gf_05/pj)*100), 
                                "gc_05": round((gc_05/pj)*100), 
                                "o15": round((o15/pj)*100), 
                                "o25": round((o25/pj)*100),
                                "pj": pj
                            }

                        n_l = normalize_text(l)
                        n_v = normalize_text(v)
                        
                        # Extraemos SOLO estadísticas de Local en casa y Visita de visitante
                        stats_L_casa = calcular_tasas_condicion(hist_L, n_l, True)
                        stats_V_fuera = calcular_tasas_condicion(hist_V, n_v, False)

                        # Crear DataFrame para mostrar estilo tabla visual
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
                        
                        st.dataframe(df_tendencias, use_container_width=True, hide_index=True)
                        st.divider()
                        # 👆 FIN DEL NUEVO BLOQUE 👆
                except Exception:
                    pass

            stats = calcular_prediccion_avanzada(tabla, l, v, racha_l_df, racha_v_df, gf_lh, gc_lh, gf_va, gc_va)
            
            if stats:
                st.info("🔥 **Motor V6.0:** Calculando Goles Exactos y Córners por Momentum.")
                
                st.markdown("#### ⚽ Probabilidades de Partido (1X2)")
                m1, m2, m3 = st.columns(3)
                m1.metric(f"Victoria {l}", f"{stats['1']:.1f}%", f"xG: {stats['xG_L']:.2f}")
                m2.metric("Empate", f"{stats['X']:.1f}%", None)
                m3.metric(f"Victoria {v}", f"{stats['2']:.1f}%", f"xG: {stats['xG_V']:.2f}", delta_color="inverse")
                
                st.divider()

                st.markdown("#### 🎯 Posibles Marcadores Exactos (Top 3)")
                sm1, sm2, sm3 = st.columns(3)
                sm1.metric("1ra Opción", stats['Top_Marcadores'][0][0], f"{stats['Top_Marcadores'][0][1]:.1f}% prob", delta_color="off")
                sm2.metric("2da Opción", stats['Top_Marcadores'][1][0], f"{stats['Top_Marcadores'][1][1]:.1f}% prob", delta_color="off")
                sm3.metric("3ra Opción", stats['Top_Marcadores'][2][0], f"{stats['Top_Marcadores'][2][1]:.1f}% prob", delta_color="off")

                st.divider()
                
                col_doble, col_btts, col_goles = st.columns(3)
                with col_doble:
                    st.markdown("#### 🛡️ Doble Oportunidad")
                    st.info(f"**1X ({l} o Empate):** {stats['1X']:.1f}%")
                    st.info(f"**X2 (Empate o {v}):** {stats['X2']:.1f}%")
                    st.info(f"**12 (Cualquiera gana):** {stats['12']:.1f}%")
                
                with col_btts:
                    st.markdown("#### 🤝 Ambos Anotan (BTTS)")
                    st.success(f"**SÍ:** {stats['BTTS_Y']:.1f}%")
                    st.error(f"**NO:** {stats['BTTS_N']:.1f}%")

                with col_goles:
                    st.markdown("#### 🎯 Mercado Totales")
                    st.success(f"**Más de 1.5:** {stats['Over15']:.1f}%")
                    st.warning(f"**Más de 2.5:** {stats['Over25']:.1f}%")
                    st.error(f"**Menos de 2.5:** {stats['Under25']:.1f}%")
                    st.info(f"**Menos de 3.5:** {stats['Under35']:.1f}%")
                    
                st.divider()

                st.markdown("#### 📊 Análisis de Goles por Equipo (Líneas Asiáticas y Goles Exactos)")
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
            
            st.markdown("### 🚩 Mercado de Córners (Tiros de Esquina)")
            stats_corners = calcular_prediccion_corners(racha_l_df, racha_v_df, l, v)
            
            if stats_corners:
                cc1, cc2, cc3 = st.columns(3)
                cc1.metric(f"xC (Córners Esperados) {l}", f"{stats_corners['xC_L']:.1f}")
                cc2.metric(f"xC (Córners Esperados) {v}", f"{stats_corners['xC_V']:.1f}")
                cc3.metric("xC Total del Partido", f"{(stats_corners['xC_L'] + stats_corners['xC_V']):.1f}")

                co1, co2, co3 = st.columns(3)
                co1.success(f"**Más de 8.5:** {stats_corners['Over85']:.1f}%")
                co2.warning(f"**Más de 9.5:** {stats_corners['Over95']:.1f}%")
                co3.error(f"**Más de 10.5:** {stats_corners['Over105']:.1f}%")
            else:
                st.info("ℹ️ Datos estadísticos de córners no disponibles para esta liga en la base de datos.")
            
            st.markdown("---")

            # --- ESCÁNER DE VALOR ---
            st.markdown("### 💰 Escáner de Valor (Ingresa tus cuotas reales)")
            c_mem = st.session_state.get('cuotas_restauradas', {})
            
            ligas_manuales = ["Bolivia - Div. Profesional", "Argentina", "Argentina - Primera Nacional", "Brasil", "Brasil - Serie B", "Libertadores", "Copa Sudamericana", "México - Liga MX", "Estados Unidos - MLS", "Estonia - Meistriliiga", "Islandia - 2da División", "Noruega - Eliteserien"]
            if liga_sel in ligas_manuales:
                t1, t2, t3, t4, t5 = st.tabs(["Ganador", "Doble Op", "Ambos Anotan", "Goles (Totales)", "Goles (Equipo)"])
                with t1: 
                    c1, c2, c3 = st.columns(3)
                    v1 = c1.number_input("Win L", 1.0, 15.0, float(c_mem.get('in_1', 1.0)), 0.01, key="in_1")
                    vx = c2.number_input("Empate", 1.0, 15.0, float(c_mem.get('in_x', 1.0)), 0.01, key="in_x")
                    v2 = c3.number_input("Win V", 1.0, 15.0, float(c_mem.get('in_2', 1.0)), 0.01, key="in_2")
                with t2:
                    c4, c5, c6 = st.columns(3)
                    v1x = c4.number_input("1X", 1.0, 15.0, float(c_mem.get('in_1x', 1.0)), 0.01, key="in_1x")
                    vx2 = c5.number_input("X2", 1.0, 15.0, float(c_mem.get('in_x2', 1.0)), 0.01, key="in_x2")
                    v12 = c6.number_input("12", 1.0, 15.0, float(c_mem.get('in_12', 1.0)), 0.01, key="in_12")
                with t3:
                    c7, c8 = st.columns(2)
                    vbtts_y = c7.number_input("SÍ Anotan", 1.0, 15.0, float(c_mem.get('in_btts_y', 1.0)), 0.01, key="in_btts_y")
                    vbtts_n = c8.number_input("NO Anotan", 1.0, 15.0, float(c_mem.get('in_btts_n', 1.0)), 0.01, key="in_btts_n")
                with t4:
                    c9, c10, c11, c11a, c11b = st.columns(5)
                    vo05 = c9.number_input("+0.5 Goles", 1.0, 15.0, float(c_mem.get('in_o05', 1.0)), 0.01, key="in_o05")
                    vo15 = c10.number_input("+1.5 Goles", 1.0, 15.0, float(c_mem.get('in_o15', 1.0)), 0.01, key="in_o15")
                    vo25 = c11.number_input("+2.5 Goles", 1.0, 15.0, float(c_mem.get('in_o25', 1.0)), 0.01, key="in_o25")
                    vu25 = c11a.number_input("-2.5 Goles", 1.0, 15.0, float(c_mem.get('in_u25', 1.0)), 0.01, key="in_u25")
                    vu35 = c11b.number_input("-3.5 Goles", 1.0, 15.0, float(c_mem.get('in_u35', 1.0)), 0.01, key="in_u35")
                with t5:
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

                st.session_state.cuotas_restauradas = {
                    'in_1': v1, 'in_x': vx, 'in_2': v2, 'in_1x': v1x, 'in_x2': vx2, 'in_12': v12, 
                    'in_btts_y': vbtts_y, 'in_btts_n': vbtts_n, 'in_o05': vo05, 'in_o15': vo15, 'in_o25': vo25, 'in_u25': vu25, 'in_u35': vu35, 
                    'in_l_o05': l_o05, 'in_l_o15': l_o15, 'in_l_u25': l_u25, 'in_l_u35': l_u35,
                    'in_v_o05': v_o05, 'in_v_o15': v_o15, 'in_v_u25': v_u25, 'in_v_u35': v_u35
                }

                todas_ops = []
                ops_valor = []
                def eval_val(prob, cuota, nombre):
                    if cuota > 1.01:
                        ev = ((prob/100) * cuota) - 1
                        
                        if prob >= 65.0: riesgo = "🟢 Seguro"
                        elif prob >= 50.0: riesgo = "🟡 Moderado"
                        else: riesgo = "🔴 Arriesgado"
                            
                        item = {"Mercado": nombre, "Prob": prob, "Cuota": cuota, "EV": ev*100, "Riesgo": riesgo}
                        todas_ops.append(item)
                        if ev > 0.01: ops_valor.append(item)
                
                eval_val(stats['1'], v1, f"Victoria {l}"); eval_val(stats['X'], vx, "Empate"); eval_val(stats['2'], v2, f"Victoria {v}")
                eval_val(stats['1X'], v1x, "Doble 1X"); eval_val(stats['X2'], vx2, "Doble X2"); eval_val(stats['12'], v12, "Doble 12")
                eval_val(stats['BTTS_Y'], vbtts_y, "Ambos Anotan (Sí)"); eval_val(stats['BTTS_N'], vbtts_n, "Ambos Anotan (No)")
                eval_val(stats['Over05'], vo05, "+0.5 Goles"); eval_val(stats['Over15'], vo15, "+1.5 Goles"); eval_val(stats['Over25'], vo25, "+2.5 Goles")
                eval_val(stats['Under25'], vu25, "-2.5 Goles"); eval_val(stats['Under35'], vu35, "-3.5 Goles")
                eval_val(stats['Team_Totals_L']['O05'], l_o05, f"{l} +0.5 Goles"); eval_val(stats['Team_Totals_L']['O15'], l_o15, f"{l} +1.5 Goles")
                eval_val(stats['Team_Totals_L']['U25'], l_u25, f"{l} -2.5 Goles"); eval_val(stats['Team_Totals_L']['U35'], l_u35, f"{l} -3.5 Goles")
                eval_val(stats['Team_Totals_V']['O05'], v_o05, f"{v} +0.5 Goles"); eval_val(stats['Team_Totals_V']['O15'], v_o15, f"{v} +1.5 Goles")
                eval_val(stats['Team_Totals_V']['U25'], v_u25, f"{v} -2.5 Goles"); eval_val(stats['Team_Totals_V']['U35'], v_u35, f"{v} -3.5 Goles")

                data_json = json.dumps(st.session_state.cuotas_restauradas)

                if todas_ops:
                    if ops_valor:
                        df_ops = pd.DataFrame(ops_valor).sort_values(by=["Prob", "EV"], ascending=[False, False]).reset_index(drop=True)
                        seguras = df_ops[df_ops['Prob'] >= 65.0]
                        
                        if not seguras.empty:
                            mejor = seguras.iloc[0]
                            st.success(f"💎 **¡Apuesta de Valor SEGURO Encontrada!**\n\nTe recomendamos fuertemente ir por: **{mejor['Mercado']}**. Tiene una altísima probabilidad del **{mejor['Prob']:.1f}%** y encima te paga una cuota con valor de **{mejor['Cuota']:.2f}**.")
                        else:
                            st.warning("⚠️ Se encontraron cuotas con valor matemático, pero ninguna es altamente segura (Todas por debajo del 65% de probabilidad). Considera invertir un Stake bajo.")
                            
                        st.dataframe(df_ops.style.format({'Prob': '{:.1f}%', 'Cuota': '{:.2f}', 'EV': '+{:.1f}%'}))
                    
                    st.markdown("#### 🎯 Arma tu Radar Personalizado")
                    opciones_multiselect = [f"{r['Mercado']} | Cuota {r['Cuota']:.2f} | EV {r['EV']:+.1f}%" for r in todas_ops]
                    selecciones = st.multiselect("Elige las apuestas que quieres guardar en tu radar (puedes seleccionar varias):", opciones_multiselect)
                    
                    if st.button("⭐ Guardar Seleccionadas en el Radar", use_container_width=True):
                        if selecciones:
                            for sel in selecciones:
                                idx = opciones_multiselect.index(sel)
                                fila = todas_ops[idx]
                                guardar_apuesta(liga_sel, l, v, fila['Mercado'], fila['Cuota'], fila['Prob'], fila['EV'], data_json)
                            st.success(f"✅ ¡{len(selecciones)} apuestas guardadas en tus Favoritos! Podrás pasarlas a la Billetera cuando te decidas.")
                        else:
                            st.warning("Selecciona al menos una apuesta de la lista arriba.")
                else:
                    st.info("Ingresa cuotas para buscar errores matemáticos.")
            else:
                try:
                    from scraper.api_odds import buscar_cuotas_en_vivo
                    with st.spinner("Consultando cuotas automáticas en vivo..."):
                        try: cuotas = buscar_cuotas_en_vivo(liga_sel, l, v)
                        except TypeError: cuotas = buscar_cuotas_en_vivo(l, v)
                        
                    if cuotas:
                        c_local = float(cuotas[0]) if isinstance(cuotas, tuple) else float(cuotas.get('local', 0))
                        c_empate = float(cuotas[1]) if isinstance(cuotas, tuple) else float(cuotas.get('empate', 0))
                        c_visita = float(cuotas[2]) if isinstance(cuotas, tuple) else float(cuotas.get('visita', 0))
                        
                        todas_ops = []
                        ops_valor = []
                        def eval_val(prob, cuota, nombre):
                            if cuota > 1.01:
                                ev = ((prob/100) * cuota) - 1
                                if prob >= 65.0: riesgo = "🟢 Seguro"
                                elif prob >= 50.0: riesgo = "🟡 Moderado"
                                else: riesgo = "🔴 Arriesgado"
                                    
                                item = {"Mercado": nombre, "Prob": prob, "Cuota": cuota, "EV": ev*100, "Riesgo": riesgo}
                                todas_ops.append(item)
                                if ev > 0.01: ops_valor.append(item)
                        
                        eval_val(stats['1'], c_local, f"Victoria {l}")
                        eval_val(stats['X'], c_empate, "Empate")
                        eval_val(stats['2'], c_visita, f"Victoria {v}")
                        
                        if todas_ops:
                            if ops_valor:
                                df_ops = pd.DataFrame(ops_valor).sort_values(by=["Prob", "EV"], ascending=[False, False]).reset_index(drop=True)
                                seguras = df_ops[df_ops['Prob'] >= 65.0]
                                if not seguras.empty:
                                    mejor = seguras.iloc[0]
                                    st.success(f"💎 **¡Apuesta de Valor SEGURO Encontrada!**\n\nTe recomendamos fuertemente: **{mejor['Mercado']}**. Tiene una probabilidad del **{mejor['Prob']:.1f}%**.")
                                else:
                                    st.warning("⚠️ Hay cuotas automáticas con valor, pero son de riesgo (Probabilidad menor al 65%).")

                                st.dataframe(df_ops.style.format({'Prob': '{:.1f}%', 'Cuota': '{:.2f}', 'EV': '+{:.1f}%'}))
                            else:
                                st.info("Las cuotas automáticas actuales no ofrecen valor matemático positivo, pero puedes guardarlas de todos modos.")
                                
                            st.markdown("#### 🎯 Arma tu Radar Personalizado")
                            opciones_multiselect = [f"{r['Mercado']} | Cuota {r['Cuota']:.2f} | EV {r['EV']:+.1f}%" for r in todas_ops]
                            selecciones = st.multiselect("Elige las apuestas que quieres guardar en tu radar (puedes seleccionar varias):", opciones_multiselect)
                            
                            if st.button("⭐ Guardar Seleccionadas en el Radar", use_container_width=True):
                                if selecciones:
                                    for sel in selecciones:
                                        idx = opciones_multiselect.index(sel)
                                        fila = todas_ops[idx]
                                        guardar_apuesta(liga_sel, l, v, fila['Mercado'], fila['Cuota'], fila['Prob'], fila['EV'], "{}")
                                    st.success(f"✅ ¡{len(selecciones)} apuestas guardadas en tus Favoritos!")
                                else:
                                    st.warning("Selecciona al menos una apuesta de la lista arriba.")
                        else:
                            st.warning("No se encontraron cuotas automáticas válidas.")
                    else:
                        st.warning("No se encontraron cuotas automáticas.")
                except Exception as e:
                    st.error("La API de cuotas automáticas no está disponible.")

            # --- ANÁLISIS CONTEXTUAL ---
            st.markdown("---")
            st.markdown("### 🧠 Análisis Contextual Avanzado (Gemini IA)")
            st.caption("Combina la estadística de Data Purity con el contexto real (lesiones, noticias, motivación).")

            fecha_hoy = datetime.datetime.now().strftime("%d de %B de %Y")

            prompt = f"""
¡ATENCIÓN! Hoy es {fecha_hoy}. Actúa como un analista profesional de fútbol, experto en estadística predictiva y apuestas deportivas. 
Tu tarea es analizar el partido entre {l} (Equipo Local) y {v} (Equipo Visitante) de la liga {liga_sel}.

DEBES escribir un análisis profundo, crítico y estructurado con viñetas.

PASO 1: INVESTIGACIÓN INDEPENDIENTE (Tu propio análisis)
Utiliza internet para buscar la información MÁS RECIENTE de ambos equipos e incluye:
- Estado de forma real (últimos 5 partidos de cada uno).
- Desempeño de {l} en casa y {v} de visita.
- Historial reciente (Cara a Cara).
- Bajas, lesiones o suspensiones CRÍTICAS actualizadas a hoy.
- Contexto (motivación, cansancio, rotaciones, peleas por título/descenso).

PASO 2: CONTRASTE CRÍTICO (Tú vs. Mi Modelo)
Examina las probabilidades matemáticas crudas calculadas por mi modelo estadístico (Data Purity):
- 1X2: Local {stats['1']:.1f}% | Empate {stats['X']:.1f}% | Visita {stats['2']:.1f}%
- Dobles: 1X {stats['1X']:.1f}% | X2 {stats['X2']:.1f}% | 12 {stats['12']:.1f}%
- Goles: +1.5 al {stats['Over15']:.1f}% | +2.5 al {stats['Over25']:.1f}%
- Ambos Anotan: Sí al {stats['BTTS_Y']:.1f}%

¡REGLA DE ORO! NO estés de acuerdo con mi modelo por obligación. Eres un auditor crítico. 
"""

            if not saved_api_key:
                st.error("⚠️ Por favor, ingresa tu API Key en el menú lateral.")
            else:
                if st.button("⚡ Solicitar Análisis Contextual", use_container_width=True, key=f"btn_unico_{l}_{v}"):
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
                                st.success("✅ Análisis completado:")
                                st.write(texto)
                            else:
                                st.error(f"❌ Error del servidor. Código: {respuesta.status_code}")
                                with st.expander("Ver detalles técnicos"):
                                    st.write(respuesta.text)
                                    
                        except requests.exceptions.Timeout:
                            st.error("⏳ ¡Tiempo agotado! El servidor tardó demasiado en responder. Intenta de nuevo.")
                        except Exception as e:
                            st.error(f"❌ Error de red crítico: {str(e)}")

# ==========================================
# PESTAÑA: CALENDARIO GLOBAL
# ==========================================
elif st.session_state.pagina == 'Calendario':
    st.header("📅 Calendario Global de Partidos")
    st.markdown("Revisa todos los próximos encuentros de tus ligas activas para saber dónde buscar oportunidades de valor hoy.")
    st.divider()

    col1, col2 = st.columns(2)

    with col1:
        with st.expander("🇧🇷 Brasil (Serie A)", expanded=True):
            try:
                with open('data_json/brazil.json', 'r', encoding='utf-8') as f:
                    fix_bra = json.load(f).get("fixture", [])
                if fix_bra: st.dataframe(pd.DataFrame(fix_bra), use_container_width=True, hide_index=True)
                else: st.info("No hay partidos programados.")
            except: st.warning("⚠️ Ejecuta el actualizador para cargar esta liga.")

        with st.expander("🇧🇷 Brasil - Serie B", expanded=True):
            try:
                with open('data_json/serie_b_brasil.json', 'r', encoding='utf-8') as f:
                    fix_brb = json.load(f).get("fixture", [])
                if fix_brb: st.dataframe(pd.DataFrame(fix_brb), use_container_width=True, hide_index=True)
                else: st.info("No hay partidos programados.")
            except: st.warning("⚠️ Ejecuta el actualizador para cargar esta liga.")

        with st.expander("🇺🇸 Estados Unidos (MLS)", expanded=True):
            try:
                with open('data_json/mls.json', 'r', encoding='utf-8') as f:
                    fix_mls = json.load(f).get("fixture", [])
                if fix_mls: st.dataframe(pd.DataFrame(fix_mls), use_container_width=True, hide_index=True)
                else: st.info("No hay partidos programados.")
            except: st.warning("⚠️ Ejecuta el actualizador para cargar esta liga.")

        with st.expander("🇲🇽 México (Liga MX)", expanded=True):
            try:
                with open('data_json/mexico.json', 'r', encoding='utf-8') as f:
                    fix_mex = json.load(f).get("fixture", [])
                if fix_mex: st.dataframe(pd.DataFrame(fix_mex), use_container_width=True, hide_index=True)
                else: st.info("No hay partidos programados.")
            except: st.warning("⚠️ Ejecuta el actualizador para cargar esta liga.")

        with st.expander("🇪🇪 Estonia (Meistriliiga)", expanded=True):
            try:
                with open('data_json/estonia.json', 'r', encoding='utf-8') as f:
                    fix_est = json.load(f).get("fixture", [])
                if fix_est: st.dataframe(pd.DataFrame(fix_est), use_container_width=True, hide_index=True)
                else: st.info("No hay partidos programados.")
            except: st.warning("⚠️ Ejecuta el actualizador para cargar esta liga.")

    with col2:
        with st.expander("🇦🇷 Argentina (Primera División)", expanded=True):
            try:
                with open('data_json/argentina.json', 'r', encoding='utf-8') as f:
                    fix_arg = json.load(f).get("fixture", [])
                if fix_arg: st.dataframe(pd.DataFrame(fix_arg), use_container_width=True, hide_index=True)
                else: st.info("No hay partidos programados.")
            except: st.warning("⚠️ Ejecuta el actualizador para cargar esta liga.")

        with st.expander("🇦🇷 Argentina - Primera Nacional", expanded=True):
            try:
                with open('data_json/primera_nacional.json', 'r', encoding='utf-8') as f:
                    fix_arg_b = json.load(f).get("fixture", [])
                if fix_arg_b: st.dataframe(pd.DataFrame(fix_arg_b), use_container_width=True, hide_index=True)
                else: st.info("No hay partidos programados.")
            except: st.warning("⚠️ Ejecuta el actualizador para cargar esta liga.")

        with st.expander("🇳🇴 Noruega (Eliteserien)", expanded=True):
            try:
                with open('data_json/norway.json', 'r', encoding='utf-8') as f:
                    fix_nor = json.load(f).get("fixture", [])
                if fix_nor: st.dataframe(pd.DataFrame(fix_nor), use_container_width=True, hide_index=True)
                else: st.info("No hay partidos programados.")
            except: st.warning("⚠️ Ejecuta el actualizador para cargar esta liga.")

        with st.expander("🇮🇸 Islandia (2da División)", expanded=True):
            try:
                with open('data_json/iceland2.json', 'r', encoding='utf-8') as f:
                    fix_ice = json.load(f).get("fixture", [])
                if fix_ice: st.dataframe(pd.DataFrame(fix_ice), use_container_width=True, hide_index=True)
                else: st.info("No hay partidos programados.")
            except: st.warning("⚠️ Ejecuta el actualizador para cargar esta liga.")

    with col2:
        with st.expander("🇧🇷 Brasil - Serie B", expanded=True):
            try:
                with open('data_json/serie_b_brasil.json', 'r', encoding='utf-8') as f:
                    fix_brb = json.load(f).get("fixture", [])
                if fix_brb:
                    st.dataframe(pd.DataFrame(fix_brb), use_container_width=True, hide_index=True)
                else:
                    st.info("No hay partidos programados.")
            except:
                st.warning("⚠️ Ejecuta el actualizador para cargar esta liga.")

        with st.expander("🇳🇴 Noruega - Eliteserien", expanded=True):
            try:
                with open('data_json/norway.json', 'r', encoding='utf-8') as f:
                    fix_nor = json.load(f).get("fixture", [])
                if fix_nor:
                    st.dataframe(pd.DataFrame(fix_nor), use_container_width=True, hide_index=True)
                else:
                    st.info("No hay partidos programados.")
            except:
                st.warning("⚠️ Ejecuta el actualizador para cargar esta liga.")

        with st.expander("🇦🇷 Argentina - Primera Nacional", expanded=True):
            try:
                with open('data_json/primera_nacional.json', 'r', encoding='utf-8') as f:
                    fix_arg_b = json.load(f).get("fixture", [])
                if fix_arg_b:
                    st.dataframe(pd.DataFrame(fix_arg_b), use_container_width=True, hide_index=True)
                else:
                    st.info("No hay partidos programados.")
            except:
                st.warning("⚠️ Ejecuta el actualizador para cargar esta liga.")
                
        with st.expander("🇪🇪 Estonia - Meistriliiga", expanded=True):
            try:
                with open('data_json/estonia.json', 'r', encoding='utf-8') as f:
                    fix_est = json.load(f).get("fixture", [])
                if fix_est:
                    st.dataframe(pd.DataFrame(fix_est), use_container_width=True, hide_index=True)
                else:
                    st.info("No hay partidos programados.")
            except:
                st.warning("⚠️ Ejecuta el actualizador para cargar esta liga.")
                
        with st.expander("🇮🇸 Islandia - 2da División", expanded=True):
            try:
                with open('data_json/iceland2.json', 'r', encoding='utf-8') as f:
                    fix_ice = json.load(f).get("fixture", [])
                if fix_ice:
                    st.dataframe(pd.DataFrame(fix_ice), use_container_width=True, hide_index=True)
                else:
                    st.info("No hay partidos programados.")
            except:
                st.warning("⚠️ Ejecuta el actualizador para cargar esta liga.")

# ==========================================
# PESTAÑA 2: FAVORITOS (RADAR)
# ==========================================
elif st.session_state.pagina == 'Favoritos':
    st.header("⭐ Radar de Apuestas (Watchlist)")
    st.markdown("Estos son los tickets que tienen valor matemático, pero aún no has apostado en ellos. Puedes pasarlos de forma **Simple** o seleccionar varios para armar una **Combinada (Parlay)**.")
    
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
            st.session_state.cuotas_restauradas = json.loads(cuotas_str)
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
                st.caption(f"Mercado: {row['mercado']} | Prob: **{row['probabilidad']:.1f}%** | Cuota: {row['cuota']} | EV: +{row['ev']:.1f}%")
            
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
            
            picks_combinados = " + ".join([f"{r['equipo_local']} ({r['mercado']})" for r in seleccionados])
            
            cuota_referencia = 1.0
            for r in seleccionados:
                cuota_referencia *= float(r['cuota'])
                
            st.write(f"**Picks Seleccionados:** {picks_combinados}")
            
            col_cmb1, col_cmb2 = st.columns(2)
            with col_cmb1:
                st.metric("Cuota Base (Multiplicada)", f"{cuota_referencia:.2f}")
            with col_cmb2:
                cuota_manual = st.number_input("Cuota Final (Ingresa la cuota real de tu casa de apuestas)", min_value=1.01, value=float(round(cuota_referencia, 2)), step=0.01)
                
            if st.button("🚀 Crear Combinada y Enviar a Billetera", type="primary", use_container_width=True):
                fecha_actual = datetime.datetime.now().strftime("%Y-%m-%d")
                liga_comb = "Apuesta Combinada"
                local_comb = "Varios"
                visita_comb = "Varios"
                mercado_comb = f"Parlay ({len(seleccionados)} selecciones)"
                
                conn = sqlite3.connect("database/football_data.db")
                cursor = conn.cursor()
                
                cursor.execute('''INSERT INTO mis_apuestas (liga, equipo_local, equipo_visita, mercado, cuota, probabilidad, ev, cuotas_json, picks, inversion, estado, en_billetera, fecha_apuesta) 
                                  VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)''', 
                               (liga_comb, local_comb, visita_comb, mercado_comb, cuota_manual, 0.0, 0.0, "{}", picks_combinados, 0.0, "Pendiente", fecha_actual))
                
                for r in seleccionados:
                    cursor.execute('DELETE FROM mis_apuestas WHERE id = ?', (r['id'],))
                    
                conn.commit()
                conn.close()
                
                st.success("✅ ¡Combinada creada con éxito! Ve a tu Billetera para gestionar la inversión.")
                st.rerun()

# ==========================================
# PESTAÑA 3: BILLETERA (GESTIÓN)
# ==========================================
elif st.session_state.pagina == 'Billetera':
    st.header("💼 Gestión de Bankroll y Billetera")
    st.markdown("Tu centro de inversiones. Controla tu progreso y registra las ganancias y pérdidas reales idéntico a tu Excel.")
    
    with st.expander("➕ Añadir Apuesta Manualmente a la Billetera", expanded=False):
        with st.form("form_manual"):
            st.write("Registra un pick de otra liga, deporte o tipster externo:")
            m_c1, m_c2 = st.columns(2)
            m_liga = m_c1.text_input("Liga / Torneo (Ej: NBA, Tenis, Liga 1)")
            m_local = m_c2.text_input("Equipo Local")
            m_visita = m_c1.text_input("Equipo Visitante")
            m_picks = m_c2.text_input("Picks / Mercado (Ej: Más 2.5 Goles)")
            
            m_c3, m_c4, m_c5, m_c6 = st.columns(4)
            m_fecha = m_c3.date_input("Fecha", datetime.date.today())
            m_cuota = m_c4.number_input("Cuota", min_value=1.01, step=0.01)
            m_stake = m_c5.number_input("Stake (Nivel de confianza 1-10)", min_value=1, max_value=10, value=5, step=1)
            m_inv = m_c6.number_input("Inversión Real ($)", min_value=0.0, step=1.0)
            
            if st.form_submit_button("✅ Guardar Directo en Billetera"):
                if m_local and m_visita and m_picks:
                    guardar_apuesta_manual(m_liga, m_local, m_visita, m_picks, m_inv, m_cuota, m_stake, m_fecha.strftime("%Y-%m-%d"))
                    st.success("¡Apuesta registrada exitosamente!")
                    st.rerun()
                else:
                    st.error("Por favor completa los campos de texto.")

    conn = sqlite3.connect("database/football_data.db")
    try:
        df_apuestas = pd.read_sql("SELECT * FROM mis_apuestas WHERE en_billetera = 1", conn)
    except:
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
        prob = float(row['probabilidad'])
        
        stake_calc = min(10, max(1, round(prob / 10))) 
        
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
        st.markdown("### 🧮 Sugerencia de Inversión Segura")
        st.caption("A mayor cuota, menor inversión sugerida (Máx 5% de tu Bank)")
        calc_cuota = st.number_input("Simular Cuota:", min_value=1.01, value=1.80, step=0.1)
        
        sim_stake = min(10, max(1, round(10 / calc_cuota)))
        max_inv = balance_total * 0.05
        sim_inv = (sim_stake / 10.0) * max_inv
        
        st.info(f"Para Cuota **{calc_cuota}** ➔ **Stake {sim_stake}/10** ➔ Invertir aprox: **$ {sim_inv:.2f}**")

    with colC:
        st.markdown(f"<div class='metric-box'><div class='metric-title'>Inversión Total</div><div class='metric-val'>$ {inv_total:.2f}</div></div><br>", unsafe_allow_html=True)
        st.markdown(f"<div class='metric-box'><div class='metric-title'>Ganancia Neta</div><div class='metric-val green'>$ {ganancia_neta_total:.2f}</div></div>", unsafe_allow_html=True)

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
    
    st.markdown("---")
    st.subheader("📋 Tabla de Apuestas en Billetera (Editor Interactivo)")
    st.caption("Haz doble clic en 'Picks', 'Inversión', 'Cuota' o 'Estado' para editar. Los cálculos se actualizarán solos. Marca '🗑️ Eliminar' para borrar un registro.")
    
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
            use_container_width=True,
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