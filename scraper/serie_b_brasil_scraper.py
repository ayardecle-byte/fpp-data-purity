import requests
from bs4 import BeautifulSoup
import json
import os
import re
import time
import unicodedata

# 👇 1. NUESTRO DICCIONARIO MAESTRO 👇
diccionario_serie_b = {
    "criciuma": "u1639-criciuma",
    "vila nova": "u1631-vila-nova",
    "juventude": "u2661-juventude",
    "operario pr": "u2663-operario-pr",
    "fortaleza": "u2637-fortaleza",
    "novorizontino": "u5491-novorizontino",
    "goias": "u2647-goias",
    "sao bernardo": "u8522-sao-bernardo",
    "sport recife": "u2641-sport-recife",
    "atletico go": "u2656-atletico-go",
    "cuiaba": "u2657-cuiaba",
    "athletic club": "u8512-athletic-club",
    "crb": "u2662-crb",
    "nautico": "u2668-nautico",
    "botafogo sp": "u2666-botafogo-sp",
    "londrina": "u1638-londrina",
    "avai": "u2667-avai",
    "ceara": "u2642-ceara",
    "ponte preta": "u2673-ponte-preta",
    "america mg": "u2674-america-mg"
}

def normalizar_nombre(nombre):
    nombre = nombre.lower().strip()
    nombre = ''.join(c for c in unicodedata.normalize('NFD', nombre) if unicodedata.category(c) != 'Mn')
    return nombre

# 👇 2. EL MOTOR MATEMÁTICO HT, HISTORIAL Y RACHA 👇
def extraer_datos_avanzados(codigo_equipo, nombre_equipo):
    url = f"https://www.soccerstats.com/teamstats.asp?league=brazil2&stats={codigo_equipo}"
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36'}
    
    estadisticas = {
        "ht_stats": {
            "partidos_jugados": 0, "over_05_ht": 0, "over_15_ht": 0,
            "ganando_ht": 0, "empatando_ht": 0, "perdiendo_ht": 0,
            "goles_a_favor_ht": 0, "goles_en_contra_ht": 0
        },
        "historial": []
    }
    
    try:
        time.sleep(1) # Pausa vital anti-bloqueo
        res = requests.get(url, headers=headers, timeout=15)
        
        if res.status_code == 200:
            soup = BeautifulSoup(res.text, 'html.parser')
            tablas_html = soup.find_all('table')
            
            for tabla in tablas_html:
                texto_tabla = tabla.text.lower()
                if '2.5+' in texto_tabla and 'fts' in texto_tabla and 'ht' in texto_tabla:
                    filas = tabla.find_all('tr')
                    
                    for tr in filas:
                        tds = tr.find_all('td')
                        if len(tds) >= 8:
                            strings_score = list(tds[2].stripped_strings)
                            if not strings_score or ':' not in strings_score[0]: continue
                            
                            score_final = strings_score[0]
                            fecha = " ".join(tds[0].stripped_strings)
                            local = " ".join(tds[1].stripped_strings)
                            visita = " ".join(tds[3].stripped_strings)
                            strings_ht = list(tds[7].stripped_strings)
                            ht_score = strings_ht[0] if strings_ht else "-"
                            
                            # CÁLCULOS HT
                            if '-' in ht_score and ht_score.replace('-', '').isdigit():
                                gl_ht, gv_ht = map(int, ht_score.split('-'))
                                somos_local = normalizar_nombre(nombre_equipo) in normalizar_nombre(local)
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
                            
                            # SEMÁFORO VISUAL (Historial)
                            try:
                                g_l, g_v = map(int, score_final.split(':'))
                                somos_local = normalizar_nombre(nombre_equipo) in normalizar_nombre(local)
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
                    break 
                    
            # Promedios HT
            stats = estadisticas["ht_stats"]
            pj = stats["partidos_jugados"]
            if pj > 0:
                stats["%_over_0.5_ht"] = round((stats["over_05_ht"] / pj) * 100, 1)
                stats["%_over_1.5_ht"] = round((stats["over_15_ht"] / pj) * 100, 1)
                stats["%_ganando_ht"] = round((stats["ganando_ht"] / pj) * 100, 1)
                stats["promedio_gf_ht"] = round(stats["goles_a_favor_ht"] / pj, 2)
                stats["promedio_gc_ht"] = round(stats["goles_en_contra_ht"] / pj, 2)
            
            # 👇 CÁLCULO DE LA RACHA (ÚLTIMOS 5 PARTIDOS) 👇
            if len(estadisticas["historial"]) > 0:
                ultimos_5 = estadisticas["historial"][-5:]
                racha_str = "".join([p["Status"] for p in ultimos_5])
                estadisticas["racha_ultimos_5"] = racha_str
            else:
                estadisticas["racha_ultimos_5"] = "Sin datos"
                
    except Exception as e:
        print(f"⚠️ Error extrayendo HT e Historial de {nombre_equipo}: {e}")
        
    return estadisticas

