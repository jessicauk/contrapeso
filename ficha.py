"""
La ficha de decisión: estructura, validación y guardado.

Cada ficha se guarda como un archivo JSON en la carpeta fichas/,
fácil de leer, de revisar a mano y de procesar después con el LLM.
"""
import json
import re
from datetime import date
from pathlib import Path

CARPETA = Path(__file__).parent / "fichas"

ACCIONES = ("comprar", "vender", "mantener", "no_comprar")
ESTADOS = ("calma", "fomo", "miedo", "impulso")
# Tipos de predicción que el código podrá resolver solo en la fase 3
TIPOS_PREDICCION = {
    "supera_a": "{token} rinde más que {referencia}",
    "precio_mayor": "{token} vale más de ${referencia} USD",
    "precio_menor": "{token} vale menos de ${referencia} USD",
}


class FichaInvalida(ValueError):
    pass


def validar(f: dict) -> None:
    """Revisa que la ficha tenga sentido. Lanza FichaInvalida con un mensaje claro."""
    errores = []
    if not re.fullmatch(r"[A-Z0-9]{1,15}", f.get("token", "")):
        errores.append("token: usa el ticker en mayúsculas, por ejemplo NEAR")
    if f.get("accion") not in ACCIONES:
        errores.append(f"accion: debe ser una de {', '.join(ACCIONES)}")
    if f.get("estado_animo") not in ESTADOS:
        errores.append(f"estado_animo: debe ser uno de {', '.join(ESTADOS)}")
    if len(f.get("tesis", "").strip()) < 20:
        errores.append("tesis: explícala en al menos una oración completa")
    if not f.get("invalidacion"):
        errores.append("invalidacion: escribe al menos una señal que te haría cambiar de opinión")

    p = f.get("prediccion") or {}
    if p.get("tipo") not in TIPOS_PREDICCION:
        errores.append(f"prediccion.tipo: debe ser uno de {', '.join(TIPOS_PREDICCION)}")
    prob = p.get("probabilidad")
    if not isinstance(prob, (int, float)) or not 0.01 <= prob <= 0.99:
        errores.append("prediccion.probabilidad: número entre 0.01 y 0.99 (nada es 0% ni 100% seguro)")
    try:
        if date.fromisoformat(p.get("fecha_resolucion", "")) <= date.fromisoformat(f["fecha"]):
            errores.append("prediccion.fecha_resolucion: debe ser posterior a la fecha de la ficha")
    except (ValueError, TypeError, KeyError):
        errores.append("prediccion.fecha_resolucion: usa el formato AAAA-MM-DD")
    if p.get("tipo") == "supera_a" and not re.fullmatch(r"[A-Z0-9]{1,15}", str(p.get("referencia", ""))):
        errores.append("prediccion.referencia: ticker del activo contra el que comparas, por ejemplo BTC")
    if p.get("tipo") in ("precio_mayor", "precio_menor"):
        if not isinstance(p.get("referencia"), (int, float)) or p["referencia"] <= 0:
            errores.append("prediccion.referencia: precio objetivo en USD, mayor a 0")

    if errores:
        raise FichaInvalida("\n  - " + "\n  - ".join(errores))


def nueva_ficha(token, accion, tesis, catalizadores, invalidacion, estado_animo,
                tipo, referencia, probabilidad, fecha_resolucion, fecha=None) -> dict:
    fecha = fecha or date.today().isoformat()
    token = token.strip().upper()
    if tipo == "supera_a":
        referencia = str(referencia).strip().upper()
    ficha = {
        "id": f"{fecha}-{token}",
        "fecha": fecha,
        "token": token,
        "accion": accion,
        "tesis": tesis.strip(),
        "catalizadores": catalizadores,
        "invalidacion": invalidacion,
        "estado_animo": estado_animo,
        "prediccion": {
            "tipo": tipo,
            "referencia": referencia,
            "enunciado": TIPOS_PREDICCION.get(tipo, "").format(token=token, referencia=referencia),
            "probabilidad": probabilidad,
            "fecha_resolucion": fecha_resolucion,
        },
        "contraargumentos": [],   # fase 2: abogado del diablo
        "resultado": None,        # fase 3: lo calcula el código
        "reflexion": None,        # la escribes tú al cierre
    }
    validar(ficha)
    return ficha


def guardar(ficha: dict) -> Path:
    CARPETA.mkdir(exist_ok=True)
    ruta = CARPETA / f"{ficha['id']}.json"
    n = 2
    while ruta.exists():  # varias fichas del mismo token el mismo día
        ruta = CARPETA / f"{ficha['id']}-{n}.json"
        n += 1
    ficha["id"] = ruta.stem
    ruta.write_text(json.dumps(ficha, ensure_ascii=False, indent=2), encoding="utf-8")
    return ruta


def cargar_todas() -> list[dict]:
    if not CARPETA.exists():
        return []
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(CARPETA.glob("*.json"))]