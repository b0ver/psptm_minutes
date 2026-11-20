import hashlib,io,json,os,shutil,tarfile,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from next_core import Budget,Halt,copy_verified,encrypt_one,require_space
from safe_restore import restore_tar
class Fast:
 def charge(self,*a,**k):pass
class CoreTests(unittest.TestCase):
 def test_copy(self):
  out=io.BytesIO();b=b'synthetic';copy_verified(io.BytesIO(b),out,Fast(),hashlib.sha256(b).hexdigest(),len(b));self.assertEqual(out.getvalue(),b)
 def test_hash_mismatch(self):
  with self.assertRaises(Halt):copy_verified(io.BytesIO(b'x'),io.BytesIO(),Fast(),'0'*64,1)
 def test_size_mismatch(self):
  with self.assertRaises(Halt):copy_verified(io.BytesIO(b'xx'),io.BytesIO(),Fast(),'0'*64,1)
 def test_guard_denies_before_read(self):
  def denied():raise Halt('guard')
  with self.assertRaises(Halt):Budget(denied).charge(1)
 def test_low_space(self):
  with self.assertRaises(Halt):require_space('/tmp',10**30)
 def archive(self,name='dir/file',kind=None,link=None):
  b=io.BytesIO()
  with tarfile.open(fileobj=b,mode='w') as t:
   m=tarfile.TarInfo(name);m.mode=0o600
   if kind is not None:m.type=kind;m.linkname=link or ''
   else:m.size=9
   t.addfile(m,None if kind else io.BytesIO(b'synthetic'))
  b.seek(0);return b
 def test_restore_roundtrip(self):
  with tempfile.TemporaryDirectory() as root:
   r=restore_tar(self.archive(),root,Fast());self.assertEqual(Path(root,'dir/file').read_bytes(),b'synthetic');self.assertEqual(r[0][1],hashlib.sha256(b'synthetic').hexdigest())
 def test_restore_traversal(self):
  for p in ('../evil','/evil','dir/../evil'):
   with self.subTest(p=p),tempfile.TemporaryDirectory() as root,self.assertRaises(Halt):restore_tar(self.archive(p),root,Fast())
 def test_reject_link_device_fifo(self):
  for kind in (tarfile.SYMTYPE,tarfile.LNKTYPE,tarfile.CHRTYPE,tarfile.FIFOTYPE):
   with self.subTest(kind=kind),tempfile.TemporaryDirectory() as root,self.assertRaises(Halt):restore_tar(self.archive(kind=kind,link='/etc/passwd'),root,Fast())
 def test_symlink_parent(self):
  with tempfile.TemporaryDirectory() as root,tempfile.TemporaryDirectory() as outside:
   Path(root,'dir').symlink_to(outside)
   with self.assertRaises(OSError):restore_tar(self.archive(),root,Fast())
   self.assertEqual(list(Path(outside).iterdir()),[])
 def test_gpg_roundtrip_resume_and_corrupt(self):
  if not shutil.which('gpg'):self.skipTest('real GPG unavailable; not cryptographically validated')
  with tempfile.TemporaryDirectory() as temp:
   d=Path(temp);home=d/'home';home.mkdir(mode=0o700);password=d/'synthetic.pass';password.write_text('synthetic-only-not-user-secret');password.chmod(0o600)
   b=b'synthetic bytes only'*2048;expected={'archive_bytes':len(b),'archive_sha256':hashlib.sha256(b).hexdigest(),'plan_sha256':'synthetic'};target=d/'sample.gpg'
   with patch('next_core.require_space'):
    self.assertEqual(encrypt_one(io.BytesIO(b),target,expected,'gpg',home,password,Fast()),'created_verified')
    self.assertEqual(encrypt_one(io.BytesIO(b),target,expected,'gpg',home,password,Fast()),'verified_existing')
    target.write_bytes(b'corrupted')
    with self.assertRaises(Halt):encrypt_one(io.BytesIO(b),target,expected,'gpg',home,password,Fast())
 def test_partial_is_not_overwritten(self):
  with tempfile.TemporaryDirectory() as temp:
   target=Path(temp)/'x';partial=Path(str(target)+'.partial');partial.write_bytes(b'old')
   with patch('next_core.require_space'),self.assertRaises(Halt):encrypt_one(io.BytesIO(),target,{'archive_bytes':0},'gpg',temp,'unused',Fast())
   self.assertEqual(partial.read_bytes(),b'old')
if __name__=='__main__':unittest.main()

