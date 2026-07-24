import os
import requests
from dotenv import load_dotenv

load_dotenv()

class EscannerCuotasAvanzado:
    def __init__(self):
        self.api_key = os.getenv("ODDS_API_KEY")
        self.base_url = "https://api.the-odds-api.com/v4/sports"

    def mostrar_ligas_activas(self):
        """Pregunta a la API qué ligas de fútbol tienen apuestas abiertas hoy"""
        print("\n🔍 Consultando radares globales de casas de apuestas...")
        
        if not self.api_key or self.api_key == "tu_nueva_llave_aqui":
            print("[❌] Error: Llave API no configurada.")
            return

        parametros = {"apiKey": self.api_key}
        
        try:
            respuesta = requests.get(self.base_url, params=parametros)
            datos = respuesta.json()
            
            if respuesta.status_code != 200:
                print(f"[❌] Error de la API: {datos.get('message', 'Desconocido')}")
                return

            # Filtramos para que solo nos muestre fútbol (Soccer)
            ligas_futbol = [d for d in datos if d.get('group') == 'Soccer']
            
            print(f"[✅] Se encontraron {len(ligas_futbol)} ligas de fútbol con cuotas activas hoy.\n")
            print("--- TOP 15 LIGAS ACTIVAS (COPIA EL CÓDIGO) ---")
            for liga in ligas_futbol[:15]:
                print(f" 📌 {liga['title']}")
                print(f"    Código -> {liga['key']}")
            print("-" * 50)
            print("[i] Usa el 'Código' para escanear los partidos en la Opción 2.")
            
        except Exception as e:
            print(f"[❌] Error de conexión: {e}")

    def buscar_cuotas_completas(self, codigo_liga):
        """Busca cuotas 1X2 y Goles para la liga que le indiquemos"""
        print(f"\n📡 ESCANEANDO MERCADOS PARA: {codigo_liga} ...")
        
        url = f"{self.base_url}/{codigo_liga}/odds"
        parametros = {
            "apiKey": self.api_key,
            "regions": "eu,us",
            "markets": "h2h,totals",
            "oddsFormat": "decimal"
        }

        try:
            respuesta = requests.get(url, params=parametros)
            datos = respuesta.json()

            if respuesta.status_code != 200:
                print(f"[❌] Error de la API: {datos.get('message', 'Desconocido')}")
                return

            if not datos:
                print("[!] No hay partidos con cuotas abiertas para esta liga.")
                return

            print(f"[>] Se encontraron {len(datos)} partidos disponibles.\n")

            for partido in datos[:3]: 
                local = partido['home_team']
                visita = partido['away_team']
                
                print(f"🏟️  PARTIDO: {local} vs {visita}")
                print("=" * 60)
                
                bookmakers = partido.get('bookmakers', [])
                if not bookmakers:
                    print("   [!] No hay casas de apuestas ofreciendo cuotas activas para este encuentro.")
                    print("-" * 60)
                    continue
                
                # Tomamos la primera casa disponible (suele ser la más rápida en fijar cuotas)
                casa = bookmakers[0]
                print(f"🏦 Proveedor de Datos: {casa['title']}")
                
                for mercado in casa.get('markets', []):
                    tipo_mercado = mercado['key']
                    cuotas = mercado['outcomes']
                    
                    if tipo_mercado == "h2h":
                        print("\n   📈 Mercado de Ganador (1X2):")
                        for cuota in cuotas:
                            print(f"      ➡️ {cuota['name']}: {cuota['price']}")
                            
                    elif tipo_mercado == "totals":
                        print("\n   🥅 Mercado de Goles Totales (Over/Under):")
                        for cuota in cuotas:
                            tipo_apuesta = "Más de" if cuota['name'] == "Over" else "Menos de"
                            print(f"      ➡️ {tipo_apuesta} {cuota.get('point')} Goles: {cuota['price']}")
                
                print("\n" + "-" * 60 + "\n")

        except Exception as e:
            print(f"[❌] Error de conexión: {e}")

if __name__ == "__main__":
    escaner = EscannerCuotasAvanzado()
    
    while True:
        print("\n" + "="*50)
        print("💰 RADAR DE CUOTAS DE APUESTAS GLOBALES")
        print("="*50)
        print("1. Ver qué ligas tienen partidos activos hoy")
        print("2. Escanear cuotas de una liga específica")
        print("3. Salir")
        
        opcion = input("\nElige una opción (1/2/3): ")
        
        if opcion == '1':
            escaner.mostrar_ligas_activas()
        elif opcion == '2':
            codigo = input("\nEscribe el 'Código' de la liga (Ej: soccer_brazil_campeonato): ")
            escaner.buscar_cuotas_completas(codigo)
        elif opcion == '3':
            break
        else:
            print("\n[❌] Opción no válida.")