"""Synthetic transport helpers for CPU tests, never a model or acceptance oracle."""
from app.source_wire import unpack


def source_spans(packet):
    if 'unit_columns' not in packet:
        return unpack({k: packet[k] for k in ['source_columns', 'source_spans']})
    result = []
    for row in packet['source_spans']:
        span = dict(zip(packet['source_columns'], row))
        span['text'] = ''.join(unit[1] for unit in span.pop('source_units'))
        result.append(span)
    return result


def inventory_value(packet, roles):
    acts = []
    for row in packet['source_spans']:
        span = dict(zip(packet['source_columns'], row))
        value = roles.get(span['source_id'])
        if value is not None:
            acts.append({'unit_refs': [unit[0] for unit in span['source_units']],
                         'roles': [value] if isinstance(value, str) else value})
    return {'source_reading': 'complete', 'unit_plan_sha256': packet['unit_plan_sha256'], 'acts': acts}
