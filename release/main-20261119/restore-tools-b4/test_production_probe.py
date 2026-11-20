import json,subprocess,unittest
from production_probe import validate_resources,production,probe_json,RESERVE
from resource_pause import ResourceDenied
class ResourceTests(unittest.TestCase):
 def setUp(self):self.c={'light_cpu_allowed':True,'sample':{'io_pressure_avg10':0.,'memory_pressure_avg10':0.}};self.raw=dict(io_some=0.,io_full=0.,memory_some=0.,memory_full=0.)
 def check(self,root=None,target=None):return validate_resources(self.c,self.raw,root if root is not None else RESERVE+1,target if target is not None else RESERVE+1)
 def test_idle_pass(self):self.assertIn('raw_psi_avg10',self.check())
 def test_some_pressure_denied_even_when_full_zero(self):
  self.raw['io_some']=1.1
  with self.assertRaisesRegex(ResourceDenied,'raw_io_pressure') as error:self.check()
  self.assertEqual(error.exception.sample['raw_psi_avg10']['io_some'],1.1)
 def test_memory_some_denied(self):
  self.raw['memory_some']=.01
  with self.assertRaisesRegex(ResourceDenied,'raw_memory_pressure'):self.check()
 def test_root_disk_denied(self):
  with self.assertRaisesRegex(ResourceDenied,'root_disk_reserve'):self.check(root=RESERVE-1)
 def test_target_disk_denied(self):
  with self.assertRaisesRegex(ResourceDenied,'target_disk_reserve'):self.check(target=RESERVE-1)
 def test_canonical_gate_still_required(self):
  self.c['light_cpu_allowed']=False
  with self.assertRaisesRegex(ResourceDenied,'canonical_light_cpu_denied'):self.check()
 def test_invalid_telemetry_denied(self):
  self.raw['io_some']=float('nan')
  with self.assertRaisesRegex(ResourceDenied,'psi_telemetry_invalid'):self.check()
class ProbeTests(unittest.TestCase):
 def runner(self,*values):
  values=iter(values)
  return lambda *a,**kw:subprocess.CompletedProcess([],0,json.dumps(next(values)).encode(),b'')
 def test_actual_probe_contract_idle(self):self.assertEqual(production(self.runner({'total':88,'blocked':0},{'meetings':0,'analysis':0,'reports':0}))['processing']['reports'],0)
 def test_recording_or_recovery_denied(self):
  with self.assertRaisesRegex(ResourceDenied,'agent_work_or_recovery_active'):production(self.runner({'total':88,'blocked':1}))
 def test_processing_denied(self):
  with self.assertRaisesRegex(ResourceDenied,'production_processing_active'):production(self.runner({'total':88,'blocked':0},{'meetings':0,'analysis':1,'reports':0}))
 def test_exit_diagnostics_do_not_include_private_stderr(self):
  run=lambda *a,**k:subprocess.CompletedProcess([],7,b'',b'PRIVATE_PAYLOAD')
  with self.assertRaisesRegex(ResourceDenied,'probe_exit') as error:probe_json([], 'probe',1,run)
  self.assertNotIn('PRIVATE_PAYLOAD',json.dumps(error.exception.sample));self.assertEqual(error.exception.sample['returncode'],7)
 def test_timeout_is_distinguished(self):
  def run(*a,**k):raise subprocess.TimeoutExpired('docker',1)
  with self.assertRaisesRegex(ResourceDenied,'probe_timeout'):probe_json([], 'probe',1,run)
if __name__=='__main__':unittest.main()

import tempfile,time,threading,os
from unittest.mock import patch
from pathlib import Path
from delta_writer import Guard,Stopped
class CooperativePauseTests(unittest.TestCase):
 def test_fresh_pause_stops_payload_then_resumes(self):
  with tempfile.TemporaryDirectory() as folder:
   path=Path(folder)/'lease.json';boot=Path('/proc/sys/kernel/random/boot_id').read_text().strip()
   def lease(allowed,reason):
    temp=path.with_suffix('.tmp');temp.write_text(json.dumps({'allowed':allowed,'reason':reason,'monotonic_time':time.monotonic(),'boot_id':boot}));os.replace(temp,path)
   lease(False,'resource_pause');done=threading.Event();errors=[]
   def target():
    try:Guard(folder,reserve=0).check()
    except Exception as e:errors.append(type(e).__name__)
    finally:done.set()
   with patch.dict(os.environ,{'BACKUP_LEASE_PATH':str(path)}):
    thread=threading.Thread(target=target);thread.start()
    try:
     self.assertFalse(done.wait(.2));lease(True,'resource_resumed');self.assertTrue(done.wait(2));self.assertEqual(errors,[])
    finally:lease(False,'controller_finished');thread.join(2)
 def test_pause_never_overrides_expired_lease(self):
  with tempfile.TemporaryDirectory() as folder,patch.dict(os.environ,{'BACKUP_LEASE_PATH':folder+'/lease.json'}):
   Path(folder,'lease.json').write_text(json.dumps({'allowed':False,'reason':'resource_pause','monotonic_time':time.monotonic()-31,'boot_id':Path('/proc/sys/kernel/random/boot_id').read_text().strip()}))
   with self.assertRaisesRegex(Stopped,'lease_expired'):Guard(folder,reserve=0).check()
 def test_pause_never_overrides_disk_reserve(self):
  with tempfile.TemporaryDirectory() as folder,patch.dict(os.environ,{'BACKUP_LEASE_PATH':folder+'/lease.json'}):
   Path(folder,'lease.json').write_text(json.dumps({'allowed':False,'reason':'resource_pause','monotonic_time':time.monotonic(),'boot_id':Path('/proc/sys/kernel/random/boot_id').read_text().strip()}))
   with patch('delta_writer.shutil.disk_usage') as usage:
    usage.return_value.free=1
    with self.assertRaisesRegex(Stopped,'low_space'):Guard(folder).check()
