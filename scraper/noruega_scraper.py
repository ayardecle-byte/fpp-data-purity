import requests
from bs4 import BeautifulSoup
import json
import os
import re
import time
import unicodedata
from datetime import datetime, timedelta

# 👇 1. DICCIONARIO MAESTRO NORUEGA (ELITESERIEN) 👇
diccionario_noruega = {
    "aalesund": "u4639-aalesund", "bodo/glimt": "u7879-bodo-glimt", "brann": "u7880-brann",
    "fredrikstad": "u4648-fredrikstad", "hamkam": "u4641-hamkam", "kfum oslo": "u4644-kfum-oslo",
    "kristiansund": "u7883-kristiansund", "lillestrom": "u7884-lillestrom", "molde": "u7892-molde",
    "rosenborg": "u7887-rosenborg", "sandefjord": "u7886-sandefjord", "sarpsborg 08": "u7889-sarpsborg-08",
    "start": "u4654-start", "tromso": "u7882-tromso", "valerenga": "u7891-valerenga", "viking": "u7890-viking"
}

def normalizar_nombre(nombre):
    nombre = nombre.lower().strip()
    nombre = ''.join(c for c in unicodedata.normalize('NFD', nombre) if unicodedata.category(c) != 'Mn')
    return nombre

def ajustar_hora_bolivia(fecha_str, hora_str):
    try:
        meses = {'Jan': 1, 'Feb': 2, 'Mar': 3, 'Apr': 4, 'May': 5, 'Jun': 6, 
                 'Jul': 7, 'Aug': 8, 'Sep': 9, 'Oct': 10, 'Nov': 11, 'Dec': 12}
        dias_inv = {0: 'Mon', 1: 'Tue', 2: 'Wed', 3: 'Thu', 4: 'Fri', 5: 'Sat', 6: 'Sun'}
        
        partes = fecha_str.split()
        if len(partes) != 3: return fecha_str, hora_str
        
        dia_num = int(partes[1])
        mes_num = meses.get(partes[2])
        horas, mins = map(int, hora_str.split(':'))
        
        año_actual = datetime.now().year
        dt = datetime(año_actual, mes_num, dia_num, horas, mins)
        dt_local = dt - timedelta(hours=4)
        
        nueva_fecha = f"{dias_inv[dt_local.weekday()]} {dt_local.day} {list(meses.keys())[list(meses.values()).index(dt_local.month)]}"
        nueva_hora = dt_local.strftime("%H:%M")
        return nueva_fecha, nueva_hora
    except:
        return fecha_str, hora_str

