import unittest
from unittest.mock import Mock, patch

import research_cancel_probe as probe


class ProbeAdmissionHandoffTests(unittest.TestCase):
    def test_busy_at_final_handoff_repeats_settle_with_original_deadline(self):
        current = {'container': 'owned'}
        with patch.object(probe.time, 'time', return_value=100), patch.object(probe, 'wait_for_admission') as settle, patch.object(probe.window, 'gate', side_effect=[probe.window.resources.Refused('gpu_busy'), None]) as gate:
            probe.final_admission(current, 125)
        self.assertEqual(settle.call_args_list[0].args, (current, 125))
        self.assertEqual(settle.call_args_list[1].args, (current, 125))
        self.assertEqual(gate.call_count, 2)
        self.assertTrue(all(call.kwargs == {'starting': True} for call in gate.call_args_list))

    def test_repeated_handoff_busy_cannot_extend_45_second_local_limit(self):
        with patch.object(probe.time, 'time', side_effect=[100, 101, 120, 145]), patch.object(probe, 'wait_for_admission') as settle, patch.object(probe.window, 'gate', side_effect=probe.window.resources.Refused('gpu_busy')):
            with self.assertRaisesRegex(probe.window.resources.Refused, 'probe_gpu_settle_timeout'):
                probe.final_admission({}, 1000)
        self.assertEqual(settle.call_count, 2)
        self.assertTrue(all(call.args[1] == 145 for call in settle.call_args_list))

    def test_expired_global_budget_never_checks_or_starts_work(self):
        with patch.object(probe.time, 'time', return_value=100), patch.object(probe, 'wait_for_admission') as settle, patch.object(probe.window, 'gate') as gate:
            with self.assertRaises(probe.window.resources.Refused):
                probe.final_admission({}, 99)
        settle.assert_not_called()
        gate.assert_not_called()

    def test_foreign_or_production_refusals_are_never_retried(self):
        for code in ['foreign_model_loaded', 'foreign_gpu_process', 'production_gpu_busy', 'production_worker_unavailable']:
            with self.subTest(code=code), patch.object(probe.time, 'time', return_value=100), patch.object(probe, 'wait_for_admission') as settle, patch.object(probe.window, 'gate', side_effect=probe.window.resources.Refused(code)):
                with self.assertRaisesRegex(probe.window.resources.Refused, code):
                    probe.final_admission({}, 200)
                settle.assert_called_once()

    def test_settle_refusal_propagates_without_final_gate(self):
        with patch.object(probe.time, 'time', return_value=100), patch.object(probe, 'wait_for_admission', side_effect=probe.window.resources.Refused('foreign_model_loaded')), patch.object(probe.window, 'gate') as gate:
            with self.assertRaisesRegex(probe.window.resources.Refused, 'foreign_model_loaded'):
                probe.final_admission({}, 200)
        gate.assert_not_called()

    def test_unavailable_gate_is_not_treated_as_idle(self):
        with patch.object(probe.time, 'time', return_value=100), patch.object(probe, 'wait_for_admission'), patch.object(probe.window, 'gate', side_effect=RuntimeError('unavailable')):
            with self.assertRaises(RuntimeError):
                probe.final_admission({}, 200)


if __name__ == '__main__':
    unittest.main()
