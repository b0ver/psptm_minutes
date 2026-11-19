"""Serial off-host encryption helpers. Never opens a passphrase itself.
Caller supplies a fail-closed guard; no production state changes.
"""
import hashlib,json,os,shutil,stat,subprocess,time
from pathlib import Path

class Halt(RuntimeError):pass

class Budget:
 def __init__(self,guard,rate=8*1024**2):self.guard=guard;self.rate=rate;self.last=0
 def charge(self,n,io_passes=2):
  self.guard();until=max(time.monotonic(),self.last)+n*io_passes/self.rate
  while time.monotonic()<until:
   time.sleep(min(.1,max(0,until-time.monotonic())));self.guard()
  self.last=until

def metered_read(source,budget,io_passes=1,maximum=64*1024):
 budget.charge(0,io_passes)
 b=source.read(maximum)
 if b:budget.charge(len(b),io_passes)
 return b

def copy_verified(source,dest,budget,expected_hash,expected_size):
 h=hashlib.sha256();size=0
 while True:
  b=metered_read(source,budget,2)
  if not b:break
  size+=len(b)
  if size>expected_size:raise Halt('source_too_long')
  h.update(b);dest.write(b)
 if size!=expected_size or h.hexdigest()!=expected_hash:raise Halt('source_integrity_mismatch')
 return size

def require_space(destination,projected_bytes,reserve=15*1024**3):
 if shutil.disk_usage(destination).free-projected_bytes<reserve:raise Halt('offhost_space_reserve')

def atomic_json(path,value):
 p=Path(str(path)+'.tmp')
 fd=os.open(p,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
 with os.fdopen(fd,'w') as f:json.dump(value,f,sort_keys=True);f.flush();os.fsync(f.fileno())
 os.replace(p,path)

def gpg_args(gpg,home,passfile):
 # Pass only the local filename to GPG. Never read/print/copy the passphrase.
 return [gpg,'--homedir',str(home),'--batch','--quiet','--pinentry-mode','loopback','--passphrase-file',str(passfile)]

def decrypt_verify(cipher,gpg,home,passfile,budget,expected_hash,expected_size):
 # One sequential verification subprocess. Plaintext goes only to a hash sink.
 # Ciphertext SHA needs a separate paced read; both passes stay serial.
 before=os.stat(cipher,follow_symlinks=False)
 if not stat.S_ISREG(before.st_mode):raise Halt("ciphertext_not_regular")
 cipher_hash=hashlib.sha256();cipher_size=0
 with open(cipher,'rb') as f:
  while True:
   b=metered_read(f,budget,1)
   if not b:break
   cipher_hash.update(b);cipher_size+=len(b)
 p=subprocess.Popen(gpg_args(gpg,home,passfile)+['--decrypt',str(cipher)],stdout=subprocess.PIPE,stderr=subprocess.DEVNULL)
 h=hashlib.sha256();size=0
 try:
  while True:
   b=metered_read(p.stdout,budget,1)
   if not b:break
   size+=len(b)
   if size>expected_size:raise Halt('decryption_too_long')
   h.update(b)
  budget.charge(max(0,cipher_size-size),1)
  if p.wait(timeout=30)!=0 or size!=expected_size or h.hexdigest()!=expected_hash:raise Halt('decryption_verification_failed')
 finally:
  if p.poll() is None:p.kill();p.wait()
  p.stdout.close()
 after=os.stat(cipher,follow_symlinks=False)
 if (before.st_dev,before.st_ino,before.st_size,before.st_mtime_ns,before.st_ctime_ns)!=(after.st_dev,after.st_ino,after.st_size,after.st_mtime_ns,after.st_ctime_ns):raise Halt('ciphertext_changed_during_verification')
 return {'ciphertext_sha256':cipher_hash.hexdigest(),'ciphertext_bytes':cipher_size,'plaintext_sha256':h.hexdigest(),'plaintext_bytes':size,'decryption_hash_verified':True}

def encrypt_one(source,destination,expected,gpg,home,passfile,budget,source_done=lambda:None):
 """source is a PIPE from a separately guarded exporter, never a secret file.
 Must only run after packaging ended and remote control lock is held by exporter.
 """
 destination=Path(destination);receipt=Path(str(destination)+'.json');partial=Path(str(destination)+'.partial')
 require_space(destination.parent,expected['archive_bytes']+1024**2)
 if receipt.exists():
  old=json.loads(receipt.read_text())
  if old.get('source')!=expected:raise Halt('receipt_source_binding_mismatch')
  source_done()
  fresh=decrypt_verify(destination,gpg,home,passfile,budget,expected['archive_sha256'],expected['archive_bytes'])
  if fresh!=old['verification']:raise Halt('ciphertext_changed')
  return 'verified_existing'
 if destination.exists() or partial.exists():raise Halt('unverified_existing_output_requires_quarantine')
 fd=os.open(partial,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
 with os.fdopen(fd,'wb') as out:
  p=subprocess.Popen(gpg_args(gpg,home,passfile)+['--symmetric','--cipher-algo','AES256','--compress-algo','none','--output','-'],stdin=subprocess.PIPE,stdout=out,stderr=subprocess.DEVNULL)
  try:
   copy_verified(source,p.stdin,budget,expected['archive_sha256'],expected['archive_bytes']);source_done();p.stdin.close()
   if p.wait(timeout=30)!=0:raise Halt('encryption_failed')
   out.flush();os.fsync(out.fileno())
  finally:
   if p.poll() is None:p.kill();p.wait()
   if not p.stdin.closed:p.stdin.close()
 budget.charge(max(0,partial.stat().st_size-expected['archive_bytes']),1)
 verification=decrypt_verify(partial,gpg,home,passfile,budget,expected['archive_sha256'],expected['archive_bytes'])
 os.rename(partial,destination)
 atomic_json(receipt,{'source':expected,'verification':verification})
 return 'created_verified'
