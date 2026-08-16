"""
PICKS DEL DÍA - Football Predictor Pro
======================================
Escanea los fixtures y devuelve los partidos donde el modelo calibrado
tiene confianza en 1X2 o Doble Oportunidad.

Sistema de tres niveles:
  🟢 VIABLE     -> supera el umbral
  🟡 ZONA GRIS  -> hasta 5% por debajo del umbral (menor confianza)
  ❌ DESCARTADO -> no se muestra

Uso desde dashboard.py:
    import picks_dia
    picks_dia.renderizar_pestana()
"""

import os
import json
import datetime
import pandas as pd
import streamlit as st

import motor_v2

MAPA_FIXTURES = {
    "Inglaterra - Premier League": "england",
    "España - La Liga": "spain",
    "Italia - Serie A": "italy",
    "Francia - Ligue 1": "france",
    "Argentina": "argentina",
    "Argentina - Primera Nacional": "primera_nacional",
    "Brasil": "brazil",
    "Brasil - Serie B": "serie_b_brasil",
    "Champions League": "champions",
    "Libertadores": "libertadores",
    "Copa Sudamericana": "sudamericana",
    "Europa League": "europa",
    "Noruega - Eliteserien": "norway",
    "Estados Unidos - MLS": "mls",
    "México - Liga MX": "mexico",
    "Bolivia - Div. Profesional": "bolivia",
    "Estonia - Meistriliiga": "estonia",
    "Islandia - 2da División": "iceland2",
    "Dinamarca - Superliga": "denmark",
    "China - Super League": "china",
    "Suecia - Allsvenskan": "sweden",
    "Islandia - 1ra División": "iceland",
}

MESES = {1: 'Jan', 2: 'Feb', 3: 'Mar', 4: 'Apr', 5: 'May', 6: 'Jun',
         7: 'Jul', 8: 'Aug', 9: 'Sep', 10: 'Oct', 11: 'Nov', 12: 'Dec'}
DIAS = {0: 'Mon', 1: 'Tue', 2: 'Wed', 3: 'Thu', 4: 'Fri', 5: 'Sat', 6: 'Sun'}

NOMBRES_MERCADO = {
    "1": "Gana Local",
    "X": "Empate",
    "2": "Gana Visita",
    "1X": "Local o Empate (1X)",
    "X2": "Empate o Visita (X2)",
    "12": "Sin Empate (12)",
}

ICONO_CALIDAD = {"ALTA": "🟢", "MEDIA": "🟡", "BAJA": "🔴", "NULA": "⛔", "DESCONOCIDA": "⚪"}


def fecha_a_formato_fixture(fecha):
    return f"{DIAS[fecha.weekday()]} {fecha.day} {MESES[fecha.month]}"


def cargar_fixture(liga_display):
    archivo = MAPA_FIXTURES.get(liga_display)
    if not archivo:
        return []
    ruta = f"data_json/{archivo}.json"
    if not os.path.exists(ruta):
        return []
    try:
        with open(ruta, "r", encoding="utf-8") as f:
            return json.load(f).get("fixture", [])
    except Exception:
        return []


