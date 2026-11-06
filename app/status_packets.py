"""Lossless final-context assembly. Preparation is never semantic acceptance."""
import json
from copy import deepcopy
from app.status_windows import SourceWindowError
from app.status_scan import scan_all_windows, digest, SCAN_SCHEMA, REVIEW_SCHEMA
from app.source_unit_protocol import PIPELINE_REVISION


def prepare(source, windows, candidates, ledger, validate, fits, system, schema, maximum_calls):
    revision = ledger.get('pipeline_revision')
    if revision not in (None, PIPELINE_REVISION):
        raise SourceWindowError('source_status_final_pipeline_revision_changed')
    inventory = revision == PIPELINE_REVISION
    if (ledger.get('source_hash') != digest(source) or ledger.get('candidates_hash') != digest(candidates)
            or ledger.get('action_quality_accepted') is not False
            or ledger.get('final_reconciliation_required') is not True
            or len(ledger.get('windows', [])) != len(windows)):
        raise SourceWindowError('source_status_final_ledger_provenance_changed')
    if inventory:
        from app.source_wire import pack
        from app.source_unit_protocol import SCHEMA as INVENTORY_SCHEMA, plan as unit_plan
        if (type(ledger.get('model_calls')) is not int
                or ledger['model_calls'] < 3 * len(windows)
                or type(ledger.get('native_calls_before')) is not int
                or type(ledger.get('native_calls_after')) is not int
                or ledger['native_calls_after'] - ledger['native_calls_before'] != ledger['model_calls']):
            raise SourceWindowError('source_status_final_native_accounting_changed')
    needed = (ledger['model_calls'] if inventory else 2 * len(windows)) + 2 * len(candidates)
    if type(maximum_calls) is not int or not needed <= maximum_calls <= 64:
        raise SourceWindowError('source_status_final_total_call_budget_exceeded')

    class Replay:
        def __init__(self):
            self.index = 0
            if inventory:
                self.native_calls = ledger['native_calls_before']

        def chat(self, _system, user, spec):
            index, phase = divmod(self.index, 3 if inventory else 2)
            opposed = phase == (2 if inventory else 1)
            record = ledger['windows'][index]
            packet = json.loads(user)
            if inventory:
                expected = unit_plan(windows[index])['wire'] if phase == 0 else pack(windows[index])
                if phase != 0:
                    expected.update(candidates=candidates, source_acts=record['source_inventory']['source_acts'])
                if opposed:
                    expected['scan'] = record['scan']
                expected_schema = INVENTORY_SCHEMA if phase == 0 else REVIEW_SCHEMA if opposed else SCAN_SCHEMA
                expected_sha = (record['source_inventory']['source_packet_sha256'] if phase == 0 else
                                record['opposed_packet_sha256' if opposed else 'scan_packet_sha256'])
                import hashlib
                attempts = record.get('native_attempts')
                if (record['window_index'] != index or record['window'] != windows[index]
                        or record['window_hash'] != digest(windows[index])
                        or packet != expected or spec != expected_schema
                        or hashlib.sha256(user.encode()).hexdigest() != expected_sha
                        or not isinstance(attempts, list) or len(attempts) != 3
                        or any(type(n) is not int or n < 1 for n in attempts)):
                    raise SourceWindowError('source_status_final_window_receipt_changed')
                self.native_calls += attempts[phase]
                self.index += 1
                return deepcopy(record['source_inventory']['source_model_response'] if phase == 0
                                else record['opposed_review' if opposed else 'scan'])
            if (record['window_index'] != index or record['window'] != windows[index]
                    or record['window_hash'] != digest(windows[index])
                    or packet['source_spans'] != windows[index]
                    or packet['candidates'] != candidates
                    or spec != (REVIEW_SCHEMA if opposed else SCAN_SCHEMA)):
                raise SourceWindowError('source_status_final_window_receipt_changed')
            self.index += 1
            return deepcopy(record['opposed_review' if opposed else 'scan'])

    # Reapply every structural/quote/coverage gate to the complete journal.
    checked = scan_all_windows(source, windows, candidates, Replay(), validate, fits, maximum_calls,
                               inventory=inventory)
    if checked != ledger:
        raise SourceWindowError('source_status_final_scan_receipt_changed')
    packets = []
    for candidate in candidates:
        positive = checked['positive_window_indices'][candidate['action_ref']]
        # Full positive windows and both neighboring windows. Never a quote-only summary.
        included = sorted({neighbor for index in positive for neighbor in (index - 1, index, index + 1)
                           if 0 <= neighbor < len(windows)})
        relevant = [deepcopy(span) for index in included for span in windows[index]]
        excluded = [index for index in range(len(windows)) if index not in included]
        packet = {'candidate': deepcopy(candidate), 'source_spans': relevant,
                  'complete_source_hash': digest(source), 'complete_scan_ledger_hash': digest(checked),
                  'included_window_indices': included, 'excluded_window_indices': excluded,
                  'excluded_windows_checked_absent': True,
                  'context_policy': 'all_relevant_complete_windows_and_neighbors_in_original_order'}
        if inventory:
            packet.update(pack(relevant))
        if fits(system, json.dumps(packet, ensure_ascii=False), schema) is not True:
            raise SourceWindowError('source_status_final_relevant_context_does_not_fit')
        packets.append(packet)
    return {'packets': packets, 'source_hash': digest(source), 'scan_ledger_hash': digest(checked),
            'total_required_calls': needed, 'action_quality_accepted': False,
            'final_model_classification_and_opposed_review_required': True,
            'native_payload_admission_required': True}


def opposed_packet(packet, classification, fits, system, schema):
    """Check the actual full packet AFTER classification; never pre-admit a shorter proxy."""
    result = {**deepcopy(packet), 'classification': deepcopy(classification)}
    if fits(system, json.dumps(result, ensure_ascii=False), schema) is not True:
        raise SourceWindowError('source_status_final_opposed_context_does_not_fit')
    return result