# 👇 2. EL MOTOR MATEMÁTICO (OMNI-ESCÁNER ACTUALIZADO) 👇
def extraer_datos_avanzados(codigo_equipo, nombre_equipo):
    url = f"https://www.soccerstats.com/teamstats.asp?league=norway&stats={codigo_equipo}"
    headers = {'User-Agent': 'Mozilla/5.0'}
    
    estadisticas = {"ht_stats": {"partidos_jugados": 0, "over_05_ht": 0, "over_15_ht": 0, "ganando_ht": 0, "empatando_ht": 0, "perdiendo_ht": 0, "goles_a_favor_ht": 0, "goles_en_contra_ht": 0}, "historial": []}
    
    try:
        time.sleep(1)
        res = requests.get(url, headers=headers, timeout=15)
        if res.status_code != 200:
            return estadisticas
            
        soup = BeautifulSoup(res.text, 'html.parser')
        partidos_procesados = set()
        
        for tr in soup.find_all('tr'):
            tds = tr.find_all('td')
            if 4 <= len(tds) <= 15:
                score_col = -1
                score_final = ""
                
                for i, td in enumerate(tds):
                    # 👇 EL TRUCO CONTRA EL TOOLTIP 👇
                    txt = td.get_text(separator=" ", strip=True)
                    match = re.search(r'^(\d+\s*[-:]\s*\d+)', txt)
                    if match:
                        score_col = i
                        score_final = match.group(1).replace('-', ':').replace(' ', '')
                        break
                        
                if score_col > 0 and score_col < len(tds) - 1:
                    td_fecha = tds[0]
                    td_local = tds[score_col - 1]
                    td_visita = tds[score_col + 1]
                    
                    fecha = td_fecha.get_text(strip=True)[:12]
                    local = td_local.get_text(strip=True)
                    visita = td_visita.get_text(strip=True)
                    
                    if not local or not visita or local.isdigit() or visita.isdigit():
                        continue
                        
                    # 🎯 Identificar Negrita
                    somos_local = td_local.find('b') is not None
                    somos_visita = td_visita.find('b') is not None
                    
                    if not somos_local and not somos_visita:
                        def simplificar(t): return re.sub(r'[^a-z0-9]', '', unicodedata.normalize('NFD', t.lower()))
                        nn = simplificar(nombre_equipo)
                        nl = simplificar(local)
                        nv = simplificar(visita)
                        somos_local = (nn in nl or nl in nn)
                        somos_visita = (nn in nv or nv in nn)
                        
                    if not somos_local and not somos_visita:
                        continue 
                        
                    id_partido = f"{fecha}-{local}-{visita}"
                    if id_partido in partidos_procesados:
                        continue
                    partidos_procesados.add(id_partido)
                    
                    ht_score = "-"
                    for td_extra in tds[score_col+1:]:
                        txt_extra = td_extra.get_text(strip=True)
                        match_ht = re.search(r'^\(?(\d+)\s*-\s*(\d+)\)?$', txt_extra)
                        if match_ht:
                            ht_score = f"{match_ht.group(1)}-{match_ht.group(2)}"
                            break
                            
                    try:
                        g_l, g_v = map(int, score_final.split(':'))
                        if somos_local:
                            icon = "🟢" if g_l > g_v else "🔴" if g_l < g_v else "🟡"
                        else:
                            icon = "🟢" if g_v > g_l else "🔴" if g_v < g_l else "🟡"
                    except Exception:
                        icon = "⚪"
                        
                    estadisticas["historial"].append({"Status": icon, "Fecha": fecha, "Local": local, "Res": f"[{score_final}]", "Visita": visita, "HT": ht_score})
                    
                    if ht_score != "-":
                        try:
                            gl_ht, gv_ht = map(int, ht_score.split('-'))
                            if somos_local:
                                gn_ht, gr_ht = gl_ht, gv_ht
                            else:
                                gn_ht, gr_ht = gv_ht, gl_ht
                            tot_ht = gn_ht + gr_ht
                            
                            estadisticas["ht_stats"]["partidos_jugados"] += 1
                            estadisticas["ht_stats"]["goles_a_favor_ht"] += gn_ht
                            estadisticas["ht_stats"]["goles_en_contra_ht"] += gr_ht
                            
                            if tot_ht >= 1: estadisticas["ht_stats"]["over_05_ht"] += 1
                            if tot_ht >= 2: estadisticas["ht_stats"]["over_15_ht"] += 1
                            
                            if gn_ht > gr_ht: estadisticas["ht_stats"]["ganando_ht"] += 1
                            elif gn_ht < gr_ht: estadisticas["ht_stats"]["perdiendo_ht"] += 1
                            else: estadisticas["ht_stats"]["empatando_ht"] += 1
                        except Exception:
                            pass

        estadisticas["historial"] = [p for p in estadisticas["historial"] if p["Status"] != "⚪"]
        stats = estadisticas["ht_stats"]
        pj = stats["partidos_jugados"]
        if pj > 0:
            stats["%_over_0.5_ht"] = round((stats["over_05_ht"] / pj) * 100, 1)
            stats["%_over_1.5_ht"] = round((stats["over_15_ht"] / pj) * 100, 1)
            stats["%_ganando_ht"] = round((stats["ganando_ht"] / pj) * 100, 1)
            stats["promedio_gf_ht"] = round(stats["goles_a_favor_ht"] / pj, 2)
            stats["promedio_gc_ht"] = round(stats["goles_en_contra_ht"] / pj, 2)
        
        if len(estadisticas["historial"]) > 0:
            estadisticas["racha_ultimos_5"] = "".join([p["Status"] for p in estadisticas["historial"][-5:]])
        else:
            estadisticas["racha_ultimos_5"] = "Sin datos"
            
    except Exception:
        pass
        
    return estadisticas

