"""
Contrapeso · Ingesta de posts de X hacia una base inmutable.

Uso:
    python ingesta.py                   # trae los posts nuevos de todas las cuentas
    python ingesta.py --cuenta NOMBRE   # solo una cuenta de cuentas.yaml
    python ingesta.py --gasto           # gasto del mes contra el tope (no llama a X)
    python ingesta.py --verificar       # recalcula la cadena de hashes y revisa las anclas
    python ingesta.py --anclar          # sella el último hash fuera de tu control (OpenTimestamps)

Garantías (en CÓDIGO y en la BASE, no en la disciplina):
    - tweets_crudos y eventos tienen triggers que abortan cualquier UPDATE o DELETE.
    - Cada corrida, alta, retiro o cambio de razón de una cuenta queda en `eventos`,
      encadenado por SHA-256; --verificar detecta cualquier alteración.
    - El tope mensual se revisa ANTES de cada llamada con el peor caso posible.
    - Una cuenta nueva solo trae sus posts más recientes, nunca su historial.
    - Si una cuenta falla, se registra el error y se sigue con las demás.
    - La cadena sola no detecta que se borren los últimos eventos ni que se reescriba completa
      con hashes recalculados. --anclar guarda el último hash en anclas/ y, si está instalado
      opentimestamps-client, lo sella en Bitcoin: una prueba que ni tú puedes modificar.

Variables (.env o exportadas):
    X_BEARER               token Bearer de la API de X (obligatorio para ingerir)
    X_PRECIO_POST_USD      precio por post leído (obligatorio; revísalo en la consola de X)
    X_PRECIO_USUARIO_USD   precio por búsqueda de usuario (obligatorio)
    CONTRAPESO_TOPE_USD    tope de gasto mensual de la ingesta (5 por defecto)
"""
import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
import yaml
from dotenv import load_dotenv

BASE = Path(__file__).parent
load_dotenv(BASE / ".env")

API = "https://api.x.com/2"
DB = BASE / "ingesta.db"
CUENTAS = BASE / "cuentas.yaml"
MAX_POR_CUENTA = 20           # L. posts máximos por cuenta en cada corrida
MAX_ESPERA_LIMITE = 900       # L. segundos que se acepta esperar a que X libere el límite
MAX_INTENTOS = 3              # I. intentos por llamada ante fallas de red o errores 5xx
TIMEOUT = 30                  # segundos por petición HTTP
TOPE_PREDETERMINADO = 5.00
CAMPOS_TWEET = ("created_at,author_id,conversation_id,lang,public_metrics,referenced_tweets,entities,"
                "note_tweet")  # note_tweet: texto completo de los posts largos
GENESIS = "0" * 64

ESQUEMA = """
CREATE TABLE IF NOT EXISTS tweets_crudos (
    id          INTEGER PRIMARY KEY,      -- id del post en X
    user_id     TEXT NOT NULL,
    username    TEXT NOT NULL,
    creado      TEXT,
    texto       TEXT NOT NULL,
    crudo       TEXT NOT NULL,            -- JSON tal como lo entregó X
    ingestado   TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS eventos (
    n           INTEGER PRIMARY KEY,
    fecha       TEXT NOT NULL,
    tipo        TEXT NOT NULL,
    datos       TEXT NOT NULL,
    hash_prev   TEXT NOT NULL,
    hash        TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS cuentas (
    username    TEXT PRIMARY KEY,         -- en minúsculas
    user_id     TEXT,
    razon       TEXT NOT NULL,
    activa      INTEGER NOT NULL DEFAULT 1
);
CREATE TRIGGER IF NOT EXISTS tweets_sin_update BEFORE UPDATE ON tweets_crudos
    BEGIN SELECT RAISE(ABORT, 'tweets_crudos es inmutable'); END;
CREATE TRIGGER IF NOT EXISTS tweets_sin_delete BEFORE DELETE ON tweets_crudos
    BEGIN SELECT RAISE(ABORT, 'tweets_crudos es inmutable'); END;
CREATE TRIGGER IF NOT EXISTS eventos_sin_update BEFORE UPDATE ON eventos
    BEGIN SELECT RAISE(ABORT, 'eventos es inmutable'); END;
CREATE TRIGGER IF NOT EXISTS eventos_sin_delete BEFORE DELETE ON eventos
    BEGIN SELECT RAISE(ABORT, 'eventos es inmutable'); END;
"""