def escanear(fechas_objetivo, umbral_1x2, umbral_doble, margen_gris, solo_ligas_validadas):
    picks = []
    ligas_sin_modelo = []
    partidos_analizados = 0

    for liga_display in MAPA_FIXTURES.keys():
        calidad = motor_v2.calidad_liga(liga_display)

        if solo_ligas_validadas and not calidad.get("apostar", False):
            continue

        fixture = cargar_fixture(liga_display)
        if not fixture:
            continue

        # Tolerante a versiones de motor_v2 sin info_muestra
        try:
            muestra = motor_v2.info_muestra(liga_display)
        except AttributeError:
            muestra = None

        partidos_liga = [p for p in fixture if p.get("Fecha") in fechas_objetivo]
        if not partidos_liga:
            continue

        modelo_ok = False
        for p in partidos_liga:
            local = str(p.get("Local", "")).strip()
            visita = str(p.get("Visita", "")).strip()
            if not local or not visita:
                continue

            partidos_analizados += 1
            stats = motor_v2.predecir(liga_display, local, visita)
            if not stats:
                continue
            modelo_ok = True

            grupos = [
                (["1", "X", "2"], "1X2", umbral_1x2),
                (["1X", "X2", "12"], "Doble Oportunidad", umbral_doble),
            ]

            for mercados, tipo, umbral in grupos:
                for mkt in mercados:
                    prob = stats.get(mkt, 0)
                    piso_gris = umbral - margen_gris

                    liga_validada = calidad.get("apostar", False)

                    if prob >= umbral:
                        nivel = "VIABLE" if liga_validada else "SIN VALIDAR"
                    elif prob >= piso_gris:
                        nivel = "ZONA GRIS" if liga_validada else "SIN VALIDAR"
                    else:
                        continue

                    picks.append({
                        "Muestra": (muestra or {}).get("n_partidos", 0),
                        "Liga": liga_display,
                        "Calidad": calidad["nivel"],
                        "Nivel": nivel,
                        "Fecha": p.get("Fecha", ""),
                        "Hora": p.get("Hora", ""),
                        "Partido": f"{local} vs {visita}",
                        "Local": local,
                        "Visita": visita,
                        "Tipo": tipo,
                        "Mercado": NOMBRES_MERCADO[mkt],
                        "Codigo": mkt,
                        "Prob": round(prob, 1),
                        "Cuota_minima": round(100 / prob, 2) if prob > 0 else None,
                        "xG_L": round(stats.get("xG_L", 0), 2),
                        "xG_V": round(stats.get("xG_V", 0), 2),
                    })

        if partidos_liga and not modelo_ok:
            ligas_sin_modelo.append(liga_display)

    return picks, ligas_sin_modelo, partidos_analizados


def _mostrar_pick(r, clave_unica="0"):
    if r["Nivel"] == "VIABLE":
        borde = "🟢"
        etiqueta = ""
    elif r["Nivel"] == "ZONA GRIS":
        borde = "🟡"
        etiqueta = " · ⚠️ *por debajo del umbral*"
    else:
        borde = "⚪"
        etiqueta = " · ⚪ *liga sin ventaja validada*"

    icono_cal = ICONO_CALIDAD.get(r["Calidad"], "⚪")

    cA, cB, cC = st.columns([3.5, 1.5, 1.5])
    with cA:
        st.markdown(f"{borde} **{r['Partido']}**{etiqueta}")
        detalle = f"{icono_cal} {r['Liga']} · 📅 {r['Fecha']} {r['Hora']} · xG {r['xG_L']} - {r['xG_V']}"
        if r.get("Muestra"):
            detalle += f" · 📊 {r['Muestra']} partidos de respaldo"
        st.caption(detalle)

        # Botón para abrir el análisis completo en la Cartelera
        if r.get("Local") and r.get("Visita"):
            clave = f"pick_analizar_{clave_unica}"
            if st.button("🔎 Ver análisis completo", key=clave, width="stretch"):
                st.session_state.pagina = "Cartelera"
                st.session_state.sel_liga = r["Liga"]
                st.session_state.res_l = r["Local"]
                st.session_state.res_v = r["Visita"]
                st.session_state.last_l = r["Local"]
                st.session_state.last_v = r["Visita"]
                st.session_state.analizar = True
                st.rerun()
    with cB:
        st.metric(r["Mercado"], f"{r['Prob']}%")
    with cC:
        st.metric("Cuota mínima", f"{r['Cuota_minima']}")
        st.caption("para tener valor")
    st.divider()


