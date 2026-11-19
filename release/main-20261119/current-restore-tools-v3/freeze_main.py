"""Bounded original-service freeze, after online current restore and worker rollback.

No service recreation. On failure/timeout, resumes only unchanged original IDs.
Successful handoff requires verified frozen backup and encrypted off-host metadata.
"""
import hashlib,fcntl,importlib.util,json,os,re,select,signal,subprocess,sys,time
from pathlib import Path
B=Path('/home/epopov/projects/psptm_minutes-ops/main-release-20261119')
sys.path[:0]=[str(B),str(B/'code-b4')]
from frozen_source_probe import validate_frozen

def main(attempt,online='c1',frozen='c2'):
 for v in [attempt,online,frozen]:assert re.fullmatch('[a-z][a-z0-9]{0,15}',v)
 os.umask(0o077);baseline=json.loads((B/'baseline-metadata/running-services.json').read_text())['services']
 d=B/('freeze-'+attempt);assert not d.exists();d.mkdir(mode=0o700)
 owner=(B/'main-cutover.lock').open('a');fcntl.flock(owner,fcntl.LOCK_EX|fcntl.LOCK_NB)
 def load(p):return json.loads(p.read_text())
 assert load(B/'restore-b4/archive-restore.json')['archives']==85
 online_snapshot=load(B/('database-snapshot-'+online)/'receipt.json')
 restored=load(B/('restore-current-database-'+online)/'data-db-key-proof.json')
 assert restored['status']=='CURRENT_DATA_DB_KEY_RESTORE_VERIFIED' and restored['source_dump_sha256']==online_snapshot['sha256'] and restored['source_data_attempt']==online
 assert load(B/('restore-current-database-'+online)/'agent-state-startup.json')['state_files_changed']==0
 rollback=load(B/'rollback-roundtrip-current-clone/receipt.json');gpu=load(B/'registry-images/gpu-installed.json')
 assert rollback['status']=='PASSED' and rollback['source_dump_sha256']==online_snapshot['sha256'] and rollback['cycles'][1]['image_id']==gpu['host_image_id']
 prepared=load(B/'candidate-c5/prepared.json');assert prepared['status']=='PREPARED_NOT_ACCEPTED_OR_DEPLOYED'
 assert hashlib.sha256((B/'candidate-c5/compose.yaml').read_bytes()).hexdigest()==prepared['compose_sha256'],'prepared_candidate_changed'
 log=(d/'commands.private.log').open('xb');guard=None;closed=False;handoff=False
 def run(args,timeout=60,data=None):
  r=subprocess.run(args,input=data,stdout=subprocess.PIPE,stderr=log,timeout=timeout)
  if r.returncode:raise RuntimeError('freeze_command_failed')
  return r.stdout
 def event(phase,**extra):
  row={'phase':phase,'time':time.time(),**extra};(d/'state.json').write_text(json.dumps(row,indent=2)+'\n');print(json.dumps(row),flush=True)
 def inspect():
  rows=json.loads(run(['docker','inspect',*[r['Id'] for r in baseline.values()]],15));by={r['Id']:r for r in rows}
  current={role:by[r['Id']] for role,r in baseline.items()}
  for role,row in current.items():assert row['Id']==baseline[role]['Id'] and row['Image']==baseline[role]['Image'],'original_identity_changed'
  return current
 def close_guard():
  nonlocal guard
  if guard is None:return
  try:guard.stdin.write('\n');guard.stdin.flush()
  except (BrokenPipeError,ValueError):pass
  guard.wait(timeout=15);assert guard.returncode==0,'queue_guard_failed';guard=None
 def health(role):
  port=8090 if role=='telemost-agent' else 8080
  code="import urllib.request;assert urllib.request.urlopen('http://127.0.0.1:"+str(port)+"/healthz',timeout=3).status==200"
  deadline=time.monotonic()+45
  while time.monotonic()<deadline:
   r=subprocess.run(['docker','exec',baseline[role]['Id'],'python','-c',code],stdout=subprocess.DEVNULL,stderr=log,timeout=8)
   if r.returncode==0:return
   time.sleep(1)
  raise RuntimeError('original_resume_health_failed')
 def inventory(label):
  run(['python3',str(B/'run_source_inventory.py'),label],60)
  return load(B/('source-inventory-'+label)/'receipt.json')
 signal.signal(signal.SIGALRM,lambda *_:(_ for _ in ()).throw(RuntimeError('freeze_total_deadline')));signal.alarm(1320)
 try:
  current=inspect();assert all(r['State']['Running'] and not r['State']['Paused'] and not r['State']['Restarting'] for r in current.values())
  inventory(attempt+'pre');event('closing_producers')
  closed=True
  run(['docker','stop','-t','45',baseline['telegram']['Id'],baseline['calendar-scheduler']['Id']],100)
  inventory(attempt+'closed')
  queue_code=(B/'code-b4/queue_guard.py').read_text() if (B/'code-b4/queue_guard.py').exists() else (B/'queue_guard.py').read_text()
  queue_code=queue_code.replace('connection.execute(text("SET LOCAL lock_timeout', 'connection.execute(text("SET LOCAL idle_in_transaction_session_timeout = \'120s\'"))\n            connection.execute(text("SET LOCAL lock_timeout',1)
  guard=subprocess.Popen(['docker','exec','-i',baseline['api']['Id'],'python','-u','-c',queue_code],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=log,text=True)
  ready,_,_=select.select([guard.stdout],[],[],20)
  assert ready and guard.stdout.readline().strip()=='idle_locked','processing_or_queue_lock_busy'
  run(['docker','stop','-t','45',baseline['worker']['Id'],baseline['analysis-worker']['Id']],100)
  assert guard.poll() is None,'queue_lock_lost_during_consumer_stop'
  close_guard();event('consumers_stopped_with_queue_locks')
  run(['docker','stop','-t','60',baseline['api']['Id']],80)
  before=inventory(attempt+'drained')
  run(['docker','stop','-t','60',baseline['telemost-agent']['Id']],80)
  after=inventory(attempt+'frozen')
  assert after['all_state_hashes_sha256']==before['all_state_hashes_sha256'],'agent_state_changed_at_idle_shutdown'
  validate_frozen(baseline,inspect());event('frozen_awaiting_current_backup',deadline_seconds=1200)
  deadline=time.monotonic()+1200
  while time.monotonic()<deadline:
   validate_frozen(baseline,inspect())
   marker=d/'handoff-requested.json'
   if marker.exists():
    request=load(marker);assert request=={'frozen_attempt':frozen,'database_label':frozen,'metadata_label':frozen},'invalid_freeze_handoff'
    delta=load(B/('catchup-20261120-'+frozen)/'controller-complete.json');assert delta['status']=='CURRENT_DELTA_APPLIED_AWAITING_FRESH_DB_AND_LIVE_SOURCE_BINDING'
    start=load(B/('catchup-20261120-'+frozen)/'started.json');assert start['source_admission_scope']=='frozen' and start['parent_attempt']==online
    snapshot=load(B/('database-snapshot-'+frozen)/'receipt.json');proof=load(B/('restore-current-database-'+frozen)/'data-db-key-proof.json')
    assert snapshot['source_admission_scope']=='frozen' and snapshot['current_data_attempt']==frozen
    assert proof['status']=='CURRENT_DATA_DB_KEY_RESTORE_VERIFIED' and proof['source_dump_sha256']==snapshot['sha256'] and proof['source_data_attempt']==frozen
    assert load(B/('restore-current-database-'+frozen)/'agent-state-startup.json')['state_files_changed']==0
    metadata=B/('final-metadata-'+frozen);small=load(metadata/'receipt.json');off=load(metadata/'offhost-confirmed.json')
    assert small['database_label']==frozen and small['database_included'] and off['decryption_hash_verified'] and off['sha256']==small['sha256'] and off['plaintext_bytes']==small['bytes']
    assert hashlib.sha256((B/'candidate-c5/compose.yaml').read_bytes()).hexdigest()==small['candidate_compose_sha256'],'candidate_changed_after_final_backup'
    binding=load(B/('source-binding-'+frozen+'.json'));assert binding['status']=='CURRENT_SOURCE_METADATA_BOUND' and binding['metadata_stable'] and binding['attempt']==frozen and 0<=time.time()-binding['time']<=60
    assert binding['target_manifest_sha256']==load(B/('catchup-20261120-'+frozen)/'writer/current-file-proof.json')['target_manifest_sha256']
    final=inventory(attempt+'handoff');assert final['all_state_hashes_sha256']==after['all_state_hashes_sha256']
    assert final['table_counts']==load(B/('database-snapshot-'+frozen)/'source-counts.private.json')['counts'],'database_changed_after_frozen_snapshot'
    validate_frozen(baseline,inspect());handoff=True;event('frozen_handoff_accepted',auto_resume_disabled=True);return
   time.sleep(2)
  raise RuntimeError('frozen_backup_handoff_timeout')
 finally:
  signal.alarm(0)
  if guard is not None:
   try:close_guard()
   except Exception:event('queue_guard_cleanup_requires_review')
  if closed and not handoff:
   event('resuming_original_services')
   inspect()
   run(['docker','start',baseline['telemost-agent']['Id'],baseline['api']['Id']],30);health('telemost-agent');health('api')
   run(['docker','start',baseline['worker']['Id'],baseline['analysis-worker']['Id'],baseline['calendar-scheduler']['Id'],baseline['telegram']['Id']],30)
   assert all(r['State']['Running'] for r in inspect().values());event('original_services_resumed')
  log.close();owner.close()
if __name__=='__main__':
 for sig in [signal.SIGTERM,signal.SIGINT,signal.SIGHUP]:signal.signal(sig,lambda *_:(_ for _ in ()).throw(RuntimeError('freeze_controller_signal')))
 try:main(*sys.argv[1:])
 except Exception as e:print(json.dumps({'phase':'STOPPED','error':type(e).__name__,'code':str(e) if isinstance(e,(RuntimeError,AssertionError)) else 'freeze_failed'}),flush=True);raise SystemExit(1)
