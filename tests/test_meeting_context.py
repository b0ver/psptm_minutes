import json,hashlib
from uuid import uuid4
import pytest
from pydantic import ValidationError
from app.meeting_context import Context,annotate,roster
from app.db import Session,Meeting,MeetingContext
from app.reports import lines,line_text


def context():
    return {'source':'telemost_ui','title':'План проекта','participants':[{'name':'Иван Петров','role':'moderator'}],'turns':[{'start':0,'end':10,'names':['Иван Петров']}]}


def test_speaker_hint_requires_unambiguous_coverage_and_does_not_change_quote():
    c=context();segments=[{'start':1,'end':3,'text':'Я подготовлю отчёт.'}]
    enriched=annotate(segments,c)
    assert enriched[0]['speaker_hint']=='Иван Петров' and enriched[0]['text']==segments[0]['text']
    assert 'speaker_hint' not in segments[0]
    c['turns']=[{'start':0,'end':2,'names':['Иван Петров']},{'start':2,'end':10,'names':['Иван Петров','Другой участник']}]
    assert 'speaker_hint' not in annotate(segments,c)[0]
    c['turns']=[];assert 'speaker_hint' not in annotate(segments,c)[0]
    assert 'Вероятный спикер' in line_text(lines(enriched))
    assert 'должность не установлена' in roster(context())
    c=context();c['timing_reliable']=False
    assert 'speaker_hint' not in annotate(segments,c)[0]


def test_context_rejects_overlapping_or_nonfinite_turns():
    c=context();c['turns']=[{'start':0,'end':3,'names':['Иван Петров']},{'start':2,'end':5,'names':['Иван Петров']}]
    with pytest.raises(ValidationError):Context.model_validate(c)
    c['turns']=[{'start':0,'end':float('inf'),'names':[]}]
    with pytest.raises(ValidationError):Context.model_validate(c)


def test_import_stores_context_and_retries_with_same_digest(client):
    cid=str(uuid4());c=context()
    body={'title':'План проекта','import_session':cid,'telemost_context':json.dumps(c)}
    def post(body):return client.post('/api/meetings',data=body,files={'recording':('meeting.webm',b'synthetic')})
    first=post(body);assert first.status_code==201
    repeated=post({**body,'telemost_context':json.dumps(c,sort_keys=True)})
    assert repeated.status_code==200 and repeated.json()['id']==first.json()['id']
    saved=client.get('/api/recording-imports/'+cid).json()
    expected=['План проекта',['tldr','meeting_notes','action_items','key_meanings','chapters'],'','',True,json.dumps(c,sort_keys=True,ensure_ascii=False)]
    assert saved['request_sha256']==hashlib.sha256(json.dumps(expected,ensure_ascii=False).encode()).hexdigest()
    c['title']='Changed';assert post({**body,'telemost_context':json.dumps(c)}).status_code==409
    mid=first.json()['id']
    with Session.begin() as s:
        assert s.get(MeetingContext,mid).observations['participants'][0]['name']=='Иван Петров'
        s.get(Meeting,mid).transcript=[{'start':1,'end':2,'text':'Точная цитата'}]
    data=client.get('/api/meetings/'+mid).json()
    assert data['transcript'][0]['speaker_hint']=='Иван Петров'


def test_context_without_import_and_untrusted_business_role_rejected(client):
    c=context()
    result=client.post('/api/meetings',data={'title':'test','telemost_context':json.dumps(c)},files={'recording':('test.webm',b'synthetic')})
    assert result.status_code==422
    c['participants'][0]['role']='CEO'
    with pytest.raises(ValidationError):Context.model_validate(c)
