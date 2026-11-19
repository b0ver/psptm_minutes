"""Verified incremental catch-up of an OWNED isolated restore; never writes source."""
import hashlib,json,os,re,stat,tarfile
from pathlib import Path
from plan import entries,digest
from restore_controller import comparable,remove_obsolete,regular_fd
from safe_restore import restore_tar,member_path,parent_fd
from next_core import Halt,metered_read,atomic_json


def snapshot_strict(root,guard=lambda:None):
 rows=[]
 def fail(error):raise Halt('snapshot_unreadable') from None
 for folder,dirs,files in os.walk(root,followlinks=False,onerror=fail):
  guard()
  for name in dirs+files:
   p=Path(folder)/name;s=p.lstat();relative=os.path.relpath(p,root)
   rows.append([relative,s.st_mode,s.st_size,s.st_mtime_ns,os.readlink(p) if stat.S_ISLNK(s.st_mode) else None])
 return sorted(rows)

def fingerprint(s):return [s.st_dev,s.st_ino,s.st_mode,s.st_size,s.st_mtime_ns,s.st_ctime_ns]

def validate_parent(root,rows,proof,guard):
 expected=entries(rows);stored={r[0]:(r[1],r[2]) for r in proof['files']}
 regular={p for p,r in expected.items() if stat.S_ISREG(r[1])}
 if len(stored)!=len(proof['files']) or any(not isinstance(h,str) or not re.fullmatch(r'[a-f0-9]{64}',h) for h,_ in stored.values()):raise Halt('invalid_parent_proof')
 if set(stored)!=regular or comparable(snapshot_strict(root,guard))!=comparable(rows):raise Halt('parent_manifest_mismatch')
 rootfd=os.open(root,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
 try:
  for name in sorted(regular):
   guard();fd=regular_fd(rootfd,name)
   try:
    if fingerprint(os.fstat(fd))!=stored[name][1]:raise Halt('parent_fingerprint_mismatch')
   finally:os.close(fd)
 finally:os.close(rootfd)
 return {p:stored[p][0] for p in regular}

def verify_package(path,package,receipt,plan_sha,budget):
 if receipt['plan_sha256']!=plan_sha or receipt['selection_sha256']!=digest(package['rows']):raise Halt('delta_receipt_binding')
 before=os.stat(path,follow_symlinks=False)
 if not stat.S_ISREG(before.st_mode):raise Halt('delta_not_regular')
 h=hashlib.sha256();size=0
 with open(path,'rb') as source:
  while block:=metered_read(source,budget,1):size+=len(block);h.update(block)
 if size!=receipt['archive_bytes'] or h.hexdigest()!=receipt['archive_sha256']:raise Halt('delta_archive_integrity')
 selected={r[0]:r for r in package['rows']};seen=set()
 with tarfile.open(path,'r:') as archive:
  for item in archive:
   budget.charge(0,1);name=member_path(item)
   if not item.isfile() or name not in selected or name in seen or item.size!=selected[name][2] or item.mode&0o777!=stat.S_IMODE(selected[name][1]):raise Halt('delta_members_mismatch')
   seen.add(name)
 if seen!=set(selected):raise Halt('delta_members_missing')
 if fingerprint(os.stat(path,follow_symlinks=False))!=fingerprint(before):raise Halt('delta_archive_changed')
 return fingerprint(before)

class HashReader:
 def __init__(self,source,budget):self.source=source;self.budget=budget;self.hash=hashlib.sha256();self.size=0
 def read(self,n=-1):
  data=metered_read(self.source,self.budget,1,64*1024 if n<0 else min(n,64*1024));self.size+=len(data);self.hash.update(data);return data

def apply_metadata(root,rows,budget):
 rootfd=os.open(root,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
 try:
  for name,row in sorted(entries(rows).items(),key=lambda x:(-x[0].count('/'),x[0])):
   budget.charge(0,1)
   if stat.S_ISDIR(row[1]):
    parent=parent_fd(rootfd,name)
    try:
     try:os.mkdir(name.split('/')[-1],0o700,dir_fd=parent)
     except FileExistsError:pass
     fd=os.open(name.split('/')[-1],os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=parent)
    finally:os.close(parent)
   else:fd=regular_fd(rootfd,name)
   try:
    s=os.fstat(fd)
    if stat.S_IFMT(s.st_mode)!=stat.S_IFMT(row[1]) or (stat.S_ISREG(s.st_mode) and s.st_size!=row[2]):raise Halt('current_metadata_mismatch')
    if stat.S_IMODE(s.st_mode)!=stat.S_IMODE(row[1]):os.fchmod(fd,stat.S_IMODE(row[1]))
    if s.st_mtime_ns!=row[3]:os.utime(fd,ns=(row[3],row[3]))
   finally:os.close(fd)
 finally:os.close(rootfd)

def apply_current(root,out,prior_rows,new_rows,parent_proof,plan,packages,budget,guard):
 root=Path(root);out=Path(out)
 if root.is_symlink() or out.is_symlink() or root.resolve()==Path('/') or root.resolve()==out.resolve():raise Halt('unsafe_current_roots')
 if (out/'apply-started.json').exists():raise Halt('partial_current_apply_requires_review')
 if plan['baseline_manifest_sha256']!=digest(prior_rows) or plan['target_manifest_sha256']!=digest(new_rows):raise Halt('current_plan_binding')
 claimed=dict(plan);sha=claimed.pop('plan_sha256')
 if digest(claimed)!=sha:raise Halt('current_plan_digest')
 if len(packages)!=len(plan['packages']):raise Halt('current_package_count')
 hashes=validate_parent(root,prior_rows,parent_proof,guard);verified=[]
 for package,(path,receipt) in zip(plan['packages'],packages):verified.append(verify_package(path,package,receipt,sha,budget))
 atomic_json(out/'apply-started.json',{'plan_sha256':sha,'parent_proof_canonical_sha256':digest(parent_proof)})
 guard();remove_obsolete(root,{'baseline':prior_rows,'target':new_rows})
 for index,(package,(path,receipt)) in enumerate(zip(plan['packages'],packages)):
  if fingerprint(os.stat(path,follow_symlinks=False))!=verified[index]:raise Halt('verified_archive_changed')
  with open(path,'rb') as source:
   reader=HashReader(source,budget);restored=restore_tar(reader,root,budget)
   while reader.read(64*1024):pass
  if reader.size!=receipt['archive_bytes'] or reader.hash.hexdigest()!=receipt['archive_sha256']:raise Halt('applied_archive_integrity')
  result={r[0]:r[1] for r in restored}
  if result!=dict(receipt['file_sha256']):raise Halt('restored_delta_hashes_mismatch')
  hashes.update(result)
 apply_metadata(root,new_rows,budget)
 if comparable(snapshot_strict(root,guard))!=comparable(new_rows):raise Halt('current_target_mismatch')
 bound=[]
 for row in new_rows:
  guard()
  if stat.S_ISREG(row[1]):
   if row[0] not in hashes:raise Halt('current_missing_hash')
   bound.append([row[0],hashes[row[0]],fingerprint((root/row[0]).lstat())])
 proof={'method':'verified_parent_extraction_hashes_and_verified_delta_not_full_source_reread','baseline_spec_sha256':parent_proof.get('baseline_spec_sha256',parent_proof.get('spec_sha256')),'parent_proof_canonical_sha256':digest(parent_proof),'target_manifest_sha256':digest(new_rows),'delta_plan_sha256':sha,'archives':[{'sha256':r['archive_sha256'],'bytes':r['archive_bytes']} for _,r in packages],'files':bound}
 atomic_json(out/'current-manifest.json',new_rows);atomic_json(out/'current-file-proof.json',proof);atomic_json(out/'apply-complete.json',{'plan_sha256':sha,'proof_canonical_sha256':digest(proof),'file_count':len(bound)})
 return proof

class BothDisks:
 def __init__(self,out_guard,target_guard):self.out=out_guard;self.target=target_guard
 def check(self):self.out.check();self.target.check()
 def pace(self,n):self.out.pace(n);self.target.check()
