"""Large source registries must survive bounded reduce and report construction."""
import copy
import json
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import select

from app import reports
from app.ai import ProcessingError
from app.db import Session, Meeting, MeetingAnalysis, MeetingReport

META = {'meeting_title': 'Synthetic', 'meeting_date': 'не указана', 'participants': 'не указаны', 'glossary': 'нет'}


def value_for(spec):
    types = spec.get('type')
    if isinstance(types, list):
        if 'null' in types:
            return None
        types = types[0]
    if 'enum' in spec:
        return spec['enum'][0]
    if types == 'object':
        return {key: value_for(child) for key, child in spec['properties'].items()}
    if types == 'array':
        return []
    return 'Synthetic'


def source_parts(count=15, padding=5500):
    spec = reports.schema('facts')
    parts, items = [], []
    for index in range(count):
        quote = f'Дословная синтетическая цитата {index}.'
        part = value_for(spec)
        part['summary'] = f'Обзор части {index}.'
        for kind, child in spec['properties'].items():
            if kind == 'summary':
                continue
            record = value_for(child['items'])
            record[next(iter(record))] = f'{kind}-{index}'
            if 'evidence' in record:
                record['evidence'] = quote
                record['confidence'] = 'high'
            if kind == 'quotes':
                record['quote'] = quote
            if kind == 'topics':
                record['summary'] = f'Все детали части {index}: ' + 'x' * padding
            part[kind] = [record]
        reports.validate_json(part, spec)
        parts.append(part)
        items.append({'start': index * 10, 'end': index * 10 + 5, 'text': quote})
    return parts, items


class BoundedLLM:
    def __init__(self):
        self.calls = []

    def chat(self, system, user, spec=None):
        assert len(system) + len(user) <= reports.MAX_PROMPT_CHARS
        self.calls.append((system, user, spec))
        if spec and set(spec['properties']) == {'summary'}:
            return {'summary': 'Синтетический общий обзор.'}
        if spec:
            raise AssertionError('An overflowing registry must not be sent for semantic rewrite')
        return '# Синтетическая часть отчёта'

    def close(self):
        pass


def test_large_registry_preserves_all_categories_and_exact_quotes():
    parts, items = source_parts()
    original = copy.deepcopy(parts)
    llm = BoundedLLM()
    assert not reports.fits_prompt(reports.merge_prompt(parts, META))
    result = reports.preserve_parts(parts, META, llm, lambda *args: None)
    for kind in parts[0]:
        if kind != 'summary':
            assert result[kind] == [record for part in parts for record in part[kind]]
    assert result['_source_summaries'] == [part['summary'] for part in parts]
    verified = reports.verify_facts(result, reports.lines(items), llm, META)
    assert [entry['quote'] for entry in verified['quotes']] == [item['text'] for item in items]
    assert [entry['evidence'] for entry in verified['action_items']] == [item['text'] for item in items]
    assert not verified['rejected_items'] and parts == original


def test_only_exact_duplicates_removed_and_conflicting_statuses_retained():
    parts, _ = source_parts(count=1)
    another = copy.deepcopy(parts[0])
    another['decisions'][0]['status'] = 'предложено'
    result = reports.preserve_parts(parts + [copy.deepcopy(parts[0]), another], META, BoundedLLM(), lambda *args: None)
    assert len(result['topics']) == 1
    assert [item['status'] for item in result['decisions']] == ['принято', 'предложено']


@pytest.mark.parametrize('template', reports.DEFAULT_TYPES)
def test_every_record_reaches_bounded_render_once_for_each_template(template):
    parts, items = source_parts()
    llm = BoundedLLM()
    facts = reports.verify_facts(reports.preserve_parts(parts, META, llm, lambda *args: None), reports.lines(items), llm, META)
    batches = reports.report_batches(template, facts)
    assert len(batches) > 1
    for kind, values in facts.items():
        if not kind.startswith('_') and isinstance(values, list) and kind != 'rejected_items':
            assert [entry for batch in batches for entry in batch[kind]] == values
    assert [s for batch in batches for s in batch['source_summaries']] == facts['_source_summaries']
    before = len(llm.calls)
    report = reports.render_report(template, facts, llm)
    assert 'Статусы поручений и решений между частями требуют сквозной проверки' in report
    assert f'из {len(batches)}' in report
    assert len(llm.calls) - before == len(batches)
    assert all(system.endswith(reports.PARTITION_SCOPE) for system, _, _ in llm.calls[before:])


def test_single_oversized_fact_fails_without_clipping_or_inference():
    parts, items = source_parts(count=1, padding=60000)
    facts = {**parts[0], '_meta': META}
    llm = BoundedLLM()
    with pytest.raises(ProcessingError, match='analysis_fact_too_large'):
        reports.render_report('tldr', facts, llm)
    assert not llm.calls and len(facts['topics'][0]['summary']) > 60000


def test_regular_merge_result_with_large_render_registry_is_also_partitioned():
    parts, _ = source_parts()
    facts = {**parts[0], '_meta': META}
    facts['topics'] = [part['topics'][0] for part in parts]
    llm = BoundedLLM()
    assert 'Часть 1' in reports.render_report('tldr', facts, llm)
    assert len(llm.calls) > 1


