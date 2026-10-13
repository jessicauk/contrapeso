"""
Contrapeso · Validador de resultados: ¿se cumplió la predicción?

Uso:
    python resultados.py              # resuelve las predicciones vencidas de ingesta.db
    python resultados.py --historial  # aciertos por cuenta
    python resultados.py --fichas     # resuelve tus fichas vencidas (pide confirmación)
    python resultados.py --verificar  # cadena de hashes + integridad de predicciones y resultados

Cómo se decide (todo en CÓDIGO, sin LLM):
    Precios: velas diarias en UTC de TOKENUSDT, datos públicos de Binance (sin clave ni costo).
    Solo se resuelve cuando el día de fecha_resolucion ya cerró en UTC.
    - precio_mayor / precio_menor
        al_cierre: el cierre del día de resolución está por encima / por debajo del objetivo.
        toco:      el máximo / mínimo tocó el objetivo en algún momento entre el día siguiente
                   al post y el día de resolución (el día del post no cuenta: pudo ser antes del post).
        trivial:   ya se cumplía al cierre del día del post; no cuenta como acierto en el historial.
    - supera_a
        al_cierre: el rendimiento del token superó al de la referencia, del cierre del día del
                   post al cierre del día de resolución. `toco` no aplica.
    Si falta un precio (token que Binance no lista, o sin velas en esas fechas), la predicción
    queda pendiente y se reintenta en la siguiente corrida; el intento queda en la bitácora.

ASILO:
    S. SUPERVISIÓN      -> tus fichas solo se escriben si apruebas (s/n)
    L. LÍMITES          -> MAX_POR_CORRIDA, timeouts, reintentos acotados; sin costo por llamada
    O. OBSERVABILIDAD   -> cada resultado en la bitácora encadenada, con el digesto de su fila
"""
import argparse
import hashlib
import json
import sqlite3
import sys
import time
from datetime import date, datetime, time as hora, timedelta, timezone
from pathlib import Path

import requests

import extractor
import ingesta
from ficha import CARPETA as FICHAS, cargar_todas

API_PRECIOS = "https://data-api.binance.vision/api/v3/klines"
FUENTE = "binance:USDT:1d"
MAX_POR_CORRIDA = 200           # L. predicciones por corrida
MAX_INTENTOS = 3                # L. intentos por consulta ante red o 5xx
TIMEOUT = 20

ESQUEMA = """
CREATE TABLE IF NOT EXISTS resultados (
    prediccion_id   INTEGER PRIMARY KEY REFERENCES predicciones(id),
    fecha           TEXT NOT NULL,
    fuente          TEXT NOT NULL,
    precio_inicio   REAL NOT NULL,      -- cierre del día del post
    precio_cierre   REAL NOT NULL,      -- cierre del día de resolución
    extremo         REAL,               -- máximo (precio_mayor) o mínimo (precio_menor) de la ventana
    ref_inicio      REAL,               -- supera_a: cierre de la referencia el día del post
    ref_cierre      REAL,               -- supera_a: cierre de la referencia el día de resolución
    acierto_cierre  INTEGER NOT NULL,
    acierto_toco    INTEGER,            -- NULL en supera_a
    trivial         INTEGER NOT NULL
);
CREATE TRIGGER IF NOT EXISTS resultados_sin_update BEFORE UPDATE ON resultados
    BEGIN SELECT RAISE(ABORT, 'resultados es inmutable'); END;
CREATE TRIGGER IF NOT EXISTS resultados_sin_delete BEFORE DELETE ON resultados
    BEGIN SELECT RAISE(ABORT, 'resultados es inmutable'); END;
"""


class SinDato(Exception):
    pass


# ---------------- Precios ----------------
def ms(d: date, fin_del_dia: bool = False) -> int:
    t = datetime.combine(d, hora.max if fin_del_dia else hora.min, tzinfo=timezone.utc)
    return int(t.timestamp() * 1000)


