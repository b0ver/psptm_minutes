"""Run on Mac only; uses existing local GPG storage, never reads key material.
One package per invocation; no automatic overlapping phases or silent retries.
"""
import argparse,fcntl,json,os,subprocess,sys
from pathlib import Path
from next_core import Budget,Halt,encrypt_one,require_space
PLAN='c7f83a04b9efd9de2e84bc0ed7175707ff1fffbabb310a3e70cda30c88cc6a99'
REMOTE='/home/epopov/projects/psptm_minutes-ops/backup-gate-20261118/next-stage/export_one.py'

def main(a):
 if sys.platform!='darwin':raise Halt('mac_only_existing_key_storage')
 if not 0<=a.index<84:raise Halt('package_index')
 out=Path(a.output);out.mkdir(mode=0o700,parents=True,exist_ok=True)
 if out.is_symlink() or out.stat().st_mode&0o077:raise Halt('private_output_permissions')
 os.umask(0o077);os.nice(19)
 with open(out/'transfer.lock','a+') as lock:
  fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
  require_space(out,0)
  # The default Mac SSH route already authenticated to Bender; no keys copied.
  target=out/f'part-{a.index:06d}.tar.gpg'
  metadata_only=Path(str(target)+'.json').exists()
  source=subprocess.Popen(['ssh','-o','BatchMode=yes','-o','ConnectTimeout=8','-o','ServerAliveInterval=5','-o','ServerAliveCountMax=2','100.111.184.29','python3',REMOTE,str(a.index)]+(['--metadata-only'] if metadata_only else []),stdout=subprocess.PIPE,stderr=subprocess.DEVNULL)
  try:
   line=source.stdout.readline(4097)
   if len(line)>4096 or not line.endswith(b'\n'):raise Halt('source_header')
   expected=json.loads(line)
   if expected.get('index')!=a.index or expected.get('plan_sha256')!=PLAN:raise Halt('source_scope')
   target=out/f'part-{a.index:06d}.tar.gpg'
   def source_done():
    if source.wait(timeout=30)!=0:raise Halt('export_incomplete')
   result=encrypt_one(source.stdout,target,expected,a.gpg,Path(a.gpg_home),Path(a.passphrase_file),Budget(lambda:require_space(out,0)),source_done=source_done)
   if source.wait(timeout=30)!=0:raise Halt('export_incomplete')
   print(json.dumps({'package':a.index,'result':result}))
  finally:
   if source.poll() is None:source.terminate();source.wait(timeout=15)
   source.stdout.close()
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--index',type=int,required=True);p.add_argument('--output',required=True);p.add_argument('--gpg-home',required=True);p.add_argument('--passphrase-file',required=True);p.add_argument('--gpg',default='gpg');a=p.parse_args()
 try:main(a)
 except Exception as e:print(json.dumps({'status':'STOPPED','reason':str(e) if isinstance(e,Halt) else type(e).__name__}));raise SystemExit(1)
