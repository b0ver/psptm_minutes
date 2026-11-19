"""Single approved online restore, no production freeze/write, no NAS changes."""
import fcntl,json,os,subprocess,sys,time
from pathlib import Path
B=Path('/home/epopov/projects/psptm_minutes-ops/backup-gate-20261118');sys.path.insert(0,str(B/'code'));import controller as c
from owned_cleanup import stop_owned
OWNER=False
TOKEN="minutes-a2-20261118-1630-7c0b584b"
V={'data':'minutes-restore-data-20261118-a2','state':'minutes-restore-state-20261118-a2','spec':'minutes-restore-spec-20261118-a2'}
READER='minutes-restore-reader-20261118-a2';WRITER='minutes-restore-online-20261118-a2'
def event(v):print(json.dumps(v),flush=True)
original_preflight=c.preflight
def logged_preflight():
 g=original_preflight();event({'time':time.time(),'kind':'exact_resource_guard_before_decision','sample_and_reasons':g});return g
c.preflight=logged_preflight

def run():
 global OWNER
 os.umask(0o077)
 with open(B/'controller.lock','r') as lock:
  fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
  OWNER=True;event({"kind":"restore_controller_started","pid":os.getpid(),"watchdog_seconds":21600})
  for name in V.values():
   obj=json.loads(subprocess.check_output(['docker','volume','inspect',name]))[0]
   assert obj['Name']==name and obj['Driver']=='local' and not obj.get('Options') and obj['Labels']=={'minutes.restore.attempt':'20261118-a2'}
  paused=c.paused_evidence();v,_,_=c.probe(52112,None,paused);event({'kind':'initial_admission','guard':v});assert v['allowed']
  c.atomic_lease(v);started=time.monotonic();deadline=started+6*3600
  for index in range(85):
   c.paused_evidence(paused);v,_,_=c.probe(52112,None,paused);event({'kind':'phase_admission','index':index,'guard':v});assert v['allowed'];c.atomic_lease(v)
   path=Path('/home/epopov/projects/psptm_minutes-ops/recorder-hotfix-20261107/online-recordings.tar') if index==0 else B/'writer-data'/f'part-{index-1:06d}.tar'
   reader_cid=B/f'a2-reader-{index:03d}.cid';writer_cid=B/f'a2-writer-{index:03d}.cid'
   assert not reader_cid.exists() and not writer_cid.exists()
   uid='1000:1000' if index==0 else '10001:10001'
   read_cmd=['docker','run','--rm','--name',READER,'--label','minutes.restore.owner='+TOKEN,'--cidfile',str(reader_cid),'--network','none','--read-only','--cpus','.1','--memory','64m','--cap-drop','ALL','--security-opt','no-new-privileges','--user',uid,'-v',str(path)+':/input.tar:ro','--entrypoint','sh',c.IMAGE,'-c','exec nice -n19 ionice -c3 cat /input.tar']
   write_cmd=['docker','run','--rm','-i','--name',WRITER,'--label','minutes.restore.owner='+TOKEN,'--cidfile',str(writer_cid),'--network','none','--read-only','--cpus','.5','--memory','768m','--cap-drop','ALL','--security-opt','no-new-privileges','--user','10001:10001','-e','PYTHONDONTWRITEBYTECODE=1','-e','MINUTES_ISOLATED_RESTORE=1','-e','BACKUP_LEASE_PATH=/control/lease.json','-e','MINUTES_RESTORE_PROGRESS=/progress/current.json','-e','MINUTES_RESTORE_INDEX='+str(index),'-v',V['data']+':/restore:rw','-v',V['state']+':/state:rw','-v',V['spec']+':/spec:ro','-v',str(B/'guard-public')+':/control:ro','-v',str(B/'restore-progress-a2')+':/progress:rw','-v',str(B/'next-stage')+':/next:ro','-v',str(B/'code-public')+':/writer-code:ro','--entrypoint','sh',c.IMAGE,'-c','exec nice -n19 ionice -c3 python /next/restore_cli.py '+str(index)]
   producer=None;consumer=None
   with open(B/f'restore-a2-phase-{index:03d}.log','ab') as log:
    try:
     producer=subprocess.Popen(read_cmd,stdout=subprocess.PIPE,stderr=log)
     consumer=subprocess.Popen(write_cmd,stdin=producer.stdout,stdout=log,stderr=log);producer.stdout.close()
     event({'kind':'phase_started','index':index,'reader_pid':producer.pid,'writer_pid':consumer.pid,'container':WRITER})
     next_guard=0;last_bytes=-1;last_advance=time.monotonic();phase_start=time.monotonic()
     while consumer.poll() is None:
      if time.monotonic()>deadline:raise TimeoutError('six_hour_restore_deadline')
      c.paused_evidence(paused)
      if time.monotonic()>=next_guard:
       v,_,_=c.probe(52112,None,paused);event({'kind':'guard','index':index,'value':v});c.atomic_lease(v)
       if not v['allowed']:raise RuntimeError('resource_or_recorder_guard_denied')
       next_guard=time.monotonic()+10
       progress=B/'restore-progress-a2/current.json'
       if progress.exists():
        p=json.loads(progress.read_text())
        if p.get('index')==index:
         event({'kind':'progress','value':p})
         if p['archive_bytes_read']>last_bytes:last_bytes=p['archive_bytes_read'];last_advance=time.monotonic()
       if time.monotonic()-last_advance>300:raise TimeoutError('restore_progress_stalled_300s')
      time.sleep(1)
     if consumer.returncode!=0:raise RuntimeError('restore_container_failed')
     if producer.wait(timeout=30)!=0:raise RuntimeError('archive_reader_failed')
     event({'kind':'phase_completed','index':index,'elapsed_seconds':time.monotonic()-phase_start})
    finally:
     cleanup_errors=[]
     try:c.atomic_lease({'time':time.time(),'allowed':False,'reason':'restore_phase_ended'})
     except Exception as error:cleanup_errors.append('lease_'+type(error).__name__);event({'kind':'lease_cleanup_error','reason':type(error).__name__})
     for proc,name,cid in [(consumer,WRITER,writer_cid),(producer,READER,reader_cid)]:
      if proc is not None:
       try:stop_owned(name,cid,TOKEN,log,expected_image=c.IMAGE)
       except Exception as error:cleanup_errors.append(type(error).__name__);event({'kind':'cleanup_error','container':name,'reason':str(error)})
       if proc.poll() is None:
        try:proc.wait(timeout=20)
        except subprocess.TimeoutExpired:proc.terminate()
     if cleanup_errors:raise RuntimeError('owned_cleanup_incomplete')
  event({'kind':'all_archives_restored','status':'AWAITING_REAL_DB_KEY_APP_SMOKE','archives':85})
if __name__=='__main__':
 try:run()
 except Exception as e:
  event({'kind':'STOPPED','time':time.time(),'reason':type(e).__name__,'code':str(e)[:160]})
  if OWNER:c.atomic_lease({'time':time.time(),'allowed':False,'reason':'restore_controller_stopped'})
  raise SystemExit(1)