class ConfigInvalida(Exception):
    pass


class TopeAlcanzado(Exception):
    pass


# ---------------- Base de datos y bitácora encadenada ----------------
def conectar(ruta: Path = DB) -> sqlite3.Connection:
    con = sqlite3.connect(ruta)
    con.executescript(ESQUEMA)
    return con


def ahora() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def calcular_hash(n: int, fecha: str, tipo: str, datos: str, hash_prev: str) -> str:
    contenido = json.dumps([n, fecha, tipo, datos, hash_prev], ensure_ascii=False)
    return hashlib.sha256(contenido.encode("utf-8")).hexdigest()


def registrar(con: sqlite3.Connection, tipo: str, **datos) -> None:
    """Agrega un evento a la cadena. Hace commit para que nada quede a medias."""
    fila = con.execute("SELECT n, hash FROM eventos ORDER BY n DESC LIMIT 1").fetchone()
    n, prev = (fila[0] + 1, fila[1]) if fila else (1, GENESIS)
    fecha = ahora()
    texto = json.dumps(datos, ensure_ascii=False, sort_keys=True)
    con.execute("INSERT INTO eventos VALUES (?, ?, ?, ?, ?, ?)",
                (n, fecha, tipo, texto, prev, calcular_hash(n, fecha, tipo, texto, prev)))
    con.commit()


def digesto_tweets(con: sqlite3.Connection, ids: list[int]) -> str:
    """Huella de los posts de una corrida: si alguien altera el archivo .db a mano, no cuadra."""
    h = hashlib.sha256()
    for i in sorted(ids):
        fila = con.execute("SELECT id, crudo FROM tweets_crudos WHERE id = ?", (i,)).fetchone()
        h.update(json.dumps(fila, ensure_ascii=False).encode("utf-8") if fila else b"FALTA")
    return h.hexdigest()


def verificar(con: sqlite3.Connection) -> list[str]:
    errores, prev, esperado = [], GENESIS, 1
    for n, fecha, tipo, datos, hash_prev, h in con.execute("SELECT * FROM eventos ORDER BY n"):
        if n != esperado:
            errores.append(f"evento {n}: falta el evento {esperado}")
        if hash_prev != prev:
            errores.append(f"evento {n}: hash_prev no coincide con el evento anterior")
        if calcular_hash(n, fecha, tipo, datos, hash_prev) != h:
            errores.append(f"evento {n}: su contenido fue alterado")
        if tipo == "cuenta_ingerida":
            d = json.loads(datos)
            if digesto_tweets(con, d["ids_nuevos"]) != d["digesto"]:
                errores.append(f"evento {n}: los posts de @{d['username']} fueron alterados")
        prev, esperado = h, n + 1
    return errores + verificar_anclas(con)


# ---------------- Anclaje externo ----------------
def carpeta_anclas(con: sqlite3.Connection) -> Path | None:
    """anclas/ junto al archivo de la base (así cada base tiene las suyas)."""
    archivo = con.execute("PRAGMA database_list").fetchone()[2]
    return Path(archivo).parent / "anclas" if archivo else None


def comando_ots() -> str | None:
    """`ots` del entorno virtual (cron no lo activa) o del PATH."""
    local = Path(sys.executable).parent / "ots"
    return str(local) if local.exists() else shutil.which("ots")


def leer_ancla(ruta: Path) -> tuple[int, str]:
    partes = dict(p.split("=", 1) for p in ruta.read_text(encoding="utf-8").strip().split("|")[1:])
    return int(partes["evento"]), partes["hash"]


