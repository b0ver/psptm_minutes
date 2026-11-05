import json
import unittest
from unittest.mock import patch
import httpx
from app import reports
from app.ai import ProcessingError


class NativeApplicationBudget(unittest.TestCase):
    def make_client(self, response, fail_first=False):
        with patch.object(reports, 'validate_endpoint'):
            llm = reports.LocalLLM()
        llm.client.close()
        calls = []

        def transport(request):
            calls.append(json.loads(request.content))
            if fail_first and len(calls) == 1:
                return httpx.Response(503)
            return httpx.Response(200, json=response)
        llm.client = httpx.Client(base_url='http://local', transport=httpx.MockTransport(transport))
        self.addCleanup(llm.close)
        return llm, calls

    def test_real_payload_admission_keeps_limits_and_charges_actual_format(self):
        payload = reports.native_payload('Synthetic system', 'Synthetic source', {'type': 'object'})
        receipt = reports.native_payload_admission(payload)
        self.assertEqual(payload['options'], {'num_ctx': 32768, 'num_predict': 12000, 'temperature': 0})
        self.assertTrue(receipt['admitted'])
        large = reports.native_payload('Synthetic system', 'Synthetic source', {'enum': ['слово ' * 40000]})
        self.assertFalse(reports.native_payload_admission(large)['admitted'])

    def test_every_attempt_counts_toward_same_global_budget(self):
        llm, calls = self.make_client({'done': True, 'done_reason': 'stop', 'prompt_eval_count': 20,
                                     'eval_count': 3, 'message': {'content': 'Synthetic'}}, fail_first=True)
        llm.native_calls = 63
        with patch.object(reports, 'verify_model'), patch('app.llm_retry.time.sleep'):
            with self.assertRaisesRegex(ProcessingError, 'native_call_budget'):
                llm.chat('Synthetic system', 'Synthetic source')
        self.assertEqual(llm.native_calls, 64)
        self.assertEqual(len(calls), 1)

    def test_missing_native_usage_fails_closed_without_retry(self):
        llm, calls = self.make_client({'done': True, 'done_reason': 'stop', 'message': {'content': 'Synthetic'}})
        with patch.object(reports, 'verify_model'):
            with self.assertRaisesRegex(ProcessingError, 'native_usage_unverified'):
                llm.chat('Synthetic system', 'Synthetic source')
        self.assertEqual(len(calls), 1)

    def test_conservative_grammar_overflow_sends_no_request(self):
        llm, calls = self.make_client({})
        with patch.object(reports, 'verify_model'):
            with self.assertRaisesRegex(ProcessingError, 'native_context_too_large'):
                llm.chat('Synthetic', 'Synthetic', {'enum': ['слово ' * 40000]})
        self.assertEqual(calls, [])
