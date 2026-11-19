"""Bounded remote backup controller. No production mutations or secret access."""
import contextlib,fcntl,json,os,sqlite3,subprocess,sys,time
from pathlib import Path
OWNER=False
BASE=Path('/home/epopov/projects/psptm_minutes-ops/backup-gate-20261118')
IMAGE='sha256:24e3f8bacc4bd7af6f6dc56f8bdc0e1effd3f094756b52a3d9c7d7c1140800b8'
sys.path.insert(0,'/home/epopov/projects/psptm_mail_copilot/lab')
from resource_gate import preflight
CODE="""import glob,json,time,collections
counts=collections.Counter();bad=0;active=0
for p in glob.glob('/data/telemost/*/state.json'):
 try:
  s=json.load(open(p));status=s.get('status');counts[status]+=1
  if status in ('uploaded','failed','cancelled','partial_saved'):continue
  active+=1
  if status!='recording' or s.get('capture_health')!='healthy' or time.time()-float(s.get('last_checkpoint_at') or 0)>90:bad+=1
 except Exception:bad+=1
print(json.dumps({'counts':dict(counts),'active':active,'unhealthy_or_unknown':bad}))"""

NAS_ROOT=Path('/mnt/models/mail-archive/nas-transfer-20261031-001')
NAS_LOCKS=[NAS_ROOT/'missing-copy-20261113/step.lock',NAS_ROOT/'final-verify-20261113/controller.lock',NAS_ROOT/'final-verify-20261113/step.lock']

def nas_rows():
 c=sqlite3.connect('file:'+str(NAS_ROOT/'final-verify-20261113/ledger.sqlite')+'?mode=ro',uri=True,timeout=.2)
 try:
  c.execute('PRAGMA query_only=ON');return c.execute('SELECT id,phase,result FROM attempts ORDER BY id DESC LIMIT 4').fetchall()
 finally:c.close()

def nas_latest():return max((r[0] for r in nas_rows() if r[2]=='committed'),default=0)

def paused_evidence(expected=None):
 # Hold all three only for the bounded read-only snapshot; never edit lock files.
 with contextlib.ExitStack() as stack:
  for path in NAS_LOCKS:
   f=stack.enter_context(path.open('rb'));fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)
  rows=nas_rows()
  if not rows or rows[0][0]!=52112 or any(r[2]!='committed' for r in rows):raise RuntimeError('paused_checkpoint_changed')
  if expected is not None and rows!=expected:raise RuntimeError('paused_checkpoint_changed')
  return rows

def atomic_lease(value):
 p=BASE/'guard-public'/'lease.json.tmp'
 with open(p,'w') as f:json.dump(value,f);f.flush();os.fsync(f.fileno())
 os.chmod(p,0o644);os.replace(p,BASE/'guard-public'/'lease.json')

def probe(latest,last_progress,paused=None):
 if paused is not None:paused_evidence(paused)
 g=preflight();s=g['sample'];n=nas_latest()
 if n>latest:latest=n;last_progress=time.monotonic()
 age=None if last_progress is None else time.monotonic()-last_progress
 h=json.loads(subprocess.check_output(['docker','run','--rm','--network','none','--read-only','--cpus','0.25','--memory','128m','--cap-drop','ALL','--security-opt','no-new-privileges','--volumes-from','psptm-minutes-api-1:ro','--entrypoint','python',IMAGE,'-c',CODE],timeout=12))
 allowed=(g.get('light_cpu_allowed') is True and s['io_pressure_avg10']<=1 and s['memory_pressure_avg10']==0 and h['unhealthy_or_unknown']==0 and (paused is not None or (age is not None and age<180)))
 v={'time':time.time(),'allowed':allowed,'nas_latest_committed':latest,'nas_mode':'paused_not_accepted' if paused is not None else 'active','nas_progress_age':round(age,1) if age is not None else None,'load_1m':s['load_1m'],'io_pressure_avg10':s['io_pressure_avg10'],'memory_pressure_avg10':s['memory_pressure_avg10'],'recorder':h}
 return v,latest,last_progress

def run():
 global OWNER
 os.umask(0o077)
 with open(BASE/'controller.lock','a+') as lock:
  fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
  OWNER=True
  paused=paused_evidence() if '--nas-paused' in sys.argv else None
  latest=nas_latest();last_progress=None
  if paused is None:
   for _ in range(12):
    time.sleep(10)
    if nas_latest()>latest:break
  v,latest,last_progress=probe(latest,last_progress,paused)
  atomic_lease(v);print(json.dumps(v),flush=True)
  if not v['allowed']:return 2
  if '--probe-only' in sys.argv:return 0
  log=open(BASE/'writer.log','a')
  cmd=['docker','run','--rm','--name','minutes-backup-delta-20261118','--network','none','--read-only','--cpus','0.5','--memory','512m','--cap-drop','ALL','--security-opt','no-new-privileges','--user','10001:10001','--volumes-from','psptm-minutes-api-1:ro','-v',str(BASE/'writer-data')+':/backup:rw','-v',str(BASE/'code-public')+':/code:ro','-v',str(BASE/'guard-public')+':/control:ro','-e','BACKUP_LEASE_PATH=/control/lease.json','-e','PYTHONDONTWRITEBYTECODE=1','--entrypoint','sh',IMAGE,'-c','exec nice -n19 ionice -c3 python /code/delta_writer.py /data /backup /backup/plan.json']
  worker=subprocess.Popen(cmd,stdout=log,stderr=log)
  try:
   deadline=time.monotonic()+7200
   next_full=time.monotonic()
   while worker.poll() is None and time.monotonic()<deadline:
    time.sleep(1)
    if paused is not None:paused_evidence(paused)
    if time.monotonic()<next_full:continue
    v,latest,last_progress=probe(latest,last_progress,paused);atomic_lease(v);print(json.dumps(v),flush=True)
    next_full=time.monotonic()+10
    if not v['allowed']:break
  finally:
   atomic_lease({'time':time.time(),'allowed':False,'reason':'controller_finished_or_guard_denied'})
  try:result=worker.wait(timeout=40)
  except subprocess.TimeoutExpired:
   # Stop only this dedicated backup container; never production/NAS processes.
   subprocess.run(['docker','stop','-t','5','minutes-backup-delta-20261118'],timeout=15,stdout=log,stderr=log)
   result=worker.wait(timeout=10)
  print(json.dumps({'backup_writer_exit':result}),flush=True);return result

if __name__=='__main__':
 try:raise SystemExit(run())
 except Exception as e:
  if OWNER: atomic_lease({'time':time.time(),'allowed':False,'reason':type(e).__name__})
  print(json.dumps({'status':'STOPPED','reason':type(e).__name__}),flush=True);raise SystemExit(1)