def anclar(con: sqlite3.Connection, ejecutar=subprocess.run) -> Path:
    """Escribe el último hash en anclas/ancla_<n>.txt y lo sella con OpenTimestamps si se puede.
    El sello prueba que en esa fecha la bitácora ya llegaba a ese hash."""
    fila = con.execute("SELECT n, hash FROM eventos ORDER BY n DESC LIMIT 1").fetchone()
    if not fila:
        raise ConfigInvalida("La bitácora está vacía: no hay nada que anclar.")
    n, h = fila
    carpeta = carpeta_anclas(con)
    carpeta.mkdir(exist_ok=True)
    ruta = carpeta / f"ancla_{n:06d}.txt"
    if ruta.exists():
        raise ConfigInvalida(f"El evento {n} ya está anclado en {ruta.name}.")
    ruta.write_text(f"contrapeso|evento={n}|hash={h}|fecha={ahora()}\n", encoding="utf-8")
    print(f"Ancla escrita: {ruta}")
    sellado, ots = False, comando_ots()
    if ots:
        try:
            r = ejecutar([ots, "stamp", str(ruta)], capture_output=True, text=True, timeout=120)
            sellado = r.returncode == 0
            detalle = (r.stderr or r.stdout).strip()[:300]
        except (OSError, subprocess.TimeoutExpired) as e:
            detalle = str(e)
        print("Sellada con OpenTimestamps. Bitcoin la confirma en unas horas; luego corre --verificar."
              if sellado else f"OpenTimestamps falló: {detalle}. El ancla queda solo local.")
    else:
        print("OpenTimestamps no está instalado: el ancla queda solo local.\n"
              "Instálalo con: pip install opentimestamps-client")
    registrar(con, "ancla", evento_anclado=n, hash=h, archivo=ruta.name, sellado_ots=sellado)
    return ruta


def verificar_anclas(con: sqlite3.Connection) -> list[str]:
    """Lo que la cadena sola no ve: bitácora truncada, reescrita, o anclas borradas."""
    carpeta, errores = carpeta_anclas(con), []
    if not carpeta:
        return errores
    for ruta in sorted(carpeta.glob("ancla_*.txt")):
        try:
            n, h = leer_ancla(ruta)
        except (ValueError, KeyError, OSError):
            errores.append(f"{ruta.name}: archivo de ancla ilegible")
            continue
        fila = con.execute("SELECT hash FROM eventos WHERE n = ?", (n,)).fetchone()
        if fila is None:
            errores.append(f"{ruta.name}: el evento {n} ya no existe (bitácora truncada)")
        elif fila[0] != h:
            errores.append(f"{ruta.name}: el hash del evento {n} cambió (bitácora reescrita)")
    for n, datos in con.execute("SELECT n, datos FROM eventos WHERE tipo = 'ancla'"):
        if not (carpeta / json.loads(datos)["archivo"]).exists():
            errores.append(f"evento {n}: falta el archivo {json.loads(datos)['archivo']} en anclas/")
    return errores


def estado_sellos(con: sqlite3.Connection, ejecutar=subprocess.run) -> list[str]:
    """Estado en Bitcoin de cada sello .ots. No poder verificar no es lo mismo que alteración."""
    carpeta, ots, lineas = carpeta_anclas(con), comando_ots(), []
    for ruta in sorted(carpeta.glob("ancla_*.txt")) if carpeta and carpeta.exists() else []:
        prueba = ruta.with_name(ruta.name + ".ots")
        if not prueba.exists():
            lineas.append(f"{ruta.name}: sin sello externo (solo local)")
            continue
        if not ots:
            lineas.append(f"{ruta.name}: tiene sello .ots; instala opentimestamps-client para verificarlo")
            continue
        try:
            ejecutar([ots, "upgrade", str(prueba)], capture_output=True, text=True, timeout=120)
            r = ejecutar([ots, "verify", str(prueba)], capture_output=True, text=True, timeout=120)
            salida = (r.stdout + r.stderr).strip()
        except (OSError, subprocess.TimeoutExpired) as e:
            r, salida = None, str(e)
        if r is not None and r.returncode == 0 and "Success" in salida:
            lineas.append(f"{ruta.name}: confirmada en Bitcoin")
        elif "pending" in salida.lower():
            lineas.append(f"{ruta.name}: sello pendiente de confirmación en Bitcoin")
        else:
            ultima = salida.splitlines()[-1][:120] if salida else "sin salida"
            lineas.append(f"{ruta.name}: no se pudo verificar en Bitcoin ({ultima})")
    return lineas


