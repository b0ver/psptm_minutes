import json
import time
from pathlib import Path

import runner

ROOT = Path(__file__).resolve().parent


def gold_facts(case, gold, kit):
    facts = {key: [] for key in kit['schema']('facts')['properties'] if key != 'summary'}
    facts['summary'] = ' '.join(gold['must_include']) if gold['must_include'] else 'Детали по теме не обсуждались.'
    for expected in gold['expected_tasks']:
        evidence = next((segment['text'] for segment in case['segments'] if expected['task'].lower() in segment['text'].lower() and (expected['assignee'] is None or expected['assignee'] in segment['text']) and (expected['due_raw'] is None or expected['due_raw'] in segment['text'])), None)
        if evidence is None:
            raise runner.ProcessingError('gold_task_evidence_missing')
        source = next(segment for segment in case['segments'] if segment['text'] == evidence)
        facts['action_items'].append({'task': expected['task'], 'assignee': expected['assignee'], 'assigned_by': None, 'due_raw': expected['due_raw'], 'due_date': None, 'kind': 'назначено' if expected['assignee'] is None else 'взято_на_себя', 'priority': None, 'evidence': evidence, 'timestamp': kit['timestamp'](source['start']), 'confidence': 'high', 'note': None})
    for quote in gold['expected_evidence'][:2]:
        facts['quotes'].append({'quote': quote, 'speaker': None, 'timestamp': '00:00:00', 'why': 'Эталонный контекст сценария'})
    kit['validate_json'](facts, kit['schema']('facts'))
    facts['_meta'] = {'meeting_title': case['title'], 'meeting_date': 'не указана', 'participants': 'не указаны', 'glossary': 'нет'}
    facts['rejected_items'] = []
    return facts


def main():
    kit = runner.load_kit()
    models = json.loads((ROOT / 'models.json').read_text())['models']
    assets = runner.digest(json.loads((ROOT / 'manifest.json').read_text()))
    gold = {item['id']: item for item in map(json.loads, (ROOT / 'gold.jsonl').read_text().splitlines())}
    for case in map(json.loads, (ROOT / 'dataset.jsonl').read_text().splitlines()):
        facts = gold_facts(case, gold[case['id']], kit)
        for model in models:
            target = ROOT / 'private/render-diagnostics' / (runner.digest({'facts': facts, 'type': case['type'], 'model': model, 'assets': assets}) + '.json')
            if target.exists():
                continue
            llm = runner.LLM(model, kit, assets)
            llm.stage = 'render_only'
            aggregate = {'case': case['id'], 'type': case['type'], 'model': model['name'], 'track': 'render_only'}
            try:
                report = kit['render_report'](case['type'], facts, llm)
                aggregate.update(status='completed', automatic=runner.automatic(case, gold[case['id']], {'facts': facts}, report, kit))
                runner.atomic(target, {'aggregate': aggregate, 'facts': facts, 'report': report})
            except Exception as error:
                aggregate.update(status='failed', error=error.code if isinstance(error, runner.ProcessingError) else type(error).__name__)
                runner.atomic(target, {'aggregate': aggregate})
            runner.append(ROOT / 'render-diagnostics.jsonl', aggregate)
    runner.atomic(ROOT / 'progress.json', {'phase': 'render_diagnostics_finished_finalist_audit_pending', 'time': time.time()})


if __name__ == '__main__':
    main()