# 👇 3. EL ORQUESTADOR DEFINITIVO (POSICIONES + HT + FIXTURE) 👇
def actualizar_serie_b():
    url = "https://www.soccerstats.com/latest.asp?league=brazil2" 
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36'}
    
    print(f"⏳ 1. Conectando a SoccerStats (Tabla y Fixture de Serie B)...")
    try:
        response = requests.get(url, headers=headers, timeout=15)
        soup = BeautifulSoup(response.text, 'html.parser')
        tablas = soup.find_all('table')
        
        posiciones = [["", "Team", "GP", "W", "D", "L", "GF", "GA", "GD", "Pts"]]
        estadisticas_avanzadas = {}
        equipos_guardados = 0
        
        # --- A. EXTRACCIÓN DE LA TABLA DE POSICIONES Y HT ---
        print(f"📊 2. Tabla encontrada. Extrayendo datos y calculando métricas...")
        for tabla in tablas:
            if 'League Table' in tabla.text or ('GP' in tabla.text and 'Pts' in tabla.text):
                filas = tabla.find_all('tr')
                for row in filas:
                    tds = row.find_all('td')
                    if len(tds) >= 10:
                        textos = [t.get_text(strip=True) for t in tds]
                        pos = textos[0]
                        
                        if pos.isdigit():
                            equipo = textos[1]
                            gp, w, d, l, gf, ga, gd, pts = textos[2:10]
                            gd = gd.replace('+', '')
                            
                            equipos_guardados += 1
                            posiciones.append([pos, equipo, gp, w, d, l, gf, ga, gd, pts])
                            
                            nombre_normalizado = normalizar_nombre(equipo)
                            if nombre_normalizado in diccionario_serie_b:
                                codigo = diccionario_serie_b[nombre_normalizado]
                                print(f"   🔍 [{pos}/20] Analizando Racha y HT de: {equipo}...")
                                datos_extra = extraer_datos_avanzados(codigo, equipo)
                                estadisticas_avanzadas[equipo] = datos_extra
                            else:
                                print(f"   ⚠️ Advertencia: '{equipo}' no está en el diccionario.")
                if equipos_guardados > 0:
                    break
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

        url_results = "https://www.soccerstats.com/results.asp?league=brazil2"
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
            print("❌ No pudimos extraer los equipos.")
            return
            
        print(f"\n💾 4. ¡Éxito! Guardando datos generales en serie_b_brasil.json...")
        
        data = {
            "posiciones": posiciones,
            "estadisticas_avanzadas": estadisticas_avanzadas,
            "fixture": proximos_partidos 
        }
        
        os.makedirs('data_json', exist_ok=True)
        with open('data_json/serie_b_brasil.json', 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=4, ensure_ascii=False)
            
        print("✅ ¡Operación completada al 100%! Posiciones, Racha, Historial, HT y Fixture guardados.")
        
    except Exception as e:
        print(f"❌ Error crítico: {e}")

if __name__ == "__main__":
    actualizar_serie_b()