def test_long_summary_is_reduced_with_bounded_requests_but_sources_unchanged():
    parts, _ = source_parts(count=1, padding=0)
    parts[0]['summary'] = 'обзор ' * 14000
    original = parts[0]['summary']
    result = reports.preserve_parts(parts, META, BoundedLLM(), lambda *args: None)
    assert len(result['summary']) <= reports.MAX_SUMMARY_CHARS
    assert result['_source_summaries'] == [original]


def test_oversized_model_overview_is_explicit_error():
    parts, _ = source_parts(count=1)
    class InvalidOverview(BoundedLLM):
        def chat(self, *args):
            return {'summary': 'x' * (reports.MAX_SUMMARY_CHARS + 1)}
    with pytest.raises(ProcessingError, match='analysis_summary_too_large'):
        reports.preserve_parts(parts, META, InvalidOverview(), lambda *args: None)


def test_extract_uses_cached_chunks_and_bounded_fallback(monkeypatch):
    parts, items = source_parts()
    monkeypatch.setattr(reports, 'split_lines', lambda items: [[] for _ in parts])
    job = SimpleNamespace(meeting_id='unused', meeting_date=None, participants='',
                          chunk_cache={str(i): part for i, part in enumerate(parts)})
    result = reports.extract_facts(items, 'Synthetic', job, BoundedLLM(), lambda *args: None)
    assert result['_merge_mode'] == 'partitioned' and len(result['topics']) == len(parts)


def test_failed_late_render_does_not_publish_partial_report_and_retry_uses_facts(client, monkeypatch):
    parts, items = source_parts()
    with Session.begin() as session:
        meeting = Meeting(title='Synthetic', recording='fixture.wav', status='ready', transcript=items)
        session.add(meeting); session.flush()
        mid = meeting.id
    client.post(f'/api/meetings/{mid}/analysis', json={'types': ['tldr']})
    def extract(segments, title, job, llm, progress):
        return reports.verify_facts(reports.preserve_parts(parts, META, llm, progress), reports.lines(items), llm, META)
    monkeypatch.setattr(reports, 'extract_facts', extract)
    class FailingSecondRender(BoundedLLM):
        def chat(self, system, user, spec=None):
            if spec is None and any(call[2] is None for call in self.calls):
                raise httpx.ReadTimeout('synthetic')
            return super().chat(system, user, spec)
    assert reports.process_analysis_one(FailingSecondRender)
    with Session() as session:
        report = session.scalar(select(MeetingReport).where(MeetingReport.meeting_id == mid))
        assert report.status == 'failed' and report.content is None
        assert session.get(MeetingAnalysis, mid).facts['_merge_mode'] == 'partitioned'
    monkeypatch.setattr(reports, 'extract_facts', lambda *args: pytest.fail('Saved registry should survive failed rendering'))
    client.post(f'/api/meetings/{mid}/analysis', json={'types': ['tldr']})
    assert reports.process_analysis_one(BoundedLLM)
    state = client.get(f'/api/meetings/{mid}/analysis').json()
    assert state['status'] == 'ready' and state['reports'][0]['status'] == 'ready'


def test_summary_packing_accounts_for_json_escaping():
    parts, _ = source_parts(count=4, padding=0)
    for part in parts:
        part['summary'] = '\x01' * reports.MAX_SUMMARY_CHARS
    llm = BoundedLLM()
    result = reports.preserve_parts(parts, META, llm, lambda *args: None)
    assert len(llm.calls) > 1
    assert result['_source_summaries'] == [part['summary'] for part in parts]


def test_intermediate_merge_growth_falls_back_to_original_records(monkeypatch):
    parts, items = source_parts(count=4, padding=1000)
    assert reports.fits_prompt(reports.merge_prompt(parts, META))
    monkeypatch.setattr(reports, 'split_lines', lambda items: [[] for _ in parts])
    job = SimpleNamespace(meeting_id='unused', meeting_date=None, participants='',
                          chunk_cache={str(i): part for i, part in enumerate(parts)})
    class GrowingMerge(BoundedLLM):
        def chat(self, system, user, spec=None):
            if spec and 'topics' in spec['properties']:
                assert len(system) + len(user) <= reports.MAX_PROMPT_CHARS
                self.calls.append((system, user, spec))
                result = value_for(spec)
                result['summary'] = 'Intermediate overview'
                result['topics'] = [dict(parts[0]['topics'][0], summary='x' * 26000)]
                return result
            return super().chat(system, user, spec)
    llm = GrowingMerge()
    result = reports.extract_facts(items, 'Synthetic', job, llm, lambda *args: None)
    assert result['_merge_mode'] == 'partitioned'
    assert result['topics'] == [part['topics'][0] for part in parts]
    assert sum('topics' in spec['properties'] for _, _, spec in llm.calls if spec) == 2


def test_non_contracting_overviews_stop_after_bounded_rounds():
    parts, _ = source_parts(count=2, padding=0)
    for part in parts:
        part['summary'] = '\x01' * reports.MAX_SUMMARY_CHARS
    class NonContracting(BoundedLLM):
        def chat(self, system, user, spec=None):
            super().chat(system, user, spec)
            return {'summary': '\x01' * reports.MAX_SUMMARY_CHARS}
    llm = NonContracting()
    with pytest.raises(ProcessingError, match='analysis_summary_not_reduced'):
        reports.preserve_parts(parts, META, llm, lambda *args: None)
    assert len(llm.calls) == 20
