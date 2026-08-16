# PLAN MAESTRO — Football Predictor Pro v2

> **Para Claude Code:** este documento contiene todo el contexto de una auditoría
> completa del proyecto. Leelo entero antes de tocar código. Las tareas están al
> final, ordenadas por prioridad. El usuario **no programa** — explicale en lenguaje
> simple qué hacés y por qué, y no le pidas que edite código a mano.

---

## 1. Contexto del proyecto

App de análisis estadístico de fútbol para apuestas deportivas. Stack: Python +
Streamlit + SQLite, con scraping de fuentes gratuitas (principalmente SoccerStats).

**Estructura actual:**
- `dashboard.py` — app Streamlit monolítica (~2.300 líneas): cartelera, calendario,
  radar de apuestas, billetera
- `database/football_data.db` — SQLite con 24.041 partidos históricos
- `data_json/*.json` — posiciones, fixtures, estadísticas avanzadas y córners por liga
- `scraper/` — ~15 scrapers, uno por liga o grupo de ligas
- `modelos/motor_v2.pkl` — motor nuevo entrenado (ver sección 5)

**Objetivo del usuario:** llevar esta versión gratuita a su máximo potencial. Más
adelante planea una versión Pro con API de pago.

---

## 2. Hallazgos validados (backtest sobre ~19.000 partidos)

Todo lo que sigue fue **medido**, no supuesto. Metodología: backtest cronológico
sin fuga de información futura, calibración isotónica entrenada en la primera
mitad de los datos y evaluada en la segunda.

### 2.1 El motor viejo no tenía ventaja

La fórmula original de `calcular_prediccion_avanzada()` (Poisson con pesos
temporada 0.5 / racha 0.2 / localía 0.3, elegidos a ojo):

- Acierto 1X2: **39.9%**
- Apostar siempre al local: **45.0%** ← el motor perdía contra la regla más tonta
- Brier: 0.6560

**Calibrar los pesos NO resolvió el problema.** Se probaron 20 combinaciones; la
mejor (0.4/0.1/0.5) apenas subió a 40.8%. Conclusión: el problema era la fórmula,
no los porcentajes.

Dato adicional: **la "racha" (últimos 5 partidos) es ruido.** En 9 de 15 ligas la
mejor configuración tenía racha = 0.0. Además estaba doblemente contada (los
últimos 5 partidos ya están dentro del promedio de temporada).

### 2.2 Dixon-Coles sí funciona

Modelo con fuerza de ataque/defensa por equipo, ventaja de localía estimada de los
datos, decaimiento temporal y corrección de empates (rho):

- Acierto 1X2: **46.2%** (supera al 45.0% de la regla trivial)
- Partidos con confianza ≥60%: pasó de 1.010 a 5.704

**PERO venía sobreconfiado:** decía 90% y acertaba 62%. Esto es peligroso porque
infla falsamente el EV del escáner de valor. **Se corrigió con calibración
isotónica** — tras calibrar, los desvíos quedan dentro de ±5 puntos.

### 2.3 Qué mercados sirven y cuáles no

Skill = cuánto mejora el modelo respecto de decir siempre la frecuencia histórica.

| Mercado | Skill | Veredicto |
|---|---|---|
| 1 / X2 | +0.0129 | ✅ Aporta (fuerte) |
| 2 / 1X | +0.0100 | ✅ Aporta (moderado) |
| X / 12 | +0.0009 | ⚠️ Muy poco |
| Over 1.5 / 2.5 / 3.5 | ≈0 o negativo | ❌ No aporta |
| BTTS | **-0.0006** | ❌ No aporta (peor que trivial) |

**Los mercados de goles NO son predecibles con estos datos.** Se probaron tres
enfoques distintos (Poisson, promedio empírico de frecuencias por localía,
combinación multiplicativa en odds) y **los tres fallaron**. No es un problema de
implementación: la señal no está en los datos disponibles.

Causa: SoccerStats **no publica xG ni tiros**, que es lo que se necesitaría para
modelar goles con precisión. Los goles son eventos raros y ruidosos.