class Precios:
    """Velas diarias (UTC) por símbolo, con caché para no repetir consultas en la misma corrida."""

    def __init__(self, sesion=None, dormir=time.sleep):
        self.sesion = sesion or requests.Session()
        self.dormir = dormir
        self.cache: dict[tuple, dict[date, tuple[float, float, float, float]]] = {}

    def velas(self, token: str, desde: date, hasta: date) -> dict[date, tuple[float, float, float, float]]:
        """{día: (apertura, máximo, mínimo, cierre)} de desde a hasta, inclusive."""
        clave = (token, desde, hasta)
        if clave in self.cache:
            return self.cache[clave]
        params = {"symbol": f"{token}USDT", "interval": "1d", "limit": 1000,
                  "startTime": ms(desde), "endTime": ms(hasta, fin_del_dia=True)}
        for intento in range(1, MAX_INTENTOS + 1):
            try:
                r = self.sesion.get(API_PRECIOS, params=params, timeout=TIMEOUT)
            except requests.RequestException as e:
                if intento == MAX_INTENTOS:
                    raise RuntimeError(f"error de red consultando {token}: {e}") from e
                self.dormir(2 ** intento)
                continue
            if r.status_code in (429, 418) or 500 <= r.status_code < 600:
                if intento == MAX_INTENTOS:
                    break
                self.dormir(int(r.headers.get("Retry-After", 0) or 0) or 2 ** intento)
                continue
            break
        if r.status_code == 400 and "Invalid symbol" in r.text:
            raise SinDato(f"Binance no lista {token}USDT")
        if r.status_code != 200:
            raise RuntimeError(f"Binance respondió {r.status_code} para {token}: {r.text[:200]}")
        velas = {datetime.fromtimestamp(v[0] / 1000, timezone.utc).date():
                 (float(v[1]), float(v[2]), float(v[3]), float(v[4])) for v in r.json()}
        self.cache[clave] = velas
        return velas


def cierre(velas: dict, dia: date, token: str) -> float:
    if dia not in velas:
        raise SinDato(f"sin precio de {token} el {dia}")
    return velas[dia][3]


# ---------------- Reglas de resolución ----------------
def resolver(tipo: str, token: str, referencia, inicio: date, fin: date, precios: Precios) -> dict:
    """Aplica las reglas del encabezado. `referencia` es un precio o un ticker según el tipo."""
    velas = precios.velas(token, inicio, fin)
    p0, p1 = cierre(velas, inicio, token), cierre(velas, fin, token)
    r = {"precio_inicio": p0, "precio_cierre": p1, "extremo": None, "ref_inicio": None,
         "ref_cierre": None, "acierto_toco": None}
    if tipo == "supera_a":
        ref = precios.velas(referencia, inicio, fin)
        r0, r1 = cierre(ref, inicio, referencia), cierre(ref, fin, referencia)
        r.update(ref_inicio=r0, ref_cierre=r1, acierto_cierre=p1 / p0 > r1 / r0, trivial=False)
        return r
    ventana = [v for d, v in velas.items() if inicio < d <= fin]
    if len(ventana) < (fin - inicio).days:
        raise SinDato(f"faltan velas de {token} entre {inicio} y {fin}")
    if tipo == "precio_mayor":
        extremo = max(v[1] for v in ventana)
        r.update(extremo=extremo, acierto_cierre=p1 > referencia, acierto_toco=extremo > referencia,
                 trivial=p0 > referencia)
    elif tipo == "precio_menor":
        extremo = min(v[2] for v in ventana)
        r.update(extremo=extremo, acierto_cierre=p1 < referencia, acierto_toco=extremo < referencia,
                 trivial=p0 < referencia)
    else:
        raise ValueError(f"tipo desconocido: {tipo}")
    return r


# ---------------- Base ----------------
def conectar(ruta: Path = ingesta.DB) -> sqlite3.Connection:
    con = extractor.conectar(ruta)
    con.executescript(ESQUEMA)
    return con


def hoy_utc() -> date:
    return datetime.now(timezone.utc).date()