import sys,types
sys.path.insert(0,'/writer-code')
import export_stream
class ExportTests(unittest.TestCase):
 def setUp(self):
  self.t=tempfile.TemporaryDirectory();self.root=Path(self.t.name);self.data=b'synthetic'
  (self.root/'part-000000.tar').write_bytes(self.data)
  (self.root/'part-000000.json').write_text(json.dumps({'plan_sha256':'p','archive_bytes':len(self.data),'archive_sha256':hashlib.sha256(self.data).hexdigest()}))
  (self.root/'archive-set-complete.json').write_text(json.dumps({'plan_sha256':'p','packages':1}))
 def tearDown(self):self.t.cleanup()
 def test_metadata_only(self):
  out=io.BytesIO();export_stream.export(self.root,0,out,True);self.assertEqual(len(out.getvalue().splitlines()),1)
 def test_complete_stream(self):
  out=io.BytesIO()
  with patch.object(export_stream,'Guard',return_value=types.SimpleNamespace(pace=lambda n:None)):export_stream.export(self.root,0,out)
  self.assertEqual(out.getvalue().split(b'\n',1)[1],self.data)
 def test_incomplete_denied(self):
  (self.root/'archive-set-complete.json').unlink()
  with self.assertRaises(FileNotFoundError):export_stream.export(self.root,0,io.BytesIO())
 def test_plan_mismatch(self):
  (self.root/'archive-set-complete.json').write_text(json.dumps({'plan_sha256':'wrong','packages':1}))
  with self.assertRaises(ValueError):export_stream.export(self.root,0,io.BytesIO())
 def test_archive_changed_size(self):
  (self.root/'part-000000.tar').write_bytes(b'tampered size')
  with self.assertRaises(ValueError):export_stream.export(self.root,0,io.BytesIO())
 def test_archive_symlink(self):
  (self.root/'part-000000.tar').unlink();(self.root/'part-000000.tar').symlink_to('/etc/passwd')
  with self.assertRaises(ValueError):export_stream.export(self.root,0,io.BytesIO())

sys.modules['resource_gate']=types.SimpleNamespace(preflight=lambda:None)
import export_one,fcntl
class ExportGuardTests(unittest.TestCase):
 def test_current_packaging_lock_denied(self):
  with tempfile.TemporaryDirectory() as d,patch.object(export_one,'BASE',Path(d)):
   with open(Path(d)/'controller.lock','w') as f:
    fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)
    with self.assertRaises(BlockingIOError):export_one.main(0)
 def test_resource_denied_before_subprocess(self):
  with tempfile.TemporaryDirectory() as d,patch.object(export_one,'BASE',Path(d)),patch.object(export_one.c,'paused_evidence',return_value=[]),patch.object(export_one.c,'probe',return_value=({'allowed':False},52112,None)),patch.object(export_one.subprocess,'Popen') as launch:
   with self.assertRaises(RuntimeError):export_one.main(0)
   launch.assert_not_called()

class PrefixTests(unittest.TestCase):
 archive=CoreTests.archive
 def test_baseline_data_prefix(self):
  with tempfile.TemporaryDirectory() as root:
   restore_tar(self.archive('data/dir/file'),root,Fast(),'data');self.assertEqual(Path(root,'dir/file').read_bytes(),b'synthetic')
 def test_mixed_root_deny(self):
  with tempfile.TemporaryDirectory() as root,self.assertRaises(Halt):restore_tar(self.archive('other/file'),root,Fast(),'data')
 def test_double_prefix_deny(self):
  with tempfile.TemporaryDirectory() as root,self.assertRaises(Halt):restore_tar(self.archive('data/data/file'),root,Fast(),'data')
 def test_unsafe_prefixed_path(self):
  with tempfile.TemporaryDirectory() as root,self.assertRaises(Halt):restore_tar(self.archive('data/../file'),root,Fast(),'data')

class AccountingTests(unittest.TestCase):
 def test_exact_small_empty_partial_charges(self):
  class Count:
   def __init__(self):self.total=0
   def charge(self,n,io_passes=2):self.total+=n*io_passes
  count=Count();sizes=[0,1,17,65535,65536,65537,262145]
  for size in sizes:
   data=b'x'*size;copy_verified(io.BytesIO(data),io.BytesIO(),count,hashlib.sha256(data).hexdigest(),size)
  self.assertEqual(count.total,2*sum(sizes))
 def test_empty_hash_input_zero(self):
  from restore_controller import HashInput
  class Count:
   def __init__(self):self.total=0
   def charge(self,n,io_passes=2):self.total+=n*io_passes
  count=Count();reader=HashInput(io.BytesIO(),count,0);self.assertEqual(reader.read(),b'');self.assertEqual(count.total,0)