# ---------------- Configuración ----------------
def cargar_cuentas(ruta: Path = CUENTAS) -> dict[str, str]:
    """{username_minúsculas: razon}. Se niega si alguna cuenta no explica por qué está."""
    datos = yaml.safe_load(ruta.read_text(encoding="utf-8")) or {}
    cuentas, problemas = {}, []
    for i, c in enumerate(datos.get("cuentas") or [], 1):
        usuario = str((c or {}).get("username") or "").strip().lstrip("@")
        razon = str((c or {}).get("razon") or "").strip()
        if not usuario:
            problemas.append(f"la cuenta #{i} no tiene username")
        elif not razon:
            problemas.append(f"@{usuario} no tiene razon")
        elif usuario.lower() in cuentas:
            problemas.append(f"@{usuario} está repetida")
        else:
            cuentas[usuario.lower()] = razon
    if problemas:
        raise ConfigInvalida("cuentas.yaml no es válido:\n  - " + "\n  - ".join(problemas))
    if not cuentas:
        raise ConfigInvalida("cuentas.yaml no tiene cuentas.")
    return cuentas


def leer_precios() -> tuple[float, float]:
    valores = []
    for var in ("X_PRECIO_POST_USD", "X_PRECIO_USUARIO_USD"):
        crudo = os.getenv(var, "").strip()
        try:
            v = float(crudo)
        except ValueError:
            raise ConfigInvalida(f"Define {var} con el precio vigente en la consola de X.")
        if v < 0:
            raise ConfigInvalida(f"{var} debe ser mayor o igual a 0.")
        valores.append(v)
    return valores[0], valores[1]


def leer_tope() -> float:
    try:
        tope = float(os.getenv("CONTRAPESO_TOPE_USD") or TOPE_PREDETERMINADO)
    except ValueError:
        raise ConfigInvalida("CONTRAPESO_TOPE_USD debe ser un número.")
    if tope < 0:
        raise ConfigInvalida("CONTRAPESO_TOPE_USD debe ser mayor o igual a 0.")
    return tope


def sincronizar_cuentas(con: sqlite3.Connection, cuentas: dict[str, str]) -> None:
    """Toda alta, retiro o cambio de razón respecto a la última corrida queda en la bitácora."""
    previas = {u: (r, a) for u, r, a in con.execute("SELECT username, razon, activa FROM cuentas")}
    for usuario, razon in cuentas.items():
        if usuario not in previas:
            con.execute("INSERT INTO cuentas (username, razon) VALUES (?, ?)", (usuario, razon))
            registrar(con, "cuenta_alta", username=usuario, razon=razon)
        elif not previas[usuario][1]:
            con.execute("UPDATE cuentas SET activa = 1, razon = ? WHERE username = ?", (razon, usuario))
            registrar(con, "cuenta_reactivada", username=usuario, razon=razon)
        elif previas[usuario][0] != razon:
            con.execute("UPDATE cuentas SET razon = ? WHERE username = ?", (razon, usuario))
            registrar(con, "cuenta_razon_cambiada", username=usuario,
                      razon_anterior=previas[usuario][0], razon=razon)
    for usuario, (razon, activa) in previas.items():
        if activa and usuario not in cuentas:
            con.execute("UPDATE cuentas SET activa = 0 WHERE username = ?", (usuario,))
            registrar(con, "cuenta_retirada", username=usuario, razon=razon)


# ---------------- Gasto ----------------
def mes_actual() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m")


def gastado_este_mes(con: sqlite3.Connection) -> float:
    total = 0.0
    for fecha, datos in con.execute("SELECT fecha, datos FROM eventos WHERE tipo = 'llamada_x'"):
        if fecha.startswith(mes_actual()):
            total += json.loads(datos)["usd"]
    return total


