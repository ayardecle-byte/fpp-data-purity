import os
import requests
from dotenv import load_dotenv

load_dotenv()

# Mapeo de nombres de ligas de tu aplicación hacia los códigos internos de The Odds API
LIGAS_MAPPING = {
    "Inglaterra - Premier League": "soccer_epl",
    "España - La Liga": "soccer_spain_la_liga",
    "Italia - Serie A/B": "soccer_italy_serie_a",
    "Francia - Ligue 1": "soccer_france_ligue_one"
}

def buscar_cuotas_en_vivo(liga_nombre, local_nombre, visita_nombre):
    """Busca las cuotas actuales en Bet365 o casas europeas para un partido específico"""
    api_key = os.getenv("ODDS_API_KEY")
    
    # Si la liga no está soportada en este API gratuito o no hay llave, devolvemos valores vacíos
    if liga_nombre not in LIGAS_MAPPING or not api_key:
        return None, None, None

    liga_codigo = LIGAS_MAPPING[liga_nombre]
    url = f"https://api.the-odds-api.com/v4/sports/{liga_codigo}/odds/"
    
    params = {
        'apiKey': api_key,
        'regions': 'eu', # Mercados europeos (Bet365, William Hill, etc.)
        'markets': 'h2h', # Ganador del partido (1X2)
        'oddsFormat': 'decimal'
    }

    try:
        res = requests.get(url, params=params)
        if res.status_code != 200:
            return None, None, None
            
        partidos = res.json()
        
        # Buscamos nuestro partido comparando los nombres de los equipos
        for p in partidos:
            home_team = p['home_team']
            away_team = p['away_team']
            
            # Hacemos una comparación flexible (por si un API dice 'Man City' y el otro 'Manchester City')
            if (local_nombre.lower() in home_team.lower() or home_team.lower() in local_nombre.lower()) and \
               (visita_nombre.lower() in away_team.lower() or away_team.lower() in visita_nombre.lower()):
                
                # Buscamos los datos de Bet365 o la primera casa disponible
                for bookmaker in p['bookmakers']:
                    if bookmaker['key'] in ['bet365', 'williamhill', 'marathonbet']:
                        markets = bookmaker['markets'][0]['outcomes']
                        
                        cuota_L, cuota_E, cuota_V = 1.0, 1.0, 1.0
                        for outcome in markets:
                            if outcome['name'] == home_team:
                                cuota_L = float(outcome['price'])
                            elif outcome['name'] == away_team:
                                cuota_V = float(outcome['price'])
                            else:
                                cuota_E = float(outcome['price'])
                                
                        return cuota_L, cuota_E, cuota_V
                        
        return None, None, None
    except:
        return None, None, None