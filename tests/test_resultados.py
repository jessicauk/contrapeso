import json
import sqlite3
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import ingesta
import resultados


class Respuesta:
    def __init__(self, status, cuerpo=None, texto=""):
        self.status_code, self._cuerpo, self.headers = status, cuerpo, {}
        self.text = texto or json.dumps(cuerpo)

    def json(self):
        return self._cuerpo


class BinanceFalso:
    """precios[token] = {día: (apertura, máximo, mínimo, cierre)}"""

    def __init__(self, precios, fallas=None):
        self.precios, self.fallas, self.llamadas = precios, list(fallas or []), []

    def get(self, url, params=None, timeout=None):
        self.llamadas.append(params)
        if self.fallas:
            return self.fallas.pop(0)
        token = params["symbol"].removesuffix("USDT")
        if token not in self.precios:
            return Respuesta(400, texto='{"code":-1121,"msg":"Invalid symbol."}')
        desde = datetime.fromtimestamp(params["startTime"] / 1000, timezone.utc).date()
        hasta = datetime.fromtimestamp(params["endTime"] / 1000, timezone.utc).date()
        return Respuesta(200, [[resultados.ms(d), *map(str, v), 0, 0]
                               for d, v in sorted(self.precios[token].items()) if desde <= d <= hasta])


D0 = date(2026, 9, 1)


def serie(*velas):
    """velas consecutivas desde D0: (máximo, mínimo, cierre)"""
    return {D0 + timedelta(days=i): (c, h, l, c) for i, (h, l, c) in enumerate(velas)}


class ResultadosTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.con = resultados.conectar(Path(self.tmp.name) / "t.db")
        self.addCleanup(self.con.close)
        self.n = 0

    def prediccion(self, token, tipo, referencia, dias=3, username="ana", fecha_post=D0):
        self.n += 1
        self.con.execute("INSERT INTO tweets_crudos VALUES (?, '1', ?, ?, 'x', '{}', ?)",
                         (self.n, username, fecha_post.isoformat(), ingesta.ahora()))
        self.con.execute(
            "INSERT INTO predicciones (tweet_id, version, username, token, tipo, referencia, enunciado, "
            "fecha_post, fecha_resolucion, plazo_explicito, cita) VALUES (?, ?, ?, ?, ?, ?, '', ?, ?, 1, 'x')",
            (self.n, resultados.extractor.VERSION, username, token, tipo, json.dumps(referencia),
             fecha_post.isoformat(), (fecha_post + timedelta(days=dias)).isoformat()))
        self.con.commit()

    def resolver(self, precios, **kw):
        self.api = BinanceFalso(precios, **kw)
        return resultados.resolver_vencidas(self.con, resultados.Precios(self.api, dormir=lambda s: None))

    def fila(self, pid=1):
        return self.con.execute("SELECT acierto_cierre, acierto_toco, trivial, extremo FROM resultados "
                                "WHERE prediccion_id = ?", (pid,)).fetchone()

    def test_precio_mayor_toco_pero_no_cerro(self):
        self.prediccion("ETH", "precio_mayor", 4000)
        # día del post (no cuenta su máximo), luego toca 4100 y cierra en 3800
        r = self.resolver({"ETH": serie((4500, 3400, 3500), (3700, 3500, 3600), (4100, 3600, 3900),
                                        (3950, 3700, 3800))})
        self.assertEqual(r["resueltas"], 1)
        self.assertEqual(self.fila(), (0, 1, 0, 4100))

    def test_precio_menor_y_trivial(self):
        self.prediccion("BTC", "precio_menor", 60000)
        self.resolver({"BTC": serie((59000, 58000, 58500), (59000, 57000, 58000), (59000, 57000, 58000),
                                    (59000, 57000, 58000))})
        self.assertEqual(self.fila(), (1, 1, 1, 57000))  # ya estaba debajo al publicar

    def test_supera_a(self):
        self.prediccion("SOL", "supera_a", "ETH", dias=1)
        self.resolver({"SOL": serie((100, 100, 100), (120, 100, 110)),
                       "ETH": serie((4000, 4000, 4000), (4500, 4000, 4200))})
        self.assertEqual(self.fila(), (1, None, 0, None))  # +10 % contra +5 %

    def test_no_resuelve_antes_de_que_cierre_el_dia(self):
        hoy = resultados.hoy_utc()
        self.prediccion("ETH", "precio_mayor", 4000, dias=0, fecha_post=hoy)
        self.assertEqual(resultados.vencidas(self.con), [])

    def test_token_no_listado_queda_pendiente(self):
        self.prediccion("RARO", "precio_mayor", 1)
        self.prediccion("ETH", "precio_mayor", 1)
        r = self.resolver({"ETH": serie(*[(2, 1, 2)] * 4)})
        self.assertEqual((r["sin_dato"], r["resueltas"]), (1, 1))
        self.assertEqual([v[0] for v in resultados.vencidas(self.con)], [1])  # se reintenta

    def test_faltan_velas_es_sin_dato(self):
        self.prediccion("ETH", "precio_mayor", 4000)
        precios = serie(*[(4100, 3900, 4000)] * 4)
        del precios[D0 + timedelta(days=2)]
        self.assertEqual(self.resolver({"ETH": precios})["sin_dato"], 1)

    def test_reintenta_5xx(self):
        self.prediccion("ETH", "precio_mayor", 1)
        r = self.resolver({"ETH": serie(*[(2, 1, 2)] * 4)}, fallas=[Respuesta(503, {})])
        self.assertEqual(r["resueltas"], 1)

    def test_inmutable_y_verificable(self):
        self.prediccion("ETH", "precio_mayor", 1)
        self.resolver({"ETH": serie(*[(2, 1, 2)] * 4)})
        self.assertEqual(resultados.verificar(self.con), [])
        with self.assertRaises(sqlite3.IntegrityError):
            self.con.execute("UPDATE resultados SET acierto_cierre = 0")
        self.con.execute("DROP TRIGGER resultados_sin_update")
        self.con.execute("UPDATE resultados SET acierto_cierre = 0")
        self.assertTrue(any("alterado" in e for e in resultados.verificar(self.con)))

    def test_historial_no_cuenta_triviales(self):
        alto = serie(*[(5000, 4500, 4800)] * 4)
        self.prediccion("ETH", "precio_mayor", 4000)   # trivial
        self.prediccion("ETH", "precio_mayor", 4900)   # tocó, no cerró
        self.prediccion("ETH", "precio_mayor", 4700, username="beto")  # trivial
        self.resolver({"ETH": alto})
        h = {f["username"]: f for f in resultados.historial(self.con)}
        self.assertEqual((h["ana"]["evaluadas"], h["ana"]["aciertos_cierre"], h["ana"]["aciertos_toco"]),
                         (1, 0, 1))
        self.assertEqual(h["beto"]["evaluadas"], 0)

    def test_fichas_solo_con_aprobacion(self):
        carpeta = Path(self.tmp.name) / "fichas"
        carpeta.mkdir()
        ficha = {"id": "2026-09-01-ETH", "fecha": D0.isoformat(), "token": "ETH", "resultado": None,
                 "prediccion": {"tipo": "precio_mayor", "referencia": 4000, "probabilidad": 0.6,
                                "enunciado": "ETH vale más de $4000 USD",
                                "fecha_resolucion": (D0 + timedelta(days=3)).isoformat()}}
        ruta = carpeta / "2026-09-01-ETH.json"
        ruta.write_text(json.dumps(ficha))
        precios = resultados.Precios(BinanceFalso({"ETH": serie(*[(4200, 4000, 4100)] * 4)}))
        with patch("resultados.FICHAS", carpeta), patch("resultados.cargar_todas",
                                                        lambda: [json.loads(ruta.read_text())]):
            self.assertEqual(resultados.resolver_fichas(precios, confirmar=lambda _: "n"), 0)
            self.assertIsNone(json.loads(ruta.read_text())["resultado"])
            self.assertEqual(resultados.resolver_fichas(precios, confirmar=lambda _: "s"), 1)
        guardada = json.loads(ruta.read_text())
        self.assertIs(guardada["resultado"], True)
        self.assertEqual(guardada["resultado_detalle"]["fuente"], resultados.FUENTE)


if __name__ == "__main__":
    unittest.main()
