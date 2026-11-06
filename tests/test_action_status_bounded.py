"""Structural application integration tests; no semantic quality claim or model calls."""
import copy
import json
import unittest
from app.action_status import classify_actions, CLASSIFY_SCHEMA, CHECK_SCHEMA
from app.status_scan import SCAN_SCHEMA, REVIEW_SCHEMA
from app.source_act_inventory import SCHEMA as INVENTORY_SCHEMA
from app.source_wire import unpack
from app.ai import ProcessingError

ITEMS = [{'id': 'L00001', 'start': 0, 'end': 1, 'text': 'Принимаю задачу.'},
         {'id': 'L00002', 'start': 2, 'end': 3, 'text': 'Отменяем задачу.'}]
ACTIONS = [{'task': 'Задача', 'evidence': ITEMS[0]['text'], 'kind': 'поручено',
            'assignee': None, 'due_date': None}]


def validate(value, spec):
    # Use the actual unchanged candidate validator without application/database dependencies.
    from app.reports import validate_json
    validate_json(value, spec)


class LedgerModel:
    def __init__(self, mutation=None, consumed=0):
        self.native_calls, self.calls, self.mutation = consumed, [], mutation

    def status_packet_fits(self, system, user, schema):
        return 'source' not in json.loads(user)  # Exercise the bounded path, preserving actual final packets.

    def chat(self, system, user, spec):
        packet = json.loads(user)
        self.calls.append(packet)
        self.native_calls += 1
        if spec == INVENTORY_SCHEMA:
            spans = unpack(packet)
            value = {'source_reading': 'complete', 'acts': [
                {'span_ref': span['span_ref'], 'quote': span['text'],
                 'roles': ['cancellation'] if span['source_id'] == 'L00002' else ['assignment']}
                for span in spans]}
        elif spec in [SCAN_SCHEMA, REVIEW_SCHEMA]:
            spans = unpack({key: packet[key] for key in ['source_columns', 'source_spans']})
            support = [{'span_ref': row['span_ref'], 'start_char': row['start_char'],
                        'end_char': row['end_char'], 'quote': row['text']} for row in spans]
            roles = ['assignment', 'cancellation'] if len(spans) == 2 else ['assignment']
            row = {'action_ref': 'A00001', 'presence': 'present', 'roles': roles, 'support': support}
            if spec == REVIEW_SCHEMA:
                row['verdict'] = 'confirmed'
                row['reason_code'] = 'window_assessment_supported'
                value = {'assessment_coverage': 'complete', 'unreviewed_action_refs': [], 'uncertain_assessment_refs': [], 'results': [row]}
            else:
                value = {'results': [row]}
        elif spec == CLASSIFY_SCHEMA:
            value = {'state': 'cancelled', 'support': [{'source_id': 'L00002', 'quote': ITEMS[-1]['text']}]}
        else:
            value = {'verdict': 'confirmed', 'reason_code': 'state_supported',
                     'support': [{'source_id': 'L00002', 'quote': ITEMS[-1]['text']}]}
        if self.mutation:
            self.mutation(value, packet, spec)
        return value


