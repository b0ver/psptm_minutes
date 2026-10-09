import json

import httpx
import pytest

from app.ai import verify_model, ProcessingError, extract, chunks, validate_endpoint
from app.settings import model_policy, OLLAMA_MODEL


def catalog(model):
    return httpx.Client(base_url='http://local',transport=httpx.MockTransport(lambda r: httpx.Response(200,json={'models':[model]})))


@pytest.mark.parametrize('model,code', [
    ({'name':OLLAMA_MODEL,'digest':'wrong'},'model_digest_mismatch'),
    ({'name':OLLAMA_MODEL,'digest':model_policy()[OLLAMA_MODEL],'remote_host':'https://cloud.invalid'},'cloud_model_forbidden'),
    ({'name':OLLAMA_MODEL,'digest':model_policy()[OLLAMA_MODEL],'remote_model':'remote'},'cloud_model_forbidden'),
    ({'name':'unknown','digest':'anything'},'model_missing'),
])
def test_model_gate(model,code):
    with catalog(model) as c, pytest.raises(ProcessingError,match=code):
        verify_model(c,OLLAMA_MODEL,model_policy())


def test_cloud_name_rejected_before_request():
    def no_request(r): raise AssertionError('Network must not be called')
    with httpx.Client(base_url='http://local',transport=httpx.MockTransport(no_request)) as c:
        with pytest.raises(ProcessingError,match='model_not_allowed'):
            verify_model(c,'anything:cloud',{'anything:cloud':'digest'})


def test_public_endpoint_rejected(monkeypatch):
    monkeypatch.setattr('socket.getaddrinfo',lambda *a,**k:[(0,0,0,'',('8.8.8.8',11434))])
    with pytest.raises(ProcessingError,match='nonlocal_ollama_endpoint'):
        validate_endpoint('http://public.invalid:11434')


@pytest.mark.parametrize('url',['https://192.168.1.90:11434','http://user:password@192.168.1.90:11434','http://192.168.1.90:11434/?route=cloud'])
def test_endpoint_override_rejected(url):
    with pytest.raises(ProcessingError,match='invalid_ollama_endpoint'):
        validate_endpoint(url)


def test_all_segments_preserved_by_chunks():
    segments=[{'text':'a'*12000,'start':0},{'text':'last','start':12}]
    result=list(chunks(segments))
    assert len(result)>1
    assert ''.join(p['text'] for b in result for p in b)==''.join(s['text'] for s in segments)


def mock_extraction(evidence,owner=None,done=True):
    calls=[]
    def transport(r):
        calls.append(r.url.path)
        if r.url.path=='/api/tags':
            return httpx.Response(200,json={'models':[{'name':OLLAMA_MODEL,'digest':model_policy()[OLLAMA_MODEL]}]})
        payload=json.loads(r.content)
        assert payload['stream'] is False and not payload.get('tools')
        result={'minutes':'Краткий протокол','actions':[{'text':'Подготовить отчёт','owner':owner,'segment_index':0,'evidence':evidence}]}
        return httpx.Response(200,json={'done':done,'done_reason':'stop','message':{'content':json.dumps(result)}})
    return httpx.Client(base_url='http://local',transport=httpx.MockTransport(transport)),calls


def test_valid_evidence_and_no_invented_owner():
    c,calls=mock_extraction('Подготовить отчёт',owner='Несказанное имя')
    with c:
        minutes,actions=extract([{'text':'Подготовить отчёт','start':12}],c)
    assert actions[0]['owner'] is None and actions[0]['source_seconds']==12
    assert 'due_on' not in actions[0]
    assert calls==['/api/tags','/api/chat']


def test_invented_evidence_rejected():
    c,_=mock_extraction('Выдуманная цитата')
    with c:
        minutes,actions=extract([{'text':'Подготовить отчёт','start':12}],c)
    assert minutes=='Краткий протокол'
    assert actions==[] and actions.unverified_count==1


def test_incomplete_response_rejected():
    c,_=mock_extraction('Подготовить отчёт',done=False)
    with c,pytest.raises(ProcessingError,match='llm_output_incomplete'):
        extract([{'text':'Подготовить отчёт','start':12}],c)


def test_output_size_limits_still_enforced_after_generation():
    from pydantic import ValidationError
    def transport(r):
        if r.url.path == '/api/tags':
            return httpx.Response(200, json={'models':[{'name':OLLAMA_MODEL,'digest':model_policy()[OLLAMA_MODEL]}]})
        return httpx.Response(200, json={'done':True,'done_reason':'stop','message':{
            'content':json.dumps({'minutes':'x'*10001,'actions':[]})}})
    with httpx.Client(base_url='http://local', transport=httpx.MockTransport(transport)) as c:
        with pytest.raises(ValidationError):
            extract([{'text':'Synthetic test','start':0}],c)


def test_wrong_index_repaired_only_with_unique_verbatim_source():
    from types import SimpleNamespace
    from app.ai import evidence_index
    candidate=SimpleNamespace(evidence='Подготовить отчёт',segment_index=999)
    batch=[{'segment_index':7,'text':'Подготовить отчёт к встрече.'}]
    assert evidence_index(candidate,batch)==7
    assert evidence_index(candidate,batch+[{'segment_index':8,'text':'Подготовить отчёт'}]) is None
    assert evidence_index(candidate,[{'segment_index':8,'text':'Выдуманная цитата'}]) is None
    assert evidence_index(SimpleNamespace(evidence='  ',segment_index=7),batch) is None


def test_valid_actions_survive_an_unverified_candidate():
    def transport(r):
        if r.url.path=='/api/tags':
            return httpx.Response(200,json={'models':[{'name':OLLAMA_MODEL,'digest':model_policy()[OLLAMA_MODEL]}]})
        candidates=[{'text':'Отчёт','owner':None,'segment_index':0,'evidence':'Подготовить отчёт'},
                    {'text':'Выдуманное поручение','owner':None,'segment_index':0,'evidence':'Такого не сказано'}]
        return httpx.Response(200,json={'done':True,'message':{'content':json.dumps({'minutes':'Протокол','actions':candidates})}})
    with httpx.Client(base_url='http://local',transport=httpx.MockTransport(transport)) as c:
        minutes,actions=extract([{'text':'Подготовить отчёт','start':12}],c)
    assert minutes=='Протокол' and len(actions)==1
    assert actions[0]['evidence']=='Подготовить отчёт' and actions.unverified_count==1
