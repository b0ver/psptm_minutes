import importlib.util,json,sys,types,unittest
from unittest.mock import patch
sys.modules['resource_gate']=types.SimpleNamespace(preflight=lambda:None)
import controller as c
class Tests(unittest.TestCase):
 def invoke(self,io=0,light=True,mem=0,bad=0,advance=True,age=0):
  g={'light_cpu_allowed':light,'sample':{'load_1m':1,'io_pressure_avg10':io,'memory_pressure_avg10':mem}}
  with patch.object(c,'preflight',return_value=g),patch.object(c,'nas_latest',return_value=11 if advance else 10),patch.object(c.subprocess,'check_output',return_value=json.dumps({'unhealthy_or_unknown':bad,'active':0}).encode()),patch.object(c.time,'monotonic',return_value=1000):
   return c.probe(10,1000-age)[0]
 def test_pass(self):self.assertTrue(self.invoke()['allowed'])
 def test_io(self):self.assertFalse(self.invoke(io=1.01)['allowed'])
 def test_cpu(self):self.assertFalse(self.invoke(light=False)['allowed'])
 def test_mem(self):self.assertFalse(self.invoke(mem=.01)['allowed'])
 def test_recorder(self):self.assertFalse(self.invoke(bad=1)['allowed'])
 def test_stale_nas(self):self.assertFalse(self.invoke(advance=False,age=181)['allowed'])
 def test_recent_no_advance(self):self.assertTrue(self.invoke(advance=False,age=30)['allowed'])
 def test_probe_exception_no_grant(self):
  with patch.object(c,'preflight',side_effect=RuntimeError):
   with self.assertRaises(RuntimeError):c.probe(10,0)

import tempfile,fcntl,os
from pathlib import Path
class PausedTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.paths=[Path(self.tmp.name)/str(i) for i in range(3)]
  for p in self.paths:p.write_bytes(b'')
  self.rows=[(52112,'verify','committed'),(52111,'verify','committed')]
 def tearDown(self):self.tmp.cleanup()
 def invoke(self,rows=None):
  with patch.object(c,'NAS_LOCKS',self.paths),patch.object(c,'nas_rows',return_value=self.rows if rows is None else rows):return c.paused_evidence(self.rows)
 def test_paused_free_stable(self):self.assertEqual(self.invoke(),self.rows)
 def test_changed_committed(self):
  with self.assertRaises(RuntimeError):self.invoke([(52113,'verify','committed')])
 def test_new_inflight(self):
  with self.assertRaises(RuntimeError):self.invoke([(52113,'verify','started')]+self.rows)
 def test_lock_held(self):
  with self.paths[1].open('rb') as f:
   fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)
   with self.assertRaises(BlockingIOError):self.invoke()
 def test_missing_lock(self):
  self.paths[1].unlink()
  with self.assertRaises(FileNotFoundError):self.invoke()
 def test_telemetry_failure(self):
  with patch.object(c,'NAS_LOCKS',self.paths),patch.object(c,'nas_rows',side_effect=RuntimeError):
   with self.assertRaises(RuntimeError):c.paused_evidence(self.rows)
 def test_releases_locks(self):
  self.invoke()
  for p in self.paths:
   with p.open('rb') as f:fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)
