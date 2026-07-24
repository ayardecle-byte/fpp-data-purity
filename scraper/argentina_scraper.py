import requests
from bs4 import BeautifulSoup
import json
import os
import re
import time
import unicodedata

# 👇 1. DICCIONARIO MAESTRO ARGENTINA PRIMERA DIVISIÓN 👇
diccionario_primera_div = {
    "a. tucuman": "u7388-a.-tucuman",
    "aldosivi": "u7383-aldosivi",
    "argentinos jrs": "u7400-argentinos-jrs",
    "banfield": "u7402-banfield",
    "barracas c.": "u4035-barracas-c.",
    "belgrano": "u4017-belgrano",
    "boca juniors": "u7380-boca-juniors",
    "central cordoba": "u7401-central-cordoba",
    "d. riestra": "u4015-d.-riestra",
    "defensa y j.": "u7394-defensa-y-j.",
    "e. rio cuarto": "u4011-e.-rio-cuarto",
    "estudiantes": "u7382-estudiantes",
    "g. mendoza": "u4013-g.-mendoza",
    "gimnasia": "u7389-gimnasia",
    "huracan": "u7393-huracan",
    "i. rivadavia": "u4037-i.-rivadavia",
    "independiente": "u7399-independiente",
    "instituto": "u4034-instituto",
    "lanus": "u7387-lanus",
    "newells": "u7385-newells",
    "platense": "u7390-platense",
    "racing club": "u7392-racing-club",
    "river plate": "u7397-river-plate",
    "rosario central": "u7404-rosario-central",
    "san lorenzo": "u7396-san-lorenzo",
    "sarmiento": "u7381-sarmiento",
    "t. de cordoba": "u7386-t.-de-cordoba",
    "tigre": "u4018-tigre",
    "union santa fe": "u7379-union-santa-fe",
    "velez sarsfield": "u7391-velez-sarsfield"
}

def normalizar_nombre(nombre):
    nombre = nombre.lower().strip()
    nombre = ''.join(c for c in unicodedata.normalize('NFD', nombre) if unicodedata.category(c) != 'Mn')
    return nombre

