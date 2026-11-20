import json,tempfile,unittest,time,subprocess
from pathlib import Path
from unittest.mock import patch
from delta_writer import Guard,Stopped
from owned_cleanup import stop_owned
class GuardTests(unittest.TestCase):
 def setUp(self):
  self.t=tempfile.TemporaryDirectory();self.p=Path(self.t.name);self.lease=self.p/'lease.json';self.boot=Path('/proc/sys/kernel/random/boot_id').read_text().strip();self.env=patch.dict('os.environ',{'BACKUP_LEASE_PATH':str(self.lease)});self.env.start()
 def tearDown(self):self.env.stop();self.t.cleanup()
 def check(self,changes=None,free=10**12):
  lease={'allowed':True,'monotonic_time':time.monotonic(),'boot_id':self.boot,'time':-10**12};lease.update(changes or {});self.lease.write_text(json.dumps(lease))
  with patch('delta_writer.shutil.disk_usage') as d:
   d.return_value.free=free;Guard(self.p).check()
 def test_wall_clock_not_ttl(self):self.check()
 def test_denied(self):
  with self.assertRaisesRegex(Stopped,'lease_denied'):self.check({'allowed':False})
 def test_expired(self):
  with self.assertRaisesRegex(Stopped,'lease_expired'):self.check({'monotonic_time':time.monotonic()-31})
 def test_future(self):
  with self.assertRaisesRegex(Stopped,'lease_expired'):self.check({'monotonic_time':time.monotonic()+50})
 def test_boot_changed(self):
  with self.assertRaisesRegex(Stopped,'lease_boot_mismatch'):self.check({'boot_id':'other'})
 def test_low_space(self):
  with self.assertRaisesRegex(Stopped,'low_space'):self.check(free=1)
 def test_missing_lease(self):
  with self.assertRaisesRegex(Stopped,'lease_unreadable'):Guard(self.p).check()
 def test_lowercase_missing_container_cleanup(self):
  result=subprocess.CompletedProcess([],1,'','Error response from daemon: no such object: x')
  self.assertEqual(stop_owned('x',self.p/'absent','token',None,run=lambda *a,**k:result),'absent')
 def test_unrelated_daemon_failure_not_ignored(self):
  result=subprocess.CompletedProcess([],1,'','daemon unavailable')
  with self.assertRaisesRegex(RuntimeError,'cleanup_inspect_failed'):stop_owned('x',self.p/'absent','token',None,run=lambda *a,**k:result)
if __name__=='__main__':unittest.main()

from owned_cleanup import set_paused_owned
from resource_pause import ResourceDenied,wait_for_admission
class PauseTests(unittest.TestCase):
 def test_lease_refreshed_before_resume_after_two_checks(self):
  events=[];calls=[0]
  def guard():
   calls[0]+=1
   if calls[0]==1:raise ResourceDenied('busy',{})
   return {'ok':True}
  result=wait_for_admission(guard,lambda:events.append('nas'),lambda:events.append('pause'),lambda:events.append('resume'),lambda ok,s:events.append('allow' if ok else 'deny'),100,clock=lambda:0,sleep=lambda _:None)
  self.assertEqual(calls[0],3);self.assertEqual(events[:2],['pause','deny']);self.assertEqual(events[-2:],['allow','resume']);self.assertTrue(result['ok'])
 def test_nas_change_never_resumes(self):
  events=[]
  def nas():raise RuntimeError('nas_changed')
  with self.assertRaisesRegex(RuntimeError,'nas_changed'):wait_for_admission(lambda:{},nas,lambda:events.append('pause'),lambda:events.append('resume'),lambda ok,s:events.append(ok),100,clock=lambda:0,sleep=lambda _:None)
  self.assertEqual(events,['pause',False])
 def test_deadline_never_resumes(self):
  events=[]
  with self.assertRaisesRegex(RuntimeError,'resource_pause_deadline'):wait_for_admission(lambda:{},lambda:None,lambda:events.append('pause'),lambda:events.append('resume'),lambda ok,s:events.append(ok),1,clock=lambda:2,sleep=lambda _:None)
  self.assertEqual(events,['pause',False])
 def test_pause_exact_owned_container(self):
  with tempfile.TemporaryDirectory() as d:
   path=Path(d)/'cid';path.write_text('123');commands=[]
   def run(args,**kwargs):
    commands.append(args)
    obj={'Id':'123','Name':'/own','Image':'image','Config':{'Labels':{'minutes.restore.owner':'token'}},'State':{'Running':True,'Paused':False}}
    return subprocess.CompletedProcess(args,0,json.dumps([obj]),'')
   set_paused_owned('own',path,'token','image',True,run=run)
   self.assertEqual(commands[-1],['docker','pause','123'])
 def test_wrong_owner_never_paused(self):
  with tempfile.TemporaryDirectory() as d:
   path=Path(d)/'cid';path.write_text('123');commands=[]
   def run(args,**kwargs):
    commands.append(args);obj={'Id':'123','Name':'/own','Image':'image','Config':{'Labels':{'minutes.restore.owner':'other'}},'State':{'Running':True,'Paused':False}}
    return subprocess.CompletedProcess(args,0,json.dumps([obj]),'')
   with self.assertRaisesRegex(RuntimeError,'pause_identity_mismatch'):set_paused_owned('own',path,'token','image',True,run=run)
   self.assertEqual(len(commands),1)