### 2.4 Ranking de ligas por ventaja del modelo

| Nivel | Ligas | Skill | Acierto |
|---|---|---|---|
| 🟢 ALTA | spain | +0.0238 | 51.0% |
| 🟢 ALTA | scotland | +0.0216 | 52.5% |
| 🟢 ALTA | brazil | +0.0174 | 44.9% |
| 🟢 ALTA | england | +0.0172 | 47.4% |
| 🟢 ALTA | norway | +0.0172 | 53.8% |
| 🟢 ALTA | italy | +0.0167 | 50.1% |
| 🟢 ALTA | france | +0.0145 | 52.6% |
| 🟡 MEDIA | bolivia | +0.0084 | 48.4% |
| 🟡 MEDIA | champions | +0.0077 | 49.5% |
| 🟡 MEDIA | libertadores | +0.0073 | 47.3% |
| 🔴 BAJA | serie_b_brasil | +0.0032 | 40.0% |
| 🔴 BAJA | sudamericana | +0.0022 | 45.2% |
| 🔴 BAJA | europa | +0.0018 | 50.5% |
| 🔴 BAJA | argentina | +0.0014 | 40.0% |
| ⛔ NULA | primera_nacional | 0.0000 | 35.9% |

### 2.5 Estado de los datos

Auditoría de `database/football_data.db`:

- **Tablas vacías / legado a limpiar:** `competitions`, `teams`, `seasons`,
  `matches`, `favoritos` (0 filas). Pertenecen a un esquema abandonado.
- **Sistema de apuestas duplicado:** `historial_apuestas` + `capital_usuario`
  (3 filas, sin uso — el usuario no recuerda haberlas creado) conviven con
  `mis_apuestas` + `config_billetera` (82 filas, el sistema real).
  **Decisión: archivar las dos primeras.**
- **`partidos` (24.041 filas):** sin duplicados ni fechas nulas ✅, pero:
  - **Formatos de fecha mezclados:** 20.738 en `DD/MM/YYYY`, 3.303 en ISO con hora
  - **No tiene columna `liga`** — hoy se infiere cruzando nombres contra los JSON
  - Córners solo en 36.2% de las filas; medio tiempo (HT) en **0.9%**
- **`equipos` (17 filas):** solo cubre Bolivia. El resto del sistema usa nombres
  de texto libre, de ahí los múltiples parches de fuzzy matching.
- **Campo `liga` fragmentado en `mis_apuestas`:** "Noruega", "Noruega - Eliteserien"
  y "Liga de Noruega" son la misma liga. Causa: campo de texto libre en el
  formulario manual de la Billetera.

### 2.6 Rendimiento real del usuario

80 apuestas decididas, 70 con inversión real registrada:
- Inversión total: $494.76 · Retorno: $428.62 · **Yield: -13.4%**
- **32 de 80 apuestas (40%) fueron combinadas con `probabilidad = 0`** — armadas
  multiplicando cuotas a mano, sin ningún cálculo de valor. Probablemente la causa
  individual más grande del resultado negativo.

---

## 3. Decisiones tomadas

1. **Motor:** Dixon-Coles + recalibración isotónica obligatoria. Reemplaza a
   `calcular_prediccion_avanzada()`, con fallback al motor viejo si falla.
2. **Mercados para apostar:** solo 1X2 y doble oportunidad. Hándicaps ±0.5 también
   (son matemáticamente idénticos a 1/1X/2/X2 — ver Tarea 6).
3. **Over/Under y BTTS:** se siguen mostrando como información, marcados con
   advertencia "sin ventaja validada". **No se eliminan.**
4. **Ligas:** avisar el nivel de calidad en pantalla (verde/amarillo/rojo). No se
   bloquea ninguna, pero se advierte.
5. **Combinadas:** deben calcular probabilidad conjunta real, no multiplicar cuotas.
6. **Staking:** Kelly fraccionado (1/4) con tope del 5% del bankroll, en reemplazo
   de la fórmula `(balance * prob) / 6.5` que no tiene base matemática.