# 👇 2. EL MOTOR MATEMÁTICO (VERSIÓN BLINDADA OMNI-ESCÁNER) 👇
def extraer_datos_avanzados(codigo_equipo, nombre_equipo):
    url = f"https://www.soccerstats.com/teamstats.asp?league=argentina&stats={codigo_equipo}"
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36'}
    
    estadisticas = {
        "ht_stats": {
            "partidos_jugados": 0, "over_05_ht": 0, "over_15_ht": 0,
            "ganando_ht": 0, "empatando_ht": 0, "perdiendo_ht": 0,
            "goles_a_favor_ht": 0, "goles_en_contra_ht": 0
        },
        "historial": []
    }
    
    def limpiar_prefijos(nombre):
        basura = ['ca ', 'cd ', 'cs ', 'atletico ', 'deportivo ', 'club ', 'a. ', 'd. ', 'sm ']
        n = nombre
        for b in basura:
            if n.startswith(b):
                n = n.replace(b, '', 1)
        return n.strip()
    
    try:
        time.sleep(1) # Pausa vital anti-bloqueo
        res = requests.get(url, headers=headers, timeout=15)
        
        if res.status_code == 200:
            soup = BeautifulSoup(res.text, 'html.parser')
            partidos_procesados = set()
            
            nombre_norm = normalizar_nombre(nombre_equipo)
            nn_limpio = limpiar_prefijos(nombre_norm)
            
            for tr in soup.find_all('tr'):
                tds = tr.find_all('td')
                if 4 <= len(tds) <= 12:
                    textos = [td.get_text(separator=" ", strip=True) for td in tds]
                    
                    score_col = -1
                    for i, txt in enumerate(textos):
                        if re.match(r'^\d+\s*[-:]\s*\d+$', txt.strip()):
                            score_col = i
                            break
                            
                    if score_col > 0 and score_col < len(textos) - 1:
                        fecha = textos[0][:12] 
                        local = textos[score_col - 1].strip()
                        visita = textos[score_col + 1].strip()
                        score_final = textos[score_col].replace('-', ':').replace(' ', '')
                        
                        if not local or not visita or len(local) > 30 or len(visita) > 30:
                            continue
                        if local.isdigit() or visita.isdigit():
                            continue
                            
                        local_norm = normalizar_nombre(local)
                        visita_norm = normalizar_nombre(visita)
                        nl_limpio = limpiar_prefijos(local_norm)
                        nv_limpio = limpiar_prefijos(visita_norm)
                        
                        somos_local = False
                        somos_visita = False
                        
                        if nn_limpio in nl_limpio or nl_limpio in nn_limpio:
                            somos_local = True
                        elif nn_limpio in nv_limpio or nv_limpio in nn_limpio:
                            somos_visita = True
                            
                        if not somos_local and not somos_visita:
                            continue 
                            
                        id_partido = f"{fecha}-{local}-{visita}"
                        if id_partido in partidos_procesados:
                            continue
                        partidos_procesados.add(id_partido)
                        
                        ht_score = "-"
                        for txt in textos[score_col+1:]:
                            txt_c = txt.strip()
                            match_ht = re.search(r'\(\s*(\d+)\s*-\s*(\d+)\s*\)', txt_c)
                            if match_ht:
                                ht_score = f"{match_ht.group(1)}-{match_ht.group(2)}"
                                break
                            if re.match(r'^\d+\s*-\s*\d+$', txt_c):
                                ht_score = txt_c.replace(" ", "")
                                break
                                
                        try:
                            g_l, g_v = map(int, score_final.split(':'))
                            if somos_local:
                                icon = "🟢" if g_l > g_v else "🔴" if g_l < g_v else "🟡"
                            else:
                                icon = "🟢" if g_v > g_l else "🔴" if g_v < g_l else "🟡"
                        except:
                            icon = "⚪"
                            
                        estadisticas["historial"].append({
                            "Status": icon, "Fecha": fecha, "Local": local,
                            "Res": f"[{score_final}]", "Visita": visita, "HT": ht_score
                        })
                        
                        if ht_score != "-":
                            try:
                                gl_ht, gv_ht = map(int, ht_score.split('-'))
                                goles_nuestros_ht = gl_ht if somos_local else gv_ht
                                goles_rival_ht = gv_ht if somos_local else gl_ht
                                total_goles_ht = goles_nuestros_ht + goles_rival_ht
                                
                                estadisticas["ht_stats"]["partidos_jugados"] += 1
                                estadisticas["ht_stats"]["goles_a_favor_ht"] += goles_nuestros_ht
                                estadisticas["ht_stats"]["goles_en_contra_ht"] += goles_rival_ht
                                
                                if total_goles_ht >= 1: estadisticas["ht_stats"]["over_05_ht"] += 1
                                if total_goles_ht >= 2: estadisticas["ht_stats"]["over_15_ht"] += 1
                                
                                if goles_nuestros_ht > goles_rival_ht: estadisticas["ht_stats"]["ganando_ht"] += 1
                                elif goles_nuestros_ht < goles_rival_ht: estadisticas["ht_stats"]["perdiendo_ht"] += 1
                                else: estadisticas["ht_stats"]["empatando_ht"] += 1
                            except: pass

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
                ultimos_5 = estadisticas["historial"][-5:]
                racha_str = "".join([p["Status"] for p in ultimos_5])
                estadisticas["racha_ultimos_5"] = racha_str
            else:
                estadisticas["racha_ultimos_5"] = "Sin datos"
                
    except Exception as e:
        print(f"⚠️ Error extrayendo HT e Historial de {nombre_equipo}: {e}")
        
    return estadisticas

