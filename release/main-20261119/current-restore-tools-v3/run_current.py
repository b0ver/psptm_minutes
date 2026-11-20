"""Bounded current-delta controller, only after B4 baseline completed."""
import fcntl,hashlib,importlib.util,json,os,re,signal,subprocess,sys,time
from pathlib import Path
BASE=Path('/home/epopov/projects/psptm_minutes-ops/main-release-20261119');CODE=Path(__file__).resolve().parent
sys.path[:0]=[str(BASE/'code-b4'),str(CODE)]
from resource_pause import ResourceDenied,wait_for_admission
from production_probe import observe
from owned_cleanup import stop_owned
from container_options import isolated_run_options,assert_no_binary_log
spec=importlib.util.spec_from_file_location('b4_observation',BASE/'code-b4/run_restore.py');b4=importlib.util.module_from_spec(spec);spec.loader.exec_module(b4)
BOOT=Path('/proc/sys/kernel/random/boot_id').read_text().strip()
def event(x):print(json.dumps({'time':time.time(),**x}),flush=True)
def main(attempt,parent_attempt=None,scope="online"):
 assert scope in {"online","frozen"}
 if scope=="frozen":
  from frozen_source_probe import observe_frozen
 assert re.fullmatch(r'[a-z][a-z0-9]{0,15}',attempt);os.umask(0o077)
 baseline=json.loads((BASE/'restore-b4/archive-restore.json').read_text());assert baseline['archives']==85
 original=json.loads((BASE/'restore-b4/setup.json').read_text());image=original['image'];volumes=original['volumes'];target=Path(original['target'])
 snap=json.loads((BASE/'baseline-metadata/running-services.json').read_text());source=next(m['Name'] for m in snap['services']['api']['Mounts'] if m['Destination']=='/data' and m['Type']=='volume');assert source not in volumes.values()
 for name in volumes.values():
  assert not subprocess.check_output(['docker','ps','-q','--filter','volume='+name]).strip(),'baseline_restore_still_mounted'
 data=json.loads(subprocess.check_output(['docker','volume','inspect',volumes['data']]))[0];assert data['Labels']=={'minutes.restore.attempt':'20261119-b4'} and data['Options']=={'device':str(target),'o':'bind','type':'none'}
 parent_dir=None
 if parent_attempt is not None:
  assert re.fullmatch(r'[a-z][a-z0-9]{0,15}',parent_attempt) and parent_attempt!=attempt
  parent_dir=BASE/('catchup-20261120-'+parent_attempt)
  previous=json.loads((parent_dir/'controller-complete.json').read_text());assert previous['status']=='CURRENT_DELTA_APPLIED_AWAITING_FRESH_DB_AND_LIVE_SOURCE_BINDING'
  previous_start=json.loads((parent_dir/'started.json').read_text());assert previous_start['restore_volumes']==volumes and previous_start['baseline_spec_sha256']==original['spec_sha256']
 d=BASE/('catchup-20261120-'+attempt);assert not d.exists(),'current_attempt_exists';d.mkdir(mode=0o700)
 for name in ('control','writer'): (d/name).mkdir(mode=0o755);(d/name).chmod(0o755)
 token='minutes-restore-current-20261120-'+attempt;expected=b4.nas_snapshot();deadline=time.monotonic()+7200
 def guard():
  b4.nas_snapshot(expected)
  observer=observe_frozen if scope=='frozen' else observe
  return observer(b4.old.preflight(),BASE/'rollback',target)
 def lease(allowed,reason,details=None):
  p=d/'control/lease.json';tmp=p.with_suffix('.tmp');v={'allowed':allowed,'reason':reason,'boot_id':BOOT,'monotonic_time':time.monotonic(),'time':time.time(),**(details or {})};tmp.write_text(json.dumps(v));tmp.chmod(0o644);os.replace(tmp,p)
 def command(args,timeout=30):
  r=subprocess.run(args,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=timeout)
  if r.returncode:raise RuntimeError('current_setup_command_failed')
  return r.stdout
 with (b4.OLD/'controller.lock').open('r') as lock:
  fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB);g=guard()
  approval=json.loads((BASE/'current-exclusions-approval.json').read_text());ex=(BASE/'current-exclusions.private.json').read_bytes();assert approval['approved_after_exact_type_target_and_terminal_session_check'] and hashlib.sha256(ex).hexdigest()==approval['descriptor_sha256']
  (d/'control/exclusions.json').write_bytes(ex);(d/'control/exclusions.json').chmod(0o644)
  init="from pathlib import Path;import os;p=Path('/out');assert not any(p.iterdir());os.chown(p,10001,10001);os.chmod(p,0o700)"
  command(['docker','run','--rm','--log-driver','none','--network','none','--read-only','--cpus','.1','--memory','64m','--cap-drop','ALL','--cap-add','CHOWN','--cap-add','FOWNER','--security-opt','no-new-privileges','--user','0','-v',str(d/'writer')+':/out:rw','--entrypoint','python',image,'-c',init])
  (d/'started.json').write_text(json.dumps({'attempt':attempt,'source_volume':source,'restore_volumes':volumes,'baseline_spec_sha256':original['spec_sha256'],'controller_pid':os.getpid(),'parent_attempt':parent_attempt,'source_admission_scope':scope,'watchdog_seconds':7200,'source_mounted_readonly':True,'source_production_services_modified':False,'code_sha256':{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in CODE.glob('*.py')}},indent=2)+'\n')
  try:
   for phase in ('package','apply'):
    lease(True,'phase_admitted',guard());name=token+'-'+phase;cidfile=d/(phase+'.cid');assert not cidfile.exists()
    args=['docker','run',*isolated_run_options(token),'--name',name,'--cidfile',str(cidfile),'--cpus','.5','--memory','1g','--user','10001:10001','-e','PYTHONDONTWRITEBYTECODE=1','-e','MINUTES_CURRENT_RESTORE=1','-e','MINUTES_CURRENT_MAX_BYTES=8589934592','-e','BACKUP_LEASE_PATH=/control/lease.json','-v',source+':/source:ro','-v',volumes['data']+':/restore:'+('ro' if phase=='package' else 'rw'),'-v',volumes['state']+':/parent-state:ro','-v','minutes-restore-spec-20261118-a2:/spec:ro','-v',str(d/'control')+':/control:ro','-v',str(d/'control/exclusions.json')+':/current-exclusions.json:ro','-v',str(d/'writer')+':/out:rw','-v',str(BASE/'code-b4')+':/next:ro','-v',str(BASE/'code-b4')+':/writer-code:ro','-v',str(CODE)+':/catchup:ro','--entrypoint','python',image,'/catchup/catchup_cli.py',phase]
    if parent_dir is not None:args[-3:-3]=['-e','MINUTES_CURRENT_PARENT=delta','-v',str(parent_dir/'writer')+':/current-parent:ro']
    proc=None
    with (d/(phase+'.log')).open('xb') as log:
     try:
      proc=subprocess.Popen(args,stdout=log,stderr=log);event({'phase':'current_'+phase+'_started'});next_guard=0;verified=False
      while proc.poll() is None:
       if time.monotonic()>deadline:raise RuntimeError('current_controller_deadline')
       b4.nas_snapshot(expected)
       if not verified and cidfile.exists():
        c=json.loads(command(['docker','inspect',cidfile.read_text().strip()]))[0];assert_no_binary_log(c,token,image);verified=True
       if time.monotonic()>=next_guard:
        try:g=guard()
        except ResourceDenied as e:
         event({'phase':'resource_pause','code':str(e),'sample':e.sample})
         def alive():
          if proc.poll() is not None and proc.returncode!=0:raise RuntimeError('current_writer_failed_paused')
         g=wait_for_admission(guard,lambda:b4.nas_snapshot(expected),lambda:None,lambda:None,lambda allowed,sample:lease(allowed,'resource_resumed' if allowed else 'resource_pause',sample),min(deadline,time.monotonic()+3600),alive=alive)
        lease(True,'guard_pass',g);next_guard=time.monotonic()+10
       time.sleep(1)
      if proc.returncode!=0:raise RuntimeError('current_'+phase+'_failed')
      event({'phase':'current_'+phase+'_completed'})
     finally:
      lease(False,'phase_finished')
      if proc is not None:
       stop_owned(name,cidfile,token,log,expected_image=image)
       try:proc.wait(timeout=20)
       except subprocess.TimeoutExpired:proc.terminate()
   receipt={'status':'CURRENT_DELTA_APPLIED_AWAITING_FRESH_DB_AND_LIVE_SOURCE_BINDING','attempt':attempt,'production_services_modified':False,'source_mounted_readonly':True}
   (d/'controller-complete.json').write_text(json.dumps(receipt,indent=2)+'\n');event(receipt)
  finally:lease(False,'controller_finished')
if __name__=='__main__':
 for sig in (signal.SIGTERM,signal.SIGINT,signal.SIGHUP):signal.signal(sig,lambda *_:(_ for _ in ()).throw(RuntimeError('current_controller_signal')))
 try:main(*sys.argv[1:])
 except Exception as e:event({'phase':'STOPPED','code':str(e) if isinstance(e,(RuntimeError,AssertionError)) else type(e).__name__});raise SystemExit(1)
