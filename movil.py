"""
ADAPTACIÓN A MÓVIL
===================
Detecta si la app se está viendo desde un teléfono y ajusta la interfaz.

Cómo detecta:
  1. Lee el User-Agent del navegador (disponible en Streamlit reciente).
  2. Si no puede, permite forzarlo con ?movil=1 en la dirección.
  3. Además aplica CSS que se adapta solo por ancho de pantalla, lo que
     funciona siempre aunque la detección falle.

Uso desde dashboard.py:
    import movil
    movil.configurar()          # una sola vez, después de set_page_config
    if movil.es_movil(): ...    # para simplificar contenido
"""

import streamlit as st

_PALABRAS_MOVIL = (
    "android", "iphone", "ipad", "ipod", "mobile", "opera mini",
    "windows phone", "blackberry", "webos", "silk", "kindle",
)


def _detectar_por_navegador():
    """Lee el User-Agent. Devuelve None si no está disponible."""
    try:
        headers = st.context.headers
        ua = (headers.get("User-Agent") or headers.get("user-agent") or "").lower()
        if not ua:
            return None
        return any(p in ua for p in _PALABRAS_MOVIL)
    except Exception:
        return None


def es_movil():
    """True si conviene mostrar la versión compacta."""
    if "es_movil" in st.session_state:
        return st.session_state.es_movil

    # 1) Parámetro en la dirección: ?movil=1 fuerza el modo compacto
    try:
        valor = st.query_params.get("movil")
        if valor is not None:
            st.session_state.es_movil = str(valor) in ("1", "true", "si", "sí")
            return st.session_state.es_movil
    except Exception:
        pass

    # 2) User-Agent del navegador
    detectado = _detectar_por_navegador()
    st.session_state.es_movil = bool(detectado) if detectado is not None else False
    return st.session_state.es_movil


CSS = """
<style>
/* ================================================================
   AJUSTES PARA PANTALLAS CHICAS
   Se aplican solos por ancho de pantalla, sin depender de detección.
   ================================================================ */
@media (max-width: 640px) {

    /* Aprovechar todo el ancho disponible */
    .block-container {
        padding-top: 1rem !important;
        padding-bottom: 3rem !important;
        padding-left: 0.7rem !important;
        padding-right: 0.7rem !important;
        max-width: 100% !important;
    }

    /* Títulos más chicos: en el celular ocupaban media pantalla */
    h1 { font-size: 1.45rem !important; line-height: 1.25 !important; }
    h2 { font-size: 1.2rem !important; }
    h3 { font-size: 1.05rem !important; }
    h4 { font-size: 0.98rem !important; }

    /* Números grandes de las métricas */
    [data-testid="stMetricValue"] { font-size: 1.4rem !important; }
    [data-testid="stMetricLabel"] { font-size: 0.75rem !important; }

    /* Botones cómodos para el dedo */
    .stButton button {
        min-height: 2.9rem !important;
        font-size: 0.95rem !important;
        padding: 0.5rem 0.8rem !important;
    }

    /* Menos espacio muerto entre bloques */
    [data-testid="stVerticalBlock"] > div { gap: 0.4rem !important; }
    hr { margin: 0.5rem 0 !important; }

    /* Tablas: que se puedan deslizar de costado */
    [data-testid="stDataFrame"] { font-size: 0.8rem !important; }
    [data-testid="stDataFrame"] > div { overflow-x: auto !important; }

    /* Pestañas: deslizables cuando son muchas */
    [data-testid="stTabs"] [data-baseweb="tab-list"] {
        overflow-x: auto !important;
        flex-wrap: nowrap !important;
    }
    [data-testid="stTabs"] [data-baseweb="tab"] {
        font-size: 0.85rem !important;
        padding: 0.4rem 0.7rem !important;
        white-space: nowrap !important;
    }

    /* Campos de texto y selectores más altos, más fáciles de tocar */
    .stSelectbox div[data-baseweb="select"] > div,
    .stNumberInput input,
    .stTextInput input {
        min-height: 2.7rem !important;
        font-size: 0.95rem !important;
    }

    /* Avisos más compactos */
    .stAlert { padding: 0.6rem 0.8rem !important; font-size: 0.88rem !important; }
    .stAlert p { margin-bottom: 0.2rem !important; }

    /* Barra lateral por encima del contenido */
    [data-testid="stSidebar"] { min-width: 80vw !important; }

    /* Ocultar el pie de página de Streamlit */
    footer { display: none !important; }
}

/* Pantallas muy angostas */
@media (max-width: 400px) {
    h1 { font-size: 1.25rem !important; }
    [data-testid="stMetricValue"] { font-size: 1.2rem !important; }
    [data-testid="stDataFrame"] { font-size: 0.72rem !important; }
}
</style>
"""


def configurar():
    """Aplica los estilos. Llamar una vez al inicio del dashboard."""
    st.markdown(CSS, unsafe_allow_html=True)


def columnas_tabla(completo=True):
    """
    Qué columnas mostrar en la tabla de posiciones.
    En el celular no entran las diez.
    """
    if es_movil():
        return ["Pos", "Club", "Pts", "PJ"]
    return ["Pos", "Club", "Pts", "PJ", "G", "E", "P"] if completo else ["Pos", "Club", "Pts"]


def limite_filas(normal=40):
    """Cuántas filas mostrar en listados largos."""
    return 12 if es_movil() else normal


def selector_vista():
    """
    Control en la barra lateral para cambiar de vista a mano,
    por si la detección automática se equivoca.
    """
    actual = es_movil()
    nuevo = st.checkbox(
        "📱 Vista compacta",
        value=actual,
        help="Se activa sola en pantallas chicas. Podés cambiarla si querés.",
        key="check_vista_movil",
    )
    if nuevo != actual:
        st.session_state.es_movil = nuevo
        st.rerun()
    return nuevo