# 👇 3. EL ORQUESTADOR DEFINITIVO 👇
def actualizar_primera_division():
    url = "https://www.soccerstats.com/latest.asp?league=argentina" 
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36'}
    
    print(f"⏳ 1. Conectando a SoccerStats (Argentina Primera División)...")
    try:
        response = requests.get(url, headers=headers, timeout=15)
        soup = BeautifulSoup(response.text, 'html.parser')
        tablas = soup.find_all('table')
        
        posiciones = [["", "Team", "GP", "W", "D", "L", "GF", "GA", "GD", "Pts"]]
        estadisticas_avanzadas = {}
        equipos_guardados = 0
        
        print(f"📊 2. Buscando tablas. Extrayendo datos y métricas...")
        for tabla in tablas:
            filas = tabla.find_all('tr')
            for row in filas:
                tds = row.find_all('td')
                
                if len(tds) >= 8:
                    textos = [t.get_text(strip=True) for t in tds]
                    pos = textos[0].replace('.', '').strip() 
                    pts_str = textos[-1].replace('*', '').strip() 
                    
                    if pos.isdigit() and pts_str.isdigit():
                        equipo = textos[1]
                        
                        if any(equipo == fila[1] for fila in posiciones):
                            continue
                            
                        gp = textos[2]
                        pts = textos[-1]
                        
                        if len(textos) >= 10:
                            w, d, l, gf, ga, gd = textos[3:9]
                            gd = gd.replace('+', '')
                        else:
                            w, d, l, gf, ga, gd = "0", "0", "0", "0", "0", "0"
                            
                        equipos_guardados += 1
                        posiciones.append([pos, equipo, gp, w, d, l, gf, ga, gd, pts])
                        
                        nombre_normalizado = normalizar_nombre(equipo)
                        if nombre_normalizado in diccionario_primera_div:
                            codigo = diccionario_primera_div[nombre_normalizado]
                            print(f"   🔍 Analizando HT e Historial de: {equipo}...")
                            datos_extra = extraer_datos_avanzados(codigo, equipo)
                            estadisticas_avanzadas[equipo] = datos_extra
                        else:
                            print(f"   ⚠️ Advertencia: '{equipo}' no está en el diccionario.")
        
        # 👇 3. NUEVO FIXTURE LIMPIO CON RELOJ BOLIVIANO 👇
        print(f"\n📅 3. Extrayendo el Fixture Limpio (Solo próximos partidos)...")
        
        def ajustar_hora_bolivia(fecha_str, hora_str):
            try:
                from datetime import datetime, timedelta
                meses = {'Jan': 1, 'Feb': 2, 'Mar': 3, 'Apr': 4, 'May': 5, 'Jun': 6, 
                         'Jul': 7, 'Aug': 8, 'Sep': 9, 'Oct': 10, 'Nov': 11, 'Dec': 12}
                dias_inv = {0: 'Mon', 1: 'Tue', 2: 'Wed', 3: 'Thu', 4: 'Fri', 5: 'Sat', 6: 'Sun'}
                partes = fecha_str.split()
                if len(partes) != 3: return fecha_str, hora_str
                dia_num, mes_num = int(partes[1]), meses.get(partes[2])
                horas, mins = map(int, hora_str.split(':'))
                dt = datetime(datetime.now().year, mes_num, dia_num, horas, mins) - timedelta(hours=4)
                return f"{dias_inv[dt.weekday()]} {dt.day} {list(meses.keys())[list(meses.values()).index(dt.month)]}", dt.strftime("%H:%M")
            except: return fecha_str, hora_str

        url_results = "https://www.soccerstats.com/results.asp?league=argentina"
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
                    
                    if re.match(r'^\d{1,2}:\d{2}$', hora_o_resultado) or hora_o_resultado.lower() == 'pp.':
                        if local and visita and local.lower() != "matches":
                            if re.match(r'^\d{1,2}:\d{2}$', hora_o_resultado):
                                fecha_texto, hora_o_resultado = ajustar_hora_bolivia(fecha_texto, hora_o_resultado)
                                
                            partido_id = f"{local}-{visita}"
                            if partido_id not in partidos_vistos:
                                partidos_vistos.add(partido_id)
                                proximos_partidos.append({"Fecha": fecha_texto, "Hora": hora_o_resultado, "Local": local, "Visita": visita})
        # 👆 FIN DEL FIXTURE 👆
        
        if equipos_guardados == 0:
            print("❌ No pudimos extraer los equipos. La estructura de la tabla no coincide.")
            return
            
        print(f"\n💾 4. ¡Éxito! Guardando {equipos_guardados} equipos en argentina.json...")
        
        data = {
            "posiciones": posiciones,
            "estadisticas_avanzadas": estadisticas_avanzadas,
            "fixture": proximos_partidos 
        }
        
        os.makedirs('data_json', exist_ok=True)
        with open('data_json/argentina.json', 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=4, ensure_ascii=False)
            
        print("✅ ¡Operación completada al 100%! Posiciones, Racha, HT y Fixture guardados.")
        
    except Exception as e:
        print(f"❌ Error crítico: {e}")

if __name__ == "__main__":
    actualizar_primera_division()