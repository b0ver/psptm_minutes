"""Owner-requested isolated online restore. New attempt; no NAS/production writes."""
import contextlib,fcntl,hashlib,json,os,signal,subprocess,sys,time
from pathlib import Path
BASE=Path('/home/epopov/projects/psptm_minutes-ops/main-release-20261119');D=BASE/'restore-b2'
OLD=Path('/home/epopov/projects/psptm_minutes-ops/backup-gate-20261118')
sys.path[:0]=[str(OLD/'code-public'),str(BASE/'code-b2')]
import controller as old
from owned_cleanup import stop_owned,set_paused_owned
SETUP=json.loads((D/'setup.json').read_text());IMAGE=SETUP['image'];V=SETUP['volumes'];TOKEN='minutes-restore-20261119-b2'
BOOT=Path('/proc/sys/kernel/random/boot_id').read_text().strip()
def event(x):print(json.dumps({'time':time.time(),**x}),flush=True)
def nas_snapshot(expected=None):
 with contextlib.ExitStack() as stack:
  for p in old.NAS_LOCKS:
   f=stack.enter_context(p.open('rb'));fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)
  rows=old.nas_rows()
  if not rows or any(x[2] not in {'committed','source_changed'} for x in rows):raise RuntimeError('nas_not_idle_checkpoint')
  if expected is not None and rows!=expected:raise RuntimeError('nas_checkpoint_changed')
  return rows

def write_lease(allowed,reason,details=None):
 value={'time':time.time(),'monotonic_time':time.monotonic(),'boot_id':BOOT,'allowed':allowed,'reason':reason,**(details or {})}
 path=D/'control/lease.json';tmp=path.with_suffix('.tmp')
 with tmp.open('w') as f:json.dump(value,f);f.flush();os.fsync(f.fileno())
 tmp.chmod(0o644);os.replace(tmp,path)

from resource_pause import ResourceDenied,wait_for_admission

def guard(expected):
 nas_snapshot(expected);g=old.preflight();s=g['sample']
 if g.get('light_cpu_allowed') is not True:raise ResourceDenied('light_cpu_denied',g)
 if s['io_pressure_avg10']>1 or s['memory_pressure_avg10']!=0:raise ResourceDenied('pressure_denied',g)
 # Existing read-only helper checks all durable agent states and SQL processing.
 sys.path.insert(0,str(BASE));import rollback_export as recovery
 try:recovery.guard()
 except (AssertionError,RuntimeError,subprocess.TimeoutExpired):raise ResourceDenied('production_or_resource_guard_denied',g) from None
 return {'load_1m':s['load_1m'],'io_pressure_avg10':s['io_pressure_avg10'],'memory_pressure_avg10':s['memory_pressure_avg10'],'nas_mode':'fresh_idle_checkpoint_not_accepted'}

