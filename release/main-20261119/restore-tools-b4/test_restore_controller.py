import hashlib,io,json,os,stat,tarfile,tempfile,unittest,time
from pathlib import Path
from unittest.mock import patch
from next_core import Halt
from plan import digest
from restore_controller import make_spec,restore_one,verify_content,snapshot,hash_file
class Fast:
 def charge(self,*a,**k):pass
class RestoreTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();base=Path(self.temp.name);self.source=base/'source';self.root=base/'root';self.state=base/'state'
  for p in [self.source,self.root,self.state]:p.mkdir()
  (self.source/'file').write_bytes(b'original');os.utime(self.source/'file',ns=(123456789000,123456789000))
  self.target=snapshot(self.source);self.archive=self.tar({'file':b'original'})
  self.spec=self.spec_for(self.target,self.target,[self.archive])
 def tearDown(self):self.temp.cleanup()
 def tar(self,files):
  b=io.BytesIO()
  with tarfile.open(fileobj=b,mode='w') as t:
   for name,data in files.items():
    info=tarfile.TarInfo(name);info.mode=0o600;info.size=len(data);t.addfile(info,io.BytesIO(data))
  return b.getvalue()
 def spec_for(self,old,target,archives):
  return make_spec(old,target,[],[{'id':str(i),'kind':'baseline' if i==0 else 'delta','size':len(a),'sha256':hashlib.sha256(a).hexdigest(),'plan_sha256':'p'} for i,a in enumerate(archives)],'p')
 def restore(self):return restore_one(self.root,self.state,self.spec,0,io.BytesIO(self.archive),Fast())
 def q(self):return {'admitted':True,'plan_sha256':'p','target_manifest_sha256':digest(self.spec['target']),'exclusions_sha256':self.spec['exclusions_sha256'],'source_identity':'synthetic-container','observed_at':time.time()}
 def verify(self,guard=None):return verify_content(self.root,self.source,self.spec,self.q(),Fast(),guard or self.q)
 def test_roundtrip_metadata_content(self):
  self.restore();self.assertEqual(self.verify()['files'],1);self.assertEqual((self.root/'file').stat().st_mtime_ns,123456789000)
 def test_corrupt_archive_and_resume_denied(self):
  bad=bytearray(self.archive);bad[512]^=1
  with self.assertRaises(Halt):restore_one(self.root,self.state,self.spec,0,io.BytesIO(bad),Fast())
  with self.assertRaises(Halt):self.restore()
 def test_missing(self):
  self.restore();(self.root/'file').unlink()
  with self.assertRaises(Halt):self.verify()
 def test_extra(self):
  self.restore();(self.root/'extra').write_bytes(b'x')
  with self.assertRaises(Halt):self.verify()
 def test_content_corrupt_same_metadata(self):
  self.restore();(self.root/'file').write_bytes(b'corrupt!');os.utime(self.root/'file',ns=(123456789000,123456789000));os.chmod(self.root/'file',stat.S_IMODE(self.target[0][1]))
  with self.assertRaises(Halt):self.verify()
 def test_final_manifest_changed(self):
  self.restore();(self.source/'file').write_bytes(b'new')
  with self.assertRaises(Halt):self.verify()
 def test_live_quiescence_false(self):
  self.restore()
  with self.assertRaises(Halt):self.verify(lambda:False)
 def test_wrong_identity(self):
  self.restore();q=self.q();q['source_identity']='changed'
  with self.assertRaises(Halt):self.verify(lambda:q)
 def test_wrong_binding(self):
  self.restore();q=self.q();q['plan_sha256']='wrong'
  with self.assertRaises(Halt):verify_content(self.root,self.source,self.spec,q,Fast(),lambda:q)
 def test_symlink_root(self):
  link=self.root.parent/'link';link.symlink_to(self.root)
  with self.assertRaises(Halt):restore_one(link,self.state,self.spec,0,io.BytesIO(self.archive),Fast())
 def test_spec_mutation(self):
  self.spec['plan_sha256']='changed'
  with self.assertRaises(Halt):self.restore()
 def test_sequence(self):
  with self.assertRaises(Halt):restore_one(self.root,self.state,self.spec,1,io.BytesIO(self.archive),Fast())
 def test_tombstone_file_to_directory(self):
  old=[['gone',0o100600,1,1,None],['swap',0o100600,1,1,None]]
  (self.source/'file').unlink();(self.source/'swap').mkdir();(self.source/'swap'/'new').write_bytes(b'new')
  target=snapshot(self.source);a=self.tar({'gone':b'x','swap':b'x'});b=self.tar({'swap/new':b'new'});self.spec=self.spec_for(old,target,[a,b])
  restore_one(self.root,self.state,self.spec,0,io.BytesIO(a),Fast());restore_one(self.root,self.state,self.spec,1,io.BytesIO(b),Fast());self.assertEqual(self.verify()['files'],1);self.assertFalse((self.root/'gone').exists())
 def test_directory_to_file(self):
  old=[['swap',0o40700,0,1,None],['swap/old',0o100600,1,1,None]]
  (self.source/'file').unlink();(self.source/'swap').write_bytes(b'new');target=snapshot(self.source);a=self.tar({'swap/old':b'x'});b=self.tar({'swap':b'new'});self.spec=self.spec_for(old,target,[a,b])
  restore_one(self.root,self.state,self.spec,0,io.BytesIO(a),Fast());restore_one(self.root,self.state,self.spec,1,io.BytesIO(b),Fast());self.assertEqual(self.verify()['files'],1)
 def test_hash_race(self):
  fd=os.open(self.source,os.O_RDONLY|os.O_DIRECTORY);row=self.target[0];path=self.source/'file'
  class Mutate:
   def charge(self,*a,**k):path.write_bytes(b'mutating')
  try:
   with self.assertRaises(Halt):hash_file(fd,self.source,row,Mutate())
  finally:os.close(fd)

 def test_same_root_refused(self):
  self.restore()
  with self.assertRaises(Halt):verify_content(self.source,self.source,self.spec,self.q(),Fast(),self.q)
 def test_quiescence_lost_midstream(self):
  self.restore();calls=[0]
  def guard():
   calls[0]+=1;return self.q() if calls[0]==1 else {'admitted':False}
  with self.assertRaises(Halt):self.verify(guard)
 def test_exact_runtime_exclusion(self):
  from runtime_scope import persistent
  p=self.source/'telemost'/'session'/'pulse';p.mkdir(parents=True);(p/'id-runtime').symlink_to('/tmp/pulse-synthetic')
  rows=snapshot(self.source);excluded=[r for r in rows if r[4] is not None];target=persistent(rows,excluded)
  a=self.tar({'file':b'original'});self.spec=make_spec(target,target,excluded,[{'id':'0','kind':'baseline','size':len(a),'sha256':hashlib.sha256(a).hexdigest(),'plan_sha256':'p'}],'p')
  restore_one(self.root,self.state,self.spec,0,io.BytesIO(a),Fast());self.assertEqual(self.verify()['files'],1)
 def test_changed_exclusion_denied(self):
  from runtime_scope import persistent
  p=self.source/'telemost'/'session'/'pulse';p.mkdir(parents=True);link=p/'id-runtime';link.symlink_to('/tmp/pulse-synthetic')
  rows=snapshot(self.source);excluded=[r for r in rows if r[4] is not None];target=persistent(rows,excluded)
  a=self.tar({'file':b'original'});self.spec=make_spec(target,target,excluded,[{'id':'0','kind':'baseline','size':len(a),'sha256':hashlib.sha256(a).hexdigest(),'plan_sha256':'p'}],'p')
  restore_one(self.root,self.state,self.spec,0,io.BytesIO(a),Fast());link.unlink();link.write_bytes(b'not a runtime link')
  with self.assertRaises(ValueError):self.verify()

 def test_stale_quiescence(self):
  self.restore();q=self.q();q['observed_at']=time.time()-120
  with self.assertRaises(Halt):self.verify(lambda:q)
 def test_future_quiescence(self):
  self.restore();q=self.q();q['observed_at']=time.time()+120
  with self.assertRaises(Halt):self.verify(lambda:q)
