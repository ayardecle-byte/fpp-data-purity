import requests
from bs4 import BeautifulSoup
import json
import os
import re
import time
import unicodedata

# 👇 1. DICCIONARIO MAESTRO ARGENTINA 👇
diccionario_argentina = {
    "a. atlanta": "u4025-a.-atlanta",
    "a. rafaela": "u4039-a.-rafaela",
    "acassuso": "u3491-acassuso",
    "agropecuario": "u4014-agropecuario",
    "all boys": "u4026-all-boys",
    "almagro": "u4030-almagro",
    "almirante brown": "u3153-almirante-brown",
    "atletico mitre": "u4023-atletico-mitre",
    "ca estudiantes": "u4021-ca-estudiantes",
    "ca guemes": "u4028-ca-guemes",
    "central norte": "u5808-central-norte",
    "chacarita j.": "u4024-chacarita-j.",
    "chaco for ever": "u5810-chaco-for-ever",
    "ciudad bolivar": "u5786-ciudad-bolivar",
    "colegiales": "u3152-colegiales",
    "colon santa fe": "u7398-colon-santa-fe",
    "d. de belgrano": "u4033-d.-de-belgrano",
    "d. madryn": "u5794-d.-madryn",
    "deportivo maipu": "u4012-deportivo-maipu",
    "deportivo moron": "u4027-deportivo-moron",
    "ferro carril": "u4029-ferro-carril",
    "gimnasia jujuy": "u4032-gimnasia-jujuy",
    "gimnasia y tiro": "u5807-gimnasia-y-tiro",
    "godoy cruz": "u7403-godoy-cruz",
    "los andes": "u11305-los-andes",
    "midland": "u8184-midland",
    "nueva chicago": "u4022-nueva-chicago",
    "patronato": "u7384-patronato",
    "quilmes": "u4016-quilmes",
    "racing cordoba": "u5804-racing-cordoba",
    "san miguel": "u3159-san-miguel",
    "san telmo": "u3149-san-telmo",
    "sm san juan": "u4038-sm-san-juan",
    "sm tucuman": "u4019-sm-tucuman",
    "temperley": "u4042-temperley",
    "tristan suarez": "u3154-tristan-suarez"
}

def normalizar_nombre(nombre):
    nombre = nombre.lower().strip()
    nombre = ''.join(c for c in unicodedata.normalize('NFD', nombre) if unicodedata.category(c) != 'Mn')
    return nombre

# 👇 2. EL MOTOR MATEMÁTICO HT, HISTORIAL Y RACHA (VERSIÓN BLINDADA) 👇
def extraer_datos_avanzados(codigo_equipo, nombre_equipo):
    url = f"https://www.soccerstats.com/teamstats.asp?league=argentina3&stats={codigo_equipo}"
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36'}
    
    estadisticas = {
        "ht_stats": {
            "partidos_jugados": 0, "over_05_ht": 0, "over_15_ht": 0,
            "ganando_ht": 0, "empatando_ht": 0, "perdiendo_ht": 0,
            "goles_a_favor_ht": 0, "goles_en_contra_ht": 0
        },
        "historial": []
    }
    
    # Función interna para limpiar nombres de prefijos que confunden al bot
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
            
            # Recorremos TODAS las filas de la página buscando el patrón de un partido
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
                        
                        # Filtro 1: Bloquea basura (fechas o minutos que fingen ser equipos)
                        if not local or not visita or len(local) > 30 or len(visita) > 30:
                            continue
                        if local.isdigit() or visita.isdigit():
                            continue
                            
                        # Filtro 2: ¿Están jugando nuestros equipos realmente aquí?
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
                            continue # Es una tabla genérica, la ignoramos.
                            
                        id_partido = f"{fecha}-{local}-{visita}"
                        if id_partido in partidos_procesados:
                            continue
                        partidos_procesados.add(id_partido)
                        
                        # Buscar Medio Tiempo (HT) oculto en las columnas
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
                                
                        # SEMÁFORO VISUAL
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
                        
                        # CÁLCULOS HT (Si el dato existe en SoccerStats)
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

            # Limpiamos basura final
            estadisticas["historial"] = [p for p in estadisticas["historial"] if p["Status"] != "⚪"]
            
            # Promedios HT
            stats = estadisticas["ht_stats"]
            pj = stats["partidos_jugados"]
            if pj > 0:
                stats["%_over_0.5_ht"] = round((stats["over_05_ht"] / pj) * 100, 1)
                stats["%_over_1.5_ht"] = round((stats["over_15_ht"] / pj) * 100, 1)
                stats["%_ganando_ht"] = round((stats["ganando_ht"] / pj) * 100, 1)
                stats["promedio_gf_ht"] = round(stats["goles_a_favor_ht"] / pj, 2)
                stats["promedio_gc_ht"] = round(stats["goles_en_contra_ht"] / pj, 2)
            
            # RACHA
            if len(estadisticas["historial"]) > 0:
                ultimos_5 = estadisticas["historial"][-5:]
                racha_str = "".join([p["Status"] for p in ultimos_5])
                estadisticas["racha_ultimos_5"] = racha_str
            else:
                estadisticas["racha_ultimos_5"] = "Sin datos"
                
    except Exception as e:
        print(f"⚠️ Error extrayendo HT e Historial de {nombre_equipo}: {e}")
        
    return estadisticas
    url = f"https://www.soccerstats.com/teamstats.asp?league=argentina3&stats={codigo_equipo}"
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
            partidos_procesados = set()
            
            # Recorremos TODAS las filas de la página buscando el patrón de un partido
            for tr in soup.find_all('tr'):
                tds = tr.find_all('td')
                if 4 <= len(tds) <= 12:
                    textos = [td.get_text(separator=" ", strip=True) for td in tds]
                    
                    # Buscamos en qué columna está el resultado (ej: "2 - 1" o "2:1")
                    score_col = -1
                    for i, txt in enumerate(textos):
                        if re.match(r'^\d+\s*[-:]\s*\d+$', txt):
                            score_col = i
                            break
                            
                    # Si encontramos un resultado y tiene equipos a los lados, ¡es un partido!
                    if score_col > 0 and score_col < len(textos) - 1:
                        fecha = textos[0][:12] # Tomar solo el principio por si hay basura
                        local = textos[score_col - 1]
                        visita = textos[score_col + 1]
                        score_final = textos[score_col].replace('-', ':').replace(' ', '')
                        
                        # Filtro de seguridad
                        if not local or not visita or len(local) > 30 or len(visita) > 30:
                            continue
                            
                        id_partido = f"{fecha}-{local}-{visita}"
                        if id_partido in partidos_procesados:
                            continue
                        partidos_procesados.add(id_partido)
                        
                        # Buscar si en alguna columna está el Medio Tiempo (HT) entre paréntesis ej: "(1-0)"
                        ht_score = "-"
                        for txt in textos:
                            match_ht = re.search(r'\((\d+\s*-\s*\d+)\)', txt)
                            if match_ht:
                                ht_score = match_ht.group(1).replace(' ', '')
                                break
                                
                        # SEMÁFORO VISUAL
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
                        
                        # CÁLCULOS HT (Solo si detectó datos HT reales en la página)
                        if ht_score != "-":
                            try:
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
                            except: pass

            # Nos quedamos con los partidos ya jugados (evitando fixtures sin resultado)
            estadisticas["historial"] = [p for p in estadisticas["historial"] if p["Status"] != "⚪"]
            
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
    url = f"https://www.soccerstats.com/teamstats.asp?league=argentina3&stats={codigo_equipo}"
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

