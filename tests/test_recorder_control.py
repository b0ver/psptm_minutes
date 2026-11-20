import asyncio,time
import httpx
from integrations.telemost import service
from integrations.telemost.signed_control import sign
from integrations.telemost.stale_capture_watchdog import StaleCaptureWatchdog
SID='00000000-0000-0000-0000-000000000044'
KEY=b'synthetic-test-only-key-0000000000000000'
def initialize(monkeypatch,tmp_path,stale=True):
    monkeypatch.setattr(service,'DATA',tmp_path)
    service.sessions.clear();service.tasks.clear();service.capture_watchdogs.clear()
    service.sessions[SID]={'id':SID,'created_at':100,'status':'recording'}
    service.tasks[SID]=object()
    w=StaleCaptureWatchdog(time.monotonic()-(601 if stale else 0));service.capture_watchdogs[SID]=w
    key=tmp_path/'key';key.write_bytes(KEY)
    monkeypatch.setenv('MINUTES_RECOVERY_CONTROL_KEY_FILE',str(key));monkeypatch.setenv('MINUTES_RECOVERY_CONTROL_ALLOWED_USERS','101')
def body(**updates):
    data={'issued_at':time.time(),'request_id':'request-one','user_id':'101','session_id':SID,'generation':'100','ownership_verified':True}
    data.update(updates);return data
async def post(path,data,signed=True):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=service.app),base_url='http://test') as c:
        return await c.post(path,json=data,headers={'X-Minutes-Control-Signature':sign(KEY,'POST',path,data) if signed else 'invalid'})
def test_manual_stale_requests_existing_run_only(monkeypatch,tmp_path):
    initialize(monkeypatch,tmp_path)
    r=asyncio.run(post('/control/confirm',body()))
    assert r.status_code==202 and r.json()['result']=='accepted'
    assert service.sessions[SID]['repair_requested']
    assert len(service.tasks)==1
    assert not (tmp_path/SID/'recovered.webm').exists()
    r=asyncio.run(post('/control/confirm',body()))
    assert r.status_code==202
    service.tasks.clear()
def test_healthy_recording_protected(monkeypatch,tmp_path):
    initialize(monkeypatch,tmp_path,False)
    assert asyncio.run(post('/control/confirm',body())).status_code==409
    assert not service.sessions[SID].get('repair_requested');service.tasks.clear()
def test_signature_required(monkeypatch,tmp_path):
    initialize(monkeypatch,tmp_path)
    assert asyncio.run(post('/control/confirm',body(),False)).status_code==403;service.tasks.clear()
def test_generation_and_user_rechecked(monkeypatch,tmp_path):
    initialize(monkeypatch,tmp_path)
    assert asyncio.run(post('/control/confirm',body(generation='old'))).status_code==409
    assert asyncio.run(post('/control/confirm',body(user_id='999'))).status_code==403;service.tasks.clear()
def test_pending_recovery_rejected(monkeypatch,tmp_path):
    initialize(monkeypatch,tmp_path);service.sessions[SID]['recovery_pending']=True
    assert asyncio.run(post('/control/confirm',body())).status_code==409;service.tasks.clear()
def test_missing_configuration_closed(monkeypatch,tmp_path):
    initialize(monkeypatch,tmp_path);monkeypatch.delenv('MINUTES_RECOVERY_CONTROL_KEY_FILE')
    assert asyncio.run(post('/control/confirm',body())).status_code==503;service.tasks.clear()
def test_durable_recovery_pagination_not_recent30(monkeypatch,tmp_path):
    initialize(monkeypatch,tmp_path)
    for i in range(60):service.sessions[f'{i:036d}']={'status':'partial_saved','recovery_event':{'event_id':str(i)}}
    first=service.recovery_events(limit=50)
    second=service.recovery_events(after=first['next_cursor'],limit=50)
    assert len(first['items'])==50 and len(second['items'])==10 and second['next_cursor'] is None
    service.tasks.clear()
