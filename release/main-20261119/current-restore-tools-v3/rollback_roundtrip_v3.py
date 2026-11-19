"""Isolated candidate -> reconstructed worker rollback. No production service writes."""
import hashlib,json,os,signal,subprocess,sys,time
from pathlib import Path
BASE=Path('/home/epopov/projects/psptm_minutes-ops/main-release-20261119')
PG='postgres:16.10-alpine@sha256:029660641a0cfc575b14f336ba448fb8a75fd595d42e1fa316b9fb4378742297'

def main(candidate,attempt,dump=None):
 assert candidate.startswith('sha256:') and len(candidate)==71
 assert attempt in {'synthetic','synthetic-v2','synthetic-v3','current-clone'}
 out=BASE/('rollback-roundtrip-'+attempt);out.mkdir(mode=0o700);os.umask(0o077)
 signal.signal(signal.SIGALRM,lambda *_:(_ for _ in ()).throw(RuntimeError('isolated_rollback_deadline')));signal.alarm(360)
 old=json.loads((BASE/'rollback/worker/receipt.json').read_text())['reconstructed_image']
 password=BASE/'rollback/synthetic-db-password';assert password.read_text()=='rollback-test-only\n'
 prefix='minutes-worker-roundtrip-20261119-'+attempt;net=prefix+'-net';db=prefix+'-db';owned=[];net_created=False
 log=(out/'commands.log').open('xb')
 def run(args,data=None,timeout=45):
  p=subprocess.run(args,input=data,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=timeout)
  if p.returncode:
   log.write(p.stderr);log.flush();raise RuntimeError('isolated_command_failed_'+str(p.returncode))
  return p.stdout
 def sql(q):return run(['docker','exec','-i',db,'psql','-X','-v','ON_ERROR_STOP=1','-qAt','-U','minutes','-d','minutes'],q.encode()).strip()
 def app(image,name=None,detach=False,code=None):
  args=['docker','run','--log-driver','none','--runtime','runc','-e','NVIDIA_VISIBLE_DEVICES=void','--network',net,'--read-only','--cpus','.5','--memory','768m','--cap-drop','ALL','--security-opt','no-new-privileges','--label','minutes.rollback.owner='+prefix,'--tmpfs','/tmp:size=64m,mode=1777','--tmpfs','/data:size=16m,uid=10001,gid=10001','-e','DB_HOST='+db,'-e','DB_PASSWORD_FILE=/run/probe-password','-e','DATA_DIR=/data','--mount','type=bind,src='+str(password)+',dst=/run/probe-password,readonly','--entrypoint','python']
  if detach:args[2:2]=['-d','--name',name]
  else:args[2:2]=['--rm','-i']
  args+=[image]
  args+=['-m','app.worker'] if code is None else ['-']
  result=run(args,None if code is None else code.encode())
  if detach:owned.append((result.decode().strip(),image))
  return result
 def stop(cid,image):
  c=json.loads(run(['docker','inspect',cid]))[0];assert c['Config']['Labels'].get('minutes.rollback.owner')==prefix and c['Image']==image
  run(['docker','stop','-t','5',cid]);run(['docker','rm',cid]);owned.remove((cid,image))
 try:
  run(['docker','network','create','--internal','--label','minutes.rollback.owner='+prefix,net]);net_created=True
  args=['docker','run','-d','--log-driver','none','--name',db,'--label','minutes.rollback.owner='+prefix,'--network',net,'--cpus','.5','--memory','768m','--tmpfs','/var/lib/postgresql/data:rw,nosuid,size=512m','-e','POSTGRES_USER=minutes','-e','POSTGRES_DB=minutes','-e','POSTGRES_PASSWORD_FILE=/run/probe-password','--mount','type=bind,src='+str(password)+',dst=/run/probe-password,readonly',PG]
  cid=run(args).decode().strip();db_image=json.loads(run(['docker','inspect',cid]))[0]['Image'];owned.append((cid,db_image))
  for _ in range(30):
   p=subprocess.run(['docker','exec',db,'sh','-c','[ "$(cat /proc/1/comm)" = postgres ] && exec psql -X -qAt -U minutes -d minutes -c "SELECT 1"'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=5)
   if p.returncode==0:break
   time.sleep(1)
  else:raise RuntimeError('isolated_postgres_unready')
  dump_sha=None
  if dump:
   path=Path(dump);assert path.is_file() and path.stat().st_size<256*1024**2
   dump_sha=hashlib.sha256(path.read_bytes()).hexdigest()
   run(['docker','exec','-i',db,'pg_restore','--exit-on-error','--no-owner','-U','minutes','-d','minutes'],path.read_bytes(),timeout=90)
  else:app(old,code='from app.db import Base,engine;Base.metadata.create_all(engine)')
  assert sql("select count(*) from meetings where status in ('queued','processing')")==b'0','restored_live_jobs_refused'
  before=sql("select encode(sha256(convert_to(coalesce(string_agg(row_to_json(m)::text,'' order by id),''),'UTF8')),'hex') from meetings m")
  count=int(sql('select count(*) from meetings'))
  cycles=[]
  for index,image in enumerate((old,candidate,old)):
   identity='rollproof-'+str(index)
   code="""import os
assert not os.path.exists("/dev/nvidiactl") and not os.path.exists("/dev/nvidia0"),"gpu_devices_forbidden"
from app.db import Session,Meeting
from app.worker import process_one
from sqlalchemy import select
with Session.begin() as s:s.add(Meeting(id=IDENTITY,title='Synthetic rollback proof',recording='synthetic.wav',status='queued',transcript=[{'start':0,'end':1,'text':'synthetic fixture'}]))
def no_stt(*a):raise AssertionError('model_invocation_forbidden')
assert process_one(transcriber=no_stt,extractor=lambda transcript:('synthetic result',[]))
with Session() as s:
 m=s.get(Meeting,IDENTITY);assert m.status=='ready' and m.attempts==1 and m.minutes=='synthetic result'
 rows=list(s.scalars(select(Meeting).where(Meeting.id.like('rollproof-%'))));assert all(m.status=='ready' and m.minutes=='synthetic result' for m in rows)
""".replace('IDENTITY',repr(identity))
   app(image,code=code)
   app(image,name=prefix+'-worker-'+str(index),detach=True);worker_cid=owned[-1][0]
   for _ in range(15):
    if sql('select pg_try_advisory_lock(90601008)')==b'f':break
    time.sleep(1)
   else:raise RuntimeError('real_worker_session_lock_unconfirmed')
   actual=json.loads(run(['docker','inspect',worker_cid]))[0];state=actual['State'];assert state['Running']
   assert actual['HostConfig']['Runtime']=='runc' and not actual['HostConfig'].get('DeviceRequests')
   run(['docker','exec',worker_cid,'python','-c',"import os;assert not os.path.exists('/dev/nvidiactl') and not os.path.exists('/dev/nvidia0')"])
   stop(worker_cid,image);assert sql('select pg_try_advisory_lock(90601008)')==b't'
   assert sql("select encode(sha256(convert_to(coalesce(string_agg(row_to_json(m)::text,'' order by id),''),'UTF8')),'hex') from meetings m where id not like 'rollproof-%'")==before,'existing_rows_changed'
   assert int(sql('select count(*) from meetings'))==count+index+1
   cycles.append({'index':index,'image_id':image,'native_boot_and_session_lock':True,'synthetic_job_ready':True,'previous_jobs_preserved':True})
  receipt={'status':'PASSED','scope':attempt,'cycles':cycles,'source_dump_sha256':dump_sha,'existing_meetings_preserved':count,'network_internal':True,'production_services_modified':False,'models_or_gpu_used':False,'extractor':'explicit_synthetic_fixture_not_real_AI_quality','native_platform':'linux/amd64','isolation_runtime':'runc','gpu_devices_exposed':False}
  (out/'receipt.json').write_text(json.dumps(receipt,indent=2)+'\n');print(json.dumps(receipt),flush=True)
 finally:
  for cid,image in list(reversed(owned)):
   try:stop(cid,image)
   except Exception:print(json.dumps({'cleanup_failed':True,'owned_scope':attempt}),flush=True)
  if net_created:subprocess.run(['docker','network','rm',net],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=15)
  log.close()
if __name__=='__main__':
 for sig in (signal.SIGTERM,signal.SIGINT,signal.SIGHUP):signal.signal(sig,lambda *_:(_ for _ in ()).throw(RuntimeError('isolated_rollback_signal')))
 main(*sys.argv[1:])
