"""Container-only exporter: stdout is binary protocol, never run in a chat tool."""
import json,sys
from pathlib import Path
sys.path.insert(0,'/writer-code')
from delta_writer import Guard

def export(root,index,out,metadata_only=False):
 root=Path(root);complete=json.loads((root/'archive-set-complete.json').read_text());r=json.loads((root/f'part-{index:06d}.json').read_text())
 if r['plan_sha256']!=complete['plan_sha256'] or not 0<=index<complete['packages']:raise ValueError('binding')
 p=root/f'part-{index:06d}.tar'
 if p.is_symlink() or not p.is_file() or p.stat().st_size!=r['archive_bytes']:raise ValueError('archive')
 h={'index':index,'archive_bytes':r['archive_bytes'],'archive_sha256':r['archive_sha256'],'plan_sha256':r['plan_sha256']}
 out.write(json.dumps(h,sort_keys=True).encode()+b'\n');out.flush()
 if metadata_only:return
 guard=Guard('/tmp',reserve=0)
 with p.open('rb') as f:
  while True:
   guard.pace(2*256*1024);b=f.read(256*1024)
   if not b:break
   out.write(b);out.flush()
if __name__=='__main__':export('/archives',int(sys.argv[1]),sys.stdout.buffer,'--metadata-only' in sys.argv)
