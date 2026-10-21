"""Inspect ALL durable states, not the API's last 30 sessions. Fail closed."""
import json
from pathlib import Path
from states import TERMINAL

states = [json.loads(path.read_text()) for path in Path('/data/telemost').glob('*/state.json')]
# Uncertain imports and incomplete recordings need operator reconciliation before upgrade.
blocked = sum(state['status'] not in TERMINAL or state['status'] in {'upload_uncertain', 'partial_saved'}
              for state in states)
print(json.dumps({'sessions': len(states), 'blocked': blocked}))
raise SystemExit(1 if blocked else 0)
