"""Explicit active-state fixture for pre-existing structural/packing tests only."""
import json
from app.action_status import CLASSIFY_SCHEMA, CHECK_SCHEMA


def status_fixture(user, spec):
    if spec == CHECK_SCHEMA:
        return {'verdict': 'confirmed'}
    if spec == CLASSIFY_SCHEMA:
        value = json.loads(user)
        quote = value['candidate']['original_quote']
        row = next(row for row in value['source'] if row['text'].count(quote) == 1)
        return {'state': 'active', 'support': [{'source_id': row['source_id'], 'quote': quote}]}
    raise AssertionError('not_an_action_status_fixture_call')
