from playwright.sync_api import sync_playwright
from bs4 import BeautifulSoup
import time

class BaseScraper:
    def __init__(self, nombre_fuente):
        self.nombre_fuente = nombre_fuente

    def obtener_codigo_pagina(self, url):
        print(f"[{self.nombre_fuente}] Abriendo navegador VISIBLE para conectar a: {url}")
        
        try:
            with sync_playwright() as p:
                # CAMBIO 1: headless=False hace que el navegador sea visible.
                # slow_mo=50 hace que el programa actúe un poco más lento.
                browser = p.chromium.launch(headless=False, slow_mo=50)
                
                # CAMBIO 2: Simulamos que usamos una pantalla grande (1920x1080)
                context = browser.new_context(
                    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                    viewport={'width': 1920, 'height': 1080}
                )
                page = context.new_page()
                
                # Vamos a la página
                page.goto(url, timeout=60000)
                
                # CAMBIO 3: Pausa de 6 segundos.
                # Esto le da tiempo a la página para pasar la prueba de "Verificando si eres humano"
                print(f"[{self.nombre_fuente}] Esperando 6 segundos a que pase la seguridad...")
                page.wait_for_timeout(15000)
                
                # Extraemos el código
                html_completo = page.content()
                
                # Cerramos todo
                browser.close()
                
                print(f"[{self.nombre_fuente}] ¡Seguridad evadida! Código descargado exitosamente.")
                return BeautifulSoup(html_completo, 'html.parser')
                
        except Exception as error:
            print(f"[{self.nombre_fuente}] Hubo un problema con el navegador: {error}")
            return None