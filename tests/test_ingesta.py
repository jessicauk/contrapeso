import json
import sqlite3

import requests
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from pathlib import Path
import tempfile

import ingesta


class Respuesta:
    def __init__(self, status, cuerpo=None, headers=None):
        self.status_code, self._cuerpo, self.headers = status, cuerpo or {}, headers or {}
        self.text = json.dumps(self._cuerpo)

    def json(self):
        return self._cuerpo


class XFalso:
    """API simulada: usuarios por nombre y posts por user_id, más fallas inyectables."""

    def __init__(self):
        self.headers, self.llamadas, self.fallas = {}, [], {}
        self.usuarios = {"ana": "1", "beto": "2"}
        self.posts = {"1": [{"id": str(i), "text": f"post {i}"} for i in (105, 104, 103)],
                      "2": [{"id": "205", "text": "hola"}]}

    def get(self, url, params=None, timeout=None):
        self.llamadas.append((url, dict(params or {})))
        for clave, respuestas in self.fallas.items():
            if clave in url and respuestas:
                r = respuestas.pop(0)
                if isinstance(r, Exception):
                    raise r
                return r
        if url.endswith("/users/by"):
            nombres = params["usernames"].split(",")
            return Respuesta(200, {"data": [{"id": self.usuarios[n], "username": n.capitalize()}
                                            for n in nombres if n in self.usuarios]})
        uid = url.split("/users/")[1].split("/")[0]
        datos = [p for p in self.posts[uid] if int(p["id"]) > int(params.get("since_id", 0))]
        datos = datos[: params["max_results"]]
        return Respuesta(200, {"data": datos, "meta": {"result_count": len(datos)}})


class IngestaTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.con = ingesta.conectar(Path(self.tmp.name) / "t.db")
        self.addCleanup(self.con.close)
        self.api = XFalso()
        self.cuentas = {"ana": "trader verificada", "beto": "analista"}

    def cliente(self, tope=5.0):
        return ingesta.ClienteX(self.con, "tok", 0.01, 0.02, tope, sesion=self.api, dormir=lambda s: None)

    def contar(self):
        return self.con.execute("SELECT COUNT(*) FROM tweets_crudos").fetchone()[0]

    def test_guarda_y_no_duplica(self):
        r = ingesta.correr(self.con, self.cliente(), self.cuentas)
        self.assertEqual(r["nuevos"], {"ana": 3, "beto": 1})
        self.api.posts["1"].insert(0, {"id": "106", "text": "nuevo"})
        r = ingesta.correr(self.con, self.cliente(), self.cuentas)
        self.assertEqual(r["nuevos"], {"ana": 1, "beto": 0})
        self.assertEqual(self.contar(), 5)
        # la segunda corrida usa since_id y no vuelve a buscar el user_id
        ultima = [p for u, p in self.api.llamadas if "/users/1/tweets" in u][-1]
        self.assertEqual(ultima["since_id"], 105)
        busquedas = [p for u, p in self.api.llamadas if u.endswith("/users/by")]
        self.assertEqual(busquedas, [{"usernames": "ana,beto"}])  # una sola llamada, en lote
        self.assertEqual(ingesta.verificar(self.con), [])

    def test_cuenta_que_falla_no_detiene_a_las_demas(self):
        self.api.fallas["/users/1/tweets"] = [Respuesta(403, {"title": "prohibido"})]
        r = ingesta.correr(self.con, self.cliente(), self.cuentas)
        self.assertIn("ana", r["errores"])
        self.assertEqual(r["nuevos"], {"beto": 1})
        self.assertEqual(ingesta.verificar(self.con), [])

    def test_limite_de_tasa_usa_x_rate_limit_reset(self):
        esperas = []
        reset = str(int(time.time()) + 10)
        self.api.fallas["/users/2/tweets"] = [Respuesta(429, headers={"x-rate-limit-reset": reset})]
        x = ingesta.ClienteX(self.con, "tok", 0.01, 0.02, 5, sesion=self.api, dormir=esperas.append)
        r = ingesta.correr(self.con, x, self.cuentas)
        self.assertEqual(r["nuevos"]["beto"], 1)
        self.assertEqual(len(esperas), 1)
        self.assertTrue(5 < esperas[0] <= 12)

    def test_reintenta_red_y_5xx_con_espera_creciente(self):
        esperas = []
        self.api.fallas["/users/2/tweets"] = [requests.ConnectionError("sin red"), Respuesta(503)]
        x = ingesta.ClienteX(self.con, "tok", 0.01, 0.02, 5, sesion=self.api, dormir=esperas.append)
        r = ingesta.correr(self.con, x, self.cuentas)
        self.assertEqual(r["nuevos"]["beto"], 1)
        self.assertEqual(esperas, [2, 4])

    def test_5xx_persistente_falla_solo_esa_cuenta(self):
        self.api.fallas["/users/1/tweets"] = [Respuesta(500)] * ingesta.MAX_INTENTOS
        r = ingesta.correr(self.con, self.cliente(), self.cuentas)
        self.assertIn("500", r["errores"]["ana"])
        self.assertEqual(r["nuevos"], {"beto": 1})

    def test_cuenta_inexistente_no_detiene_a_las_demas(self):
        r = ingesta.correr(self.con, self.cliente(), dict(self.cuentas, fantasma="no existe"))
        self.assertIn("fantasma", r["errores"])
        self.assertEqual(r["nuevos"], {"ana": 3, "beto": 1})
        # solo se cobran las cuentas encontradas
        self.assertAlmostEqual(ingesta.gastado_este_mes(self.con), 2 * 0.02 + 4 * 0.01)

    def test_inmutabilidad(self):
        ingesta.correr(self.con, self.cliente(), self.cuentas)
        for sql in ("DELETE FROM tweets_crudos", "UPDATE tweets_crudos SET texto = 'x'",
                    "DELETE FROM eventos", "UPDATE eventos SET datos = '{}'"):
            with self.assertRaises(sqlite3.IntegrityError, msg=sql):
                self.con.execute(sql)

    def test_verificar_detecta_alteracion(self):
        ingesta.correr(self.con, self.cliente(), self.cuentas)
        self.con.execute("DROP TRIGGER eventos_sin_update")
        self.con.execute("UPDATE eventos SET datos = '{\"usd\": 0}' WHERE n = 3")
        self.assertTrue(any("alterado" in e for e in ingesta.verificar(self.con)))

    def test_verificar_detecta_post_alterado(self):
        ingesta.correr(self.con, self.cliente(), self.cuentas)
        self.con.execute("DROP TRIGGER tweets_sin_update")
        self.con.execute("UPDATE tweets_crudos SET crudo = '{}' WHERE id = 104")
        self.assertTrue(any("@ana" in e for e in ingesta.verificar(self.con)))

    def test_tope_se_revisa_antes_con_peor_caso(self):
        # user lookup 0.02 + peor caso 20 posts * 0.01 = 0.22 > 0.10: no debe salir ninguna llamada de posts
        r = ingesta.correr(self.con, self.cliente(tope=0.10), self.cuentas)
        self.assertIsNotNone(r["tope"])
        self.assertFalse(any("/tweets" in u for u, _ in self.api.llamadas))
        self.assertLessEqual(ingesta.gastado_este_mes(self.con), 0.10)

    def test_cuenta_nueva_solo_trae_lo_reciente(self):
        self.api.posts["1"] = [{"id": str(i), "text": "x"} for i in range(500, 400, -1)]
        r = ingesta.correr(self.con, self.cliente(), self.cuentas, solo="ana")
        self.assertEqual(r["nuevos"], {"ana": ingesta.MAX_POR_CUENTA})
        self.assertEqual(sum("/tweets" in u for u, _ in self.api.llamadas), 1)

    def test_altas_y_retiros_en_bitacora(self):
        ingesta.correr(self.con, self.cliente(), self.cuentas)
        ingesta.correr(self.con, self.cliente(), {"ana": "otra razón"})
        tipos = [t for (t,) in self.con.execute("SELECT tipo FROM eventos")]
        self.assertEqual(tipos.count("cuenta_alta"), 2)
        self.assertIn("cuenta_retirada", tipos)
        self.assertIn("cuenta_razon_cambiada", tipos)

    def test_cuentas_sin_razon_se_rechazan(self):
        ruta = Path(self.tmp.name) / "c.yaml"
        ruta.write_text("cuentas:\n  - username: ana\n    razon: ''\n", encoding="utf-8")
        with self.assertRaises(ingesta.ConfigInvalida):
            ingesta.cargar_cuentas(ruta)


class AnclasTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.con = ingesta.conectar(Path(self.tmp.name) / "t.db")
        self.addCleanup(self.con.close)
        self.anclas = Path(self.tmp.name).resolve() / "anclas"
        for i in range(3):
            ingesta.registrar(self.con, "prueba", i=i)
        sin_ots = patch("ingesta.comando_ots", return_value=None)
        sin_ots.start()
        self.addCleanup(sin_ots.stop)

    def reescribir(self, sql):
        self.con.execute("DROP TRIGGER IF EXISTS eventos_sin_delete")
        self.con.execute("DROP TRIGGER IF EXISTS eventos_sin_update")
        self.con.execute(sql)

    def test_ancla_local_y_verificacion(self):
        ruta = ingesta.anclar(self.con)
        self.assertEqual(ruta.resolve(), self.anclas / "ancla_000003.txt")
        self.assertEqual(ingesta.leer_ancla(ruta)[0], 3)
        self.assertEqual(ingesta.verificar(self.con), [])
        ultimo = self.con.execute("SELECT tipo, datos FROM eventos ORDER BY n DESC LIMIT 1").fetchone()
        self.assertEqual(ultimo[0], "ancla")
        self.assertIn('"sellado_ots": false', ultimo[1])
        # el mismo evento no se ancla dos veces (el último ahora es el 4, "ancla")
        (self.anclas / "ancla_000004.txt").write_text("ocupado")
        with self.assertRaises(ingesta.ConfigInvalida):
            ingesta.anclar(self.con)

    def test_detecta_truncamiento(self):
        ingesta.anclar(self.con)
        self.reescribir("DELETE FROM eventos WHERE n >= 3")
        self.assertEqual(ingesta.verificar(self.con)[0], "ancla_000003.txt: el evento 3 ya no existe "
                                                         "(bitácora truncada)")

    def test_detecta_reescritura_completa(self):
        ingesta.anclar(self.con)
        # reescribe toda la cadena con hashes recalculados: la cadena sola cuadra, el ancla no
        self.reescribir("DELETE FROM eventos")
        for i in range(3):
            ingesta.registrar(self.con, "prueba", i=i * 10)
        errores = ingesta.verificar(self.con)
        self.assertTrue(any("bitácora reescrita" in e for e in errores))
        self.assertTrue(all("cadena" not in e for e in errores))

    def test_detecta_ancla_borrada(self):
        ruta = ingesta.anclar(self.con)
        ruta.unlink()
        self.assertTrue(any("falta el archivo ancla_000003.txt" in e for e in ingesta.verificar(self.con)))

    def test_sello_ots(self):
        llamadas = []

        def ots(cmd, **kw):
            llamadas.append(cmd[1])
            if cmd[1] == "stamp":
                Path(cmd[2] + ".ots").write_bytes(b"ots")
            salida = "Pending confirmation in Bitcoin blockchain" if cmd[1] == "verify" else ""
            return SimpleNamespace(returncode=1 if cmd[1] == "verify" else 0, stdout="", stderr=salida)

        with patch("ingesta.comando_ots", return_value="ots"):
            ingesta.anclar(self.con, ejecutar=ots)
            estado = ingesta.estado_sellos(self.con, ejecutar=ots)
        self.assertEqual(llamadas, ["stamp", "upgrade", "verify"])
        self.assertIn("pendiente de confirmación", estado[0])
        self.assertIn('"sellado_ots": true', self.con.execute(
            "SELECT datos FROM eventos WHERE tipo = 'ancla'").fetchone()[0])

    def test_sin_ots_no_falla(self):
        ingesta.anclar(self.con)
        self.assertEqual(ingesta.estado_sellos(self.con), ["ancla_000003.txt: sin sello externo (solo local)"])


if __name__ == "__main__":
    unittest.main()