def vencidas(con: sqlite3.Connection, limite: int = MAX_POR_CORRIDA) -> list[tuple]:
    """Predicciones de la versión vigente del extractor cuyo día de resolución ya cerró en UTC."""
    return con.execute(
        "SELECT p.id, p.username, p.token, p.tipo, p.referencia, p.fecha_post, p.fecha_resolucion "
        "FROM predicciones p LEFT JOIN resultados r ON r.prediccion_id = p.id "
        "WHERE r.prediccion_id IS NULL AND p.version = ? AND p.fecha_resolucion < ? "
        "ORDER BY p.fecha_resolucion, p.id LIMIT ?",
        (extractor.VERSION, hoy_utc().isoformat(), limite)).fetchall()


def digesto_resultado(con: sqlite3.Connection, prediccion_id: int) -> str:
    fila = con.execute("SELECT * FROM resultados WHERE prediccion_id = ?", (prediccion_id,)).fetchone()
    return hashlib.sha256(json.dumps(fila).encode("utf-8")).hexdigest()


def verificar(con: sqlite3.Connection) -> list[str]:
    errores = extractor.verificar(con)
    for n, datos in con.execute("SELECT n, datos FROM eventos WHERE tipo = 'resultado' ORDER BY n"):
        d = json.loads(datos)
        if digesto_resultado(con, d["prediccion_id"]) != d["digesto"]:
            errores.append(f"evento {n}: el resultado de la predicción {d['prediccion_id']} fue alterado")
    return errores


def resolver_vencidas(con: sqlite3.Connection, precios: Precios) -> dict:
    lote = vencidas(con)
    ingesta.registrar(con, "resultados_inicio", vencidas=len(lote), fuente=FUENTE)
    resumen = {"resueltas": 0, "sin_dato": 0, "error": 0}
    for pid, username, token, tipo, referencia, fecha_post, fecha_res in lote:
        try:
            r = resolver(tipo, token, json.loads(referencia), date.fromisoformat(fecha_post),
                         date.fromisoformat(fecha_res), precios)
        except SinDato as e:
            resumen["sin_dato"] += 1
            ingesta.registrar(con, "resultado_sin_dato", prediccion_id=pid, motivo=str(e))
            print(f"#{pid} @{username} {token}: sin dato ({e})")
            continue
        except Exception as e:  # un fallo de la fuente no tumba el lote; se reintenta otro día
            resumen["error"] += 1
            ingesta.registrar(con, "resultado_error", prediccion_id=pid, error=str(e)[:300])
            print(f"#{pid} @{username} {token}: ERROR {e}")
            continue
        con.execute("INSERT INTO resultados VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (pid, ingesta.ahora(), FUENTE, r["precio_inicio"], r["precio_cierre"], r["extremo"],
                     r["ref_inicio"], r["ref_cierre"], int(r["acierto_cierre"]),
                     None if r["acierto_toco"] is None else int(r["acierto_toco"]), int(r["trivial"])))
        con.commit()
        ingesta.registrar(con, "resultado", prediccion_id=pid, acierto_cierre=bool(r["acierto_cierre"]),
                          acierto_toco=r["acierto_toco"], trivial=bool(r["trivial"]),
                          digesto=digesto_resultado(con, pid))
        resumen["resueltas"] += 1
        marca = "✓" if r["acierto_cierre"] else "✗"
        toco = "" if r["acierto_toco"] is None else f" · tocó: {'sí' if r['acierto_toco'] else 'no'}"
        print(f"#{pid} @{username} {token} {tipo}: {marca} al cierre{toco}"
              f"{' · trivial' if r['trivial'] else ''}")
    ingesta.registrar(con, "resultados_fin", **resumen)
    return resumen


def historial(con: sqlite3.Connection) -> list[dict]:
    """Aciertos por cuenta. Las triviales (ya cumplidas al publicar) no cuentan."""
    filas = con.execute(
        "SELECT p.username, COUNT(*), SUM(r.trivial), "
        "SUM(CASE WHEN r.trivial = 0 THEN r.acierto_cierre END), "
        "SUM(CASE WHEN r.trivial = 0 AND r.acierto_toco IS NOT NULL THEN 1 END), "
        "SUM(CASE WHEN r.trivial = 0 THEN r.acierto_toco END) "
        "FROM resultados r JOIN predicciones p ON p.id = r.prediccion_id "
        "GROUP BY p.username ORDER BY p.username").fetchall()
    return [{"username": u, "resueltas": n, "triviales": t or 0, "evaluadas": n - (t or 0),
             "aciertos_cierre": c or 0, "con_toco": nt or 0, "aciertos_toco": at or 0}
            for u, n, t, c, nt, at in filas]


