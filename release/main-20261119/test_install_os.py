import copy,importlib.util,json,unittest
from pathlib import Path
BASE=Path(__file__).parent
spec=importlib.util.spec_from_file_location('install_os',BASE/'install_os.py');module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
class ManifestTests(unittest.TestCase):
 def setUp(self):self.manifest=json.loads((BASE/'os-downloads.json').read_text());self.pins=(BASE/'runtime-os.binary.lock').read_text()
 def test_runtime_exact_pins(self):self.assertEqual(len(module.selected(self.manifest,'runtime',self.pins)),20)
 def test_recorder_has_chrome_and_runtime_fixes(self):
  rows=module.selected(self.manifest,'recorder',self.pins);self.assertEqual(len(rows),31);self.assertTrue({x['binary'] for x in module.selected(self.manifest,'runtime',self.pins)}<={x['binary'] for x in rows})
 def test_missing_pin_rejected(self):
  with self.assertRaises(AssertionError):module.selected(self.manifest,'runtime',self.pins+'\nmissing==1')
 def test_duplicate_rejected(self):
  self.manifest['files'].append(self.manifest['files'][0])
  with self.assertRaises(AssertionError):module.selected(self.manifest,'recorder',self.pins)
 def test_untrusted_host_rejected(self):
  self.manifest['files'][0]['url']='https://example.com/package.deb'
  with self.assertRaises(AssertionError):module.selected(self.manifest,'recorder',self.pins)
 def test_path_escape_rejected(self):
  self.manifest['files'][0]['filename']='../package.deb'
  with self.assertRaises(AssertionError):module.selected(self.manifest,'recorder',self.pins)
if __name__=='__main__':unittest.main()

import struct,tempfile
spec=importlib.util.spec_from_file_location('surface_probe',BASE/'surface_probe.py');surface=importlib.util.module_from_spec(spec);spec.loader.exec_module(surface)
class ElfProbeTests(unittest.TestCase):
 def test_reads_only_undefined_relevant_symbols(self):
  data=bytearray(1024);data[:6]=b'\x7fELF\x02\x01';struct.pack_into('<Q',data,40,64);struct.pack_into('<HH',data,58,64,3)
  strings=b'\0regcomp\0wscanf\0';data[256:256+len(strings)]=strings
  struct.pack_into('<IIQQQQIIQQ',data,128,0,3,0,0,256,len(strings),0,0,1,0)
  struct.pack_into('<IIQQQQIIQQ',data,192,0,11,0,0,512,48,1,0,8,24)
  struct.pack_into('<IBBHQQ',data,512,1,0,0,0,0,0);struct.pack_into('<IBBHQQ',data,536,9,0,0,1,0,0)
  with tempfile.TemporaryDirectory() as folder:
   path=Path(folder)/'elf';path.write_bytes(data);self.assertEqual(surface.undefined_symbols(path),['regcomp'])
 def test_non_elf_ignored(self):
  with tempfile.TemporaryDirectory() as folder:
   path=Path(folder)/'plain';path.write_bytes(b'not ELF');self.assertEqual(surface.undefined_symbols(path),[])
 def test_bad_section_table_rejected(self):
  data=bytearray(64);data[:6]=b'\x7fELF\x02\x01';struct.pack_into('<Q',data,40,4096);struct.pack_into('<HH',data,58,64,1)
  with tempfile.TemporaryDirectory() as folder:
   path=Path(folder)/'elf';path.write_bytes(data)
   with self.assertRaisesRegex(RuntimeError,'invalid_elf_sections'):surface.undefined_symbols(path)
