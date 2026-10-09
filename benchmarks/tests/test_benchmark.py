import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('benchmark_runner', ROOT / 'runner.py')
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)


class BenchmarkTests(unittest.TestCase):
    def test_matrix_has_ten_distinct_inputs_per_genre(self):
        cases = [json.loads(line) for line in (ROOT / 'dataset.jsonl').read_text().splitlines()]
        self.assertEqual(len(cases), 160)
        self.assertEqual(len({case['input_hash'] for case in cases}), 160)
        for template in {case['type'] for case in cases}:
            group = [case for case in cases if case['type'] == template]
            self.assertEqual(len(group), 10)
            self.assertEqual(len({case['scenario'] for case in group}), 10)
        gold = [json.loads(line) for line in (ROOT / 'gold.jsonl').read_text().splitlines()]
        self.assertEqual({case['id'] for case in cases}, {item['id'] for item in gold})
        self.assertTrue(all(item['expected_sections'] for item in gold))

    def test_cache_isolation(self):
        base = {'model': 'candidate', 'digest': 'first', 'input': 'input', 'seed': 42, 'assets': 'assets'}
        for field in base:
            changed = {**base, field: str(base[field]) + 'changed'}
            self.assertNotEqual(runner.digest(base), runner.digest(changed))

    def test_gate_rejects_stale_or_busy_resources(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            root.joinpath('gate.json').write_text(json.dumps({'ready': True, 'time': 1, 'gpu_utilization': 0}))
            with patch.object(runner, 'ROOT', root), patch.object(runner, 'api', return_value={'models': []}), patch.object(runner.time, 'sleep', side_effect=RuntimeError('wait')):
                with self.assertRaisesRegex(RuntimeError, 'wait'):
                    runner.await_gate({'digest': 'candidate', 'name': 'candidate'})

    def test_gate_rejects_foreign_loaded_model(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            root.joinpath('gate.json').write_text(json.dumps({'ready': True, 'time': runner.time.time(), 'gpu_utilization': 0}))
            with patch.object(runner, 'ROOT', root), patch.object(runner, 'api', return_value={'models': [{'digest': 'foreign'}]}), patch.object(runner.time, 'sleep', side_effect=RuntimeError('wait')):
                with self.assertRaisesRegex(RuntimeError, 'wait'):
                    runner.await_gate({'digest': 'candidate', 'name': 'candidate'})

    def test_snapshot_hash_is_stable_under_dictionary_order(self):
        self.assertEqual(runner.digest([{'text': 'Синтетический пример', 'start': 0}]), runner.digest([{'start': 0, 'text': 'Синтетический пример'}]))

    def test_conservative_context_guard_sends_no_request(self):
        model = {'options': {'num_predict_json': 20, 'num_ctx': 100, 'num_predict_markdown': 20}}
        llm = runner.LLM(model, {}, 'assets')
        with patch.object(runner, 'api') as call:
            with self.assertRaises(runner.ProcessingError) as raised:
                llm.chat('текст', 'источник', {'type': 'object'})
            self.assertEqual(raised.exception.code, 'context_budget_guard')
            call.assert_not_called()


if __name__ == '__main__':
    unittest.main()
