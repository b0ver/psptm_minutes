"""CPU protocol proofs only. Scripted answers are not native model qualification."""
import copy
import hashlib
import json
import unittest
from app.reports import validate_json, native_payload, native_payload_admission, MAX_PROMPT_CHARS
from app.ai import ProcessingError
from app.source_wire import pack, unpack, SYSTEM_SUFFIX
from app.status_scan import SCAN_SYSTEM, SCAN_SCHEMA, INVENTORY_RULES
from app.status_windows import plan_windows
from app.source_unit_protocol import plan, resolve, discover, SYSTEM, SCHEMA, UnitProtocolError


class SourceUnits(unittest.TestCase):
    def setUp(self):
        self.text = 'Обязательство. ' + 'Повтор. ' * 115
        self.window = [{'span_ref': 'S000001', 'source_id': 'L1', 'start_char': 10,
                        'end_char': 10 + len(self.text), 'start': 1.25, 'end': 4, 'text': self.text,
                        'speaker_hint': None}]
        self.planned = plan(self.window)

    def value(self, refs):
        return {'source_reading': 'complete', 'unit_plan_sha256': self.planned['unit_plan_sha256'],
                'acts': [{'unit_refs': refs, 'roles': ['reference']}]}

    def test_all_characters_times_and_hint_presence_roundtrip_without_filtering(self):
        for text in [self.text, '', ' е\u0308 中文🙂\t\nУсловие: если да; действие.  Конец!?\n']:
            window = copy.deepcopy(self.window)
            window[0].update(text=text, end_char=10 + len(text))
            wire = json.loads(json.dumps(plan(window)['wire'], ensure_ascii=False))
            row = wire['source_spans'][0]
            restored = dict(zip(wire['source_columns'], row))
            restored['text'] = ''.join(unit[1] for unit in restored.pop('source_units'))
            self.assertEqual(restored, window[0])

    def test_repeated_text_selects_explicit_occurrence_never_first_match(self):
        ref = list(self.planned['lookup'])[71]
        unit = self.planned['lookup'][ref]
        result = resolve(self.value([ref]), self.window, self.planned, validate_json)[0]
        self.assertEqual((result['start_char'], result['end_char']), (unit['start_char'], unit['end_char']))
        self.assertNotEqual(result['start_char'], self.window[0]['start_char'] + self.text.index(result['quote']))
        self.assertEqual(result['quote'], self.text[unit['start_char'] - 10:unit['end_char'] - 10])

    def test_whole_consecutive_units_keep_condition_and_every_whitespace(self):
        refs = list(self.planned['lookup'])[:3]
        result = resolve(self.value(refs), self.window, self.planned, validate_json)[0]
        self.assertEqual(result['quote'], ''.join(self.planned['lookup'][r]['text'] for r in refs))

    def test_forged_plan_offsets_or_header_refuse_before_model(self):
        for target in ['lookup', 'header', 'tail']:
            modified = copy.deepcopy(self.planned)
            if target == 'lookup': modified['lookup']['U000001']['start_char'] += 1
            elif target == 'header': modified['wire']['source_columns'][0] = 'action_ref'
            else: modified['wire']['source_spans'][0][6].pop()
            with self.subTest(target=target), self.assertRaises(UnitProtocolError):
                resolve(self.value(['U000001']), self.window, modified, validate_json)
        self.assertEqual(plan(self.window), self.planned)

    def test_foreign_reference_foreign_plan_and_model_offsets_cannot_replace_provenance(self):
        for target in ['reference', 'plan', 'offsets']:
            value = self.value(['U000001'])
            if target == 'reference': value['acts'][0]['unit_refs'] = ['A00001']
            elif target == 'plan': value['unit_plan_sha256'] = '0' * 64
            else: value['acts'][0]['start_char'] = 0
            with self.subTest(target=target), self.assertRaises((UnitProtocolError, ProcessingError)):
                resolve(value, self.window, self.planned, validate_json)

    def test_missing_middle_reversed_and_duplicate_unit_ranges_refuse(self):
        for refs in [['U000001', 'U000003'], ['U000002', 'U000001'], ['U000001', 'U000001']]:
            with self.subTest(refs=refs), self.assertRaises((UnitProtocolError, ProcessingError)):
                resolve(self.value(refs), self.window, self.planned, validate_json)
        value = self.value(['U000001']);value['acts'] *= 2
        with self.assertRaisesRegex(UnitProtocolError, 'duplicate_source_unit_range'):
            resolve(value, self.window, self.planned, validate_json)

    def test_uncertain_reading_or_role_and_whitespace_only_are_never_accepted(self):
        for target in ['reading', 'role']:
            value = self.value(['U000001'])
            if target == 'reading': value['source_reading'] = 'uncertain'
            else: value['acts'][0]['roles'] = ['uncertain']
            with self.subTest(target=target), self.assertRaises(UnitProtocolError):
                resolve(value, self.window, self.planned, validate_json)

    def test_actual_admission_chat_wire_binding_and_retries_include_all_native_attempts(self):
        outer = self
        class Client:
            native_calls = 5
            def chat(self, system, user, spec):
                self.native_calls += 2
                outer.assertEqual((system, user, spec), admitted[0])
                outer.assertNotIn('candidates', json.loads(user))
                return outer.value(['U000001'])
        admitted = []
        def fits(*args): admitted.append(args);return True
        result = discover(self.window, Client(), validate_json, fits, 5)
        self.assertEqual(result['calls_consumed'], 7)
        self.assertEqual(result['source_packet_sha256'], hashlib.sha256(admitted[0][1].encode()).hexdigest())
        self.assertFalse(result['action_quality_accepted'])
        self.assertFalse(result['absence_accepted'])
        self.assertTrue(result['independent_window_review_still_required'])

    def test_budget_and_exact_packet_fit_fail_closed_without_retry(self):
        class Client:
            native_calls = 64
            def chat(self, *args): raise AssertionError('must not call model')
        client = Client()
        with self.assertRaises(UnitProtocolError): discover(self.window, client, validate_json, lambda *x: True, 64)
        client.native_calls = 0
        with self.assertRaises(UnitProtocolError): discover(self.window, client, validate_json, lambda *x: False, 0)

    def test_adjacent_units_from_different_original_spans_cannot_be_joined(self):
        window = copy.deepcopy(self.window)
        other = copy.deepcopy(window[0]);other['span_ref'] = 'S000002';other['source_id'] = 'L2'
        window.append(other)
        planned = plan(window)
        refs = list(planned['lookup'])
        split = next(i for i, ref in enumerate(refs) if planned['lookup'][ref]['source_id'] == 'L2')
        value = {'source_reading': 'complete', 'unit_plan_sha256': planned['unit_plan_sha256'],
                 'acts': [{'unit_refs': refs[split - 1:split + 1], 'roles': ['reference']}]}
        with self.assertRaisesRegex(UnitProtocolError, 'noncontiguous_source_units'):
            resolve(value, window, planned, validate_json)

    def test_empty_discovery_does_not_accept_absence_and_blank_unit_is_not_work_evidence(self):
        value = self.value([]);value['acts'] = []
        self.assertEqual(resolve(value, self.window, self.planned, validate_json), [])
        window = copy.deepcopy(self.window);window[0].update(text='\n  ', end_char=13)
        planned = plan(window)
        value = {'source_reading': 'complete', 'unit_plan_sha256': planned['unit_plan_sha256'],
                 'acts': [{'unit_refs': list(planned['lookup']), 'roles': ['reference']}]}
        with self.assertRaisesRegex(UnitProtocolError, 'source_literal_changed'):
            resolve(value, window, planned, validate_json)

    def test_selected_unit_literal_matches_original_frozen_decoder_on_lossless_subspans(self):
        from app.source_act_inventory import discover_window
        chosen = list(self.planned['lookup'])[71]
        expected = resolve(self.value([chosen]), self.window, self.planned, validate_json)[0]
        subspans = []
        for ref, unit in self.planned['lookup'].items():
            span = {**self.window[0], 'span_ref': ref, 'text': unit['text'],
                    'start_char': unit['start_char'], 'end_char': unit['end_char']}
            subspans.append(span)
        self.assertEqual(''.join(s['text'] for s in subspans), self.text)
        class Offline:
            native_calls = 0
            def chat(self, *args):
                self.native_calls += 1
                return {'source_reading': 'complete', 'acts': [
                    {'span_ref': chosen, 'quote': expected['quote'], 'roles': expected['roles']}]}
        old = discover_window(subspans, Offline(), validate_json, lambda *x: True, 0)['source_acts'][0]
        for key in ['source_id', 'start_char', 'end_char', 'quote', 'roles']:
            self.assertEqual(old[key], expected[key])

    def test_missing_native_increment_or_retry_overflow_cannot_be_accepted(self):
        outer = self
        class Offline:
            native_calls = 63
            def chat(self, *args): return outer.value(['U000001'])
        with self.assertRaisesRegex(UnitProtocolError, 'actual_native_attempts_not_verified'):
            discover(self.window, Offline(), validate_json, lambda *x: True, 63)
        client = Offline()
        def retry(*args):
            client.native_calls += 2
            return outer.value(['U000001'])
        client.chat = retry
        with self.assertRaisesRegex(UnitProtocolError, 'actual_native_attempts_not_verified'):
            discover(self.window, client, validate_json, lambda *x: True, 63)

    def test_unit_provenance_cannot_bypass_original_final_support_uniqueness(self):
        from app.action_status import verify_support
        from app.status_finalize import supported_inside_context
        chosen = list(self.planned['lookup'])[71]
        act = resolve(self.value([chosen]), self.window, self.planned, validate_json)[0]
        original = [{'id': 'L1', 'text': '0123456789' + self.text}]
        with self.assertRaisesRegex(ProcessingError, 'action_status_ungrounded'):
            supported_inside_context([{'source_id': 'L1', 'quote': act['quote']}], original,
                                     {'source_spans': self.window}, {'verify_support': verify_support})

    def test_public_capacity_lower_bound_keeps_all_words_without_semantic_or_full_app_claim(self):
        from test_action_status_capacity import fixture
        results = []
        def fits(system, user, spec):
            return len(system) + len(user) <= MAX_PROMPT_CHARS and native_payload_admission(native_payload(system, user, spec))['admitted']
        for case in ['late_cancel', 'cancel_then_resume', 'other_task_cancelled', 'future_promise_not_completed']:
            source, action, expected, annotations = fixture(case)
            candidate = [{'action_ref': 'A00001', 'task': action['task'], 'original_quote': action['evidence']}]
            def window_fits(window):
                user = json.dumps({**pack(window), 'candidates': candidate, 'source_acts': []}, ensure_ascii=False)
                return (len(user) <= MAX_PROMPT_CHARS // 6
                        and fits(SCAN_SYSTEM + '\n' + SYSTEM_SUFFIX + '\n' + INVENTORY_RULES, user, SCAN_SCHEMA)
                        and fits(SYSTEM, json.dumps(plan(window)['wire'], ensure_ascii=False, separators=(',', ':')), SCHEMA))
            windows = plan_windows(source, window_fits, 20)
            for row in source:
                self.assertEqual(''.join(s['text'] for w in windows for s in w if s['source_id'] == row['id']), row['text'])
            calls = len(windows) * 3 + 2
            self.assertLessEqual(calls, 64)
            results.append({'case': case, 'windows': len(windows), 'lower_bound_calls': calls})
        print(json.dumps({'unit_protocol_capacity_only': results, 'one_action_zero_prior_calls_zero_retries': True,
                          'new_model_calls': 0, 'full_app_capacity_or_semantics_accepted': False}))
