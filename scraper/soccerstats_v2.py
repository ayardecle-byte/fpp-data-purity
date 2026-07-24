import requests
from bs4 import BeautifulSoup
import json
import os
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger(__name__)

def get_page(url):
    try:
        response = requests.get(url, timeout=10)
        return BeautifulSoup(response.content, 'html.parser') if response.status_code == 200 else None
    except: return None

def scrape_soccerstats(league_code, is_cup=False):
    """Detecta si es liga o copa y extrae datos de forma segura"""
    base_url = "https://www.soccerstats.com/"
    
    # URLs dinámicas
    # Para ligas: usa table.asp
    # Para copas: usa leagueview.asp
    if is_cup:
        url_pos = f"{base_url}leagueview.asp?league={league_code}"
    else:
        url_pos = f"{base_url}latest.asp?league={league_code}"
        
    soup = get_page(url_pos)
    if not soup: return None
    
    data = {"posiciones": [], "goles": [], "corners": []}
    
    # --- Extracción inteligente ---
    # Buscamos todas las tablas y filtramos las que tengan datos relevantes
    for table in soup.find_all('table'):
        rows = table.find_all('tr')
        if len(rows) < 5: continue
        
        # Lógica para detectar si es una tabla de posiciones
        if any("points" in r.text.lower() or "pts" in r.text.lower() for r in rows[:2]):
            for row in rows[1:]:
                cells = row.find_all(['td', 'th'])
                if len(cells) >= 6:
                    data["posiciones"].append([c.get_text().strip() for c in cells])
                    
    logger.info(f"✅ Extracción completada para {league_code}")
    return data

def guardar_json(nombre, data):
    os.makedirs('data_json', exist_ok=True)
    with open(f'data_json/{nombre}.json', 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=4)