import sys
import tempfile
import hashlib
import json
import unittest
import types
import importlib.util
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import research_window as schedule
import research_cancel_probe as probe


class ResearchWindowTests(unittest.TestCase):
    def test_native_gpu_identity_checks_service_pid_cgroup_and_stable_start_time(self):
        group = '/system.slice/ollama.service'
        state = 'ActiveState=active\nMainPID=123\nControlGroup=' + group + '\n'
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for pid in ['123', '456']:
                (root / pid).mkdir()
                (root / pid / 'cgroup').write_text('0::' + group + '\n')
            stat = '456 (runner name) ' + ' '.join(['S'] + ['0'] * 18 + ['100'])
            (root / '456/stat').write_text(stat)
            with patch.object(schedule.resources, 'command', return_value=state):
                self.assertTrue(schedule.native_gpu_owned(456, root))
                for foreign in ['/system.slice/other.service', group + '-foreign']:
                    (root / '456/cgroup').write_text('0::' + foreign + '\n')
                    self.assertFalse(schedule.native_gpu_owned(456, root))
                (root / '456/cgroup').write_text('0::' + group + '/runner\n')
                self.assertTrue(schedule.native_gpu_owned(456, root))
                (root / '123/cgroup').write_text('0::/foreign\n')
                self.assertFalse(schedule.native_gpu_owned(456, root))
            with patch.object(schedule.resources, 'command', return_value=state.replace('active', 'inactive')):
                self.assertFalse(schedule.native_gpu_owned(456, root))
            (root / '123/cgroup').write_text('0::' + group + '\n')
            original_read = Path.read_text
            samples = iter([stat, stat[:-3] + '101'])
            def read(path, *args, **kwargs):
                return next(samples) if path == root / '456/stat' else original_read(path, *args, **kwargs)
            with patch.object(schedule.resources, 'command', return_value=state), patch.object(Path, 'read_text', read):
                self.assertFalse(schedule.native_gpu_owned(456, root))
            with patch.object(schedule.resources, 'command', return_value=state):
                self.assertFalse(schedule.native_gpu_owned(999, root))

    def test_late_gpu_process_requires_live_service_identity_and_fresh_work_gates(self):
        worker = {'running': True, 'labels': {'com.docker.compose.project': 'psptm-minutes', 'com.docker.compose.service': 'worker'}}
        for owned, late_models, expected in [(True, [], None), (False, [], 'foreign_gpu_process'),
                (True, [{'digest': 'foreign'}], 'foreign_model_loaded')]:
            with self.subTest(owned=owned, expected=expected), patch.object(schedule.resources, 'production_idle') as production, patch.object(schedule.resources, 'ollama_models', side_effect=[[], late_models]), patch.object(schedule.resources, 'inspect', return_value=worker), patch.object(schedule.resources, 'container_pids', return_value={21}), patch.object(schedule.resources, 'gpu_pids', return_value={21, 123, 456}), patch.object(schedule, 'process_gpu_activity', return_value={}), patch.object(schedule, 'ollama_server_pids', return_value={123}), patch.object(schedule, 'native_gpu_owned', return_value=owned) as identity:
                if expected:
                    with self.assertRaisesRegex(schedule.resources.Refused, expected):
                        schedule.gate({'container': 'client', 'model_digests': []}, starting=False)
                else:
                    schedule.gate({'container': 'client', 'model_digests': []}, starting=False)
                    self.assertEqual(production.call_count, 2)
                identity.assert_called_once_with(456)

    def test_late_owned_gpu_process_never_overrides_new_production_work(self):
        worker = {'running': True, 'labels': {'com.docker.compose.project': 'psptm-minutes', 'com.docker.compose.service': 'worker'}}
        with patch.object(schedule.resources, 'production_idle', side_effect=[None, schedule.resources.Refused('production_busy')]), patch.object(schedule.resources, 'ollama_models', return_value=[]), patch.object(schedule.resources, 'inspect', return_value=worker), patch.object(schedule.resources, 'container_pids', return_value={21}), patch.object(schedule.resources, 'gpu_pids', return_value={456}), patch.object(schedule, 'process_gpu_activity', return_value={}), patch.object(schedule, 'ollama_server_pids', return_value={123}), patch.object(schedule, 'native_gpu_owned', return_value=True):
            with self.assertRaisesRegex(schedule.resources.Refused, 'production_busy'):
                schedule.gate({'container': 'client', 'model_digests': []}, starting=False)

    def test_screen_adapter_stops_before_second_new_cell(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            def mapped(value):
                value = str(value)
                return root / value.removeprefix('/bench/') if value.startswith('/bench/') else Path(value)
            runner = types.ModuleType('runner')
            def atomic(path, value):
                path = mapped(path)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(value))
            runner.atomic = atomic
            screen = types.ModuleType('compare_smoke')
            budgets = []
            screen.start_model_budget = lambda: budgets.append('started')
            def driver():
                screen.start_model_budget()
                runner.atomic(mapped('/bench/private/synthetic/one.json'), {'status': 'completed'})
                runner.atomic(mapped('/bench/compare-smoke-summary.json'), {'observed': 1, 'passed': False, 'rows': [{'passed': True}]})
                screen.start_model_budget()
                self.fail('second cell must not start')
            screen.main = driver
            spec = importlib.util.spec_from_file_location('screen_batch_under_test', Path(__file__).with_name('research_screen_batch.py'))
            adapter = importlib.util.module_from_spec(spec)
            with patch.dict(sys.modules, {'runner': runner, 'compare_smoke': screen}):
                spec.loader.exec_module(adapter)
            with patch.object(adapter, 'Path', side_effect=mapped), patch('builtins.print'):
                self.assertEqual(adapter.main(), 0)
            self.assertEqual(len(budgets), 1)
            self.assertEqual(json.loads(mapped('/bench/screen-batch-progress.json').read_text())['new_cells'], 1)

    def test_job_handle_survives_runtime_manifest_verification(self):
        with tempfile.TemporaryDirectory() as directory:
            job_path, job, receipt_path, receipt = self.private_job(directory)
            loaded = schedule.load_job(job_path)
            self.assertEqual(loaded['_path'], job_path)

    def test_probe_rejects_exited_or_http_failed_clients_as_cancel_proof(self):
        for running, phase in ((False, 'http_submitted'), (True, 'http_failed_before_cancel'), (False, 'http_failed_before_cancel')):
            with self.subTest(running=running, phase=phase), patch.object(probe, 'read', return_value={'phase': phase, 'response_received': False}), patch.object(probe, 'owned', return_value={'running': running}):
                with self.assertRaisesRegex(schedule.resources.Refused, 'pending_native_http_not_verified'):
                    probe.pending_client({}, {}, None)
        with patch.object(probe, 'read', return_value={'phase': 'http_submitted', 'response_received': False}), patch.object(probe, 'owned', return_value={'running': True}):
            probe.pending_client({}, {}, None)

    def test_runtime_gate_updates_only_mutable_telemetry(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'manifest.json').write_text(json.dumps({'files': {'client.py': 'frozen'}}))
            with patch.object(schedule.resources, 'command', return_value='0'):
                schedule.publish_runtime_gate({'runtime_root': directory})
                value = json.loads((root / 'gate.json').read_text())
                self.assertTrue(value['ready'])
                self.assertEqual(value['gpu_utilization'], 0)
                (root / 'manifest.json').write_text(json.dumps({'files': {'gate.json': 'frozen'}}))
                with self.assertRaisesRegex(schedule.resources.Refused, 'frozen_gate_telemetry_forbidden'):
                    schedule.publish_runtime_gate({'runtime_root': directory})

    def test_probe_waits_for_stable_full_admission_after_unload(self):
        clock = [0]
        def sleep(seconds): clock[0] += seconds
        def gate(job, starting):
            if starting and clock[0] in (0, 3):
                raise schedule.resources.Refused('gpu_busy')
        with patch.object(probe.time, 'time', side_effect=lambda: clock[0]), patch.object(probe.time, 'sleep', side_effect=sleep), patch.object(schedule, 'gate', side_effect=gate) as checked, patch.object(schedule.resources, 'command') as command:
            probe.wait_for_admission({}, 100)
            self.assertEqual(clock[0], 14)
            self.assertEqual(checked.call_count, 30)
            command.assert_not_called()

    def test_probe_settle_never_ignores_foreign_work(self):
        with patch.object(schedule, 'gate', side_effect=schedule.resources.Refused('foreign_gpu_process')), patch.object(schedule.resources, 'command') as command:
            with self.assertRaisesRegex(schedule.resources.Refused, 'foreign_gpu_process'):
                probe.wait_for_admission({}, probe.time.time() + 30)
            command.assert_not_called()

    def test_probe_settle_honors_budget_with_persistent_busy(self):
        clock = [0]
        def sleep(seconds): clock[0] += seconds
        def gate(job, starting):
            if starting: raise schedule.resources.Refused('gpu_busy')
        with patch.object(probe.time, 'time', side_effect=lambda: clock[0]), patch.object(probe.time, 'sleep', side_effect=sleep), patch.object(schedule, 'gate', side_effect=gate), patch.object(schedule.resources, 'command') as command:
            with self.assertRaisesRegex(schedule.resources.Refused, 'probe_gpu_settle_timeout'):
                probe.wait_for_admission({}, 5)
            self.assertEqual(clock[0], 5)
            command.assert_not_called()

    def test_gpu_admission_diagnostics_identify_each_blocker_without_private_data(self):
        worker = {'running': True, 'labels': {'com.docker.compose.project': 'psptm-minutes', 'com.docker.compose.service': 'worker'}}
        for usage, pids, models in [('6', {21}, []), ('0', {21, 99}, []), ('0', {21}, [{'private': 'must-not-leak'}])]:
            with self.subTest(usage=usage, processes=len(pids), models=len(models)):
                with patch.object(schedule.resources, 'production_idle'), patch.object(schedule.resources, 'ollama_models', return_value=models), patch.object(schedule.resources, 'inspect', return_value=worker), patch.object(schedule.resources, 'container_pids', return_value={21}), patch.object(schedule.resources, 'command', return_value=usage) as command, patch.object(schedule.resources, 'gpu_pids', return_value=pids), patch.object(schedule, 'process_gpu_activity', return_value={21: None}), patch('builtins.print') as output:
                    with self.assertRaisesRegex(schedule.resources.Refused, 'gpu_busy'):
                        schedule.gate({}, starting=True)
                    diagnostic = json.loads(output.call_args.args[0])
                    self.assertEqual(diagnostic['gpu_utilization_percent'], float(usage))
                    self.assertEqual(diagnostic['unexpected_compute_process_count'], len(pids - {21}))
                    self.assertEqual(diagnostic['loaded_model_count'], len(models))
                    self.assertNotIn('must-not-leak', output.call_args.args[0])
                    self.assertEqual(command.call_count, 1)

    def test_stopped_client_does_not_break_server_observation(self):
        worker = {'running': True, 'labels': {'com.docker.compose.project': 'psptm-minutes', 'com.docker.compose.service': 'worker'}}
        def inspect(identifier):
            return worker if identifier == 'psptm-minutes-worker-1' else {'running': False}
        with patch.object(schedule.resources, 'production_idle'), patch.object(schedule.resources, 'ollama_models', return_value=[]), patch.object(schedule.resources, 'inspect', side_effect=inspect), patch.object(schedule.resources, 'container_pids', return_value={21}) as members, patch.object(schedule.resources, 'gpu_pids', return_value={21, 123}), patch.object(schedule, 'process_gpu_activity', return_value={21: None}), patch.object(schedule, 'ollama_server_pids', return_value={123}):
            schedule.gate({'container': 'stopped-client', 'model_digests': []}, starting=False)
            members.assert_called_once_with('psptm-minutes-worker-1')

    def test_native_ollama_membership_includes_nested_cgroups_only(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'cgroup.procs').write_text('123\n')
            (root / 'runner').mkdir()
            (root / 'runner/cgroup.procs').write_text('456\n')
            properties = 'ActiveState=active\nMainPID=123\nControlGroup=/system.slice/ollama.service\n'
            with patch.object(schedule.resources, 'command', return_value=properties), patch.object(schedule.resources, 'container_pids', return_value={789}), patch.object(schedule, 'Path', return_value=root):
                self.assertEqual(schedule.ollama_server_pids(), {123, 456, 789})
                (root / 'cgroup.procs').write_text('999\n')
                with self.assertRaisesRegex(schedule.resources.Refused, 'native_ollama_membership_unverified'):
                    schedule.ollama_server_pids()

    def test_unverified_native_service_is_rejected(self):
        for properties in ('ActiveState=inactive\n', 'ActiveState=active\nMainPID=123\nControlGroup=/other.service\n', 'ActiveState=active\nMainPID=0\nControlGroup=/system.slice/ollama.service\n'):
            with patch.object(schedule.resources, 'command', return_value=properties):
                with self.assertRaisesRegex(schedule.resources.Refused, 'native_ollama_membership_unverified'):
                    schedule.ollama_server_pids()

    def test_only_verified_idle_production_context_is_allowed(self):
        worker = {'running': True, 'labels': {'com.docker.compose.project': 'psptm-minutes', 'com.docker.compose.service': 'worker'}}
        with patch.object(schedule.resources, 'production_idle'), patch.object(schedule.resources, 'ollama_models', return_value=[]), patch.object(schedule.resources, 'inspect', return_value=worker), patch.object(schedule.resources, 'container_pids', return_value={21}), patch.object(schedule.resources, 'command', return_value='0'), patch.object(schedule.resources, 'gpu_pids', return_value={21}), patch.object(schedule, 'process_gpu_activity', return_value={21: None}) as activity:
            schedule.gate({}, starting=True)
            activity.return_value = {21: 1}
            with self.assertRaisesRegex(schedule.resources.Refused, 'production_gpu_busy'):
                schedule.gate({}, starting=True)

    def test_unknown_gpu_context_blocks_admission(self):
        worker = {'running': True, 'labels': {'com.docker.compose.project': 'psptm-minutes', 'com.docker.compose.service': 'worker'}}
        with patch.object(schedule.resources, 'production_idle'), patch.object(schedule.resources, 'ollama_models', return_value=[]), patch.object(schedule.resources, 'inspect', return_value=worker), patch.object(schedule.resources, 'container_pids', return_value={21}), patch.object(schedule.resources, 'command', return_value='0'), patch.object(schedule.resources, 'gpu_pids', return_value={21, 99}), patch.object(schedule, 'process_gpu_activity', return_value={21: None, 99: None}):
            with self.assertRaisesRegex(schedule.resources.Refused, 'gpu_busy'):
                schedule.gate({}, starting=True)

    def test_probe_is_bound_to_single_authorized_job(self):
        with patch.object(schedule, 'read_private', return_value=(None, {'maximum_probe_jobs': 1, 'used_probe_jobs': 0, 'bound_probe_run_id': 'a'})):
            probe.authorized({'run_id': 'a'})
            with self.assertRaisesRegex(schedule.resources.Refused, 'authorization_bound_to_another_job'):
                probe.authorized({'run_id': 'b'})

    def test_consumed_probe_never_starts_again(self):
        with patch.object(probe, 'authorized', return_value={'used_probe_jobs': 1}), patch.object(schedule.resources, 'command') as command:
            with self.assertRaisesRegex(schedule.resources.Refused, 'single_probe_already_consumed'):
                probe.supervise({}, 1)
            command.assert_not_called()

    def private_job(self, directory):
        root = Path(directory)
        source = root / 'runtime'
        source.mkdir(mode=0o700)
        (source / 'client.py').write_text('pass\n')
        manifest = source / 'manifest.json'
        manifest.write_text(json.dumps({'files': {'client.py': hashlib.sha256((source / 'client.py').read_bytes()).hexdigest()}}))
        digest = hashlib.sha256(manifest.read_bytes()).hexdigest()
        receipt = {'server_cancellation_verified': True, 'kind': 'inference', 'model_digests': ['c' * 64],
                   'image_id': 'd' * 64, 'runtime_manifest_sha256': digest, 'client_protocol': 'frozen_ollama_nonstream'}
        receipt_path = root / 'synthetic-test-only-cancellation.json'
        receipt_path.write_text(json.dumps(receipt))
        receipt_path.chmod(0o600)
        job = {'run_id': 'a' * 16, 'container': 'b' * 64, 'kind': 'inference', 'model_digests': ['c' * 64],
               'runtime_root': str(source), 'runtime_manifest_sha256': digest, 'client_protocol': 'frozen_ollama_nonstream',
               'cancellation_receipt': str(receipt_path), 'cancellation_receipt_sha256': hashlib.sha256(receipt_path.read_bytes()).hexdigest()}
        job_path = root / 'job.json'
        job_path.write_text(json.dumps(job))
        job_path.chmod(0o600)
        return job_path, job, receipt_path, receipt

    def test_cancellation_proof_is_bound_to_actual_runtime(self):
        with tempfile.TemporaryDirectory() as directory:
            path, job, receipt_path, receipt = self.private_job(directory)
            self.assertEqual(schedule.load_job(path)['_image_id'], receipt['image_id'])
            (Path(job['runtime_root']) / 'client.py').write_text('raise RuntimeError()\n')
            with self.assertRaisesRegex(schedule.resources.Refused, 'cancellation_runtime_changed'):
                schedule.load_job(path)

    def test_different_client_protocol_receipt_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path, job, receipt_path, receipt = self.private_job(directory)
            receipt['client_protocol'] = 'different_streaming_client'
            receipt_path.write_text(json.dumps(receipt))
            job['cancellation_receipt_sha256'] = hashlib.sha256(receipt_path.read_bytes()).hexdigest()
            path.write_text(json.dumps(job))
            with self.assertRaisesRegex(schedule.resources.Refused, 'verified_cancellation_required'):
                schedule.load_job(path)

    def test_another_runtime_receipt_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path, job, receipt_path, receipt = self.private_job(directory)
            receipt['runtime_manifest_sha256'] = 'e' * 64
            receipt_path.write_text(json.dumps(receipt))
            job['cancellation_receipt_sha256'] = hashlib.sha256(receipt_path.read_bytes()).hexdigest()
            path.write_text(json.dumps(job))
            with self.assertRaisesRegex(schedule.resources.Refused, 'verified_cancellation_required'):
                schedule.load_job(path)

    def test_boolean_without_server_acceptance_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path, job, receipt_path, receipt = self.private_job(directory)
            receipt['server_cancellation_verified'] = False
            receipt_path.write_text(json.dumps(receipt))
            job['cancellation_receipt_sha256'] = hashlib.sha256(receipt_path.read_bytes()).hexdigest()
            path.write_text(json.dumps(job))
            with self.assertRaisesRegex(schedule.resources.Refused, 'verified_cancellation_required'):
                schedule.load_job(path)

    def test_boundaries_and_continuous_weekend(self):
        examples = [
            ('2026-10-23T05:59:59+03:00', '2026-10-23T06:00:00+03:00'),
            ('2026-10-23T06:00:00+03:00', None),
            ('2026-10-23T21:59:59+03:00', None),
            ('2026-10-23T22:00:00+03:00', '2026-10-26T06:00:00+03:00'),
            ('2026-10-24T00:00:00+03:00', '2026-10-26T06:00:00+03:00'),
            ('2026-10-25T23:59:59+03:00', '2026-10-26T06:00:00+03:00'),
            ('2026-10-26T00:00:00+03:00', '2026-10-26T06:00:00+03:00'),
            ('2026-10-26T06:00:00+03:00', None),
            ('2026-10-26T22:00:00+03:00', '2026-10-27T06:00:00+03:00'),
        ]
        for text, expected in examples:
            with self.subTest(now=text):
                actual = schedule.window_end(datetime.fromisoformat(text))
                self.assertEqual(actual.isoformat() if actual else None, expected)

    def test_utc_is_converted_to_moscow(self):
        self.assertIsNone(schedule.window_end(datetime.fromisoformat('2026-10-23T03:00:00+00:00')))
        self.assertEqual(schedule.window_end(datetime.fromisoformat('2026-10-23T19:00:00+00:00')).isoformat(), '2026-10-26T06:00:00+03:00')

    def test_every_quarter_hour_of_a_week_matches_owner_rule(self):
        start = datetime.fromisoformat('2026-10-19T00:00:00+03:00')
        for index in range(7 * 24 * 4):
            now = start + timedelta(minutes=15 * index)
            expected = now.weekday() >= 5 or now.hour < 6 or now.hour >= 22
            self.assertEqual(schedule.window_end(now) is not None, expected, now.isoformat())

    def test_naive_time_rejected(self):
        with self.assertRaises(ValueError):
            schedule.window_end(datetime(2026, 10, 23, 22))

    def test_two_hour_batch_and_shutdown_reserve(self):
        for text, expected in [
            ('2026-10-23T22:00:00+03:00', '2026-10-24T00:00:00+03:00'),
            ('2026-10-26T04:00:00+03:00', '2026-10-26T05:45:00+03:00'),
            ('2026-10-26T05:35:00+03:00', '2026-10-26T05:45:00+03:00'),
            ('2026-10-26T05:35:01+03:00', None),
            ('2026-10-26T06:00:00+03:00', None),
        ]:
            with self.subTest(now=text):
                actual = schedule.deadline(datetime.fromisoformat(text))
                self.assertEqual(actual.isoformat() if actual else None, expected)

    def test_daytime_next_open_is_same_evening(self):
        self.assertEqual(schedule.next_open(datetime.fromisoformat('2026-10-23T08:00:00+03:00')).isoformat(), '2026-10-23T22:00:00+03:00')

    def test_outside_window_never_dispatches_service(self):
        with patch.object(schedule, 'deadline', return_value=None), patch.object(schedule.resources, 'command') as command:
            with self.assertRaisesRegex(schedule.resources.Refused, 'outside_research_window'):
                schedule.start({})
            command.assert_not_called()

    def test_outside_window_never_starts_container(self):
        with patch.object(schedule, 'deadline', return_value=None), patch.object(schedule.resources, 'command') as command:
            with self.assertRaisesRegex(schedule.resources.Refused, 'outside_research_window'):
                schedule.supervise({}, 1)
            command.assert_not_called()

    def test_cleanup_rejects_foreign_container(self):
        job = {'container': 'a' * 64, 'run_id': 'b' * 16, 'kind': 'inference'}
        with patch.object(schedule.resources, 'inspect', return_value={'id': job['container'], 'labels': {}, 'running': True}), patch.object(schedule.resources, 'command') as command:
            with self.assertRaisesRegex(schedule.resources.Refused, 'research_ownership_mismatch'):
                schedule.cleanup(job)
            command.assert_not_called()

    def test_cleanup_targets_only_exact_owned_id(self):
        job = {'container': 'a' * 64}
        with patch.object(schedule, 'owned', side_effect=[{'running': True}, {'running': False}]), patch.object(schedule.resources, 'command') as command:
            schedule.cleanup(job)
            command.assert_called_once_with(['docker', 'stop', '--time', '10', job['container']], timeout=20)

    def test_live_container_is_not_restarted(self):
        with patch.object(schedule, 'deadline', return_value=datetime.now(schedule.MOSCOW)), patch.object(schedule, 'owned', return_value={'status': 'running'}), patch.object(schedule.resources, 'command') as command:
            with self.assertRaisesRegex(schedule.resources.Refused, 'fresh_container_required'):
                schedule.start({})
            command.assert_not_called()

    def test_production_busy_blocks_start_and_cleans_up_before_lock_release(self):
        with tempfile.TemporaryDirectory() as directory:
            locks = [str(Path(directory) / name) for name in ('one.lock', 'two.lock')]
            with patch.object(schedule, 'LOCKS', locks), patch.object(schedule, 'deadline', return_value=datetime.now(schedule.MOSCOW) + timedelta(hours=1)), patch.object(schedule, 'owned', return_value={'status': 'created'}), patch.object(schedule, 'gate', side_effect=schedule.resources.Refused('production_busy')), patch.object(schedule, 'cleanup') as cleanup, patch.object(schedule, 'save'), patch.object(schedule.resources, 'command') as command:
                with self.assertRaisesRegex(schedule.resources.Refused, 'production_busy'):
                    schedule.supervise({}, schedule.time.time() + 60)
                command.assert_not_called()
                cleanup.assert_called_once_with({})

    def test_busy_shared_lock_prevents_launch(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(schedule, 'LOCKS', [str(Path(directory) / 'lock')]), patch.object(schedule, 'deadline', return_value=datetime.now(schedule.MOSCOW) + timedelta(hours=1)), patch.object(schedule.fcntl, 'flock', side_effect=BlockingIOError), patch.object(schedule.resources, 'command') as command:
                with self.assertRaisesRegex(schedule.resources.Refused, 'lab_busy'):
                    schedule.supervise({}, schedule.time.time() + 60)
                command.assert_not_called()

    def test_unconfirmed_stop_fails_closed(self):
        with patch.object(schedule, 'owned', return_value={'running': True}), patch.object(schedule.resources, 'command'):
            with self.assertRaisesRegex(schedule.resources.Refused, 'research_stop_unconfirmed'):
                schedule.cleanup({'container': 'a' * 64})

    def test_image_must_match_verified_cancellation_receipt(self):
        job = {'container': 'a' * 64, 'run_id': 'b' * 16, 'kind': 'training', '_image_id': 'c' * 64}
        row = {'id': job['container'], 'labels': {schedule.OWNER_LABEL: job['run_id'], schedule.KIND_LABEL: 'training'}}
        with patch.object(schedule.resources, 'inspect', return_value=row), patch.object(schedule.resources, 'command', return_value='sha256:' + 'd' * 64):
            with self.assertRaisesRegex(schedule.resources.Refused, 'cancellation_image_mismatch'):
                schedule.owned(job)

    def test_nonfinite_deadline_never_starts(self):
        with patch.object(schedule, 'deadline', return_value=datetime.now(schedule.MOSCOW) + timedelta(hours=1)), patch.object(schedule.resources, 'command') as command:
            for value in (float('nan'), float('inf'), float('-inf')):
                with self.assertRaisesRegex(schedule.resources.Refused, 'outside_research_window'):
                    schedule.supervise({}, value)
            command.assert_not_called()


if __name__ == '__main__':
    unittest.main()