def inspect_volume(name):return json.loads(subprocess.check_output(['docker','volume','inspect',name],timeout=10))[0]
def run():
 os.umask(0o077);assert not (D/'started.json').exists(),'attempt_already_started'
 with (OLD/'controller.lock').open('r') as common:
  fcntl.flock(common,fcntl.LOCK_EX|fcntl.LOCK_NB)
  for role,name in V.items():
   v=inspect_volume(name);assert v['Labels']=={'minutes.restore.attempt':'20261119-b2'} and v['Driver']=='local'
   if role=='data':assert v['Options']=={'device':SETUP['target'],'o':'bind','type':'none'}
   else:assert not v.get('Options')
  expected=nas_snapshot();time.sleep(2);nas_snapshot(expected);g=guard(expected)
  started={'started_at':time.time(),'nas_checkpoint':expected,'nas_accepted':False,'spec_sha256':SETUP['spec_sha256'],'watchdog_seconds':21600,'aggregate_payload_bytes_per_second':8*1024**2,'original_controllers_modified':False}
  (D/'started.json').write_text(json.dumps(started,indent=2)+'\n');write_lease(True,'fresh_admission',g);event({'phase':'admitted',**g})
  deadline=time.monotonic()+21600
  try:
   for index in range(85):
    if time.monotonic()>deadline:raise RuntimeError('restore_deadline')
    write_lease(True,'phase_admission',guard(expected))
    source=Path('/home/epopov/projects/psptm_minutes-ops/recorder-hotfix-20261107/online-recordings.tar') if index==0 else OLD/'writer-data'/f'part-{index-1:06d}.tar'
    reader_name=TOKEN+'-reader';writer_name=TOKEN+'-writer';reader_cid=D/f'reader-{index:03d}.cid';writer_cid=D/f'writer-{index:03d}.cid'
    assert not reader_cid.exists() and not writer_cid.exists()
    uid='1000:1000' if index==0 else '10001:10001'
    common_args=['--rm','--network','none','--read-only','--cap-drop','ALL','--security-opt','no-new-privileges','--label','minutes.restore.owner='+TOKEN]
    reader=['docker','run',*common_args,'--name',reader_name,'--cidfile',str(reader_cid),'--cpus','.1','--memory','64m','--user',uid,'-v',str(source)+':/input.tar:ro','--entrypoint','sh',IMAGE,'-c','exec nice -n19 ionice -c3 cat /input.tar']
    writer=['docker','run','-i',*common_args,'--name',writer_name,'--cidfile',str(writer_cid),'--cpus','.5','--memory','512m','--user','10001:10001','-e','PYTHONDONTWRITEBYTECODE=1','-e','MINUTES_ISOLATED_RESTORE=1','-e','BACKUP_LEASE_PATH=/control/lease.json','-e','MINUTES_RESTORE_PROGRESS=/progress/current.json','-e','MINUTES_RESTORE_INDEX='+str(index),'-v',V['data']+':/restore:rw','-v',V['state']+':/state:rw','-v','minutes-restore-spec-20261118-a2:/spec:ro','-v',str(D/'control')+':/control:ro','-v',str(D/'progress')+':/progress:rw','-v',str(BASE/'code-b2')+':/next:ro','-v',str(BASE/'code-b2')+':/writer-code:ro','--entrypoint','sh',IMAGE,'-c','exec nice -n19 ionice -c3 python /next/restore_cli.py '+str(index)]
    producer=None;consumer=None;primary=None
    with (D/f'phase-{index:03d}.log').open('ab') as log:
     try:
      producer=subprocess.Popen(reader,stdout=subprocess.PIPE,stderr=log)
      consumer=subprocess.Popen(writer,stdin=producer.stdout,stdout=log,stderr=log);producer.stdout.close()
      event({'phase':'archive_started','index':index});next_guard=0;next_progress=0;last_bytes=-1;last_advance=time.monotonic()
      while consumer.poll() is None:
       if time.monotonic()>deadline:raise RuntimeError('restore_deadline')
       nas_snapshot(expected)
       if time.monotonic()>=next_guard:
        try:g=guard(expected)
        except ResourceDenied as denied:
         event({'phase':'resource_pause','code':str(denied),'sample':denied.sample})
         def toggle(paused):
          for name,cid in [(writer_name,writer_cid),(reader_name,reader_cid)]:set_paused_owned(name,cid,TOKEN,IMAGE,paused)
         g=wait_for_admission(lambda:guard(expected),lambda:nas_snapshot(expected),lambda:toggle(True),lambda:toggle(False),lambda allowed,sample:write_lease(allowed,'resource_recheck',sample),min(deadline,time.monotonic()+900))
         event({'phase':'resource_resumed','index':index,**g});last_advance=time.monotonic()
        write_lease(True,'guard_pass',g);next_guard=time.monotonic()+10
       progress=D/'progress/current.json'
       if progress.exists():
        p=json.loads(progress.read_text())
        if p.get('index')==index:
         if p['archive_bytes_read']>last_bytes:last_bytes=p['archive_bytes_read'];last_advance=time.monotonic()
         if time.monotonic()>=next_progress:event({'phase':'progress','index':index,'bytes':p['archive_bytes_read'],'expected':p['archive_bytes_expected']});next_progress=time.monotonic()+60
       if time.monotonic()-last_advance>300:raise RuntimeError('restore_stalled')
       time.sleep(1)
      if consumer.returncode!=0:raise RuntimeError('restore_container_exit_'+str(consumer.returncode))
      if producer.wait(timeout=30)!=0:raise RuntimeError('archive_reader_failed')
      event({'phase':'archive_completed','index':index})
     except BaseException as e:primary=e;raise
     finally:
      write_lease(False,'phase_ended');errors=[]
      for proc,name,cid in [(consumer,writer_name,writer_cid),(producer,reader_name,reader_cid)]:
       if proc is not None:
        try:stop_owned(name,cid,TOKEN,log,expected_image=IMAGE)
        except Exception as e:errors.append(type(e).__name__)
        try:proc.wait(timeout=20)
        except subprocess.TimeoutExpired:proc.terminate()
      if errors:
       event({'phase':'cleanup_errors','errors':errors,'primary':type(primary).__name__ if primary else None})
       if primary is None:raise RuntimeError('cleanup_incomplete')
   result={'status':'ARCHIVES_RESTORED_AWAITING_CURRENT_DELTA_DB_KEY_APP_SMOKE','archives':85,'spec_sha256':SETUP['spec_sha256']}
   (D/'archive-restore.json').write_text(json.dumps(result,indent=2)+'\n');event(result)
  finally:write_lease(False,'controller_finished')
if __name__=='__main__':
 for sig in (signal.SIGTERM,signal.SIGINT,signal.SIGHUP):signal.signal(sig,lambda *_:(_ for _ in ()).throw(RuntimeError('controller_signal')))
 try:run()
 except BaseException as e:
  event({'phase':'STOPPED','reason':type(e).__name__,'code':str(e) if isinstance(e,(RuntimeError,AssertionError)) else 'guard_or_io_exception'});raise SystemExit(1)
