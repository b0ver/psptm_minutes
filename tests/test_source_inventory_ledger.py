"""Structural protocol and retry tests, with synthetic responses and no model access."""
import copy
import json
import unittest
from app import action_status as contract
from app.source_unit_protocol import SCHEMA as DISCOVERY_SCHEMA
from app.source_wire import unpack
from source_unit_fixture import source_spans, inventory_value
from app.status_scan import scan_all_windows, SCAN_SCHEMA, REVIEW_SCHEMA
from app.status_packets import prepare
from app.status_finalize import finalize, CLASSIFY_PREFIX
from app.status_windows import plan_windows, SourceWindowError
from app.reports import validate_json


class SourceInventoryLedger(unittest.TestCase):
    def setUp(self):
        self.source = [
            {'id': 'L00001', 'text': 'Принимаю задачу.', 'start': 0, 'end': 1},
            {'id': 'L00002', 'text': 'Справочные сведения.', 'start': 2, 'end': 3},
            {'id': 'L00003', 'text': 'Отменяем задачу.', 'start': 4, 'end': 5}]
        self.windows = plan_windows(self.source, lambda rows: len(rows) == 1, 3)
        self.candidates = [{'action_ref': 'A00001', 'task': 'Задача', 'original_quote': 'Принимаю задачу.',
            'original_quote_source_spans': [{'source_id': 'L00001', 'start_char': 0,
                'end_char': len('Принимаю задачу.'), 'quote': 'Принимаю задачу.'}]}]
        self.contract = {key: getattr(contract, key) for key in
                         ['STATE_RULES', 'CLASSIFY_SCHEMA', 'CHECK_SCHEMA', 'verify_support']}

    def client(self, before=5, retry=False):
        outer = self
        class Scripted:
            native_calls = before
            packets = []
            def chat(self, system, user, schema):
                self.native_calls += 2 if retry and not self.packets else 1
                packet = json.loads(user)
                self.packets.append((schema, packet))
                spans = source_spans(packet)
                positive = [row for row in spans if row['source_id'] != 'L00002']
                roles = lambda row: ['assignment'] if row['source_id'] == 'L00001' else ['cancellation']
                if schema == DISCOVERY_SCHEMA:
                    outer.assertNotIn('candidates', packet)
                    return inventory_value(packet, {row['source_id']: roles(row) for row in positive})
                if schema in (SCAN_SCHEMA, REVIEW_SCHEMA):
                    value = {'action_ref': 'A00001', 'presence': 'present' if positive else 'absent',
                             'roles': sorted({role for row in positive for role in roles(row)}),
                             'support': [{'span_ref': row['span_ref'], 'quote': row['text'],
                                          'start_char': row['start_char'], 'end_char': row['end_char']} for row in positive]}
                    if schema == REVIEW_SCHEMA:
                        value.update(verdict='confirmed', reason_code='window_assessment_supported')
                        return {'assessment_coverage': 'complete', 'unreviewed_action_refs': [],
                                'uncertain_assessment_refs': [], 'results': [value]}
                    return {'results': [value]}
                support = [{'source_id': 'L00003', 'quote': outer.source[-1]['text']}]
                if schema == contract.CLASSIFY_SCHEMA:
                    return {'state': 'cancelled', 'support': support}
                return {'verdict': 'confirmed', 'reason_code': 'state_supported', 'support': support}
        return Scripted()

    def scan(self, client):
        return scan_all_windows(self.source, self.windows, self.candidates, client,
                                validate_json, lambda *args: True, 64 - client.native_calls, inventory=True)

    def final(self, client, ledger, prior=5):
        return finalize(self.source, self.windows, self.candidates, ledger, {'A00001': 'поручено'}, client,
                        validate_json, lambda *args: True, self.contract, consumed_before_scan=prior)

    def test_actual_retry_counts_replay_exactly_and_late_cancel_stays_in_final_context(self):
        client = self.client(retry=True)
        ledger = self.scan(client)
        self.assertEqual((ledger['native_calls_before'], ledger['native_calls_after'], ledger['model_calls']), (5, 15, 10))
        self.assertEqual(ledger['windows'][0]['native_attempts'], [2, 1, 1])
        result = self.final(client, ledger)
        self.assertTrue(result['status_component_confirmed'])
        self.assertFalse(result['action_quality_accepted'])
        self.assertEqual(client.native_calls, 17)
        final_packet = client.packets[-1][1]
        self.assertEqual([row['source_id'] for row in unpack({k: final_packet[k] for k in ['source_columns', 'source_spans']})],
                         ['L00001', 'L00002', 'L00003'])
        self.assertEqual(final_packet['included_window_indices'], [0, 1, 2])

    def test_changed_wire_hash_inventory_quote_or_attempt_count_blocks_before_final_call(self):
        client = self.client()
        original = self.scan(client)
        for mutation in ['hash', 'quote', 'attempts', 'system', 'missing_window']:
            ledger = copy.deepcopy(original)
            row = ledger['windows'][0]
            if mutation == 'hash': row['scan_packet_sha256'] = '0' * 64
            elif mutation == 'quote': row['source_inventory']['source_model_response']['acts'][0]['unit_refs'] = ['FOREIGN']
            elif mutation == 'attempts': row['native_attempts'][0] = 0
            elif mutation == 'system': row['opposed_system_sha256'] = '0' * 64
            else: ledger['windows'].pop()
            before = len(client.packets)
            with self.subTest(mutation=mutation), self.assertRaises((SourceWindowError, ValueError)):
                self.final(client, ledger)
            self.assertEqual(len(client.packets), before)

    def test_prior_stages_plus_real_scan_retries_reserve_all_final_calls(self):
        client = self.client(before=53, retry=True)
        ledger = self.scan(client)
        self.assertEqual(client.native_calls, 63)
        with self.assertRaisesRegex(SourceWindowError, 'total_call_budget'):
            self.final(client, ledger, prior=53)
        self.assertEqual(len(client.packets), 9)

    def test_final_context_refusal_does_not_select_only_quotes_or_last_window(self):
        client = self.client()
        ledger = self.scan(client)
        def fits(system, user, schema):
            return 'candidate' not in json.loads(user)
        with self.assertRaisesRegex(SourceWindowError, 'relevant_context_does_not_fit'):
            prepare(self.source, self.windows, self.candidates, ledger, validate_json, fits,
                    CLASSIFY_PREFIX, contract.CLASSIFY_SCHEMA, 59)
        self.assertEqual(len(client.packets), 9)

    def test_final_retries_are_included_in_actual_global_attempts_and_result(self):
        client = self.client()
        ledger = self.scan(client)
        original = client.chat
        def retry(system, user, schema):
            client.native_calls += 1
            return original(system, user, schema)
        client.chat = retry
        result = self.final(client, ledger)
        self.assertEqual(client.native_calls, 18)
        self.assertEqual(result['model_calls_including_scan'], 13)

    def test_changed_actual_counter_refuses_before_any_final_call(self):
        client = self.client()
        ledger = self.scan(client)
        client.native_calls += 1
        with self.assertRaisesRegex(SourceWindowError, 'counter_provenance'):
            self.final(client, ledger)
        self.assertEqual(len(client.packets), 9)

    def test_legacy_discovery_revision_and_foreign_unit_plan_are_not_converted(self):
        client = self.client()
        original = self.scan(client)
        for kind in ['revision', 'unit_plan']:
            ledger = copy.deepcopy(original)
            if kind == 'revision':
                ledger['pipeline_revision'] = 'source_only_packed_v1'
            else:
                ledger['windows'][0]['source_inventory']['source_model_response']['unit_plan_sha256'] = '0' * 64
            with self.subTest(kind=kind), self.assertRaises(SourceWindowError):
                self.final(client, ledger)
            self.assertEqual(len(client.packets), 9)