def _mostrar_bloque(sub):
    if sub.empty:
        st.info("Sin picks en este mercado con los umbrales actuales.")
        return

    viables = sub[sub["Nivel"] == "VIABLE"]
    grises = sub[sub["Nivel"] == "ZONA GRIS"]

    if not viables.empty:
        n_part = len(set(viables["Partido"]))
        st.markdown(f"##### 🟢 Viables — {len(viables)} oportunidades en {n_part} partidos")
        for i, (_, r) in enumerate(viables.iterrows()):
            _mostrar_pick(r, f"v{i}_{r['Mercado']}_{r['Partido'][:18]}")
    else:
        st.info("No hay picks que superen el umbral hoy. Eso es normal y esperable.")

    if not grises.empty:
        with st.expander(f"🟡 Zona gris ({len(grises)}) — por debajo del umbral, menor confianza"):
            st.caption(
                "⚠️ Estos picks NO alcanzan el umbral validado. Se muestran solo como referencia. "
                "Un día sin picks verdes es un resultado válido, no una falla del sistema."
            )
            for i, (_, r) in enumerate(grises.iterrows()):
                _mostrar_pick(r, f"g{i}_{r['Mercado']}_{r['Partido'][:18]}")

    sin_val = sub[sub["Nivel"] == "SIN VALIDAR"]
    if not sin_val.empty:
        with st.expander(f"⚪ Ligas sin ventaja validada ({len(sin_val)})"):
            st.caption(
                "⚪ En estas ligas el backtest **no encontró ventaja** (nivel BAJA, NULA o "
                "sin validar). La probabilidad puede ser alta, pero no sabemos si el modelo "
                "acierta ahí. Apostá por criterio propio, no por lo que diga el sistema."
            )
            for i, (_, r) in enumerate(sin_val.iterrows()):
                _mostrar_pick(r, f"s{i}_{r['Mercado']}_{r['Partido'][:18]}")


