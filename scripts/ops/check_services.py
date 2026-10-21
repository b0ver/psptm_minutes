"""Validate Compose JSON (array or JSON lines), without echoing environment/labels."""
import json
import sys
from pathlib import Path

raw = Path(sys.argv[1]).read_text()
try:
    rows = json.loads(raw)
    if isinstance(rows, dict):
        rows = [rows]
except json.JSONDecodeError:
    rows = [json.loads(line) for line in raw.splitlines() if line.strip()]
rows = {row['Service']: row for row in rows}
required = {'db', 'api', 'worker', 'analysis-worker', 'telegram', 'calendar-scheduler', 'telemost-agent'}
failures = []
for name in sorted(required):
    row = rows.get(name, {})
    if row.get('State') != 'running' or row.get('Health') in {'unhealthy', 'starting'}:
        failures.append(name)
    elif name in {'db', 'api', 'telemost-agent'} and row.get('Health') != 'healthy':
        failures.append(name)
print('services_ok' if not failures else 'services_failed:' + ','.join(failures))
raise SystemExit(bool(failures))