# 👇 3. EL ORQUESTADOR DEFINITIVO 👇
def actualizar_noruega():
    url_latest = "https://www.soccerstats.com/latest.asp?league=norway" 
    headers = {'User-Agent': 'Mozilla/5.0'}
    print(f"⏳ 1. Conectando a SoccerStats (Noruega)...")
    try:
        response = requests.get(url_latest, headers=headers, timeout=15)
        soup = BeautifulSoup(response.text, 'html.parser')
        posiciones = [["", "Team", "GP", "W", "D", "L", "GF", "GA", "GD", "Pts"]]
        estadisticas_avanzadas = {}
        equipos_guardados = 0
        
        for tabla in soup.find_all('table'):
            for row in tabla.find_all('tr'):
                tds = row.find_all('td')
                if len(tds) >= 10: # 👇 CORRECCIÓN: Evitar columnas vacías al final
                    textos = [t.get_text(strip=True) for t in tds]
                    pos = textos[0].replace('.', '').strip()
                    pts_candidate = textos[9].replace('*', '').strip() if len(textos) > 9 else ""
                    
                    if pos.isdigit() and pts_candidate.isdigit() and int(pos) <= 50:
                        equipo = textos[1]
                        if any(equipo == fila[1] for fila in posiciones): continue
                        
                        w, d, l, gf, ga, gd = textos[3], textos[4], textos[5], textos[6], textos[7], textos[8]
                        pts = pts_candidate
                        
                        equipos_guardados += 1
                        posiciones.append([pos, equipo, textos[2], w, d, l, gf, ga, gd.replace('+', ''), pts])
                        
                        nn = normalizar_nombre(equipo)
                        if nn in diccionario_noruega:
                            print(f"   🔍 Analizando HT e Historial de: {equipo}...")
                            estadisticas_avanzadas[equipo] = extraer_datos_avanzados(diccionario_noruega[nn], equipo)
        
        # 👇 FIXTURE LIMPIO Y CON HORAS DE BOLIVIA 👇
        print(f"\n📅 3. Extrayendo el Fixture Limpio (Solo próximos partidos)...")
        url_results = "https://www.soccerstats.com/results.asp?league=norway"
        res_results = requests.get(url_results, headers=headers, timeout=15)
        soup_results = BeautifulSoup(res_results.text, 'html.parser')
        
        proximos_partidos = []
        partidos_vistos = set()
        
        for tr in soup_results.find_all('tr'):
            tds = tr.find_all('td')
            if len(tds) >= 4:
                textos = [td.get_text(strip=True) for td in tds]
                fecha_texto = textos[0]
                if any(dia in fecha_texto for dia in ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']):
                    local, hora_o_resultado, visita = textos[1], textos[2], textos[3]
                    
                    if re.match(r'^\d{1,2}:\d{2}$', hora_o_resultado):
                        if local and visita and local.lower() != "matches":
                            fecha_texto, hora_o_resultado = ajustar_hora_bolivia(fecha_texto, hora_o_resultado)
                            partido_id = f"{local}-{visita}"
                            if partido_id not in partidos_vistos:
                                partidos_vistos.add(partido_id)
                                proximos_partidos.append({"Fecha": fecha_texto, "Hora": hora_o_resultado, "Local": local, "Visita": visita})
        
        if equipos_guardados == 0: return
            
        os.makedirs('data_json', exist_ok=True)
        with open('data_json/norway.json', 'w', encoding='utf-8') as f:
            json.dump({"posiciones": posiciones, "estadisticas_avanzadas": estadisticas_avanzadas, "fixture": proximos_partidos}, f, indent=4, ensure_ascii=False)
        print("✅ ¡Operación completada al 100%! Posiciones, HT y Fixture guardados.")
    except Exception as e: print(f"❌ Error: {e}")

if __name__ == "__main__":
    actualizar_noruega()