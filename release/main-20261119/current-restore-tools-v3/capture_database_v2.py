"""Consistent read-only source database snapshot. Never restores into production."""
import fcntl,hashlib,importlib.util,json,os,re,select,signal,subprocess,sys,time
from pathlib import Path
B=Path('/home/epopov/projects/psptm_minutes-ops/main-release-20261119');sys.path.insert(0,str(B/'code-b4'))
spec=importlib.util.spec_from_file_location('b4_readonly',B/'code-b4/run_restore.py');b4=importlib.util.module_from_spec(spec);spec.loader.exec_module(b4)
from production_probe import observe

def main(label,current_attempt,scope="online"):
 assert scope in {"online","frozen"}
 if scope=="frozen":
  from frozen_source_probe import observe_frozen
 for value in (label,current_attempt):assert re.fullmatch(r'[a-z][a-z0-9]{0,15}',value)
 os.umask(0o077);signal.signal(signal.SIGALRM,lambda *_:(_ for _ in ()).throw(RuntimeError('database_capture_deadline')));signal.alarm(240)
 assert json.loads((B/'restore-b4/archive-restore.json').read_text())['archives']==85
 assert json.loads((B/('catchup-20261120-'+current_attempt)/'controller-complete.json').read_text())['status']=='CURRENT_DELTA_APPLIED_AWAITING_FRESH_DB_AND_LIVE_SOURCE_BINDING'
 d=B/('database-snapshot-'+label);assert not d.exists();expected=b4.nas_snapshot()
 def guard():
  b4.nas_snapshot(expected)
  observer=observe_frozen if scope=='frozen' else observe
  return observer(b4.old.preflight(),B/'rollback',Path(json.loads((B/'restore-b4/setup.json').read_text())['target']))
 with (b4.OLD/'controller.lock').open('r') as common:
  fcntl.flock(common,fcntl.LOCK_EX|fcntl.LOCK_NB);guard();d.mkdir(mode=0o700);keeper=None;producer=None
  with (d/'capture.private.log').open('xb') as log:
   try:
    code="""import json,re,sys
from sqlalchemy import text
from app.db import engine,Base
with engine.connect().execution_options(isolation_level='REPEATABLE READ') as c:
 c.execute(text('SET TRANSACTION READ ONLY'))
 snapshot=c.scalar(text('SELECT pg_export_snapshot()'))
 counts={}
 for name in sorted(Base.metadata.tables):
  assert re.fullmatch('[a-z][a-z0-9_]*',name)
  counts[name]=c.scalar(text('SELECT count(*) FROM "'+name+'"'))
 print(json.dumps({'snapshot':snapshot,'counts':counts,'read_only':True}),flush=True)
 sys.stdin.readline()
"""
    baseline=json.loads((B/'baseline-metadata/running-services.json').read_text())['services'];api=baseline['api'];db=baseline['db'];networks=set(api['NetworkSettings']['Networks'])&set(db['NetworkSettings']['Networks']);assert len(networks)==1
    env=dict(x.split('=',1) for x in api['Config']['Env']);secret=next(m['Source'] for m in api['Mounts'] if m['Destination']==env['DB_PASSWORD_FILE'] and m['Type']=='bind')
    exact=json.loads((B/'registry-images/runtime-installed.json').read_text());image=exact['registry_ref'];assert exact['exact_registry_ref_resolves_locally']
    keeper_name='minutes-db-snapshot-'+label;keeper_owner='minutes-restore-db-snapshot-'+label;keeper_cid=d/'keeper.cid'
    assert subprocess.run(['docker','inspect',keeper_name],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL).returncode!=0
    args=['docker','run','--rm','-i','--name',keeper_name,'--cidfile',str(keeper_cid),'--label','minutes.restore.owner='+keeper_owner,'--log-driver','none','--runtime','runc','--network',next(iter(networks)),'--read-only','--cpus','.5','--memory','256m','--cap-drop','ALL','--security-opt','no-new-privileges','--tmpfs','/tmp:size=32m,mode=1777','-e','DB_HOST='+env.get('DB_HOST','db'),'-e','DB_PASSWORD_FILE=/run/snapshot-password','--mount','type=bind,src='+secret+',dst=/run/snapshot-password,readonly','--entrypoint','python',image,'-u','-c',code]
    keeper=subprocess.Popen(args,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=log,text=True)
    ready,_,_=select.select([keeper.stdout],[],[],30);assert ready,'source_snapshot_keeper_unready';snapshot=json.loads(keeper.stdout.readline());token=snapshot['snapshot'];assert re.fullmatch('[a-fA-F0-9]+-[a-fA-F0-9]+-[0-9]+',token)
    (d/'source-counts.private.json').write_text(json.dumps({'counts':snapshot['counts'],'read_only':True},indent=2)+'\n')
    argv=['docker','exec','-e','PGOPTIONS=-c statement_timeout=120000 -c lock_timeout=5000 -c default_transaction_read_only=on','psptm-minutes-db-1','pg_dump','-U','minutes','-d','minutes','-Fc','--no-owner','--snapshot',token]
    producer=subprocess.Popen(argv,stdout=subprocess.PIPE,stderr=log);h=hashlib.sha256();size=0;next_guard=0;deadline=time.monotonic()+180
    with (d/'database.dump').open('xb') as out:
     while True:
      if time.monotonic()>deadline:raise RuntimeError('database_dump_deadline')
      if time.monotonic()>=next_guard:guard();next_guard=time.monotonic()+10
      ready,_,_=select.select([producer.stdout],[],[],5)
      if not ready:
       if producer.poll() is not None:break
       continue
      chunk=os.read(producer.stdout.fileno(),1024**2)
      if not chunk:break
      size+=len(chunk);assert size<=256*1024**2,'database_dump_exceeds_bound';h.update(chunk);out.write(chunk)
     out.flush();os.fsync(out.fileno())
    assert producer.wait(timeout=15)==0 and size>0,'database_dump_failed';keeper.stdin.write('\n');keeper.stdin.flush();assert keeper.wait(timeout=15)==0,'snapshot_keeper_failed';guard()
    receipt={'status':'CONSISTENT_DATABASE_CAPTURE_AWAITING_ISOLATED_RESTORE','bytes':size,'sha256':h.hexdigest(),'tables':len(snapshot['counts']),'read_only_source_transaction':True,'exported_snapshot_shared_with_pg_dump':True,'current_data_attempt':current_attempt,'source_database_modified':False,'source_admission_scope':scope,'time':time.time()};(d/'receipt.json').write_text(json.dumps(receipt,indent=2)+'\n');print(json.dumps(receipt),flush=True)
   finally:
    if producer and producer.poll() is None:producer.terminate();producer.wait(timeout=15)
    if keeper:
     try:keeper.stdin.close()
     except (BrokenPipeError,OSError):pass
     if keeper.poll() is None:
      try:keeper.wait(timeout=10)
      except subprocess.TimeoutExpired:keeper.terminate();keeper.wait(timeout=10)
     if 'keeper_cid' in locals():
      from owned_cleanup import stop_owned
      stop_owned(keeper_name,keeper_cid,keeper_owner,log,expected_image=exact['host_image_id'])
if __name__=='__main__':
 try:main(*sys.argv[1:])
 except Exception as e:print(json.dumps({'status':'STOPPED','error':type(e).__name__,'code':str(e) if isinstance(e,(RuntimeError,AssertionError)) else 'database_capture_failed'}));raise SystemExit(1)