class BoundedApplicationStatus(unittest.TestCase):
    def test_late_cancellation_becomes_context_without_changing_original_action(self):
        original = copy.deepcopy(ACTIONS)
        model = LedgerModel()
        active, context, audit = classify_actions(ACTIONS, ITEMS, model, validate, 45000)
        self.assertEqual(ACTIONS, original)
        self.assertEqual(active, [])
        self.assertEqual(context[0]['activity_state'], 'cancelled')
        self.assertEqual(context[0]['source_evidence'][0]['quote'], ITEMS[-1]['text'])
        self.assertEqual(len(model.calls), 5)
        self.assertTrue(audit[0]['status_component_only'])
        self.assertEqual(audit[0]['bounded_scan_ledger']['windows'][0]['window'][-1]['text'], ITEMS[-1]['text'])

    def test_context_cannot_drop_source_speaker_time_or_characters(self):
        items = copy.deepcopy(ITEMS)
        items[1]['speaker_hint'] = 'Synthetic observer'
        model = LedgerModel()
        classify_actions(ACTIONS, items, model, validate, 45000)
        for call in model.calls:
            spans = unpack({key: call[key] for key in ['source_columns', 'source_spans']})
            self.assertEqual(spans[-1]['speaker_hint'], 'Synthetic observer')
            self.assertEqual(spans[-1]['start'], 2)
            self.assertEqual(spans[-1]['end'], 3)
            self.assertEqual(spans[-1]['text'], ITEMS[-1]['text'])

    def test_coverage_uncertainty_and_unsupported_final_state_fail_closed(self):
        for stage in ['assessment_coverage', 'final']:
            def mutate(value, packet, spec):
                if stage == 'assessment_coverage' and spec == REVIEW_SCHEMA: value['assessment_coverage'] = 'uncertain'
                if stage == 'final' and spec == CHECK_SCHEMA:
                    value.update(verdict='uncertain', reason_code='insufficient_evidence')
            with self.subTest(stage=stage), self.assertRaises(ProcessingError):
                classify_actions(ACTIONS, ITEMS, LedgerModel(mutate), validate, 45000)

    def test_existing_calls_are_included_before_starting_scan(self):
        model = LedgerModel(consumed=63)
        with self.assertRaisesRegex(ProcessingError, 'call_budget'):
            classify_actions(ACTIONS, ITEMS, model, validate, 45000)
        self.assertEqual(model.calls, [])

    def test_changed_or_ambiguous_original_quote_sends_no_request(self):
        actions = copy.deepcopy(ACTIONS)
        actions[0]['evidence'] = 'Другая задача.'
        model = LedgerModel()
        with self.assertRaises(ProcessingError): classify_actions(actions, ITEMS, model, validate, 45000)
        self.assertEqual(model.calls, [])

    def test_legacy_inventory_coverage_namespace_cannot_replace_window_assessment(self):
        def mutate(value, packet, spec):
            if spec == REVIEW_SCHEMA:
                value['coverage'] = value.pop('assessment_coverage')
        model = LedgerModel(mutate)
        with self.assertRaises(ProcessingError): classify_actions(ACTIONS, ITEMS, model, validate, 45000)
        self.assertEqual(len(model.calls), 3)

    def test_confirmed_requires_explicit_supported_window_assessment_reason(self):
        for reason in [None, 'insufficient_context']:
            def mutate(value, packet, spec):
                if spec == REVIEW_SCHEMA:
                    if reason is None: value['results'][0].pop('reason_code')
                    else: value['results'][0]['reason_code'] = reason
            model = LedgerModel(mutate)
            with self.subTest(reason=reason), self.assertRaises(ProcessingError):
                classify_actions(ACTIONS, ITEMS, model, validate, 45000)
            self.assertEqual(len(model.calls), 3)

    def test_discovery_precedes_matching_and_receipts_bind_actual_packets(self):
        import hashlib
        model = LedgerModel(consumed=8)
        _, _, audit = classify_actions(ACTIONS, ITEMS, model, validate, 45000)
        self.assertEqual(set(model.calls[0]), {'source_columns', 'source_spans'})
        ledger = audit[0]['bounded_scan_ledger']
        self.assertEqual(ledger['pipeline_revision'], 'source_only_packed_v1')
        self.assertEqual((ledger['native_calls_before'], ledger['native_calls_after']), (8, 11))
        self.assertEqual(ledger['model_calls'], 3)
        record = ledger['windows'][0]
        self.assertEqual(record['native_attempts'], [1, 1, 1])
        self.assertEqual(model.calls[1]['source_acts'], record['source_inventory']['source_acts'])
        self.assertEqual(record['scan_packet_sha256'], hashlib.sha256(
            json.dumps(model.calls[1], ensure_ascii=False).encode()).hexdigest())

    def test_uncertain_source_only_reading_prevents_matching(self):
        def mutate(value, packet, spec):
            if spec == INVENTORY_SCHEMA:
                value['source_reading'] = 'uncertain'
        model = LedgerModel(mutate)
        with self.assertRaises(ProcessingError):
            classify_actions(ACTIONS, ITEMS, model, validate, 45000)
        self.assertEqual(len(model.calls), 1)

    def test_insufficient_three_stage_budget_refuses_before_any_call(self):
        model = LedgerModel(consumed=60)
        with self.assertRaisesRegex(ProcessingError, 'call_budget'):
            classify_actions(ACTIONS, ITEMS, model, validate, 45000)
        self.assertEqual(model.calls, [])


if __name__ == '__main__':
    unittest.main()
