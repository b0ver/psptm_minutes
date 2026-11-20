import asyncio
import threading
from pathlib import Path
import httpx
from integrations.telemost import service
from integrations.telemost.stale_capture_watchdog import StaleCaptureWatchdog
SID='00000000-0000-0000-0000-000000000011'
def setup(monkeypatch,tmp_path):
    monkeypatch.setattr(service,'DATA',tmp_path)
    service.sessions.clear();service.tasks.clear();service.stops.clear()
    service.sessions[SID]={'id':SID,'status':'recording','created_at':0}
def test_recovery_keeps_api_responsive_and_never_imports(monkeypatch,tmp_path):
    setup(monkeypatch,tmp_path)
    entered=threading.Event();release=threading.Event()
    class Fake:
        def __init__(self,folder):pass
        def assemble(self,target):
            entered.set();assert release.wait(5)
            Path(target).write_bytes(b'synthetic')
            return {'recovered_audio_seconds':3,'chunks':1}
    monkeypatch.setattr(service,'Checkpoints',Fake)
    async def forbidden(*a,**kw):raise AssertionError('No import in recovery')
    monkeypatch.setattr(service,'import_recording',forbidden)
    async def go():
        task=asyncio.create_task(service.recover_partial_async(SID,'capture_ui_stalled'))
        while not entered.is_set():await asyncio.sleep(.001)
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=service.app),base_url='http://test') as client:
                r=await asyncio.wait_for(client.get('/healthz'),.5)
                assert r.status_code==200
                r=await client.get('/sessions/'+SID+'/recovery')
                assert r.json()['recovery_pending'] and r.json()['status']=='finishing'
        finally:release.set()
        await task
        assert service.sessions[SID]['status']=='partial_saved'
        assert service.sessions[SID]['recovery_event']['delivery']=='pending_owner_binding'
        assert not service.sessions[SID]['recording_complete']
    asyncio.run(go())
def test_recovery_decode_failure_preserves_original(monkeypatch,tmp_path):
    setup(monkeypatch,tmp_path)
    folder=tmp_path/SID;folder.mkdir();original=folder/'recording.webm';original.write_bytes(b'original')
    class Fake:
        def __init__(self,folder):pass
        def assemble(self,target):raise ValueError('private exception must not leak')
    monkeypatch.setattr(service,'Checkpoints',Fake)
    asyncio.run(service.recover_partial_async(SID,'capture_ui_stalled'))
    assert service.sessions[SID]['status']=='failed'
    assert original.read_bytes()==b'original'
    assert 'private exception' not in (folder/'state.json').read_text()
def test_upload_uncertain_does_not_decode_or_import(monkeypatch,tmp_path):
    setup(monkeypatch,tmp_path)
    folder=tmp_path/SID;folder.mkdir();(folder/'recording.webm').write_bytes(b'saved')
    service.sessions[SID]['status']='uploading'
    asyncio.run(service.recover_partial_async(SID,'agent_interrupted'))
    assert service.sessions[SID]['status']=='upload_uncertain'
    assert not service.sessions[SID]['recovery_pending']
def test_long_healthy_rotations_and_duplicate_chunks():
    w=StaleCaptureWatchdog(0)
    for t in range(0,172800,30):
        w.ui_seen(t);w.capture_seen(t,t+1);assert not w.expired(t)
    w=StaleCaptureWatchdog(0);w.capture_seen(0,1);w.capture_seen(599,1)
    assert w.expired(600)
def test_ui_unavailable_with_progress_does_not_stop():
    w=StaleCaptureWatchdog(0);w.capture_seen(590,1)
    assert not w.expired(600);assert w.expired(1190)

def test_mass_recovery_is_serialized(monkeypatch,tmp_path):
    import time
    setup(monkeypatch,tmp_path)
    sid2='00000000-0000-0000-0000-000000000012'
    service.sessions[sid2]={'id':sid2,'status':'recording','created_at':0}
    counts={'active':0,'max':0}
    lock=threading.Lock()
    class Fake:
        def __init__(self,folder):pass
        def assemble(self,target):
            with lock:
                counts['active']+=1;counts['max']=max(counts['max'],counts['active'])
            time.sleep(.05);Path(target).write_bytes(b'synthetic')
            with lock:counts['active']-=1
            return {'recovered_audio_seconds':1}
    monkeypatch.setattr(service,'Checkpoints',Fake)
    async def go():
        await asyncio.gather(service.recover_partial_async(SID,'agent_interrupted'),service.recover_partial_async(sid2,'agent_interrupted'))
    asyncio.run(go())
    assert counts['max']==1
    assert all(service.sessions[s]['status']=='partial_saved' for s in (SID,sid2))