class ClienteX:
    """Cada llamada revisa el tope con el peor caso ANTES de salir y registra su costo después."""

    def __init__(self, con, token, precio_post, precio_usuario, tope, sesion=None, dormir=time.sleep):
        self.con, self.tope = con, tope
        self.precio_post, self.precio_usuario = precio_post, precio_usuario
        self.sesion = sesion or requests.Session()
        self.sesion.headers["Authorization"] = f"Bearer {token}"
        self.dormir = dormir

    def _get(self, ruta: str, params: dict, peor_caso: float, unidades) -> dict:
        gastado = gastado_este_mes(self.con)
        if gastado + peor_caso > self.tope:
            raise TopeAlcanzado(f"gastado ${gastado:.4f} + peor caso ${peor_caso:.4f} "
                                f"superaría el tope de ${self.tope:.2f}")
        esperado_limite = False
        for intento in range(1, MAX_INTENTOS + 1):
            ultimo = intento == MAX_INTENTOS
            try:
                r = self.sesion.get(f"{API}{ruta}", params=params, timeout=TIMEOUT)
            except requests.RequestException as e:
                if ultimo:
                    raise RuntimeError(f"error de red en {ruta}: {e}") from e
                print(f"  Error de red ({e}); reintento en {2 ** intento} s.")
                self.dormir(2 ** intento)
                continue
            if r.status_code == 429 and not esperado_limite and not ultimo:
                # X no usa Retry-After: x-rate-limit-reset es el epoch en que se libera el límite.
                reset = int(r.headers.get("x-rate-limit-reset", 0) or 0)
                espera = max(reset - time.time(), 0) + 1
                if espera > MAX_ESPERA_LIMITE:
                    break
                print(f"  Límite de tasa de X: espero {espera:.0f} s.")
                self.dormir(espera)
                esperado_limite = True  # una sola espera larga por llamada
                continue
            if 500 <= r.status_code < 600 and not ultimo:
                print(f"  X respondió {r.status_code}; reintento en {2 ** intento} s.")
                self.dormir(2 ** intento)
                continue
            break  # 200, otro 4xx (no tiene caso reintentar) o último intento
        if r.status_code != 200:
            raise RuntimeError(f"X respondió {r.status_code} en {ruta}: {r.text[:300]}")
        cuerpo = r.json()
        usd = unidades(cuerpo)
        registrar(self.con, "llamada_x", ruta=ruta.split("/")[1], usd=round(usd, 6))
        return cuerpo

    def user_ids(self, usernames: list[str]) -> tuple[dict[str, str], dict[str, str]]:
        """Resuelve hasta 100 cuentas en una sola llamada: ({username: id}, {username: error})."""
        cuerpo = self._get("/users/by", {"usernames": ",".join(usernames)},
                           len(usernames) * self.precio_usuario,
                           lambda c: len(c.get("data", [])) * self.precio_usuario)
        ids = {u["username"].lower(): u["id"] for u in cuerpo.get("data", [])}
        errores = {u: "X no la encontró o está suspendida" for u in usernames if u not in ids}
        return ids, errores

    def posts(self, user_id: str, since_id: int | None, limite: int) -> list[dict]:
        """Hasta `limite` posts. Sin since_id (cuenta nueva) solo una página: lo más reciente."""
        traidos, token = [], None
        while len(traidos) < limite:
            pedir = max(5, min(100, limite - len(traidos)))  # X exige max_results entre 5 y 100
            params = {"max_results": pedir, "tweet.fields": CAMPOS_TWEET, "exclude": "retweets"}
            if since_id:
                params["since_id"] = since_id
            if token:
                params["pagination_token"] = token
            cuerpo = self._get(f"/users/{user_id}/tweets", params, pedir * self.precio_post,
                               lambda c: c.get("meta", {}).get("result_count", 0) * self.precio_post)
            traidos += cuerpo.get("data", [])
            token = cuerpo.get("meta", {}).get("next_token")
            if not token or not since_id:
                break
        return traidos[:limite]


# ---------------- Ingesta ----------------
def ingerir_cuenta(con: sqlite3.Connection, x: ClienteX, username: str) -> int:
    user_id = con.execute("SELECT user_id FROM cuentas WHERE username = ?", (username,)).fetchone()[0]
    since_id = con.execute("SELECT MAX(id) FROM tweets_crudos WHERE user_id = ?", (user_id,)).fetchone()[0]
    nuevos = []
    for p in x.posts(user_id, since_id, MAX_POR_CUENTA):
        cur = con.execute("INSERT OR IGNORE INTO tweets_crudos VALUES (?, ?, ?, ?, ?, ?, ?)",
                          (int(p["id"]), user_id, username, p.get("created_at"), p.get("text", ""),
                           json.dumps(p, ensure_ascii=False, sort_keys=True), ahora()))
        if cur.rowcount:
            nuevos.append(int(p["id"]))
    con.commit()
    registrar(con, "cuenta_ingerida", username=username, ids_nuevos=nuevos,
              digesto=digesto_tweets(con, nuevos), primera_vez=since_id is None)
    return len(nuevos)


