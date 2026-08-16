# FPP — Football Predictor Pro

Sistema de análisis estadístico para partidos de fútbol. Predicciones
calibradas, gestión de bankroll y seguimiento de resultados.

Hecho con Python, Streamlit y SQLite. Datos de SoccerStats.

---

## Qué hace

- **Cartelera**: análisis de partido con probabilidades calibradas,
  tabla de posiciones y fixture
- **Picks del Día**: escanea los próximos partidos y filtra por umbral
- **Calendario Global**: partidos de hoy y mañana de 28 ligas
- **Billetera**: registro de apuestas con Kelly fraccionado
- **Calibración en Vivo**: mide si el modelo acierta lo que promete

## Cómo está construido

**Motor**: Dixon-Coles con recalibración isotónica. Estima fuerza de
ataque y defensa de cada equipo resolviendo la liga completa, con
decaimiento temporal y corrección para resultados de pocos goles.

**Base**: 44.674 partidos de 42 ligas, con datos de medio tiempo.

---

## Hallazgos de la validación

Todo medido con backtest cronológico, sin fuga de información futura y
con calibración entrenada en una mitad de los datos y evaluada en la otra.

### Mercados

| Mercado | Skill | Veredicto |
|---|---|---|
| 1X2 y doble oportunidad | +0.011 a +0.016 | Ventaja sobre la referencia trivial |
| Over/Under y BTTS | ≈ 0 | Sin ventaja (medido 4 veces) |
| Medio tiempo | +0.003 a +0.004 | Marginal |

### Limitación importante

En un backtest contra **cuotas de cierre reales** de 14.656 partidos, el
sistema dio un yield de **−12%**. Es decir: el modelo predice mejor que
una referencia trivial, pero **no mejor que el mercado de apuestas**.

Son dos cosas distintas y conviene no confundirlas. Este proyecto sirve
para analizar y ordenar partidos, no como generador automático de
apuestas rentables.

---

## Instalación

```bash
git clone https://github.com/TU_USUARIO/fpp-data-purity.git
cd fpp-data-purity
python -m venv venv
venv\\Scripts\\activate        # Windows
pip install -r requirements.txt
```

## Uso

```bash
python actualizar_todas_ligas.py    # descargar datos (15-20 min)
python entrenar_motor.py            # entrenar el modelo (30 min)
streamlit run dashboard.py          # abrir la app
```

La base de datos y los modelos no están en el repositorio: se generan
con los scripts de arriba.

---

## Estructura

```
dashboard.py                  App principal
motor_v2.py                   Predicción, calidad de liga, EV, Kelly
picks_dia.py                  Pestaña de picks
registro_predicciones.py      Registro y calibración en vivo
entrenar_motor.py             Entrena el modelo
modelo_dixon_coles.py         Implementación de Dixon-Coles
backtesting_engine.py         Motor de backtest
actualizar_todas_ligas.py     Scraper de datos
scraper_historial.py          Descarga de temporadas anteriores
validar_ligas.py              Validación de ligas y mercados
data_json/                    Datos descargados por liga
```

---

## Notas técnicas

- SoccerStats devuelve 403 si se manda un User-Agent falso; funciona con
  `requests.get()` sin headers
- El sitio usa dos formatos de marcador (`1:0` y `1-0`) según la página
- Los horarios vienen en hora de Londres y hay que ajustarlos según la
  época del año (verano UTC+1, invierno UTC)
- `results.asp` muestra un mes por vez; el parámetro `pmtype=month1..12`
  permite recorrer la temporada completa

---

## Aviso

Proyecto personal de análisis estadístico. Las apuestas deportivas
implican riesgo de pérdida económica. Los resultados del backtest
muestran que este sistema **no supera al mercado de apuestas**. Usalo
como herramienta de análisis, no como consejo financiero.
