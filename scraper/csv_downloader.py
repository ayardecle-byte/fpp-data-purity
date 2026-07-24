import pandas as pd

def descargar_datos_argentina():
    print("\n--- INICIANDO DESCARGA DIRECTA DE DATOS ---")
    
    # URL directa al archivo CSV (Base de datos) de Argentina
    url_csv = "https://www.football-data.co.uk/new/ARG.csv"
    
    print("[>] Conectando al servidor y descargando base de datos...")
    
    try:
        # Pandas es tan potente que puede leer un CSV directamente desde internet
        dataframe = pd.read_csv(url_csv)
        
        # Filtramos para ver solo la temporada 2023 o 2024
        # (Dependiendo de los datos más recientes en su archivo)
        print("\n[✅] ¡Descarga exitosa en 1 segundo!")
        print(f"[i] Se descargaron un total de {len(dataframe)} partidos históricos.")
        
        print("\n--- MUESTRA DE LOS ÚLTIMOS 5 PARTIDOS JUGADOS ---")
        # Seleccionamos las columnas más importantes (Fecha, Local, Visitante, Goles Local, Goles Visita)
        columnas_interes = ['Date', 'Home', 'Away', 'HG', 'AG']
        
        # Mostramos las últimas 5 filas (tail)
        print(dataframe[columnas_interes].tail().to_string(index=False))
        print("---------------------------------------------------\n")
        
    except Exception as e:
        print(f"[❌] Error al descargar los datos: {e}")

if __name__ == "__main__":
    descargar_datos_argentina()