7. **No se elimina funcionalidad existente.** La "Radiografía Matemática" y la
   tabla de Fortalezas/Cenicientas se conservan — son descriptivas y útiles. Solo
   se ajusta la redacción para que no suenen a recomendación de apuesta.

---

## 4. Reglas de trabajo

- **No romper lo que funciona.** El usuario invirtió mucho tiempo en los mapeos de
  equipos por liga, las reglas especiales (zonas de Argentina, sufijos brasileños),
  y la infraestructura de scraping de ~20 ligas. Todo eso se conserva.
- **Cambios incrementales y verificables.** Después de cada cambio, correr la app
  y confirmar que arranca sin errores antes de seguir.
- **El usuario no programa.** Explicá en lenguaje simple. No le pidas que edite
  archivos a mano — hacelo vos.
- **Nada de `except: pass` nuevo.** El código actual tiene decenas de bloques que
  tragan errores en silencio; si el scraping falla, la app predice con datos
  incompletos sin avisar. Al menos loguear.
- **Cuidado con `fillna(0)`** en columnas de goles: convierte datos faltantes en
  ceros reales y sesga el modelo. NULL ≠ 0.

---

## 5. Archivos ya creados y funcionando

En la raíz del proyecto:

| Archivo | Qué hace | Estado |
|---|---|---|
| `backtesting_engine.py` | Motor de backtest cronológico | ✅ Probado |
| `modelo_dixon_coles.py` | Ajuste Dixon-Coles + comparación de modelos | ✅ Probado |
| `calibrador_pesos.py` | Prueba 20 combinaciones de pesos | ✅ Probado |
| `recalibracion_mercados.py` | Calibración isotónica multi-mercado | ✅ Probado |
| `modelo_goles_empirico.py` | Modelos empíricos de goles (resultado negativo) | ✅ Probado |
| `entrenar_motor.py` | Entrena y guarda `modelos/motor_v2.pkl` | ✅ Ejecutado |
| `motor_v2.py` | Módulo de predicción para el dashboard | ✅ Probado |
| `picks_dia.py` | Pestaña de picks del día | ⏳ Sin integrar |

**API de `motor_v2.py`:**
```python
motor_v2.motor_disponible()                    # bool
motor_v2.predecir(liga_display, local, visita) # dict con probs calibradas o None
motor_v2.calidad_liga(liga_display)            # {'nivel','skill','mensaje','apostar'}
motor_v2.mercado_validado(nombre)              # bool
motor_v2.prob_combinada([probs])               # probabilidad conjunta real
motor_v2.calcular_ev(prob_pct, cuota)          # EV en %
motor_v2.kelly_fraccionado(prob, cuota, bank)  # monto sugerido
```

**Estado de la integración:** el usuario empezó a pegar bloques a mano en
`dashboard.py` y quedaron errores de indentación (Ln 1550, 2292, 2294). **Primera
tarea: arreglarlos.**

---

## 6. TAREAS (ordenadas por prioridad)

### 🔴 TAREA 1 — Arreglar indentación y completar integración de motor_v2

Errores actuales en `dashboard.py`:
- Ln 1550: `Expected indented block`
- Ln 2292, 2294: `Unindent amount does not match previous indent`

Verificar que la integración esté completa:
1. `import motor_v2` al inicio
2. Reemplazar la llamada a `calcular_prediccion_avanzada()` por `motor_v2.predecir()`,
   con fallback al motor viejo si devuelve `None`
3. Mostrar aviso de calidad de liga (verde/amarillo/rojo) con `motor_v2.calidad_liga()`
4. Advertencia "sin ventaja validada" en BTTS y mercados de goles
5. Calculadora de inversión: usar `motor_v2.kelly_fraccionado()`, mostrar EV y
   "NO APOSTAR" si el EV es negativo

**Criterio de éxito:** `streamlit run dashboard.py` arranca sin errores y muestra
predicciones del motor nuevo.

