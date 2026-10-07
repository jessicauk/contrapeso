import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import llm
import contrapeso_m1 as modulo


class MultimodelTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {}, clear=True)
        self.env.start()
        self.dotenv = patch('llm.load_dotenv')
        self.dotenv.start()
        self.addCleanup(self.env.stop)
        self.addCleanup(self.dotenv.stop)

    def test_default_and_independent_models(self):
        self.assertEqual(llm.settings()['model'], 'llama3.2:3b')
        os.environ.update(DIRECTOR_LLM_PROVIDER='kimi', DIRECTOR_MODELO='modelo-prueba',
                          MOONSHOT_API_KEY='test-key', INVESTIGADOR_MODELO='local-prueba')
        self.assertEqual(llm.settings('director')['model'], 'modelo-prueba')
        self.assertEqual(llm.settings('investigador')['model'], 'local-prueba')
        director = llm.crewai_llm('director')
        investigador = llm.crewai_llm('investigador')
        self.assertIsNot(director, investigador)
        self.assertIn('modelo-prueba', director.model)
        self.assertIn('local-prueba', investigador.model)

    def test_provider_inheritance_and_missing_key(self):
        os.environ['LLM_PROVIDER'] = 'kimi'
        with self.assertRaisesRegex(ValueError, 'MOONSHOT_API_KEY'):
            llm.settings('director')
        os.environ['MOONSHOT_API_KEY'] = 'test-key'
        self.assertEqual(llm.settings('director')['model'], 'kimi-k3')

    def test_tariffs_required_for_remote(self):
        self.assertEqual(modulo.tarifas_agente('DIRECTOR'), (0, 0))
        os.environ['DIRECTOR_LLM_PROVIDER'] = 'kimi'
        with self.assertRaisesRegex(ValueError, 'DIRECTOR_PRECIO_ENTRADA'):
            modulo.tarifas_agente('DIRECTOR')
        for invalid in ('nan', '-1', 'inf'):
            os.environ['DIRECTOR_PRECIO_ENTRADA_USD_M'] = invalid
            with self.assertRaises(ValueError):
                modulo.tarifas_agente('DIRECTOR')

    def test_costs_are_separate_and_monthly_total_includes_old_records(self):
        usage = SimpleNamespace(prompt_tokens=1000000, completion_tokens=500000,
                                successful_requests=2)
        def agent(model):
            return SimpleNamespace(llm=SimpleNamespace(
                model=model, get_token_usage_summary=lambda: usage))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'gastos.jsonl'
            path.write_text(json.dumps({'fecha': modulo.datetime.now().isoformat(), 'usd': 1}) + '\n')
            with patch.object(modulo, 'GASTOS', path):
                cost = modulo.registrar_gasto(
                    {'DIRECTOR': agent('remote'), 'INVESTIGADOR': agent('local')},
                    {'DIRECTOR': (2, 4), 'INVESTIGADOR': (0, 0)}, 'test')
                self.assertEqual(cost, 4)
                self.assertEqual(modulo.gastado_este_mes(), 5)
                record = json.loads(path.read_text().splitlines()[-1])
                self.assertEqual(record['agentes'][1]['usd'], 0)


class AuditoriaTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        (Path(directory.name) / 'NEAR-anuncios.md').write_text('texto')
        fuentes = patch.object(modulo, 'FUENTES', Path(directory.name))
        fuentes.start()
        self.addCleanup(fuentes.stop)
        self.eventos = []

    def revisar(self, raw, veredicto=None):
        llamadas = []
        def kickoff(mensaje, response_format):
            llamadas.append(mensaje)
            return SimpleNamespace(pydantic=veredicto)
        revisar = modulo.auditoria(SimpleNamespace(kickoff=kickoff),
                                   lambda nombre, **datos: self.eventos.append((nombre, datos)))
        return revisar(SimpleNamespace(pydantic=None, raw=raw)), llamadas

    def analisis(self, fuente):
        return json.dumps({'contraargumentos': [{'argumento': 'a', 'fuente': fuente}],
                           'resumen': 'r'})

    def test_invented_source_is_rejected_before_calling_auditor(self):
        (ok, motivo), llamadas = self.revisar(self.analisis('inventada.md'))
        self.assertFalse(ok)
        self.assertIn('inventada.md', motivo)
        self.assertEqual(llamadas, [])
        self.assertEqual(self.eventos[0][1]['por'], 'reglas')

    def test_auditor_rejection_and_approval(self):
        rechazo = modulo.Veredicto(aprobado=False, motivos=['la fuente no lo dice'])
        (ok, motivo), _ = self.revisar(self.analisis('NEAR-anuncios.md'), rechazo)
        self.assertFalse(ok)
        self.assertIn('la fuente no lo dice', motivo)
        (ok, salida), _ = self.revisar('```json\n' + self.analisis('NEAR-anuncios.md') + '\n```',
                                       modulo.Veredicto(aprobado=True))
        self.assertTrue(ok)
        self.assertEqual(modulo.Analisis.model_validate_json(salida).contraargumentos[0].fuente,
                         'NEAR-anuncios.md')

    def test_missing_verdict_fails_closed(self):
        (ok, _), _ = self.revisar(self.analisis('NEAR-anuncios.md'), None)
        self.assertFalse(ok)
        self.assertFalse(self.eventos[-1][1]['aprobado'])


if __name__ == '__main__':
    unittest.main()