def resolver_ids(con: sqlite3.Connection, x: ClienteX, usernames: list[str]) -> dict[str, str]:
    """Busca en lote los user_id que faltan. Devuelve {username: error} de las que no se resolvieron."""
    faltan = [u for u in usernames
              if not con.execute("SELECT user_id FROM cuentas WHERE username = ?", (u,)).fetchone()[0]]
    errores = {}
    for i in range(0, len(faltan), 100):  # X acepta hasta 100 usernames por llamada
        lote = faltan[i:i + 100]
        try:
            ids, no_encontradas = x.user_ids(lote)
        except TopeAlcanzado:
            raise
        except Exception as e:
            ids, no_encontradas = {}, {u: str(e) for u in lote}
        for u, uid in ids.items():
            con.execute("UPDATE cuentas SET user_id = ? WHERE username = ?", (uid, u))
        con.commit()
        errores.update(no_encontradas)
    return errores


def correr(con: sqlite3.Connection, x: ClienteX, cuentas: dict[str, str], solo: str | None = None) -> dict:
    sincronizar_cuentas(con, cuentas)
    objetivo = [solo] if solo else list(cuentas)
    registrar(con, "corrida_inicio", cuentas=objetivo)
    resumen = {"nuevos": {}, "errores": {}, "tope": None}
    try:
        sin_id = resolver_ids(con, x, objetivo)
    except TopeAlcanzado as e:
        resumen["tope"], sin_id, objetivo = str(e), {}, []
        print(f"Tope de gasto alcanzado, se detiene la corrida: {e}")
    for usuario, error in sin_id.items():
        resumen["errores"][usuario] = error
        registrar(con, "cuenta_error", username=usuario, error=error[:500])
        print(f"@{usuario}: ERROR {error}")
    for usuario in [u for u in objetivo if u not in sin_id]:
        try:
            resumen["nuevos"][usuario] = n = ingerir_cuenta(con, x, usuario)
            print(f"@{usuario}: {n} posts nuevos")
        except TopeAlcanzado as e:
            resumen["tope"] = str(e)
            print(f"Tope de gasto alcanzado, se detiene la corrida: {e}")
            break
        except Exception as e:  # una cuenta caída no tumba a las demás
            con.rollback()
            resumen["errores"][usuario] = str(e)
            registrar(con, "cuenta_error", username=usuario, error=str(e)[:500])
            print(f"@{usuario}: ERROR {e}")
    registrar(con, "corrida_fin", nuevos=resumen["nuevos"], errores=list(resumen["errores"]),
              tope=resumen["tope"], gastado_mes=round(gastado_este_mes(con), 6))
    return resumen


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Ingesta de posts de X para Contrapeso.")
    grupo = ap.add_mutually_exclusive_group()
    grupo.add_argument("--cuenta", help="ingerir solo esta cuenta de cuentas.yaml")
    grupo.add_argument("--gasto", action="store_true", help="mostrar el gasto del mes")
    grupo.add_argument("--verificar", action="store_true", help="verificar la cadena y las anclas")
    grupo.add_argument("--anclar", action="store_true", help="sellar el último hash fuera de tu control")
    args = ap.parse_args(argv)
    con = conectar()
    try:
        if args.verificar:
            errores = verificar(con)
            total = con.execute("SELECT COUNT(*) FROM eventos").fetchone()[0]
            if errores:
                print("La cadena NO es íntegra:\n  - " + "\n  - ".join(errores))
                return 1
            print(f"Cadena íntegra: {total} eventos verificados.")
            for linea in estado_sellos(con) or ["Sin anclas todavía (corre --anclar)."]:
                print(f"  · {linea}")
            return 0
        if args.anclar:
            anclar(con)
            return 0
        tope = leer_tope()
        if args.gasto:
            g = gastado_este_mes(con)
            print(f"Gasto de la ingesta en {mes_actual()}: ${g:.4f} de ${tope:.2f} "
                  f"(quedan ${max(tope - g, 0):.4f}).")
            return 0
        cuentas = cargar_cuentas()
        solo = args.cuenta.lstrip("@").lower() if args.cuenta else None
        if solo and solo not in cuentas:
            raise ConfigInvalida(f"@{solo} no está en cuentas.yaml.")
        token = os.getenv("X_BEARER", "").strip()
        if not token:
            raise ConfigInvalida("Define X_BEARER con tu token de la API de X.")
        x = ClienteX(con, token, *leer_precios(), tope)
        resumen = correr(con, x, cuentas, solo)
        return 1 if resumen["errores"] or resumen["tope"] else 0
    except ConfigInvalida as e:
        print(e, file=sys.stderr)
        return 2
    finally:
        con.close()


if __name__ == "__main__":
    sys.exit(main())
