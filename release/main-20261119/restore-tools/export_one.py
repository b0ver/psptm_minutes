"""Host lock/guard wrapper. Stdout contains recording bytes: pipe only to local GPG runner."""
import fcntl,os,signal,subprocess,sys,threading,time
from pathlib import Path
BASE=Path('/home/epopov/projects/psptm_minutes-ops/backup-gate-20261118')
sys.path.insert(0,str(BASE/'code'));import controller as c

def main(index):
 os.umask(0o077)
 with open(BASE/'controller.lock','a+') as lock:
  fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
  # Lock prevents overlap with packaging. Complete marker inside helper is mandatory.
  paused=c.paused_evidence();v,latest,last=c.probe(52112,None,paused)
  if not v['allowed']:raise RuntimeError('guard_denied')
  c.atomic_lease(v);stop=threading.Event()
  def refresh():
   try:
    while not stop.wait(5):
     value,_,_=c.probe(52112,None,paused);c.atomic_lease(value)
     if not value['allowed']:return
   except BaseException:c.atomic_lease({'time':time.time(),'allowed':False,'reason':'export_guard_failed'})
  monitor=threading.Thread(target=refresh,daemon=True);monitor.start()
  cmd=['docker','run','--rm','--network','none','--read-only','--cpus','0.25','--memory','128m','--cap-drop','ALL','--security-opt','no-new-privileges','--user','10001:10001','--tmpfs','/tmp:rw,noexec,nosuid,size=1m','-e','BACKUP_LEASE_PATH=/control/lease.json','-e','PYTHONDONTWRITEBYTECODE=1','-v',str(BASE/'writer-data')+':/archives:ro','-v',str(BASE/'code-public')+':/writer-code:ro','-v',str(BASE/'guard-public')+':/control:ro','-v',str(BASE/'next-stage')+':/next:ro','--entrypoint','sh',c.IMAGE,'-c','exec nice -n19 ionice -c3 python /next/export_stream.py '+str(index)+(' --metadata-only' if '--metadata-only' in sys.argv else '')]
  process=None
  try:
   signal.signal(signal.SIGALRM,lambda *a:(_ for _ in ()).throw(TimeoutError('export_watchdog')));signal.alarm(1800)
   process=subprocess.Popen(cmd,stdout=sys.stdout.buffer,stderr=subprocess.DEVNULL)
   rc=process.wait()
   if rc:raise RuntimeError('export_failed')
  finally:
   signal.alarm(0);stop.set();monitor.join(timeout=15);c.atomic_lease({'time':time.time(),'allowed':False,'reason':'export_finished'})
   if process is not None and process.poll() is None:process.terminate()
if __name__=='__main__':
 try:
  n=int(sys.argv[1]);assert 0<=n<84;main(n)
 except BaseException:
  print('export_denied_or_incomplete',file=sys.stderr);raise SystemExit(1)
