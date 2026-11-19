import tempfile,unittest
from pathlib import Path
from integrations.telemost.probe_process import browser_roots
class BrowserIdentityTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.proc=self.root/'proc';self.proc.mkdir();self.exe=self.root/'chrome';self.exe.write_bytes(b'fixture');self.profile='--user-data-dir=/tmp/owned-probe'
    def tearDown(self):self.temp.cleanup()
    def process(self,pid,profile=None,renderer=False,executable=None):
        p=self.proc/str(pid);p.mkdir();args=[str(executable or self.exe),profile or self.profile]
        if renderer:args.append('--type=renderer')
        (p/'cmdline').write_bytes(b'\0'.join(a.encode() for a in args));(p/'exe').symlink_to(executable or self.exe)
    def test_binds_exact_profile_and_excludes_renderer(self):
        self.process(11);self.process(12,renderer=True);self.process(13,profile='--user-data-dir=/tmp/unrelated')
        self.assertEqual(browser_roots(self.profile,str(self.exe),self.proc),[11])
    def test_same_profile_wrong_executable_rejected(self):
        other=self.root/'other';other.write_bytes(b'fixture');self.process(11,executable=other)
        self.assertEqual(browser_roots(self.profile,str(self.exe),self.proc),[])
    def test_missing_profile_rejected(self):
        with self.assertRaises(ValueError):browser_roots('',str(self.exe),self.proc)
if __name__=='__main__':unittest.main()