def renderizar_pestana():
    st.header("🎯 Picks del Día")
    st.markdown(
        "El modelo escanea los fixtures y muestra solo los partidos donde tiene confianza "
        "en el resultado. Basado en el hallazgo del backtest: **la ventaja está en esperar "
        "pocas oportunidades buenas, no en analizar todo**."
    )

    if not motor_v2.motor_disponible():
        st.error(
            "⚠️ El motor V2 no está entrenado. Corré en la terminal:\n\n"
            "`python entrenar_motor.py`"
        )
        return

    st.info(
        "💡 **Lo más importante de esta pantalla es la columna 'Cuota mínima'.** "
        "Es la cuota a partir de la cual la apuesta tiene valor esperado positivo. "
        "Si tu casa paga MENOS que ese número, no apuestes — aunque la probabilidad sea alta."
    )

    c1, c2, c3 = st.columns([2, 1, 1])

    with c1:
        modo_fecha = st.radio(
            "Rango de fechas:",
            ["Hoy", "Hoy y mañana", "Próximos 3 días", "Elegir fecha"],
            horizontal=True,
        )

    hoy = datetime.datetime.now() - datetime.timedelta(hours=5)
    if modo_fecha == "Hoy":
        fechas = [fecha_a_formato_fixture(hoy)]
    elif modo_fecha == "Hoy y mañana":
        fechas = [fecha_a_formato_fixture(hoy),
                  fecha_a_formato_fixture(hoy + datetime.timedelta(days=1))]
    elif modo_fecha == "Próximos 3 días":
        fechas = [fecha_a_formato_fixture(hoy + datetime.timedelta(days=i)) for i in range(3)]
    else:
        elegida = st.date_input("Fecha a escanear:", hoy.date())
        fechas = [fecha_a_formato_fixture(elegida)]

    with c2:
        umbral_1x2 = st.slider("Umbral 1X2 (%)", 40, 90, 55, 1,
                               help="55% es el umbral recomendado según el backtest")
    with c3:
        umbral_doble = st.slider("Umbral Doble Op. (%)", 50, 95, 75, 1,
                                 help="75% es el umbral recomendado según el backtest")

    c4, c5 = st.columns([1, 2])
    with c4:
        margen_gris = st.slider("Margen zona gris (%)", 0, 5, 5, 1,
                                help="Cuánto por debajo del umbral mostrar como 'zona gris'")
    with c5:
        solo_validadas = st.checkbox(
            "Mostrar solo ligas con ventaja estadística validada (recomendado)",
            value=True,
            help="Excluye Argentina, Primera Nacional, Sudamericana, Serie B Brasil y Europa League, "
                 "donde el backtest no encontró ventaja."
        )

    st.caption(f"Escaneando: {', '.join(fechas)} · Umbrales: 1X2 ≥ {umbral_1x2}% · Doble ≥ {umbral_doble}% · Zona gris hasta -{margen_gris}%")

    if st.button("🔍 Buscar Picks", width="stretch", type="primary"):
        with st.spinner("Analizando partidos con el modelo calibrado..."):
            picks, sin_modelo, analizados = escanear(
                fechas, umbral_1x2, umbral_doble, margen_gris, solo_validadas
            )
        st.session_state["picks_resultado"] = picks
        st.session_state["picks_sin_modelo"] = sin_modelo
        st.session_state["picks_analizados"] = analizados

    picks = st.session_state.get("picks_resultado")
    if picks is None:
        return

    analizados = st.session_state.get("picks_analizados", 0)
    sin_modelo = st.session_state.get("picks_sin_modelo", [])

    st.divider()

    n_viables = sum(1 for p in picks if p["Nivel"] == "VIABLE")
    n_grises = sum(1 for p in picks if p["Nivel"] == "ZONA GRIS")
    n_sinval = sum(1 for p in picks if p["Nivel"] == "SIN VALIDAR")

    # Un mismo partido puede generar varios picks (ej: "Gana Local" y "1X"),
    # así que se cuentan por separado los partidos y las oportunidades.
    partidos_con_pick = len({p["Partido"] for p in picks})
    partidos_viables = len({p["Partido"] for p in picks if p["Nivel"] == "VIABLE"})

    cA, cB, cC = st.columns(3)
    cA.metric("Partidos escaneados", analizados)
    cB.metric("Partidos con alguna oportunidad", partidos_con_pick)
    cC.metric("Partidos con pick viable", partidos_viables)

    st.caption(
        f"Oportunidades encontradas: 🟢 {n_viables} viables · "
        f"🟡 {n_grises} en zona gris · ⚪ {n_sinval} sin validar. "
        "Un mismo partido puede generar más de una oportunidad "
        "(por ejemplo, «Gana Local» y «1X» a la vez)."
    )

    if sin_modelo:
        st.warning(
            "⚠️ Estas ligas tienen partidos en el fixture pero el modelo no pudo predecirlos "
            f"(faltan datos históricos o los nombres de equipo no coinciden): {', '.join(sin_modelo)}"
        )

    if not picks:
        st.info(
            "No se encontraron picks en las fechas elegidas. "
            "Puede ser porque no hay partidos programados, o porque ninguno alcanza los umbrales. "
            "**Un día sin picks es un resultado válido** — el modelo alcanza confianza alta en pocos partidos."
        )
        return

    df = pd.DataFrame(picks)
    df["_orden"] = df["Nivel"].map({"VIABLE": 0, "ZONA GRIS": 1, "SIN VALIDAR": 2})
    df = df.sort_values(["_orden", "Prob"], ascending=[True, False]).reset_index(drop=True)

    tab1, tab2 = st.tabs(["⚽ 1X2 (Ganador)", "🛡️ Doble Oportunidad"])
    with tab1:
        _mostrar_bloque(df[df["Tipo"] == "1X2"])
    with tab2:
        _mostrar_bloque(df[df["Tipo"] == "Doble Oportunidad"])

    with st.expander("📋 Ver tabla completa"):
        cols = ["Nivel", "Liga", "Calidad", "Fecha", "Hora", "Partido", "Tipo", "Mercado", "Prob", "Cuota_minima"]
        st.dataframe(df[cols], width="stretch", hide_index=True)
        st.download_button(
            "⬇️ Descargar picks en CSV",
            df[cols].to_csv(index=False).encode("utf-8"),
            file_name=f"picks_{datetime.date.today()}.csv",
            mime="text/csv",
        )
