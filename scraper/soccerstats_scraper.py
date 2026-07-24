import requests
from bs4 import BeautifulSoup
import json
import os
import logging
from datetime import datetime

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger(__name__)

def safe_request(url):
    try:
        response = requests.get(url, timeout=10)
        if response.status_code == 200:
            return response
        else:
            logger.warning(f"⚠️ URL no encontrada o inaccesible (Error {response.status_code}): {url}")
            return None
    except Exception as e:
        logger.error(f"❌ Error de conexión en {url}: {e}")
        return None

def scrape_corners_data(url):
    response = safe_request(url)
    if not response: return {}
    corner_data = {}
    soup = BeautifulSoup(response.content, 'html.parser')
    for t in soup.find_all('table'):
        # Solo procesamos si la tabla tiene cabeceras de local/visitante
        if not t.find('th', string=lambda text: text and ("home" in text.lower() or "hogar" in text.lower())): continue
        for row in t.find_all('tr')[2:]:
            cols = row.find_all('td')
            if len(cols) < 4: continue
            equipo = cols[0].text.strip()
            if "average" in equipo.lower(): continue
            if equipo not in corner_data: corner_data[equipo] = {"local": {}, "visitante": {}}
            # Lógica simplificada para corners
            corner_data[equipo]["local"] = {"favor": cols[2].text.strip(), "contra": cols[3].text.strip()}
    return corner_data

def scrape_goals_data(url):
    response = safe_request(url)
    if not response: return {}
    goals_data = {}
    soup = BeautifulSoup(response.text, 'html.parser')
    table = soup.find('table', {'id': 'btable'})
    if not table: return {}
    for row in table.find_all('tr')[1:]:
        cols = row.find_all('td')
        if len(cols) < 10: continue
        team = cols[0].get_text(strip=True)
        goals_data[team] = {'over_2_5': cols[5].get_text(strip=True), 'bts': cols[9].get_text(strip=True)}
    return goals_data

def scrape_positions_data(url):
    response = safe_request(url)
    if not response: return {}
    positions_data = {}
    soup = BeautifulSoup(response.content, 'html.parser')
    for table in soup.find_all('table'):
        rows = table.find_all('tr')
        if len(rows) < 5: continue
        pos = 1
        for row in rows[1:]:
            cells = row.find_all(['td', 'th'])
            if len(cells) < 5: continue
            texts = [c.get_text().strip() for c in cells]
            # Validación simple para encontrar filas de equipos
            for i, text in enumerate(texts):
                if text and len(text) > 2 and any(c.isalpha() for c in text) and text.upper() not in ['LEAGUES', 'HOME', 'AWAY']:
                    positions_data[text] = {"posicion": pos}
                    pos += 1
                    break
        if len(positions_data) > 5: return positions_data
    return positions_data

def run_scraper(league_name, league_code):
    logger.info(f"🔍 Escaneando liga: {league_name}")
    data = {
        "corners": scrape_corners_data(f"https://www.soccerstats.com/table.asp?league={league_code}&tid=cr"),
        "goals": scrape_goals_data(f"https://www.soccerstats.com/table.asp?league={league_code}&tid=c"),
        "positions": scrape_positions_data(f"https://www.soccerstats.com/latest.asp?league={league_code}")
    }
    
    os.makedirs('data_json', exist_ok=True)
    with open(f'data_json/{league_name}.json', 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=4)
    logger.info(f"✅ Éxito: {league_name}")