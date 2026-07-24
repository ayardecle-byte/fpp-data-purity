import requests
from bs4 import BeautifulSoup
import json
import os
import re
import time
import unicodedata
from datetime import datetime, timedelta

# 👇 DICCIONARIOS LIMPIOS 👇
dict_usa = {
    "atlanta utd": "u5283-atlanta-utd", "austin": "u5285-austin", "cf montreal": "u5280-cf-montreal",
    "charlotte": "u8735-charlotte", "chicago fire": "u5294-chicago-fire", "cincinnati": "u5293-cincinnati",
    "colorado rapids": "u5289-colorado-rapids", "columbus crew": "u5298-columbus-crew", "dallas": "u5288-dallas",
    "dc united": "u5290-dc-united", "houston dynamo": "u5276-houston-dynamo", "inter miami": "u5296-inter-miami",
    "la galaxy": "u5297-la-galaxy", "los angeles fc": "u5284-los-angeles-fc", "minnesota utd": "u5279-minnesota-utd",
    "nashville sc": "u5292-nashville-sc", "new england": "u5295-new-england", "new york city": "u5291-new-york-city",
    "new york rb": "u5286-new-york-rb", "orlando city": "u5282-orlando-city", "philadelphia": "u5299-philadelphia",
    "portland": "u5301-portland", "real salt lake": "u5302-real-salt-lake", "san diego": "u11710-san-diego",
    "seattle": "u5278-seattle", "sj earthquakes": "u5277-sj-earthquakes", "sporting kc": "u5287-sporting-kc",
    "st. louis city": "u9962-st.-louis-city", "toronto": "u5281-toronto", "vancouver": "u5300-vancouver"
}

dict_mexico = {
    "a. san luis": "u6005-a.-san-luis", "atlante": "u11542-atlante", "atlas": "u6007-atlas",
    "cf america": "u5997-cf-america", "club leon": "u6003-club-leon", "cruz azul": "u6012-cruz-azul",
    "guadalajara": "u6004-guadalajara", "juarez": "u6000-juarez", "monterrey": "u6008-monterrey",
    "necaxa": "u5998-necaxa", "pachuca": "u6002-pachuca", "puebla": "u6009-puebla",
    "pumas unam": "u6006-pumas-unam", "queretaro": "u5996-queretaro", "santos laguna": "u5999-santos-laguna",
    "tigres": "u6011-tigres", "tijuana": "u6010-tijuana", "toluca": "u6001-toluca"
}

dict_estonia = {
    "flora": "u4231-flora", "harju jk": "u8744-harju-jk", "kuressaare": "u4238-kuressaare",
    "levadia": "u4237-levadia", "narva trans": "u4236-narva-trans", "nomme kalju": "u4233-nomme-kalju",
    "nomme utd": "u4242-nomme-utd", "paide": "u4240-paide", "tammeka": "u4235-tammeka", "vaprus": "u4232-vaprus"
}

dict_iceland2 = {
    "aeggir": "u8901-aeggir", "afturelding": "u5394-afturelding", "fylkir": "u5380-fylkir",
    "grindavik": "u5392-grindavik", "grotta": "u5390-grotta", "hk kopavogur": "u5382-hk-kopavogur",
    "ir reykjavik": "u5404-ir-reykjavik", "leiknir r.": "u5377-leiknir-r.", "njardvik": "u5398-njardvik",
    "throttur r.": "u5386-throttur-r.", "vestri": "u5397-vestri", "volsungur": "u5407-volsungur"
}

# 👇 CONFIGURACIÓN DE ESCANEO 👇
ligas_config = [
    {"id": "usa", "nombre": "Estados Unidos - MLS", "archivo": "mls.json", "dic": dict_usa},
    {"id": "mexico", "nombre": "México - Liga MX", "archivo": "mexico.json", "dic": dict_mexico},
    {"id": "estonia", "nombre": "Estonia - Meistriliiga", "archivo": "estonia.json", "dic": dict_estonia},
    {"id": "iceland2", "nombre": "Islandia - 2da División", "archivo": "iceland2.json", "dic": dict_iceland2}
]

def normalizar_nombre(nombre):
    nombre = nombre.lower().strip()
    return ''.join(c for c in unicodedata.normalize('NFD', nombre) if unicodedata.category(c) != 'Mn')

def ajustar_hora_bolivia(fecha_str, hora_str):
    try:
        meses = {'Jan': 1, 'Feb': 2, 'Mar': 3, 'Apr': 4, 'May': 5, 'Jun': 6, 'Jul': 7, 'Aug': 8, 'Sep': 9, 'Oct': 10, 'Nov': 11, 'Dec': 12}
        dias_inv = {0: 'Mon', 1: 'Tue', 2: 'Wed', 3: 'Thu', 4: 'Fri', 5: 'Sat', 6: 'Sun'}
        partes = fecha_str.split()
        if len(partes) != 3:
            return fecha_str, hora_str
        dia_num = int(partes[1])
        mes_num = meses.get(partes[2])
        horas, mins = map(int, hora_str.split(':'))
        dt = datetime(datetime.now().year, mes_num, dia_num, horas, mins) - timedelta(hours=4)
        nuevo_dia = f"{dias_inv[dt.weekday()]} {dt.day} {list(meses.keys())[list(meses.values()).index(dt.month)]}"
        nueva_hora = dt.strftime("%H:%M")
        return nuevo_dia, nueva_hora
    except Exception:
        return fecha_str, hora_str

