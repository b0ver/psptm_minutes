import copy
import json
from pathlib import Path

import httpx
import pytest
from sqlalchemy import select, func

from app import reports
from app.ai import ProcessingError
from app.db import Session, Meeting, MeetingAnalysis, MeetingReport, Action


def empty_facts():
    return {k: 'Синтетическая встреча' if k == 'summary' else [] for k in reports.schema('facts')['properties']}


class FakeLLM:
    calls = []

    def __init__(self):
        self.calls = []

    def close(self):
        pass

    def chat(self, system, user, spec=None):
        self.calls.append((system, user))
        if spec:
            facts = empty_facts()
            if 'meeting_summary' in spec.get('properties', {}):
                facts.update(meeting_summary='Итог синтетической встречи', meeting_goal=None, main_idea=None)
            return facts
        return '# Черновик\n\n## Главное\n- Синтетический итог [00:00:01].'


def add_meeting():
    with Session.begin() as s:
        m = Meeting(title='Synthetic', recording='test.wav', status='ready', minutes='Original',
                    transcript=[{'start':1,'end':2,'text':'Синтетический текст'}])
        s.add(m);s.flush()
        return m.id


def test_all_kit_types_and_source_manifest_present():
    assert len(reports.catalog()) == 16
    assert set(reports.DEFAULT_TYPES) <= set(reports.CATALOG)
    assert json.loads((reports.KIT/'source-manifest.json').read_text())


def test_analysis_queue_idempotent_and_does_not_replace_original(client):
    mid=add_meeting()
    for _ in range(2):
        assert client.post('/api/meetings/'+mid+'/analysis',json={'types':['tldr']}).status_code == 202
    assert reports.process_analysis_one(FakeLLM)
    assert not reports.process_analysis_one(FakeLLM)
    detail=client.get('/api/meetings/'+mid).json()
    assert detail['minutes']=='Original' and detail['analysis']['status']=='ready'
    assert detail['analysis']['reports'][0]['content'].startswith('# Черновик')
    with Session() as s:
        assert s.scalar(select(func.count()).select_from(MeetingReport))==1
        assert s.scalar(select(func.count()).select_from(Action))==0


def test_adding_type_reuses_facts_without_stt_or_reextract(client):
    mid=add_meeting()
    client.post('/api/meetings/'+mid+'/analysis',json={'types':['tldr']})
    assert reports.process_analysis_one(FakeLLM)
    class RenderOnly(FakeLLM):
        def chat(self, system, user, spec=None):
            assert spec is None, 'Facts should be cached'
            return super().chat(system,user,spec)
    assert client.post('/api/meetings/'+mid+'/analysis',json={'types':['tldr','chapters']}).status_code==202
    assert reports.process_analysis_one(RenderOnly)
    state=client.get('/api/meetings/'+mid+'/analysis').json()
    assert len(state['reports'])==2 and all(r['status']=='ready' for r in state['reports'])


def test_invalid_types_and_untranscribed_meeting(client):
    mid=add_meeting()
    assert client.post('/api/meetings/'+mid+'/analysis',json={'types':['../../secret']}).status_code==422
    with Session.begin() as s:
        s.get(Meeting,mid).transcript=None
    assert client.post('/api/meetings/'+mid+'/analysis',json={'types':['tldr']}).status_code==409


def test_failed_stt_does_not_block_next_analysis(client):
    with Session.begin() as s:
        m=Meeting(title='failed',recording='bad.wav',status='failed',transcript=None)
        s.add(m);s.flush();reports.enqueue(s,m,['tldr'])
    mid=add_meeting();client.post('/api/meetings/'+mid+'/analysis',json={'types':['tldr']})
    assert reports.process_analysis_one(FakeLLM)
    assert client.get('/api/meetings/'+mid+'/analysis').json()['status']=='ready'