---

### 🔴 TAREA 2 — Integrar la pestaña "Picks del Día" (con zona gris)

`picks_dia.py` ya existe. Integrarlo:
- `import picks_dia` al inicio
- Botón en el sidebar: `st.button("🎯 Picks del Día", on_click=cambiar_pagina, args=('Picks',), use_container_width=True)`
- Bloque al final: `elif st.session_state.pagina == 'Picks': picks_dia.renderizar_pestana()`

**Modificación pedida por el usuario — sistema de zona gris:**

Actualmente solo muestra picks que superan el umbral. Cambiar a tres niveles:

| Nivel | Condición | Color | Etiqueta |
|---|---|---|---|
| Viable | ≥ umbral | 🟢 verde | "Supera el umbral" |
| Zona gris | entre (umbral − 5%) y umbral | 🟡 ámbar | "Por debajo del umbral — menor confianza" |
| Descartado | < umbral − 5% | no se muestra | — |

Umbrales por defecto: **55% para 1X2**, **75% para doble oportunidad**.
El margen de zona gris debe ser configurable (slider, 0-5%, por defecto 5%).

**Importante:** los picks de zona gris deben verse claramente distintos, con la
advertencia explícita de que están por debajo del umbral validado. No maquillarlos
como si fueran equivalentes a los verdes.

Mantener la columna **"Cuota mínima"** (= 100 / probabilidad) que ya tiene el
módulo — es la información más importante de la pestaña.

---

### 🟠 TAREA 3 — Arreglar el armador de combinadas

Ubicación: pestaña Favoritos, sección "Armador de Apuestas Combinadas (Parlay)".

**Problema:** hoy solo multiplica las cuotas ingresadas a mano. Guarda
`probabilidad = 0` en la base. 40% de las apuestas del usuario se hicieron así.

**Solución:**
1. Usar `motor_v2.prob_combinada([lista de probabilidades])` para la probabilidad real
2. Mostrar el EV real de la combinada con `motor_v2.calcular_ev()`
3. Guardar esa probabilidad en la columna `probabilidad` (no 0)
4. **Advertencia obligatoria** si dos selecciones son del mismo partido: no son
   independientes y el cálculo sería incorrecto
5. Mostrar comparación: "Cuota necesaria para tener valor: X.XX · Cuota que ofrece
   tu casa: Y.YY" con veredicto

---

### 🟠 TAREA 4 — Registro de TODAS las predicciones

**Por qué es crítico:** hoy solo se guardan las apuestas realizadas. Eso genera
sesgo de selección: no se puede medir si el modelo sigue calibrado en el tiempo.

Crear tabla nueva `predicciones_log`:
```sql
CREATE TABLE IF NOT EXISTS predicciones_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    fecha_prediccion TEXT,
    fecha_partido TEXT,
    liga TEXT,
    equipo_local TEXT,
    equipo_visita TEXT,
    prob_1 REAL, prob_X REAL, prob_2 REAL,
    prob_1X REAL, prob_X2 REAL, prob_12 REAL,
    xg_local REAL, xg_visita REAL,
    version_motor TEXT,
    goles_local INTEGER DEFAULT NULL,
    goles_visita INTEGER DEFAULT NULL,
    resultado TEXT DEFAULT NULL
);
```

- Guardar automáticamente cada vez que se genera una predicción (análisis o picks)
- Script `actualizar_resultados.py` que cruce contra `partidos` y complete los
  resultados cuando estén disponibles
- Pestaña o expander de "Calibración en vivo": tabla de rangos de probabilidad vs
  acierto real, para verificar que el modelo sigue honesto

---

### 🟠 TAREA 5 — Dropdown de ligas en el formulario manual

Ubicación: pestaña Billetera, "➕ Añadir Apuesta Manualmente".

El campo `m_liga = st.text_input("Liga / Torneo")` produjo registros fragmentados
("Noruega", "Noruega - Eliteserien", "Liga de Noruega", "Islanadia", "Luca", vacío).
Reemplazar por `st.selectbox` con la lista fija de `opciones_liga` + opción "Otra"
que habilite un campo libre.

