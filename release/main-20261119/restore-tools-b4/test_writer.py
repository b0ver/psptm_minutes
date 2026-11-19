import hashlib,json,os,tarfile,tempfile,time,unittest
from pathlib import Path
from unittest.mock import patch
from plan import plan
from delta_writer import Guard,Stopped,package,source_fd
class Tests(unittest.TestCase):
 def setUp(self):
  self.t=tempfile.TemporaryDirectory(); self.root=Path(self.t.name)/'root';self.out=Path(self.t.name)/'out';self.root.mkdir();self.out.mkdir()
  (self.root/'file').write_bytes(b'synthetic payload')
  s=(self.root/'file').stat(); self.row=['file',s.st_mode,s.st_size,s.st_mtime_ns,None]
  self.p=plan([],[self.row]); self.guard=Guard(self.out,reserve=0)
  self.lease={'allowed':True,'monotonic_time':time.monotonic(),'boot_id':Path('/proc/sys/kernel/random/boot_id').read_text().strip()}
  self.write_lease()
  self.env=patch.dict(os.environ,{'BACKUP_LEASE_PATH':str(self.out/'lease.json')});self.env.start();self.addCleanup(self.env.stop)
 def write_lease(self): (self.out/'lease.json').write_text(json.dumps(self.lease))
 def tearDown(self): self.t.cleanup()
 def test_roundtrip_resume(self):
  args=(self.root,self.out,self.p['packages'][0],self.p['plan_sha256'],self.guard)
  self.assertEqual(package(*args),'created');self.assertEqual(package(*args),'verified_existing')
  with tarfile.open(self.out/'part-000000.tar') as t: self.assertEqual(t.extractfile('file').read(),b'synthetic payload')
  r=json.loads((self.out/'part-000000.json').read_text());self.assertEqual(r['file_sha256'][0][1],hashlib.sha256(b'synthetic payload').hexdigest())
 def test_corrupt_archive(self):
  args=(self.root,self.out,self.p['packages'][0],self.p['plan_sha256'],self.guard);package(*args)
  (self.out/'part-000000.tar').write_bytes(b'bad')
  with self.assertRaises(Stopped):package(*args)
 def test_expired_lease(self):
  self.lease['monotonic_time']=time.monotonic()-31;self.write_lease()
  with self.assertRaisesRegex(Stopped,'lease_expired'): self.guard.check()
 def test_denied(self):
  self.lease['allowed']=False;self.write_lease()
  with self.assertRaisesRegex(Stopped,'lease_denied'): self.guard.check()
 def test_changed_source(self):
  (self.root/'file').write_bytes(b'changed')
  with self.assertRaises(Stopped):package(self.root,self.out,self.p['packages'][0],self.p['plan_sha256'],self.guard)
 def test_no_follow(self):
  (self.root/'link').symlink_to('/etc/passwd'); fd=os.open(self.root,os.O_RDONLY)
  try:
   with self.assertRaises(OSError):source_fd(fd,'link')
  finally:os.close(fd)
 def test_fifo_substitution(self):
  (self.root/'file').unlink();os.mkfifo(self.root/'file')
  with self.assertRaises(Stopped):package(self.root,self.out,self.p['packages'][0],self.p['plan_sha256'],self.guard)
 def test_low_space(self):
  with self.assertRaises(Stopped):Guard(self.out,reserve=10**30).check()
 def test_partial_not_overwritten(self):
  f=self.out/'part-000000.partial';f.write_bytes(b'partial')
  with self.assertRaises(FileExistsError):package(self.root,self.out,self.p['packages'][0],self.p['plan_sha256'],self.guard)
  self.assertEqual(f.read_bytes(),b'partial')
if __name__=='__main__':unittest.main()

from runtime_scope import persistent
class RuntimeScopeTests(unittest.TestCase):
 def test_exact_link(self):
  r=['telemost/session/browser-profile/SingletonCookie',0o120777,3,1,'123']
  self.assertEqual(persistent([r],[r]),[])
 def test_regular_same_name(self):
  r=['telemost/session/browser-profile/SingletonCookie',0o120777,3,1,'123']; n=r[:];n[1]=0o100600;n[4]=None
  with self.assertRaises(ValueError):persistent([n],[r])
 def test_unapproved_link_preserved_for_rejection(self):
  r=['telemost/session/other',0o120777,3,1,'/etc']
  self.assertEqual(persistent([r],[]),[r])
 def test_wrong_target(self):
  r=['telemost/session/pulse/id-runtime',0o120777,3,1,'/etc/passwd']
  with self.assertRaises(ValueError):persistent([r],[r])
