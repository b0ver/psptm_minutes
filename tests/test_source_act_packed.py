"""CPU protocol checks. Scripted answers do not establish model semantics."""
import copy
import hashlib
import json
import unittest
from app.reports import validate_json
from app.source_act_inventory import discover_window, InventoryError, SCHEMA, SYSTEM
from app.source_wire import unpack, SYSTEM_SUFFIX, WireError


class PackedDiscovery(unittest.TestCase):
    def setUp(self):
        self.window = [{'source_id': 'L17', 'span_ref': 'S00004', 'start_char': 6,
                        'end_char': 6 + len(' Отменяем проверку.\n'), 'text': ' Отменяем проверку.\n',
                        'start': 1.5, 'end': 9, 'speaker_hint': None}]
        self.calls = []
        self.admissions = []
        self.value = {'source_reading': 'complete', 'acts': [
            {'span_ref': 'S00004', 'quote': 'Отменяем проверку.', 'roles': ['cancellation']}]}
        outer = self
        class Client:
            native_calls = 7
            def chat(self, system, user, schema):
                self.native_calls += 1
                outer.calls.append((system, user, schema))
                return copy.deepcopy(outer.value)
        self.client = Client()

    def fits(self, *args):
        self.admissions.append(args)
        return True

    def run_window(self):
        return discover_window(self.window, self.client, validate_json, self.fits,
                               self.client.native_calls, packed=True)

    def test_admission_and_model_receive_identical_actual_packed_wire(self):
        receipt = self.run_window()
        self.assertEqual(self.calls, self.admissions)
        system, user, schema = self.calls[0]
        self.assertEqual(system, SYSTEM + '\n' + SYSTEM_SUFFIX)
        self.assertEqual(schema, SCHEMA)
        packet = json.loads(user)
        self.assertEqual(set(packet), {'source_columns', 'source_spans'})
        self.assertEqual(unpack(packet), self.window)
        self.assertEqual(receipt['source_packet_sha256'], hashlib.sha256(user.encode()).hexdigest())
        self.assertEqual(receipt['source_system_sha256'], hashlib.sha256(system.encode()).hexdigest())
        self.assertEqual(receipt['source_wire_revision'], 'lossless_columns_v1')
        self.assertEqual(receipt['source_acts'][0]['start_char'], 7)
        self.assertTrue(receipt['source_acts'][0]['source_act_ref'].startswith('W' + receipt['source_packet_sha256'] + ':'))
        self.assertFalse(receipt['action_quality_accepted'])

    def test_hint_presence_and_whitespace_are_bound_to_wire_identity(self):
        first = self.run_window()
        del self.window[0]['speaker_hint']
        second = self.run_window()
        self.assertNotEqual(first['source_packet_sha256'], second['source_packet_sha256'])
        self.assertEqual(unpack(json.loads(self.calls[-1][1])), self.window)

    def test_unknown_metadata_refuses_before_admission_or_inference(self):
        self.window[0]['unrecognized_source_metadata'] = 'must not disappear'
        with self.assertRaises(WireError):
            self.run_window()
        self.assertEqual(self.calls, [])
        self.assertEqual(self.admissions, [])

    def test_empty_inventory_still_requires_matching_and_independent_review(self):
        self.value['acts'] = []
        receipt = self.run_window()
        self.assertFalse(receipt['absence_accepted'])
        self.assertTrue(receipt['candidate_matching_still_required'])
        self.assertTrue(receipt['independent_window_review_still_required'])

    def test_source_only_wire_cannot_hide_exhausted_global_native_budget(self):
        self.client.native_calls = 64
        with self.assertRaises(InventoryError):
            self.run_window()
        self.assertEqual(self.calls, [])