**Además:** script de migración que normalice los valores existentes de la columna
`liga` en `mis_apuestas`, para poder medir rendimiento por liga con datos limpios.

---

### 🟡 TAREA 6 — Backtest de hándicaps asiáticos

Los hándicaps ±0.5 son **matemáticamente idénticos** a mercados ya validados:
- HA -0.5 local = mercado "1" · HA +0.5 local = mercado "1X"
- HA -0.5 visita = mercado "2" · HA +0.5 visita = mercado "X2"

→ **Se pueden habilitar directamente.** Valor práctico: a veces la casa paga mejor
el hándicap que el 1X2 equivalente.

Los de línea ancha (±1.5, ±2.5) **no están validados**. Backtestearlos usando la
matriz de marcadores de Dixon-Coles (distribución de diferencia de goles), con la
misma metodología: calibración isotónica, train/test cronológico, skill vs trivial.

Reutilizar la infraestructura de `recalibracion_mercados.py`.

---

### 🟡 TAREA 7 — Motor de sugerencia de mercado

Convertir el programa de "calculadora de probabilidades" a "asistente de decisión".

Para un partido dado, el usuario ingresa las cuotas de su casa y el sistema:
1. Calcula el EV de cada mercado validado
2. Los ordena de mayor a menor EV
3. Marca cuál conviene y cuáles descartar
4. Sugiere el monto con Kelly fraccionado

Ejemplo de salida esperada:
```
✅ MEJOR OPCIÓN: 1X a 1.35 → EV +8.2% → Invertir $4.20
⚠️  1 directo a 1.50 → EV -3.1% → NO APOSTAR
❌ HA -1.5 a 2.10 → EV -12.0% → NO APOSTAR
```

---

### 🟡 TAREA 8 — Ampliar cobertura de ligas (scraping de historial)

**Problema:** varias ligas están en la app pero sin historial suficiente para que
el modelo funcione:

| Liga | Partidos en BD |
|---|---|
| México | 82 |
| Suecia | 29 |
| Dinamarca | 6 |
| Estonia | 1 |
| MLS, Islandia (1ra y 2da), China | 0 |

**Mínimo necesario:** ~150-200 partidos por liga para que Dixon-Coles estime bien.

**Tareas:**
1. Scrapear historial de temporadas anteriores de SoccerStats para esas ligas
2. **Ligas nuevas que el usuario quiere agregar** (buscar en SoccerStats, verificar
   disponibilidad y códigos de liga):
   - Alemania 2. Bundesliga y 3. Liga
   - Ucrania
   - Otras ligas menores / poco cubiertas por casas de apuestas
3. **Validar cada liga nueva con el backtest antes de habilitarla.** No asumir que
   funciona: correr `backtesting_engine.py` y medir el skill. Solo habilitar en
   picks las que muestren ventaja.
4. Agregar la liga validada a `CALIDAD_LIGAS` en `motor_v2.py`
5. Re-entrenar con `python entrenar_motor.py`

**Racional del usuario:** las ligas menores tienen líneas más flojas porque los
bookmakers invierten menos en ajustarlas. Estrategia válida, pero también tienen
más varianza — por eso la validación previa es obligatoria.

---

### 🟢 TAREA 9 — Limpieza de base de datos

1. **Normalizar fechas** en `partidos` a un único formato ISO (`YYYY-MM-DD`).
   Actualmente conviven `DD/MM/YYYY` (20.738 filas) e ISO con hora (3.303).
2. **Agregar columna `liga`** a `partidos` y poblarla (hoy se infiere en cada
   ejecución cruzando nombres contra los JSON, lo que es lento y frágil).
3. **Archivar tablas muertas:** `historial_apuestas`, `capital_usuario`,
   `competitions`, `teams`, `seasons`, `matches`. Hacer backup del `.db` antes.
