"""Isolated-volume restore state machine. No Docker/prod mutations.
All callers must supply a live fail-closed guard and dedicated root/state mounts.
"""
import fcntl,hashlib,json,os,stat,time
from pathlib import Path
from next_core import Halt,atomic_json,metered_read
from safe_restore import restore_tar,parent_fd
import sys
sys.path.insert(0,'/writer-code')
from plan import entries,canonical,digest
from runtime_scope import persistent

class HashInput:
 def __init__(self,source,budget,limit):self.source=source;self.budget=budget;self.limit=limit;self.size=0;self.hash=hashlib.sha256();self.last_progress=0
 def read(self,n=-1):
  if n<0:n=256*1024
  n=min(n,64*1024);b=metered_read(self.source,self.budget,1,n);self.size+=len(b)
  if self.size>self.limit:raise Halt('archive_exceeds_bound')
  self.hash.update(b)
  if os.environ.get("MINUTES_RESTORE_PROGRESS") and time.monotonic()-self.last_progress>=5:
   path=Path(os.environ["MINUTES_RESTORE_PROGRESS"]);tmp=path.with_suffix(".tmp")
   tmp.write_text(json.dumps({"time":time.time(),"index":int(os.environ["MINUTES_RESTORE_INDEX"]),"archive_bytes_read":self.size,"archive_bytes_expected":self.limit}));tmp.chmod(0o644);os.replace(tmp,path);self.last_progress=time.monotonic()
  return b

def make_spec(baseline,target,exclusions,archives,plan_sha):
 entries(baseline);entries(target)
 if not archives or archives[0]['kind']!='baseline' or any(a['kind']!='delta' for a in archives[1:]):raise Halt('archive_order')
 if len({a['id'] for a in archives})!=len(archives):raise Halt('duplicate_archive')
 for a in archives:
  if not isinstance(a['size'],int) or a['size']<0 or len(a['sha256'])!=64:raise Halt('archive_descriptor')
  if a.get('plan_sha256')!=plan_sha:raise Halt('archive_plan_binding')
 spec={'schema':1,'baseline':baseline,'target':target,'exclusions':exclusions,'exclusions_sha256':digest(exclusions),'archives':archives,'plan_sha256':plan_sha}
 spec['spec_sha256']=digest(spec);return spec

def validate_spec(spec):
 s=dict(spec);claimed=s.pop('spec_sha256')
 if digest(s)!=claimed:raise Halt('spec_changed')
 entries(s['baseline']);entries(s['target']);return claimed

def statrow(root,name):
 p=Path(root)/name;s=p.lstat()
 return [name,s.st_mode,s.st_size,s.st_mtime_ns,os.readlink(p) if stat.S_ISLNK(s.st_mode) else None]

def snapshot(root):
 rows=[]
 for d,ds,fs in os.walk(root,followlinks=False):
  for name in ds+fs:rows.append(statrow(root,os.path.relpath(Path(d)/name,root)))
 return sorted(rows)

def regular_fd(rootfd,name):
 # Never create parents during verification; never follow links.
 fd=os.dup(rootfd)
 try:
  parts=name.split('/')
  for part in parts[:-1]:
   new=os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=fd);os.close(fd);fd=new
  return os.open(parts[-1],os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=fd)
 finally:os.close(fd)

