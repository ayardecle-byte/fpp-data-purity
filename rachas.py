"""
RACHAS ACTIVAS
===============
Detecta equipos que vienen encadenando partidos con una misma condición:
victorias seguidas, derrotas seguidas, partidos con ambos anotan, con
más de 1.5 o 2.5 goles, etc.

ADVERTENCIA IMPORTANTE
----------------------
Las rachas NO predicen. Que un equipo lleve 10 partidos seguidos con
ambos anotan no aumenta la probabilidad de que pase en el próximo. Es la
falacia del jugador, y en fútbol está bien documentada.

Además, si una racha es visible, el mercado ya la conoce y ya ajustó
la cuota.

Sirve como dato descriptivo —para detectar equipos con estilos marcados—
no como señal de apuesta.
"""

import os
import re
import json
import unicodedata

CARPETA = "data_json"


def norm(t):
    n = unicodedata.normalize("NFKD", str(t))
    n = "".join(c for c in n if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9 ]", "", n.lower().strip())


# ==========================================================
# CONDICIONES QUE SE PUEDEN ENCADENAR
# Cada una recibe (goles a favor, goles en contra) y devuelve
# True si el partido cumple la condición.
# ==========================================================
MERCADOS = {
    "victorias": {
        "nombre": "Victorias seguidas",
        "icono": "🏆",
        "descripcion": "Equipos que vienen ganando partido tras partido.",
        "test": lambda gf, gc: gf > gc,
    },
    "derrotas": {
        "nombre": "Derrotas seguidas",
        "icono": "📉",
        "descripcion": "Equipos que vienen perdiendo partido tras partido.",
        "test": lambda gf, gc: gf < gc,
    },
    "sin_perder": {
        "nombre": "Sin perder",
        "icono": "🛡️",
        "descripcion": "Partidos seguidos ganando o empatando.",
        "test": lambda gf, gc: gf >= gc,
    },
    "btts": {
        "nombre": "Ambos anotan",
        "icono": "🤝",
        "descripcion": "Partidos seguidos donde los dos equipos marcaron.",
        "test": lambda gf, gc: gf > 0 and gc > 0,
    },
    "over15": {
        "nombre": "Más de 1.5 goles",
        "icono": "⚽",
        "descripcion": "Partidos seguidos con 2 o más goles en total.",
        "test": lambda gf, gc: (gf + gc) > 1.5,
    },
    "over25": {
        "nombre": "Más de 2.5 goles",
        "icono": "🔥",
        "descripcion": "Partidos seguidos con 3 o más goles en total.",
        "test": lambda gf, gc: (gf + gc) > 2.5,
    },
    "marca15": {
        "nombre": "El equipo marca +1.5",
        "icono": "🎯",
        "descripcion": "Partidos seguidos donde el equipo metió 2 o más goles.",
        "test": lambda gf, gc: gf > 1.5,
    },
    "marca25": {
        "nombre": "El equipo marca +2.5",
        "icono": "💥",
        "descripcion": "Partidos seguidos donde el equipo metió 3 o más goles.",
        "test": lambda gf, gc: gf > 2.5,
    },
    "sin_recibir": {
        "nombre": "Sin recibir goles",
        "icono": "🧤",
        "descripcion": "Partidos seguidos manteniendo el arco en cero.",
        "test": lambda gf, gc: gc == 0,
    },
    "under25": {
        "nombre": "Menos de 2.5 goles",
        "icono": "🔒",
        "descripcion": "Partidos seguidos con 2 goles o menos en total.",
        "test": lambda gf, gc: (gf + gc) < 2.5,
    },
}


def _partidos_de(equipo, registros):
    """
    Convierte el historial en una lista de (goles a favor, goles en contra),
    del más viejo al más nuevo.
    """
    salida = []
    ne = norm(equipo)
    for r in registros or []:
        try:
            gl, gv = map(int, str(r.get("Res", "")).strip("[]").split(":"))
        except Exception:
            continue
        es_local = ne in norm(r.get("Local", ""))
        gf, gc = (gl, gv) if es_local else (gv, gl)
        salida.append({
            "gf": gf, "gc": gc,
            "local": r.get("Local", ""), "visita": r.get("Visita", ""),
            "fecha": r.get("Fecha", ""),
            "es_local": es_local,
        })
    return salida


def racha_actual(partidos, test):
    """
    Cuántos partidos seguidos, contando desde el más reciente hacia atrás,
    cumplen la condición. Devuelve (largo, cumplidos_de_los_ultimos_40).
    """
    if not partidos:
        return 0, 0, 0

    # Los registros vienen del más viejo al más nuevo: se recorre al revés
    recientes = list(reversed(partidos))

    largo = 0
    for p in recientes:
        if test(p["gf"], p["gc"]):
            largo += 1
        else:
            break

    ventana = recientes[:40]
    cumplidos = sum(1 for p in ventana if test(p["gf"], p["gc"]))
    return largo, cumplidos, len(ventana)


def calcular(ligas_incluidas=None, minimo_racha=3):
    """
    Recorre los data_json y arma las rachas de todos los equipos.

    Devuelve: {clave_mercado: [ {equipo, liga, racha, aciertos, total, pct}, ... ]}
    """
    if not os.path.isdir(CARPETA):
        return {}

    resultados = {k: [] for k in MERCADOS}

    for archivo in sorted(os.listdir(CARPETA)):
        if not archivo.endswith(".json"):
            continue
        liga = archivo[:-5]
        if ligas_incluidas and liga not in ligas_incluidas:
            continue

        try:
            with open(os.path.join(CARPETA, archivo), encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            continue

        adv = data.get("estadisticas_avanzadas", {})
        if not adv:
            continue

        # Próximo partido de cada equipo, para mostrarlo en la tabla
        proximos = {}
        for p in data.get("fixture", []):
            for lado, rival_lado in (("Local", "Visita"), ("Visita", "Local")):
                eq = norm(p.get(lado, ""))
                if eq and eq not in proximos:
                    proximos[eq] = {
                        "rival": p.get(rival_lado, ""),
                        "condicion": "🏠" if lado == "Local" else "✈️",
                        "fecha": p.get("Fecha", ""),
                        "hora": p.get("Hora", ""),
                    }

        for equipo, datos in adv.items():
            partidos = _partidos_de(equipo, datos.get("historial", []))
            if len(partidos) < minimo_racha:
                continue

            prox = proximos.get(norm(equipo))

            for clave, cfg in MERCADOS.items():
                largo, aciertos, total = racha_actual(partidos, cfg["test"])
                if largo < minimo_racha:
                    continue
                resultados[clave].append({
                    "equipo": equipo,
                    "liga": liga,
                    "racha": largo,
                    "aciertos": aciertos,
                    "total": total,
                    "pct": (aciertos / total * 100) if total else 0,
                    "proximo": prox,
                    "partidos": len(partidos),
                })

    # Ordenar cada mercado por largo de racha
    for clave in resultados:
        resultados[clave].sort(key=lambda x: (-x["racha"], -x["pct"]))

    return resultados
