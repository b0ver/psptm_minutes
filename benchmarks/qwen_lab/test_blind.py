import json,tempfile,unittest
from pathlib import Path
from blind_pairs import build
class BlindTests(unittest.TestCase):
 def test_identities_hidden_and_reversal_is_explicit(self):
  with tempfile.TemporaryDirectory() as td:
   p=Path(td)/'review';r=[{'input_hash':'h','template':'t','candidate':name,'report':name+' text'} for name in ['one','two']]
   self.assertEqual(build(r,p),1)
   packet=json.loads((p/'blinded.json').read_text())[0]
   self.assertNotIn('candidate',packet)
   reverse=json.loads((p/'position-audit.json').read_text())[0];self.assertEqual(reverse['A'],packet['B']);self.assertEqual(reverse['audit_of'],packet['id'])
   with self.assertRaises(ValueError):build(r,p)
 def test_unpaired_reference_is_not_fabricated(self):
  with tempfile.TemporaryDirectory() as td:
   with self.assertRaises(ValueError):build([{'input_hash':'h','template':'t','candidate':'one','report':'text'}],Path(td)/'review')
if __name__=='__main__':unittest.main()
