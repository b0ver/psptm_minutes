import csv
import html
import json
import statistics
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def quantile(values, fraction):
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int((len(ordered) - 1) * fraction))]


def summarize():
    models = json.loads((ROOT / 'models.json').read_text())['models']
    catalog = json.loads((ROOT / 'assets/meeting_kit/templates.json').read_text())
    records = []
    for path in (ROOT / 'private/results').glob('*.json'):
        record = json.loads(path.read_text())['aggregate']
        if record['track'] in ('synthetic', 'real'):
            records.append(record)
    judgments = []
    if (ROOT / 'judgments.jsonl').exists():
        judgments = [json.loads(line) for line in (ROOT / 'judgments.jsonl').read_text().splitlines()]
    rows = []
    for track, planned in (('synthetic', 10), ('real', 5)):
        for model in models:
            for template in catalog:
                group = [record for record in records if record['track'] == track and record['model'] == model['name'] and record['type'] == template]
                successes = [record for record in group if record['status'] == 'completed']
                calls = [call for record in group for call in record.get('calls', []) if not call.get('cached') and call.get('wall_seconds') is not None]
                independent = [judgment for judgment in judgments if judgment['track'] == track and judgment['model'] == model['name'] and judgment['type'] == template and judgment['status'] == 'completed']
                automatic = [record['automatic'] for record in successes if 'automatic' in record]
                row = {'track': track, 'model': model['name'], 'type': template, 'profile': model['profile'], 'planned': planned, 'completed': len(successes), 'failed': len(group) - len(successes), 'pending': planned - len(group), 'judgments_completed': len(independent), 'cold_calls': sum(call.get('cold', False) for call in calls), 'warm_calls': sum(not call.get('cold', False) for call in calls), 'call_p50_seconds': quantile([call['wall_seconds'] for call in calls], .5), 'call_p95_seconds': quantile([call['wall_seconds'] for call in calls], .95), 'max_loaded_vram_bytes': max((call.get('vram_bytes') or 0 for call in calls), default=None), 'guard_rejections': sum(record.get('error') == 'context_budget_guard' for record in group)}
                for name in ('facts_assertion_recall', 'report_assertion_recall', 'section_coverage', 'exact_evidence_rate'):
                    values = [item[name] for item in automatic if item.get(name) is not None]
                    row[name] = statistics.mean(values) if values else None
                rows.append(row)
    with (ROOT / 'results.csv').open('w') as output:
        writer = csv.DictWriter(output, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    columns = ['track', 'model', 'type', 'profile', 'planned', 'completed', 'failed', 'pending', 'judgments_completed']
    table = ['| ' + ' | '.join(columns) + ' |', '| ' + ' | '.join(['---'] * len(columns)) + ' |']
    table.extend('| ' + ' | '.join(str(row[column]) for column in columns) + ' |' for row in rows)
    completed = sum(row['completed'] for row in rows)
    failed = sum(row['failed'] for row in rows)
    pending = sum(row['pending'] for row in rows)
    note = f'Выполнено {completed}; failed {failed}; ожидают {pending}. Рейтинг качества и победитель не установлены: нужны полное покрытие, независимые оценки, проверка технических отказов и финалистский аудит.'
    (ROOT / 'leaderboard.md').write_text('# Покрытие benchmark\n\n' + note + '\n\n' + '\n'.join(table) + '\n')
    header = '<tr>' + ''.join('<th>' + html.escape(column) + '</th>' for column in columns) + '</tr>'
    body = ''.join('<tr>' + ''.join('<td>' + html.escape(str(row[column])) + '</td>' for column in columns) + '</tr>' for row in rows)
    (ROOT / 'leaderboard.html').write_text('<!doctype html><meta charset="utf-8"><title>Benchmark coverage</title><style>body{font:14px system-ui;margin:24px}table{border-collapse:collapse}th,td{border:1px solid #ddd;padding:5px}th{position:sticky;top:0;background:#eee}</style><h1>Покрытие benchmark</h1><p>' + html.escape(note) + '</p><table>' + header + body + '</table>')
    (ROOT / 'coverage.json').write_text(json.dumps({'completed': completed, 'failed': failed, 'pending': pending, 'rows': len(rows)}, indent=2))
    return note


if __name__ == '__main__':
    print(summarize())
