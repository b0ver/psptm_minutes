import hashlib,json,os,shutil,stat,tempfile,unittest
from pathlib import Path
from catchup_core import apply_current,snapshot_strict,fingerprint
from plan import plan
from delta_writer import package
from next_core import Halt
class Budget:
 def charge(self,*a):pass
class Guard:
 def check(self):pass
 def pace(self,*a):pass
class Tests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.base=Path(self.temp.name);self.source=self.base/'source';self.target=self.base/'target';self.out=self.base/'out'
  self.source.mkdir();self.target.mkdir();self.out.mkdir();(self.source/'keep').write_bytes(b'preserved');(self.source/'change').write_bytes(b'old');(self.source/'delete').write_bytes(b'obsolete')
  self.old=snapshot_strict(self.source)
  for p in self.source.iterdir():shutil.copy2(p,self.target/p.name)
  self.proof={'spec_sha256':'a'*64,'files':[[p.name,hashlib.sha256(p.read_bytes()).hexdigest(),fingerprint(p.stat())] for p in self.target.iterdir()]}
 def tearDown(self):self.temp.cleanup()
 def changes(self):
  (self.source/'change').write_bytes(b'new verified contents');(self.source/'delete').unlink();(self.source/'nested').mkdir();(self.source/'nested/new').write_bytes(b'new file')
 def packages(self):
  new=snapshot_strict(self.source);p=plan(self.old,new);packs=[]
  for selection in p['packages']:
   package(self.source,self.out,selection,p['plan_sha256'],Guard());stem=self.out/f"part-{selection['index']:06d}";packs.append((Path(str(stem)+'.tar'),json.loads(Path(str(stem)+'.json').read_text())))
  return new,p,packs
 def apply(self,new,p,packs):return apply_current(self.target,self.out,self.old,new,self.proof,p,packs,Budget(),lambda:None)
 def test_real_add_replace_delete_and_hash_lineage(self):
  self.changes();new,p,packs=self.packages();proof=self.apply(new,p,packs)
  self.assertEqual((self.target/'keep').read_bytes(),b'preserved');self.assertEqual((self.target/'change').read_bytes(),b'new verified contents');self.assertEqual((self.target/'nested/new').read_bytes(),b'new file');self.assertFalse((self.target/'delete').exists())
  self.assertEqual({r[0]:r[1] for r in proof['files']},{str(p.relative_to(self.source)):hashlib.sha256(p.read_bytes()).hexdigest() for p in self.source.rglob('*') if p.is_file()});self.assertTrue((self.out/'apply-complete.json').exists())
 def test_unrelated_parent_mutation_denied_before_write(self):
  self.changes();new,p,packs=self.packages();(self.target/'keep').write_bytes(b'tampered')
  with self.assertRaises(Halt):self.apply(new,p,packs)
  self.assertFalse((self.out/'apply-started.json').exists())
 def test_same_size_restored_mtime_cannot_bypass_fingerprint(self):
  self.changes();new,p,packs=self.packages();old=(self.target/'keep').stat();(self.target/'keep').write_bytes(b'123456789');os.utime(self.target/'keep',ns=(old.st_atime_ns,old.st_mtime_ns))
  with self.assertRaisesRegex(Halt,'parent_fingerprint_mismatch'):self.apply(new,p,packs)
 def test_archive_corruption_denied_before_mutation(self):
  self.changes();new,p,packs=self.packages()
  with packs[0][0].open('ab') as f:f.write(b'corruption')
  with self.assertRaisesRegex(Halt,'delta_archive_integrity'):self.apply(new,p,packs)
  self.assertEqual((self.target/'change').read_bytes(),b'old');self.assertFalse((self.out/'apply-started.json').exists())
 def test_changed_plan_denied(self):
  self.changes();new,p,packs=self.packages();p['logical_delta_bytes']+=1
  with self.assertRaisesRegex(Halt,'current_plan_digest'):self.apply(new,p,packs)
 def test_wrong_receipt_denied(self):
  self.changes();new,p,packs=self.packages();packs[0][1]['selection_sha256']='0'*64
  with self.assertRaisesRegex(Halt,'delta_receipt_binding'):self.apply(new,p,packs)
 def test_metadata_only_directory_update(self):
  (self.source/'empty').mkdir();new,p,packs=self.packages();proof=self.apply(new,p,packs)
  self.assertTrue((self.target/'empty').is_dir());self.assertEqual(len(proof['files']),3);self.assertEqual(proof['archives'],[])
 def test_existing_apply_marker_cannot_silently_resume(self):
  self.changes();new,p,packs=self.packages();(self.out/'apply-started.json').write_text('{}')
  with self.assertRaisesRegex(Halt,'partial_current_apply_requires_review'):self.apply(new,p,packs)
 def test_source_symlink_not_silently_packaged(self):
  (self.source/'unapproved').symlink_to('/etc/passwd')
  with self.assertRaisesRegex(ValueError,'unsupported file type'):self.packages()
 def test_duplicate_parent_proof_denied(self):
  self.changes();new,p,packs=self.packages();self.proof['files'].append(self.proof['files'][0])
  with self.assertRaisesRegex(Halt,'invalid_parent_proof'):self.apply(new,p,packs)
 def test_undeclared_member_denied_before_write(self):
  import tarfile,io
  self.changes();new,p,packs=self.packages();path,receipt=packs[0]
  with tarfile.open(path,'w') as archive:
   member=tarfile.TarInfo('undeclared');member.size=4;archive.addfile(member,io.BytesIO(b'evil'))
  receipt['archive_bytes']=path.stat().st_size;receipt['archive_sha256']=hashlib.sha256(path.read_bytes()).hexdigest()
  with self.assertRaisesRegex(Halt,'delta_members_mismatch'):self.apply(new,p,packs)
  self.assertFalse((self.out/'apply-started.json').exists());self.assertFalse((self.target/'undeclared').exists())
 def test_both_disk_admissions_checked(self):
  from catchup_core import BothDisks
  class Checked:
   def __init__(self):self.calls=0
   def check(self):self.calls+=1
   def pace(self,n):self.check()
  out=Checked();target=Checked();g=BothDisks(out,target);g.check();g.pace(1)
  self.assertEqual((out.calls,target.calls),(2,2))
 def test_second_delta_preserves_parent_hash_lineage(self):
  self.changes();new,p,packs=self.packages();first=self.apply(new,p,packs)
  self.old=new;self.proof=first;second_out=self.base/'second';second_out.mkdir();self.out=second_out
  (self.source/'change').write_bytes(b'second coherent delta');new,p,packs=self.packages();second=self.apply(new,p,packs)
  self.assertEqual(second['baseline_spec_sha256'],'a'*64);self.assertEqual((self.target/'change').read_bytes(),b'second coherent delta');self.assertEqual(len(second['files']),3)
if __name__=='__main__':unittest.main()