def remove_obsolete(root,spec):
 old=entries(spec['baseline']);new=entries(spec['target']);remove=[]
 for name,row in old.items():
  if name not in new or stat.S_IFMT(row[1])!=stat.S_IFMT(new[name][1]):remove.append(name)
 fd=os.open(root,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
 try:
  for name in sorted(remove,key=lambda x:(-x.count('/'),x)):
   pfd=parent_fd(fd,name)
   try:
    try:s=os.stat(name.split('/')[-1],dir_fd=pfd,follow_symlinks=False)
    except FileNotFoundError:continue
    if stat.S_ISDIR(s.st_mode):os.rmdir(name.split('/')[-1],dir_fd=pfd)
    else:os.unlink(name.split('/')[-1],dir_fd=pfd)
   finally:os.close(pfd)
 finally:os.close(fd)

def apply_metadata(root,rows):
 expected=entries(rows);fd=os.open(root,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
 try:
  for name,row in sorted(expected.items(),key=lambda x:(-x[0].count('/'),x[0])):
   if stat.S_ISDIR(row[1]):
    pfd=parent_fd(fd,name)
    try:
     try:os.mkdir(name.split('/')[-1],0o700,dir_fd=pfd)
     except FileExistsError:pass
     opened=os.open(name.split('/')[-1],os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=pfd)
    finally:os.close(pfd)
   else:opened=regular_fd(fd,name)
   try:
    s=os.fstat(opened)
    if stat.S_IFMT(s.st_mode)!=stat.S_IFMT(row[1]) or (stat.S_ISREG(s.st_mode) and s.st_size!=row[2]):raise Halt('metadata_type_size_mismatch')
    os.fchmod(opened,stat.S_IMODE(row[1]));os.utime(opened,ns=(row[3],row[3]))
   finally:os.close(opened)
 finally:os.close(fd)

def restore_one(root,state_dir,spec,index,stream,budget):
 validate_spec(spec);root=Path(root);state_dir=Path(state_dir)
 if root.is_symlink() or state_dir.is_symlink() or root.resolve()==state_dir.resolve():raise Halt('unsafe_restore_roots')
 with open(state_dir/'restore.lock','a+') as lock:
  fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB);statefile=state_dir/'state.json'
  if statefile.exists():
   state=json.loads(statefile.read_text())
   if state['spec_sha256']!=spec['spec_sha256']:raise Halt('state_spec_changed')
   if state.get('in_progress') is not None:raise Halt('partial_restore_requires_fresh_volume')
  else:
   if any(root.iterdir()):raise Halt('initial_restore_root_not_empty')
   state={'spec_sha256':spec['spec_sha256'],'completed':[],'in_progress':None}
  if index!=len(state['completed']) or not 0<=index<len(spec['archives']):raise Halt('restore_sequence')
  state['in_progress']=index;atomic_json(statefile,state)
  # Deletions/type transitions occur after baseline, before the first delta.
  if index==1:remove_obsolete(root,spec)
  item=spec['archives'][index];reader=HashInput(stream,budget,item['size']);file_hashes=restore_tar(reader,root,budget,item.get('strip_prefix'))
  while reader.read(256*1024):pass
  if reader.size!=item['size'] or reader.hash.hexdigest()!=item['sha256']:raise Halt('archive_hash_mismatch')
  atomic_json(state_dir/f'archive-{index:06d}.files.json',{'spec_sha256':spec['spec_sha256'],'archive_sha256':item['sha256'],'files':file_hashes})
  if index==len(spec['archives'])-1:
   if len(spec['archives'])==1:remove_obsolete(root,spec)
   apply_metadata(root,spec['target'])
   hashes={}
   for i in range(len(spec['archives'])):
    proof=json.loads((state_dir/f'archive-{i:06d}.files.json').read_text())
    if proof['spec_sha256']!=spec['spec_sha256'] or proof['archive_sha256']!=spec['archives'][i]['sha256']:raise Halt('fileproof_binding')
    hashes.update({r[0]:r[1] for r in proof['files']})
   bound=[]
   for row in spec['target']:
    if not stat.S_ISREG(row[1]):continue
    if row[0] not in hashes:raise Halt('missing_extraction_hash')
    st=(root/row[0]).lstat()
    bound.append([row[0],hashes[row[0]],[st.st_dev,st.st_ino,st.st_mode,st.st_size,st.st_mtime_ns,st.st_ctime_ns]])
   atomic_json(state_dir/'file-proof.json',{'method':'archive_sha_and_extraction_hash_with_post_metadata_fingerprint_not_independent_disk_reread','spec_sha256':spec['spec_sha256'],'files':bound})
  state['completed'].append(item['id']);state['in_progress']=None;state['status']='RESTORED_ARCHIVE_CHECKSUMS_VERIFIED_AWAITING_DB_SMOKE';atomic_json(statefile,state)
  return state

def comparable(rows):
 # Directory byte size is filesystem allocation detail, not portable content.
 return [[r[0],r[1],r[2] if stat.S_ISREG(r[1]) else 0,r[3],r[4]] for r in rows]

def hash_file(rootfd,root,row,budget):
 fd=regular_fd(rootfd,row[0]);h=hashlib.sha256()
 try:
  before=os.fstat(fd)
  if not stat.S_ISREG(before.st_mode) or [before.st_mode,before.st_size,before.st_mtime_ns,None]!=row[1:]:raise Halt('source_metadata_race')
  with os.fdopen(os.dup(fd),'rb') as f:
   while True:
    b=metered_read(f,budget,1)
    if not b:break
    h.update(b)
  after=os.fstat(fd);path_after=os.stat(Path(root)/row[0],follow_symlinks=False)
  fields=lambda s:(s.st_dev,s.st_ino,s.st_mode,s.st_size,s.st_mtime_ns,s.st_ctime_ns)
  if fields(before)!=fields(after) or fields(after)!=fields(path_after):raise Halt('content_hash_race')
 finally:os.close(fd)
 return h.hexdigest()

def verify_content(root,source,spec,quiescence,budget,guard):
 """OPTIONAL stronger diagnostic, NOT a mandatory project release gate.
 quiescence is evidence from authorized release admission, not self-generated approval.
 guard must actively recheck producers/queues/recorders while hashing; TTL cannot
 be used as a substitute for that live check.
 """
 validate_spec(spec)
 if Path(root).is_symlink() or Path(source).is_symlink() or Path(root).resolve()==Path(source).resolve():raise Halt('unsafe_compare_roots')
 if not(quiescence.get('admitted') is True and quiescence.get('plan_sha256')==spec['plan_sha256'] and quiescence.get('target_manifest_sha256')==digest(spec['target']) and quiescence.get('exclusions_sha256')==spec['exclusions_sha256'] and quiescence.get('source_identity')):raise Halt('quiescence_binding')
 def admitted():
  current=guard()
  if not isinstance(current,dict) or current.get('admitted') is not True or not isinstance(current.get('observed_at'),(int,float)) or not 0<=time.time()-current['observed_at']<=30 or any(current.get(k)!=quiescence.get(k) for k in ('source_identity','plan_sha256','target_manifest_sha256','exclusions_sha256')):raise Halt('live_quiescence_lost')
 class AdmissionBudget:
  def charge(self,*args,**kwargs):admitted();budget.charge(*args,**kwargs)
 checked_budget=AdmissionBudget()
 admitted();source_before=snapshot(source);restored=snapshot(root)
 source_persistent=persistent(source_before,spec['exclusions'])
 # Caller must mount a persistent-only snapshot or supply separately validated exclusions.
 if comparable(source_persistent)!=comparable(spec['target']):raise Halt('source_final_manifest_changed')
 if comparable(restored)!=comparable(spec['target']):raise Halt('restored_missing_extra_or_metadata')
 a=os.open(source,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW);b=os.open(root,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW);count=0;manifest=hashlib.sha256()
 try:
  for row in spec['target']:
   if not stat.S_ISREG(row[1]):continue
   admitted();ha=hash_file(a,source,row,checked_budget);hb=hash_file(b,root,row,checked_budget)
   if ha!=hb:raise Halt('restored_content_mismatch')
   manifest.update(canonical([row[0],ha])+b'\n');count+=1
 finally:os.close(a);os.close(b)
 admitted()
 if snapshot(source)!=source_before:raise Halt('source_changed_after_hashing')
 return {'status':'CONTENT_EQUIVALENT_FOR_BOUND_SNAPSHOT','files':count,'content_manifest_sha256':manifest.hexdigest(),'spec_sha256':spec['spec_sha256'],'source_identity':quiescence['source_identity']}
