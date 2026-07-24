import requests
from bs4 import BeautifulSoup
import json
import os

def actualizar_bolivia():
    url = "https://promediosinfo.com/liga/bolivia.html"
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    }
    
    print("⏳ 1. Conectando a la página de Promedios...")
    try:
        response = requests.get(url, headers=headers, timeout=15)
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # 1. Buscamos TODAS las tablas de la página
        tablas = soup.find_all('table')
        tabla_posiciones = None
        
        # 2. Encontramos la tabla correcta (la que tiene "Equipo" y "Pts")
        for t in tablas:
            if "Equipo" in t.text and "Pts" in t.text:
                tabla_posiciones = t
                break
                
        if not tabla_posiciones:
            print("❌ No se encontró la tabla principal.")
            return
            
        posiciones = [["", "Team", "GP", "W", "D", "L", "GF", "GA", "GD", "Pts"]]
        equipos_guardados = 0
        
        filas = tabla_posiciones.find_all('tr')
        print(f"📊 2. Encontramos la tabla correcta con {len(filas)} filas. Procesando...")
        
        for row in filas:
            cols = row.find_all(['td', 'th'])
            
            # Extraemos los textos y quitamos los que están en blanco (como los logos)
            textos = [c.text.strip() for c in cols]
            textos = [t for t in textos if t != ''] 
            
            # Sabemos que la tabla real tiene 10 o más datos útiles
            if len(textos) >= 10:
                pts_text = textos[2]
                pj_text = textos[3]
                
                # Validación definitiva: Si la columna Pts y Pj son números, es un equipo
                if pts_text.isdigit() and pj_text.isdigit():
                    equipo = textos[1]
                    pts = textos[2]
                    pj = textos[3]
                    g = textos[4]
                    e = textos[5]
                    p = textos[6]
                    gf = textos[7]
                    gc = textos[8]
                    dg = textos[9]
                    
                    equipos_guardados += 1
                    posiciones.append([str(equipos_guardados), equipo, pj, g, e, p, gf, gc, dg, pts])
        
        if equipos_guardados == 0:
            print("❌ No pudimos extraer los equipos.")
            return
            
        print(f"💾 3. ¡Éxito! Guardando {equipos_guardados} equipos en bolivia.json...")
        data = {"posiciones": posiciones}
        
        os.makedirs('data_json', exist_ok=True)
        with open('data_json/bolivia.json', 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=4)
            
        print("✅ ¡Operación completada al 100%! Ya puedes recargar tu panel.")
        
    except Exception as e:
        print(f"❌ Error crítico: {e}")

if __name__ == "__main__":
    actualizar_bolivia()