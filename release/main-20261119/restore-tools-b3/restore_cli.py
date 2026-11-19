"""Container entry point: dedicated restore/state mounts only, stdin is tar bytes.
Host wrapper must hold controller.lock and refresh verified resource/NAS lease.
Never invoke with production volumes. No acceptance is emitted here.
"""
import json,os,sys
from pathlib import Path
sys.path[:0]=['/writer-code','/next']
from delta_writer import Guard
from next_core import Budget
from restore_controller import restore_one

def main(index):
 if os.environ.get('MINUTES_ISOLATED_RESTORE')!='1':raise RuntimeError('isolated_restore_contract_required')
 # Both must be explicit independent mounts, not writable container root paths.
 mounts={line.split()[4] for line in Path('/proc/self/mountinfo').read_text().splitlines()}
 if not {'/restore','/state','/spec','/control'}.issubset(mounts):raise RuntimeError('dedicated_mounts_required')
 spec=json.loads(Path('/spec/spec.json').read_text());guard=Guard('/restore',reserve=60*1024**3)
 root=Path('/restore/data');root.mkdir(mode=0o700,exist_ok=True)
 guard.check();result=restore_one(root,'/state',spec,index,sys.stdin.buffer,Budget(guard.check));guard.check()
 print(json.dumps({'restored_archive_count':len(result['completed']),'status':result['status']}))
if __name__=='__main__':
 try:main(int(sys.argv[1]))
 except Exception as e:
  from delta_writer import Stopped
  from next_core import Halt
  print(json.dumps({'status':'STOPPED','reason':str(e) if isinstance(e,(Stopped,Halt)) else type(e).__name__,'details':getattr(e,'safe_details',{})}),flush=True);raise SystemExit(1)