4. **Crear tabla maestra `equipos`** con ID único + tabla `alias_equipos`
   (variante → id_canónico), para eliminar progresivamente el fuzzy matching por
   substring que hoy está repetido en varios lugares del código.

---

### 🟢 TAREA 10 — Robustez del scraping

1. Reemplazar los `except: pass` por logging real
2. **Alerta visible en el dashboard** cuando un scraping falló o los datos están
   desactualizados (hoy la app predice en silencio con datos viejos)
3. Guardar timestamp de última actualización por liga y mostrarlo en pantalla
4. Revisar los `fillna(0)` en columnas de goles — distinguir "0 goles" de "dato faltante"

---

### 🟢 TAREA 11 — Ajustes de redacción (no funcionales)

Conservar toda la funcionalidad, ajustar solo el texto donde suena a recomendación
en mercados sin ventaja validada:

- "🎯 Mejor Mercado (Sugerido): Más de 2.5 Goles" → "📊 Perfil de la liga: ofensiva
  (2.9 goles/partido)"
- Mantener intactas la Radiografía Matemática y la tabla de Fortalezas/Cenicientas
  (son descriptivas y el usuario las valora)
- Opcional: agregar la probabilidad del modelo junto a Fortalezas/Cenicientas para
  ver si coinciden o discrepan

---

### 🔵 TAREA 12 — Investigar Argentina

Argentina tiene 2.812 partidos (segunda liga con más datos) pero el modelo no tiene
ventaja ahí (skill +0.0014, acierto 40%). El usuario sospecha falta de datos, pero
el volumen descarta esa hipótesis.

**Hipótesis a verificar:**
1. El etiquetado mezcla torneos distintos (Apertura/Clausura/Copa de la Liga) y
   zonas (A/B) como si fueran una sola competencia
2. 28-30 equipos → muchos pares se enfrentan una sola vez por torneo → poca
   información para estimar fuerzas relativas
3. Paridad genuina del fútbol argentino (más empates que cualquier otra liga medida)

**Test:** separar por torneo/zona, re-entrenar y comparar el skill. Si mejora, el
problema es estructural y corregible. Si no, es la naturaleza de la liga.

---

### 🔵 TAREA 13 — Cuotas históricas y CLV (futuro)

1. Descargar CSVs gratuitos de **football-data.co.uk** — incluyen resultados **y
   cuotas de cierre** para ligas europeas. Permite backtestear **rentabilidad real**,
   no solo precisión. Hasta ahora solo medimos si el modelo acierta, nunca si
   ganaría dinero contra las cuotas reales.
2. Implementar seguimiento de **CLV (Closing Line Value)**: comparar la cuota a la
   que se apostó contra la cuota final antes del partido. Es la métrica que usan los
   profesionales para validar edge **sin depender de la suerte de los resultados**.
3. Comparación de cuotas entre 2-3 casas antes de apostar. Con un edge fino,
   conseguir 2.05 en vez de 1.95 puede ser la diferencia entre ganar y perder.

---

## 7. Notas finales

**Magnitud realista del edge:** el skill medido (+0.01 a +0.02) es una ventaja
pequeña pero real. Con márgenes de casa del 5-8%, puede quedar comida si no se
comparan cuotas. Este sistema no es una fuente de ingresos — es, en el mejor de los
casos, la diferencia entre perder lento y estar cerca del equilibrio.

**Estado del bankroll del usuario:** $100 inicial, -$66 acumulados, viniendo de una
racha perdedora causada en buena parte por combinadas sin cálculo de valor (Tarea 3).

**Techo de la versión gratuita:** sin xG ni tiros, los mercados de goles no son
modelables. Ese techo solo se rompe con datos de pago (ver Tarea 13 y planes futuros).

**Prioridad de datos para la futura versión Pro:**
1. xG histórico (el salto más grande posible)
2. Cuotas históricas de cierre (backtest de rentabilidad + CLV)
3. Cuotas en vivo multi-casa (captura de valor)
4. Alineaciones confirmadas y lesiones (mejora marginal)