def extraer_datos_avanzados(codigo_equipo, nombre_equipo, league_id):
    url = f"https://www.soccerstats.com/teamstats.asp?league={league_id}&stats={codigo_equipo}"
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
                    # Usamos un espacio separador para aislar el marcador del texto oculto
                    txt = td.get_text(separator=" ", strip=True)
                    # Buscamos que el bloque inicie estrictamente con el formato "2:0" o "2 - 0"
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
                        
                    # 🎯 Detectar la Negrita (<b>) para saber exactamente si somos Local o Visita
                    somos_local = td_local.find('b') is not None
                    somos_visita = td_visita.find('b') is not None
                    
                    # Plan de contingencia si la página olvida poner la negrita
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
                    
                    # ⏱️ Extraer HT Score (Lee de las columnas de la derecha buscando el formato "0-0")
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

def actualizar_multi_ligas():
    headers = {'User-Agent': 'Mozilla/5.0'}
    
    for liga in ligas_config:
        print(f"\n=======================================================")
        print(f"🚀 INICIANDO DESCARGA: {liga['nombre'].upper()}")
        
        try:
            res_tabla = requests.get(f"https://www.soccerstats.com/latest.asp?league={liga['id']}", headers=headers, timeout=15)
            soup = BeautifulSoup(res_tabla.text, 'html.parser')
            posiciones = [["", "Team", "GP", "W", "D", "L", "GF", "GA", "GD", "Pts"]]
            estadisticas_avanzadas = {}
            equipos_guardados = 0
            
            print("📊 1. Extrayendo tabla y analizando equipos (HT/Rachas)...")
            for tabla in soup.find_all('table'):
                for row in tabla.find_all('tr'):
                    tds = row.find_all('td')
                    if len(tds) >= 10:
                        textos = [t.get_text(strip=True) for t in tds]
                        pos = textos[0].replace('.', '').strip()
                        
                        # 👇 CORRECCIÓN CLAVE: Puntos siempre están en la columna index 9 👇
                        pts_candidate = textos[9].replace('*', '').strip() if len(textos) > 9 else ""
                        
                        if pos.isdigit() and pts_candidate.isdigit() and int(pos) <= 50:
                            equipo = textos[1]
                            if any(equipo == fila[1] for fila in posiciones):
                                continue
                            
                            w, d, l_col, gf, ga, gd = textos[3], textos[4], textos[5], textos[6], textos[7], textos[8]
                            pts = pts_candidate
                                
                            equipos_guardados += 1
                            posiciones.append([pos, equipo, textos[2], w, d, l_col, gf, ga, gd.replace('+', ''), pts])
                            
                            nn = normalizar_nombre(equipo)
                            if nn in liga['dic']:
                                print(f"   🔍 Analizando: {equipo}...")
                                estadisticas_avanzadas[equipo] = extraer_datos_avanzados(liga['dic'][nn], equipo, liga['id'])

            print("📅 2. Extrayendo Fixture Limpio (con reloj de Bolivia)...")
            res_fix = requests.get(f"https://www.soccerstats.com/results.asp?league={liga['id']}", headers=headers, timeout=15)
            soup_fix = BeautifulSoup(res_fix.text, 'html.parser')
            proximos_partidos = []
            partidos_vistos = set()
            
            for tr in soup_fix.find_all('tr'):
                tds = tr.find_all('td')
                if len(tds) >= 4:
                    textos = [t.get_text(strip=True) for t in tds]
                    fecha_texto = textos[0]
                    if any(dia in fecha_texto for dia in ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']):
                        local = textos[1]
                        hora_o_resultado = textos[2]
                        visita = textos[3]
                        if re.match(r'^\d{1,2}:\d{2}$', hora_o_resultado) or hora_o_resultado.lower() == 'pp.':
                            if local and visita and local.lower() != "matches":
                                if re.match(r'^\d{1,2}:\d{2}$', hora_o_resultado):
                                    fecha_texto, hora_o_resultado = ajustar_hora_bolivia(fecha_texto, hora_o_resultado)
                                p_id = f"{local}-{visita}"
                                if p_id not in partidos_vistos:
                                    partidos_vistos.add(p_id)
                                    proximos_partidos.append({"Fecha": fecha_texto, "Hora": hora_o_resultado, "Local": local, "Visita": visita})
            
            if equipos_guardados > 0:
                os.makedirs('data_json', exist_ok=True)
                with open(f"data_json/{liga['archivo']}", 'w', encoding='utf-8') as f:
                    json.dump({"posiciones": posiciones, "estadisticas_avanzadas": estadisticas_avanzadas, "fixture": proximos_partidos}, f, indent=4, ensure_ascii=False)
                print(f"✅ ¡{liga['nombre']} guardada con éxito en {liga['archivo']} con {equipos_guardados} equipos!")
            else:
                print(f"⚠️ No se detectaron equipos para {liga['nombre']}.")
                
        except Exception as e:
            print(f"❌ Error en {liga['nombre']}: {e}")

if __name__ == "__main__":
    actualizar_multi_ligas()