def test_exact_evidence_required_even_after_model_verdict():
    facts=empty_facts()
    item={'task':'Сделать отчёт','assignee':'Иван','assigned_by':None,'due_raw':'к пятнице','due_date':'2026-10-09',
          'kind':'назначено','priority':None,'evidence':'Иван, сделай отчёт к пятнице.','timestamp':None,'confidence':'high','note':None}
    facts['action_items']=[item,{**item,'evidence':'Выдуманная цитата'}]
    class Verifier(FakeLLM):
        def chat(self,*args):
            return {'verdict':'подтверждено','reason':'test','corrected':None,'evidence':'Выдуманная цитата'}
    items=reports.lines([{'start':5,'end':9,'text':'Иван, сделай отчёт к пятнице.'}])
    result=reports.verify_facts(facts,items,Verifier(),{'meeting_title':'test'})
    assert len(result['action_items'])==1 and len(result['rejected_items'])==1
    assert result['action_items'][0]['due_date'] is None
    assert result['action_items'][0]['timestamp']=='00:00:05'
    facts=empty_facts();facts['action_items']=[{**item,'due_raw':'завтра','assigned_by':'Выдуманное имя'}]
    result=reports.verify_facts(facts,items,Verifier(),{'meeting_title':'test'})
    assert result['action_items'][0]['due_raw'] is None and result['action_items'][0]['assigned_by'] is None


def test_cross_segment_quote_and_no_empty_evidence():
    items=reports.lines([{'start':0,'end':1,'text':'Первая часть'},{'start':1,'end':2,'text':'и вторая часть.'}])
    assert reports.quote_source('часть и вторая',items)['start']==0
    assert reports.quote_source('и вторая часть.',items)['start']==1
    assert reports.quote_source('  ',items) is None


def test_long_segment_split_preserves_original_text():
    text='x'*12000
    batches=reports.split_lines(reports.lines([{'start':0,'end':50,'text':text}]))
    assert sum(len(s['text']) for b in batches for s in b)>=len(text)
    assert all(s['id']=='L00001' for b in batches for s in b)


def test_original_relative_deadline_rule_overridden():
    system,user=reports.prompt('03_extract_facts.md',{'meeting_title':'test','meeting_date':'не указана','participants':'не указаны','glossary':'нет'},chunk_index=1,chunk_total=1,previous_context='',chunk='Synthetic')
    assert system.endswith(reports.OVERRIDES)
    assert 'due_date всегда null' in system


def test_structural_validation_rejects_wrong_shapes():
    with pytest.raises(ProcessingError):
        reports.validate_json({'summary':'only'},reports.schema('facts'))


@pytest.mark.parametrize('error,code', [
    (httpx.ReadTimeout('synthetic'), 'analysis_ollama_timeout'),
    (httpx.ConnectError('synthetic'), 'analysis_ollama_unavailable'),
])
def test_failed_render_keeps_facts_and_manual_retry_reuses_them(client, error, code):
    mid = add_meeting()
    client.post('/api/meetings/' + mid + '/analysis', json={'types': ['tldr']})
    class FailingRender(FakeLLM):
        def chat(self, system, user, spec=None):
            if spec is None:
                raise error
            return super().chat(system, user, spec)
    assert reports.process_analysis_one(FailingRender)
    state = client.get('/api/meetings/' + mid + '/analysis').json()
    assert state['status'] == 'failed' and state['error_code'] == code
    assert state['reports'][0]['status'] == 'failed'
    class RetryRenderOnly(FakeLLM):
        def chat(self, system, user, spec=None):
            assert spec is None, 'Saved facts must survive a failed render'
            return super().chat(system, user, spec)
    assert client.post('/api/meetings/' + mid + '/analysis', json={'types': ['tldr']}).status_code == 202
    assert reports.process_analysis_one(RetryRenderOnly)
    state = client.get('/api/meetings/' + mid + '/analysis').json()
    assert state['status'] == 'ready' and state['reports'][0]['status'] == 'ready'