# 👇 3. EL ORQUESTADOR DEFINITIVO 👇
def actualizar_primera_nacional():
    url = "https://www.soccerstats.com/latest.asp?league=argentina3" 
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36'}
    
    print(f"⏳ 1. Conectando a SoccerStats (Tabla y Fixture de Argentina Nacional B)...")
    try:
        response = requests.get(url, headers=headers, timeout=15)
        soup = BeautifulSoup(response.text, 'html.parser')
        tablas = soup.find_all('table')
        
        posiciones = [["", "Team", "GP", "W", "D", "L", "GF", "GA", "GD", "Pts"]]
        estadisticas_avanzadas = {}
        equipos_guardados = 0
        
        # --- A. EXTRACCIÓN DE LA TABLA DE POSICIONES Y HT ---
        print(f"📊 2. Buscando tablas (Zonas A y B). Extrayendo datos y métricas...")
        for tabla in tablas:
            filas = tabla.find_all('tr')
            for row in filas:
                tds = row.find_all('td')
                
                # Una fila típica de tabla de posiciones tiene al menos 8 columnas
                if len(tds) >= 8:
                    textos = [t.get_text(strip=True) for t in tds]
                    pos = textos[0].replace('.', '').strip() 
                    pts_str = textos[-1].replace('*', '').strip() 
                    
                    # Si la primera columna es posición (número) y la última es puntos (número)
                    if pos.isdigit() and pts_str.isdigit():
                        equipo = textos[1]
                        
                        # Evitar duplicados (por si SoccerStats renderiza una tabla combinada extra)
                        if any(equipo == fila[1] for fila in posiciones):
                            continue
                            
                        gp = textos[2]
                        pts = textos[-1]
                        
                        # Manejo dinámico de las columnas
                        if len(textos) >= 10:
                            w, d, l, gf, ga, gd = textos[3:9]
                            gd = gd.replace('+', '')
                        else:
                            w, d, l, gf, ga, gd = "0", "0", "0", "0", "0", "0"
                            
                        equipos_guardados += 1
                        posiciones.append([pos, equipo, gp, w, d, l, gf, ga, gd, pts])
                        
                        nombre_normalizado = normalizar_nombre(equipo)
                        if nombre_normalizado in diccionario_argentina:
                            codigo = diccionario_argentina[nombre_normalizado]
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

        url_results = "https://www.soccerstats.com/results.asp?league=argentina3"
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
            
        print(f"\n💾 4. ¡Éxito! Guardando {equipos_guardados} equipos en primera_nacional.json...")
        
        data = {
            "posiciones": posiciones,
            "estadisticas_avanzadas": estadisticas_avanzadas,
            "fixture": proximos_partidos 
        }
        
        os.makedirs('data_json', exist_ok=True)
        with open('data_json/primera_nacional.json', 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=4, ensure_ascii=False)
            
        print("✅ ¡Operación completada al 100%! Posiciones, Racha, Historial, HT y Fixture guardados.")
        
    except Exception as e:
        print(f"❌ Error crítico: {e}")

if __name__ == "__main__":
    actualizar_primera_nacional()