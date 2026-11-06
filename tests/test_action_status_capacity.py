"""Public contrast fixtures and CPU-only packet capacity oracle, not model acceptance."""
import json
import unittest
from app import reports
from app.action_status import classify_actions, CLASSIFY_SCHEMA, CHECK_SCHEMA
from app.status_scan import SCAN_SCHEMA, REVIEW_SCHEMA
from app.source_unit_protocol import SCHEMA as INVENTORY_SCHEMA
from app.source_wire import unpack
from source_unit_fixture import source_spans, inventory_value
from app.ai import ProcessingError


def fixture(name):
    promise = 'Я подготовлю схему для стенда Альфа. Принимаю это обязательство.'
    cancel = 'Подготовку схемы для стенда Альфа отменяем. Схему для стенда Альфа готовить больше не нужно.'
    resume = 'Отмену снимаем: подготовь схему для стенда Альфа. Я подтверждаю обязательство подготовить эту схему.'
    background = 'Справочные данные о синтетическом объекте. ' * 115
    texts = [promise] + [background] * 10
    roles = {0: 'promise'}
    if name == 'late_cancel':
        texts += [background] * 10 + [cancel]
        expected = 'cancelled'; roles[len(texts) - 1] = 'cancellation'
    elif name == 'cancel_then_resume':
        texts += [cancel] + [background] * 10 + [resume]
        expected = 'active'; roles[11] = 'cancellation'; roles[len(texts) - 1] = 'assignment'
    elif name == 'other_task_cancelled':
        texts += [background] * 10 + ['Подготовку списка закупок для стенда Бета отменяем.']
        expected = 'active'
    elif name == 'future_promise_not_completed':
        texts += [background] * 10 + ['Схему для стенда Альфа подготовлю в следующем месяце. Работа еще не выполнена.']
        expected = 'active'; roles[len(texts) - 1] = 'promise'
    else:
        raise ValueError('unknown_public_contrast')
    items = [{'id': f'L{i + 1:05d}', 'start': i * 2, 'end': i * 2 + 1, 'text': text}
             for i, text in enumerate(texts)]
    action = {'task': 'Подготовить схему для стенда Альфа', 'evidence': promise,
              'kind': 'поручено', 'assignee': None, 'due_date': None}
    return items, action, expected, {items[i]['id']: role for i, role in roles.items()}


class PacketOracle:
    """Known public fixture annotations, used only to measure packets without inference."""
    def __init__(self, items, expected, roles):
        self.items, self.expected, self.roles = items, expected, roles
        self.native_calls, self.packets = 0, []

    def status_packet_fits(self, system, user, spec):
        return len(system) + len(user) <= reports.MAX_PROMPT_CHARS and reports.native_payload_admission(
            reports.native_payload(system, user, spec))['admitted']

    def chat(self, system, user, spec):
        self.native_calls += 1
        packet = json.loads(user); self.packets.append(packet)
        if spec == INVENTORY_SCHEMA:
            return inventory_value(packet, self.roles)
        if spec in [SCAN_SCHEMA, REVIEW_SCHEMA]:
            spans = source_spans(packet)
            positive = [row for row in spans if row['source_id'] in self.roles]
            support = [{'span_ref': row['span_ref'], 'start_char': row['start_char'],
                        'end_char': row['end_char'], 'quote': row['text']} for row in positive]
            row = {'action_ref': 'A00001', 'presence': 'present' if positive else 'absent',
                   'roles': sorted({self.roles[span['source_id']] for span in positive}), 'support': support}
            if spec == REVIEW_SCHEMA:
                row['verdict'] = 'confirmed'
                row['reason_code'] = 'window_assessment_supported'
                return {'assessment_coverage': 'complete', 'unreviewed_action_refs': [], 'uncertain_assessment_refs': [], 'results': [row]}
            return {'results': [row]}
        source_id = list(self.roles)[-1]
        original = next(row for row in self.items if row['id'] == source_id)
        support = [{'source_id': source_id, 'quote': original['text']}]
        if spec == CLASSIFY_SCHEMA:
            return {'state': self.expected, 'support': support}
        return {'verdict': 'confirmed', 'reason_code': 'state_supported', 'support': support}


class NativeCrossWindowCapacity(unittest.TestCase):
    def test_complete_known_contexts_fit_without_native_calls_or_semantic_claims(self):
        results = []
        failures = []
        for name in ['late_cancel', 'cancel_then_resume', 'other_task_cancelled', 'future_promise_not_completed']:
            items, action, expected, roles = fixture(name)
            oracle = PacketOracle(items, expected, roles)
            try:
                active, context, audit = classify_actions([action], items, oracle, reports.validate_json, reports.MAX_PROMPT_CHARS)
                self.assertEqual(bool(active), expected == 'active')
                self.assertTrue(audit[0]['status_component_only'])
                results.append({'case': name, 'packet_slots': oracle.native_calls,
                                'full_source_characters': sum(len(row['text']) for row in items), 'capacity_only': True})
            except ProcessingError as error:
                failures.append({'case': name, 'safe_code': error.code, 'packet_slots_before_refusal': oracle.native_calls})
        print(json.dumps({'structural_capacity': results, 'capacity_failures': failures, 'new_model_calls': 0,
                          'semantic_quality_accepted': False}))
        self.assertEqual(failures, [])