def imprimir_historial(con: sqlite3.Connection) -> None:
    filas = historial(con)
    if not filas:
        print("Aún no hay predicciones resueltas.")
        return
    pct = lambda a, b: f"{a}/{b} ({a / b:.0%})" if b else "—"
    print(f"\n{'Cuenta':<22}{'Evaluadas':>10}  {'Al cierre':<16}{'Tocó objetivo':<16}Triviales")
    print("-" * 78)
    for f in filas:
        print(f"@{f['username']:<21}{f['evaluadas']:>10}  {pct(f['aciertos_cierre'], f['evaluadas']):<16}"
              f"{pct(f['aciertos_toco'], f['con_toco']):<16}{f['triviales']}")
    print("\nCon pocas predicciones el porcentaje dice poco: espera a tener al menos 20 por cuenta.")


# ---------------- Tus fichas ----------------
def resolver_fichas(precios: Precios, confirmar=input) -> int:
    """Resuelve al cierre las fichas vencidas. S. Nada se escribe sin tu aprobación."""
    propuestas = []
    for f in cargar_todas():
        p = f["prediccion"]
        if f.get("resultado") is not None or date.fromisoformat(p["fecha_resolucion"]) >= hoy_utc():
            continue
        try:
            r = resolver(p["tipo"], f["token"], p["referencia"], date.fromisoformat(f["fecha"]),
                         date.fromisoformat(p["fecha_resolucion"]), precios)
        except (SinDato, RuntimeError) as e:
            print(f"{f['id']}: no se pudo resolver ({e})")
            continue
        propuestas.append((f, r))
        print(f"{f['id']}: {p['enunciado']} para el {p['fecha_resolucion']} -> "
              f"{'✓ se cumplió' if r['acierto_cierre'] else '✗ no se cumplió'} "
              f"(cierre {r['precio_cierre']:g}, con {p['probabilidad']:.0%} de confianza)")
    if not propuestas:
        print("No hay fichas vencidas por resolver.")
        return 0
    if confirmar(f"\n¿Guardar estos {len(propuestas)} resultados en tus fichas? (s/n): ").strip().lower() != "s":
        print("No se guardó nada.")
        return 0
    for f, r in propuestas:
        f["resultado"] = bool(r["acierto_cierre"])
        f["resultado_detalle"] = {"fuente": FUENTE, "resuelto": ingesta.ahora(),
                                  **{k: v for k, v in r.items() if v is not None}}
        (FICHAS / f"{f['id']}.json").write_text(json.dumps(f, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Guardados {len(propuestas)} resultados. Escribe tu reflexión en cada ficha.")
    return len(propuestas)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Validador de resultados de Contrapeso.")
    grupo = ap.add_mutually_exclusive_group()
    grupo.add_argument("--historial", action="store_true", help="aciertos por cuenta")
    grupo.add_argument("--fichas", action="store_true", help="resolver tus fichas vencidas")
    grupo.add_argument("--verificar", action="store_true", help="verificar la integridad")
    args = ap.parse_args(argv)
    if args.fichas:
        resolver_fichas(Precios())
        return 0
    con = conectar()
    try:
        if args.verificar:
            errores = verificar(con)
            if errores:
                print("La base NO es íntegra:\n  - " + "\n  - ".join(errores))
                return 1
            print("Cadena, predicciones y resultados íntegros.")
            return 0
        if args.historial:
            imprimir_historial(con)
            return 0
        r = resolver_vencidas(con, Precios())
        print(f"\nResueltas: {r['resueltas']} · sin dato: {r['sin_dato']} · errores: {r['error']}")
        return 1 if r["error"] else 0
    finally:
        con.close()


if __name__ == "__main__":
    sys.exit(main())
