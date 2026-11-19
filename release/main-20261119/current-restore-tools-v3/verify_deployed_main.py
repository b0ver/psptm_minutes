"""Read-only deployment identity, HTTP, binding and real worker-lock verification.

Does not create a meeting, import audio, invoke a model, or send a test message.
Unknown-session requests have no matching durable state.
"""
import hashlib,ipaddress,json,os,re,subprocess,sys,time
from pathlib import Path
from datetime import datetime
B=Path('/home/epopov/projects/psptm_minutes-ops/main-release-20261119')
SOURCE='c5d0059162ba2eae72c3c56dd378affb53a34aa8'
ROLES={'api':'runtime','telegram':'runtime','calendar-scheduler':'runtime','analysis-worker':'runtime','worker':'gpu','telemost-agent':'telemost'}
LIFECYCLE={'lifecycle.js':'a5647cdcf7c2d20d1b49946a6642e0dba69352099cc74841072feba07f0d93f0','meeting_observer.js':'4720637ebb995bd7e0c245caed038a705c787984e4efcab144cbfbf074428fdc','recording_controls.py':'262b704f378a824434b36cedb6d6ad4db001c220442999af39c70f7d2b103ad3','rollover.py':'e5bd7dc63f08d7857cf7daa99d2666b0fe74c35adc3ac3bb2a71e942c1e364d4','checkpoint.js':'3af693b4a3a1b574ca61bce99e0681d8feec0d1595b21f9ee424239554639fbd','browser_session.py':'8abbc321294a6f8e361b383cbe4fbdd77e52735766387fe3ab46d9d839f750e5'}
POLICY='404c80eff2492dd6f04922ce89d249d995061b0cd99860cf33171e4afdbc87ba'

