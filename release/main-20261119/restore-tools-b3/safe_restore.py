"""Safe streamed tar reconstruction into an isolated disposable restore root.
No tar extraction APIs are used. No symlinks/hardlinks/devices/FIFOs permitted.
"""
import hashlib,os,stat,tarfile,uuid
from pathlib import PurePosixPath
from next_core import Halt

def member_path(member):
 name=member.name
 while name.startswith('./'):name=name[2:]
 if name in ('','.'): 
  if member.isdir():return None
  raise Halt('invalid_root_member')
 if name.startswith('/') or '\x00' in name or any(p in ('','.','..') for p in name.rstrip('/').split('/')):raise Halt('unsafe_member_path')
 if not(member.isdir() or member.isfile()) or member.issym() or member.islnk():raise Halt('unsafe_member_type')
 if member.mode & 0o7000:raise Halt('special_permission_bits')
 return name.rstrip('/')

def parent_fd(root,name):
 fd=os.dup(root)
 try:
  for part in name.split('/')[:-1]:
   try:os.mkdir(part,0o700,dir_fd=fd)
   except FileExistsError:pass
   new=os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=fd);os.close(fd);fd=new
  return fd
 except BaseException:os.close(fd);raise

def restore_tar(stream,root,budget,strip_prefix=None):
 if strip_prefix not in (None,"data"):raise Halt("unapproved_archive_prefix")
 rootfd=os.open(root,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW);seen=set();hashes=[]
 try:
  with tarfile.open(fileobj=stream,mode='r|') as archive:
   for member in archive:
    name=member_path(member)
    if name is None:continue
    if strip_prefix:
     if name==strip_prefix and member.isdir():continue
     if not name.startswith(strip_prefix+"/"):raise Halt("mixed_archive_root")
     name=name[len(strip_prefix)+1:]
     if name==strip_prefix or name.startswith(strip_prefix+"/"):raise Halt("multiple_archive_prefix")
    if name in seen:raise Halt('duplicate_tar_member')
    seen.add(name);fd=parent_fd(rootfd,name);leaf=name.split('/')[-1]
    try:
     if member.isdir():
      try:os.mkdir(leaf,0o700,dir_fd=fd)
      except FileExistsError:
       test=os.open(leaf,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=fd);os.close(test)
      continue
     tmp=leaf+'.restore-'+uuid.uuid4().hex
     outfd=os.open(tmp,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=fd)
     h=hashlib.sha256();size=0
     with os.fdopen(outfd,'wb') as out,archive.extractfile(member) as data:
      while True:
       budget.charge(0,1);b=data.read(64*1024)
       if not b:break
       budget.charge(len(b),1);h.update(b);size+=len(b);out.write(b)
      if size!=member.size:raise Halt('truncated_tar_member')
      out.flush();os.fsync(out.fileno());os.fchmod(out.fileno(),member.mode&0o777)
     os.replace(tmp,leaf,src_dir_fd=fd,dst_dir_fd=fd)
     hashes.append([name,h.hexdigest(),size])
    finally:os.close(fd)
 finally:os.close(rootfd)
 return hashes
