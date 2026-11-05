import copy
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import research_batch_continuation as c


class ContinuationTests(unittest.TestCase):
    def summary(self, count=1, phase='screen'):
        rows = [{'status': 'completed', 'passed': True} for _ in range(count)]
        if phase == 'full':
            rows += [{'status': 'not_started', 'passed': False} for _ in range(24 - count)]
        return {'rows': rows, 'planned': 24 if phase == 'full' else 4,
                'observed': count, 'passed': count == (24 if phase == 'full' else 4)}

    def test_four_screens_transition_to_full_original_scope(self):
        self.assertEqual(c.synthetic_summary(self.summary(4), 'original_screen_prerequisite', 4), 'full_synthetic')

    def test_full_matrix_accepts_only_completed_gold_not_pending_rows(self):
        self.assertEqual(c.synthetic_summary(self.summary(7, 'full'), 'full_synthetic', 7), 'full_synthetic')
        value = self.summary(7, 'full')
        value['rows'][0]['passed'] = False
        with self.assertRaises(c.Stopped):
            c.synthetic_summary(value, 'full_synthetic', 7)

    def test_failed_paused_and_shortened_scope_refuse(self):
        for change in ['failed', 'paused_budget', 'in_progress']:
            value = self.summary()
            value['rows'][0]['status'] = change
            with self.subTest(change=change), self.assertRaises(c.Stopped):
                c.synthetic_summary(value, 'original_screen_prerequisite', 1)
        value = self.summary(24, 'full')
        value['planned'] = 4
        with self.assertRaises(c.Stopped):
            c.synthetic_summary(value, 'full_synthetic', 24)

    def test_clean_budget_pause_still_requires_agent_audit(self):
        value = self.summary()
        value['budget_checkpoint_pending'] = True
        with self.assertRaises(c.Stopped):
            c.synthetic_summary(value, 'original_screen_prerequisite', 1)

    def test_terminal_unit_must_have_no_process(self):
        for value in [{'ActiveState': 'active', 'MainPID': '42'},
                      {'ActiveState': 'inactive', 'MainPID': '42'}]:
            with self.subTest(value=value), self.assertRaises(c.Stopped):
                c.terminal_unit(value)
        c.terminal_unit({'ActiveState': 'inactive', 'MainPID': '0'})

    def native(self):
        metrics = {'key': 'a', 'native_budget': {'admitted': True, 'truncation_enabled': False,
                    'native_content_token_envelope': 100, 'chat_envelope_allowance': 10}}
        cache = {'metrics': copy.deepcopy(metrics), 'value': {'answer': 1}}
        raw = {'metrics': copy.deepcopy(metrics), 'response': {'done': True, 'done_reason': 'stop',
               'prompt_eval_count': 50, 'message': {'content': '{"answer":1}'}}}
        return cache, raw

    def test_native_answer_reconstructed_including_text_fences(self):
        cache, raw = self.native()
        c.native_value(cache, raw, 'a')
        cache['value'] = 'Report'
        raw['response']['message']['content'] = '<think>hidden</think>```markdown\nReport\n```'
        c.native_value(cache, raw, 'a')

    def test_native_changed_values_truncation_and_envelope_refuse(self):
        for mode in ['value', 'truncated', 'envelope', 'metrics', 'admission']:
            cache, raw = self.native()
            if mode == 'value':
                cache['value'] = {'answer': 2}
            elif mode == 'truncated':
                raw['response']['done_reason'] = 'length'
            elif mode == 'envelope':
                raw['response']['prompt_eval_count'] = 111
            elif mode == 'metrics':
                raw['metrics']['key'] = 'different'
            else:
                cache['metrics']['native_budget']['admitted'] = False
                raw['metrics'] = copy.deepcopy(cache['metrics'])
            with self.subTest(mode=mode), self.assertRaises(c.Stopped):
                c.native_value(cache, raw, 'a')

    def test_in_progress_request_never_resumed_by_audit(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / 'private/requests/pending.json'
            path.parent.mkdir(parents=True)
            c.write(path, {'status': 'in_progress'})
            before = path.read_bytes()
            with self.assertRaises(c.Stopped):
                c.audit(root, 'original_screen_prerequisite')
            self.assertEqual(path.read_bytes(), before)

    def setup_tick(self, directory, row=None, properties=None):
        root = Path(directory) / 'runtime'
        root.mkdir()
        controller = Path(directory) / 'controller'
        controller.mkdir()
        state_path = Path(directory) / 'state.json'
        state = {'controller': str(controller), 'current_job': str(Path(directory) / 'job.json'),
                 'current_run_id': 'a' * 16, 'execution_root': directory,
                 'current_phase': 'original_screen_prerequisite', 'synthetic_completed': 0}
        c.write(state_path, state)
        window = SimpleNamespace(__file__=str(controller / 'research_window.py'),
                  load_job=Mock(return_value={'runtime_root': str(root), 'run_id': 'a' * 16}),
                  owned=Mock(return_value=row or {'running': True, 'status': 'running'}),
                  resources=SimpleNamespace(command=Mock(return_value=properties or 'ActiveState=active\nMainPID=42\n'), Refused=RuntimeError),
                  deadline=Mock(return_value=1), MOSCOW=None, LOCKS=[], gate=Mock())
        return window, state_path

    def test_live_handle_never_creates_or_starts_duplicate(self):
        with tempfile.TemporaryDirectory() as directory:
            window, state = self.setup_tick(directory)
            with patch.object(c, 'create_job') as create, patch.object(c.subprocess, 'run') as run:
                result = c.tick(window, state, Path(directory) / 'adapter.py', 7200)
                self.assertEqual(result['status'], 'verified_live_wait')
                create.assert_not_called()
                run.assert_not_called()

    def test_interrupted_container_does_not_trigger_new_job(self):
        with tempfile.TemporaryDirectory() as directory:
            window, state = self.setup_tick(directory, {'running': False, 'status': 'exited', 'exit_code': 137}, 'ActiveState=inactive\nMainPID=0\n')
            with patch.object(c, 'create_job') as create, self.assertRaises(c.Stopped):
                c.tick(window, state, Path(directory) / 'adapter.py', 7200)
            create.assert_not_called()

    def test_container_exit_before_watchdog_cleanup_waits_same_handle(self):
        for state in ['active', 'deactivating']:
            with self.subTest(state=state), tempfile.TemporaryDirectory() as directory:
                window, path = self.setup_tick(directory, {'running': False, 'status': 'exited', 'exit_code': 0}, 'ActiveState='+state+'\nMainPID=42\n')
                with patch.object(c, 'create_job') as create, patch.object(c.subprocess, 'run') as run:
                    result = c.tick(window, path, Path(directory) / 'adapter.py', 7200)
                    self.assertEqual(result['status'], 'awaiting_supervisor_terminal')
                    create.assert_not_called()
                    run.assert_not_called()

    def test_without_live_watchdog_running_container_refuses(self):
        with tempfile.TemporaryDirectory() as directory:
            window, state = self.setup_tick(directory, properties='ActiveState=inactive\nMainPID=0\n')
            with self.assertRaises(c.Stopped):
                c.tick(window, state, Path(directory) / 'adapter.py', 7200)

    def test_insufficient_time_never_creates_new_batch(self):
        with tempfile.TemporaryDirectory() as directory:
            window, state = self.setup_tick(directory, {'running': False, 'status': 'created'}, 'ActiveState=inactive\nMainPID=0\n')
            with patch.object(c, 'create_job') as create, patch.object(c.subprocess, 'run') as run:
                result = c.tick(window, state, Path(directory) / 'adapter.py', 599)
                self.assertIn('insufficient', result['status'])
                create.assert_not_called()
                run.assert_not_called()

    def test_foreign_work_defers_without_start(self):
        with tempfile.TemporaryDirectory() as directory:
            window, state = self.setup_tick(directory, {'running': False, 'status': 'created'}, 'ActiveState=inactive\nMainPID=0\n')
            window.gate.side_effect = RuntimeError('foreign_model_loaded')
            with patch.object(c.subprocess, 'run') as run:
                result = c.tick(window, state, Path(directory) / 'adapter.py', 7200)
                self.assertEqual(result['reason'], 'foreign_model_loaded')
                run.assert_not_called()

    def test_outside_window_does_not_start_or_create(self):
        with tempfile.TemporaryDirectory() as directory:
            window, state = self.setup_tick(directory, {'running': False, 'status': 'created'}, 'ActiveState=inactive\nMainPID=0\n')
            window.deadline.return_value = None
            with patch.object(c, 'create_job') as create, patch.object(c.subprocess, 'run') as run:
                result = c.tick(window, state, Path(directory) / 'adapter.py', 7200)
                self.assertIn('insufficient', result['status'])
                create.assert_not_called()
                run.assert_not_called()

    def test_newly_prepared_container_starts_only_through_guard_cli(self):
        with tempfile.TemporaryDirectory() as directory:
            window, state = self.setup_tick(directory, {'running': False, 'status': 'created'}, 'ActiveState=inactive\nMainPID=0\n')
            outputs = [SimpleNamespace(returncode=0, stdout='{"launch_allowed":true}'), SimpleNamespace(returncode=0, stdout='{}')]
            with patch.object(c.subprocess, 'run', side_effect=outputs) as run:
                c.tick(window, state, Path(directory) / 'adapter.py', 7200)
            self.assertEqual(run.call_args_list[0].args[0][2], 'check')
            self.assertEqual(run.call_args_list[1].args[0][2], 'start')
            self.assertTrue(all('docker' not in call.args[0] for call in run.call_args_list))


if __name__ == '__main__':
    unittest.main()
