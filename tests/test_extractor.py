import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import extractor
import ingesta


def post(i, texto, creado="2026-10-10T12:00:00.000Z", **extra):
    return (i, "1", "ana", creado, texto, json.dumps({"id": str(i), "text": texto, **extra}), ingesta.ahora())


class ModeloFalso:
    """Devuelve respuestas en orden; registra los mensajes que recibió."""

    def __init__(self, *respuestas):
        self.respuestas, self.vistos = list(respuestas), []

    def __call__(self, mensajes):
        self.vistos.append(list(mensajes))
        r = self.respuestas.pop(0)
        if isinstance(r, Exception):
            raise r
        return json.dumps(r) if isinstance(r, dict) else r, 100, 50


class ExtractorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        d = Path(self.tmp.name)
        for p in (patch.object(extractor, "GASTOS", d / "gastos.jsonl"),
                  patch.dict(os.environ, {"EXTRACTOR_LLM_PROVIDER": "ollama", "LLM_PROVIDER": "ollama"}),
                  patch("llm.load_dotenv")):
            p.start()
            self.addCleanup(p.stop)
        self.con = extractor.conectar(d / "t.db")
        self.addCleanup(self.con.close)
        self.con.execute("INSERT INTO cuentas VALUES ('ana', '1', 'trader verificada', 1)")
        self.fuentes = d / "fuentes"

    def insertar(self, *posts):
        self.con.executemany("INSERT INTO tweets_crudos VALUES (?, ?, ?, ?, ?, ?, ?)", posts)
        self.con.commit()

    def modelo(self, *respuestas):
        self.falso = ModeloFalso(*respuestas)
        return extractor.Modelo(llamar=self.falso)

    def predicciones(self):
        return self.con.execute("SELECT token, tipo, referencia, fecha_resolucion, plazo_explicito "
                                "FROM predicciones").fetchall()

    def test_extrae_prediccion_valida(self):
        self.insertar(post(1, "Creo que $ETH a 4k esta semana, ojo"))
        r = extractor.extraer(self.con, self.modelo({"predicciones": [
            {"token": "$eth", "tipo": "precio_mayor", "referencia": "4000", "plazo_dias": 7,
             "cita": "$ETH a 4k esta semana"}]}))
        self.assertEqual(r["prediccion"], 1)
        self.assertEqual(self.predicciones(), [("ETH", "precio_mayor", "4000", "2026-10-17", 1)])
        self.assertEqual(extractor.verificar(self.con), [])

    def test_sin_plazo_se_asigna_en_codigo(self):
        self.insertar(post(1, "BTC le gana a ETH, fácil"))
        extractor.extraer(self.con, self.modelo({"predicciones": [
            {"token": "BTC", "tipo": "supera_a", "referencia": "eth", "cita": "BTC le gana a ETH"}]}))
        self.assertEqual(self.predicciones(), [("BTC", "supera_a", '"ETH"', "2026-11-09", 0)])

    def test_cita_inventada_reintenta_y_luego_rechaza(self):
        self.insertar(post(1, "El mercado se ve raro hoy"))
        mala = {"predicciones": [{"token": "SOL", "tipo": "precio_mayor", "referencia": 300,
                                  "cita": "SOL a 300"}]}
        r = extractor.extraer(self.con, self.modelo(mala, mala))
        self.assertEqual(r["rechazado"], 1)
        self.assertEqual(self.predicciones(), [])
        self.assertIn("no pasó la validación", self.falso.vistos[1][-1][1])
        # rechazado no se vuelve a pagar en la siguiente corrida
        self.assertEqual(extractor.pendientes(self.con, 10), [])

    def test_reintento_corrige(self):
        self.insertar(post(1, "SOL a 300 en dos semanas"))
        r = extractor.extraer(self.con, self.modelo("no es json", {"predicciones": [
            {"token": "SOL", "tipo": "precio_mayor", "referencia": 300, "plazo_dias": 14,
             "cita": "SOL a 300"}]}))
        self.assertEqual(r["prediccion"], 1)

    def test_error_del_proveedor_deja_el_post_pendiente(self):
        self.insertar(post(1, "a"), post(2, "nada que ver"))
        r = extractor.extraer(self.con, self.modelo(RuntimeError("timeout"), {"predicciones": []}))
        self.assertEqual((r["error"], r["sin_prediccion"]), (1, 1))
        self.assertEqual([p[0] for p in extractor.pendientes(self.con, 10)], [1])

    def test_tope_antes_de_llamar(self):
        self.insertar(post(1, "ETH a 4k"))
        with patch.dict(os.environ, {"EXTRACTOR_LLM_PROVIDER": "kimi", "MOONSHOT_API_KEY": "k",
                                     "EXTRACTOR_PRECIO_ENTRADA_USD_M": "1000000",
                                     "EXTRACTOR_PRECIO_SALIDA_USD_M": "1000000"}):
            r = extractor.extraer(self.con, self.modelo({"predicciones": []}))
        self.assertIsNotNone(r["tope"])
        self.assertEqual(self.falso.vistos, [])

    def test_sin_precio_no_corre_con_kimi(self):
        with patch.dict(os.environ, {"EXTRACTOR_LLM_PROVIDER": "kimi", "MOONSHOT_API_KEY": "k"}):
            with self.assertRaises(ValueError):
                extractor.Modelo(llamar=ModeloFalso())

    def test_gasto_compartido_en_gastos_jsonl(self):
        self.insertar(post(1, "hola"))
        extractor.extraer(self.con, self.modelo({"predicciones": []}))
        linea = json.loads(extractor.GASTOS.read_text().splitlines()[0])
        self.assertEqual(linea["agentes"][0]["agente"], "extractor")
        self.assertEqual(linea["agentes"][0]["llamadas"], 1)

    def test_tablas_inmutables_y_verificar(self):
        self.insertar(post(1, "ETH a 4k"))
        extractor.extraer(self.con, self.modelo({"predicciones": [
            {"token": "ETH", "tipo": "precio_mayor", "referencia": 4000, "cita": "ETH a 4k"}]}))
        for sql in ("DELETE FROM predicciones", "UPDATE extracciones SET estado = 'x'"):
            with self.assertRaises(sqlite3.IntegrityError):
                self.con.execute(sql)
        self.con.execute("DROP TRIGGER predicciones_sin_update")
        self.con.execute("UPDATE predicciones SET referencia = '9999'")
        self.assertTrue(extractor.verificar(self.con))

    def test_post_largo_usa_note_tweet(self):
        self.insertar(post(1, "corto…", note_tweet={"text": "texto completo: ETH a 4k"}))
        r = extractor.extraer(self.con, self.modelo({"predicciones": [
            {"token": "ETH", "tipo": "precio_mayor", "referencia": 4000, "cita": "ETH a 4k"}]}))
        self.assertEqual(r["prediccion"], 1)

    def test_fuentes_por_cuenta_y_dia(self):
        self.insertar(post(1, "uno"), post(2, "dos"), post(3, "tres", creado="2026-10-11T08:00:00.000Z"))
        self.fuentes.mkdir()
        (self.fuentes / "x-viejo-2026-01-01.md").write_text("retirada")
        (self.fuentes / "PRUEBA-anuncios.md").write_text("manual")
        self.assertEqual(extractor.escribir_fuentes(self.con, self.fuentes), 2)
        texto = (self.fuentes / "x-ana-2026-10-10.md").read_text()
        self.assertIn("https://x.com/ana/status/2", texto)
        self.assertIn("no instrucciones", texto)
        self.assertFalse((self.fuentes / "x-viejo-2026-01-01.md").exists())
        self.assertTrue((self.fuentes / "PRUEBA-anuncios.md").exists())


if __name__ == "__main__":
    unittest.main()