def main(label,mode='complete'):
 assert re.fullmatch('[a-z][a-z0-9]{0,15}',label) and mode in {'core','complete'};os.umask(0o077)
 d=B/('deployed-proof-'+label);assert not d.exists();d.mkdir(mode=0o700)
 log=(d/'execution.private.log').open('xb')
 def run(args,data=None,timeout=30):
  r=subprocess.run(args,input=data,stdout=subprocess.PIPE,stderr=log,timeout=timeout)
  if r.returncode:raise RuntimeError('deployed_verification_command_failed')
  return r.stdout
 baseline=json.loads((B/'baseline-metadata/running-services.json').read_text())['services']
 roles=list(ROLES) if mode=='complete' else ['api','telemost-agent']
 rows=json.loads(run(['docker','inspect',*[baseline[r]['Name'] for r in ['db',*roles]]]))
 by_name={c['Name']:c for c in rows};current={r:by_name[baseline[r]['Name']] for r in ['db',*roles]}
 assert current['db']['Id']==baseline['db']['Id'] and current['db']['Image']==baseline['db']['Image'] and current['db']['State']['Running'],'database_was_recreated_or_stopped'
 assert current['db']['Mounts']==baseline['db']['Mounts'],'database_mounts_changed'
 for role in roles:
  c=current[role];expected=json.loads((B/('registry-images/'+ROLES[role]+'-installed.json')).read_text())
  assert c['Image']==expected['host_image_id'] and c['Config']['Image']==expected['registry_ref'],'deployed_digest_mismatch'
  assert c['Config']['Labels'].get('org.opencontainers.image.revision')==SOURCE,'deployed_revision_mismatch'
  assert c['State']['Running'] and not c['State']['Restarting'] and not c['State']['Paused']
  assert c['HostConfig']['ReadonlyRootfs'] and c['HostConfig']['CapDrop']==['ALL'] and any(x.startswith('no-new-privileges') for x in c['HostConfig']['SecurityOpt'])
  assert run(['docker','exec',c['Id'],'id','-u']).strip()==b'10001'
  assert c['Config']['Cmd']==baseline[role]['Config']['Cmd']
  for field in ['Memory','MemorySwap','NanoCpus','CpuPeriod','CpuQuota','CpusetCpus','PidsLimit','ShmSize']:
   assert c['HostConfig'].get(field)==baseline[role]['HostConfig'].get(field),'resource_limit_changed'
  if role=='worker':
   def gpu_request(rows):
    return [{k:(x.get(k) or ({} if k=='Options' else [])) if k in {'Options','DeviceIDs','Capabilities'} else x.get(k) for k in ['Driver','Count','DeviceIDs','Capabilities','Options']} for x in (rows or [])]
   assert gpu_request(c['HostConfig'].get('DeviceRequests'))==gpu_request(baseline[role]['HostConfig'].get('DeviceRequests')),'gpu_allocation_changed'
  for m in baseline[role]['Mounts']:
   actual=next(x for x in c['Mounts'] if x['Destination']==m['Destination'])
   assert (actual['Type'],actual['Source'],actual['RW'])==(m['Type'],m['Source'],m['RW']),'existing_mount_changed'
  assert set(c['NetworkSettings']['Networks'])==set(baseline[role]['NetworkSettings']['Networks']),'network_changed'
  assert (c['HostConfig']['PortBindings'] or {})==(baseline[role]['HostConfig']['PortBindings'] or {}),'port_exposure_changed'
 api=current['api']['Id'];agent=current['telemost-agent']['Id']
 for cid,port in [(api,8080),(agent,8090)]:
  run(['docker','exec',cid,'python','-c',"import urllib.request;assert urllib.request.urlopen('http://127.0.0.1:"+str(port)+"/healthz',timeout=5).status==200"])
 code="""import hashlib,json,sys
from pathlib import Path
expected=json.load(sys.stdin)
assert all(hashlib.sha256((Path('/agent')/n).read_bytes()).hexdigest()==h for n,h in expected.items())
print('lifecycle_unchanged')
"""
 assert run(['docker','exec','-i',agent,'python','-c',code],json.dumps(LIFECYCLE).encode()).strip()==b'lifecycle_unchanged'
 for role in ['api',*(['worker','analysis-worker'] if mode=='complete' else [])]:
  code="import hashlib;from pathlib import Path;assert hashlib.sha256(Path('/app/config/models.json').read_bytes()).hexdigest()=='"+POLICY+"'"
  run(['docker','exec',current[role]['Id'],'python','-c',code])
 config=json.loads((B/'candidate-c5/compose.yaml').read_text())['services']['telegram']['environment'];cutoff=config['MINUTES_RECOVERY_NOTICES_SINCE'];assert not cutoff.startswith('2099-')
 state_code="""import json,hashlib
from pathlib import Path
rows=[];blocked=0
for p in sorted(Path('/data/telemost').glob('*/state.json')):
 raw=p.read_bytes();v=json.loads(raw);rows.append([p.parent.name,hashlib.sha256(raw).hexdigest()])
 blocked+=v['status'] not in {'uploaded','failed','partial_saved','cancelled'} or bool(v.get('recovery_pending')) or bool(v.get('repair_requested'))
assert blocked==0
print(json.dumps({'sessions':len(rows),'state_sha256':hashlib.sha256(json.dumps(rows,separators=(',',':')).encode()).hexdigest()}))
"""
 states=json.loads(run(['docker','exec',api,'python','-c',state_code]))
 http_code="""import json,urllib.request,urllib.error
sid='00000000-0000-4000-8000-000000000000';base='http://127.0.0.1:8080'
rows=json.load(urllib.request.urlopen(base+'/api/telemost/sessions',timeout=8));assert isinstance(rows,list)
for method,path in [('GET','/api/telemost/sessions/'+sid),('POST','/api/telemost/sessions/'+sid+'/stop')]:
 try:urllib.request.urlopen(urllib.request.Request(base+path,method=method),timeout=8)
 except urllib.error.HTTPError as e:assert e.code==404
 else:raise AssertionError('unknown_session_not_404')
print(json.dumps({'api_recorder_proxy':True,'unknown_stop_http':404,'unknown_status_http':404,'sessions_listed':len(rows)}))
"""
 http=json.loads(run(['docker','exec',api,'python','-c',http_code]))
 locks={}
 if mode=='complete':
  tg=current['telegram']['Id'];env=dict(x.split('=',1) for x in current['telegram']['Config']['Env']);plan=json.loads((B/'access-plan.private.json').read_text())['environment']
  for key,value in plan.items():
   if key!='MINUTES_RECOVERY_CONTROL_KEY_FILE':assert env[key]==value,'telegram_binding_mismatch'
  assert env['TELEGRAM_ALLOW_ALL']=='0' and env['MINUTES_RECOVERY_NOTICES_SINCE']==cutoff
  authenticated="""import json,os,time,urllib.request,urllib.error
from pathlib import Path
from app.recovery_signing import sign
key=Path(os.environ['MINUTES_RECOVERY_CONTROL_KEY_FILE']).read_bytes();assert len(key)>=32
users=set(os.environ['MINUTES_RECOVERY_CONTROL_ALLOWED_USERS'].split(','));actor=os.environ['MINUTES_RECOVERY_CONTROL_ACTOR'];assert len(users)==2 and actor in users
body={'issued_at':time.time(),'request_id':'release-readonly-probe','user_id':actor,'session_id':'-','generation':'-','ownership_verified':True};path='/control/events'
payload=json.dumps(body).encode();req=urllib.request.Request('http://telemost-agent:8090'+path,data=payload,headers={'Content-Type':'application/json','X-Minutes-Control-Signature':sign(key,'POST',path,body)},method='POST')
page=json.load(urllib.request.urlopen(req,timeout=8));assert page['items']==[]
print(json.dumps({'authenticated_event_feed':True,'old_notices_queued':0,'allowed_operators':len(users)}))
"""
  control=json.loads(run(['docker','exec',tg,'python','-c',authenticated]));assert control['authenticated_event_feed']
  for role,lock in [('worker',90601008),('analysis-worker',90601009),('telegram',90601010)]:
   networks=set(current[role]['NetworkSettings']['Networks'])&set(current['db']['NetworkSettings']['Networks']);assert len(networks)==1
   ip=current[role]['NetworkSettings']['Networks'][next(iter(networks))]['IPAddress'];ipaddress.ip_address(ip)
   sql="SELECT EXISTS (SELECT 1 FROM pg_locks l JOIN pg_stat_activity a ON a.pid=l.pid WHERE l.locktype='advisory' AND l.granted AND l.classid=0 AND l.objid="+str(lock)+" AND a.client_addr='"+ip+"'::inet)"
   deadline=time.monotonic()+40
   while time.monotonic()<deadline:
    value=run(['docker','exec','-e','PGOPTIONS=-c default_transaction_read_only=on -c statement_timeout=3000',current['db']['Id'],'psql','-qAt','-U','minutes','-d','minutes','-c',sql]).strip()
    if value==b't':locks[role]=True;break
    time.sleep(1)
   assert role in locks,'real_worker_session_lock_missing'
  started=datetime.fromisoformat(current['calendar-scheduler']['State']['StartedAt'].replace('Z','+00:00')).timestamp()
  deadline=time.monotonic()+60;calendar_fresh=False
  while time.monotonic()<deadline:
   tick=run(['docker','exec','-e','PGOPTIONS=-c default_transaction_read_only=on -c statement_timeout=3000',current['db']['Id'],'psql','-qAt','-U','minutes','-d','minutes','-c',"SELECT extract(epoch from last_tick) FROM calendar_scheduler_state WHERE id=1"]).strip()
   if tick and float(tick)>=started and -5<=time.time()-float(tick)<90:calendar_fresh=True;break
   time.sleep(1)
  assert calendar_fresh,'calendar_heartbeat_after_restart_missing'
  getme="from app.telegram import Telegram,telegram_token;bot=Telegram(telegram_token());result=bot.call('getMe',{});assert result.get('is_bot') is True;bot.client.close();print('telegram_getme_verified')"
  assert run(['docker','exec',tg,'python','-c',getme],timeout=45).strip()==b'telegram_getme_verified'
  model="import httpx;from app.ai import validate_endpoint,verify_model;from app.settings import OLLAMA_URL,OLLAMA_MODEL,model_policy;validate_endpoint(OLLAMA_URL);client=httpx.Client(base_url=OLLAMA_URL,timeout=10,trust_env=False,follow_redirects=False);verify_model(client,OLLAMA_MODEL,model_policy());client.close();print('approved_model_digest_verified_without_inference')"
  assert run(['docker','exec',current['analysis-worker']['Id'],'python','-c',model],timeout=20).strip()==b'approved_model_digest_verified_without_inference'
 result={'status':'DEPLOYED_CORE_VERIFIED' if mode=='core' else 'DEPLOYED_MAIN_VERIFIED','source_commit':SOURCE,'verified_application_services':len(roles),'database_container_and_mounts_preserved':True,'existing_mounts_networks_ports_preserved':True,'uid':10001,'immutable_registry_refs':True,'http':http,'all_terminal_sessions':states['sessions'],'state_sha256':states['state_sha256'],'real_service_session_locks':locks,'calendar_tick_after_start':mode=='complete','telegram_readonly_getme':mode=='complete','approved_model_digest_verified_without_inference':mode=='complete','auto_leave_changed':False,'model_policy_changed':False,'real_meeting_started':False,'test_messages_sent':False,'models_invoked_by_probe':False,'time':time.time()}
 (d/'receipt.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result));log.close()
if __name__=='__main__':main(*sys.argv[1:])
