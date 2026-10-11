import ast
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parent

class LabTests(unittest.TestCase):
    def test_empty_evidence_is_not_counted_as_exact(self):
        module=ast.parse((ROOT/'lab.py').read_text())
        func=next(n for n in module.body if isinstance(n,ast.FunctionDef) and n.name=='evidence_checks')
        ns={};exec(compile(ast.Module(body=[func],type_ignores=[]),'checks','exec'),ns)
        r=ns['evidence_checks']({'facts':{'action_items':[{'evidence':''},{'evidence':'Правда'}],'decisions':[]}},'## Задачи',{'segments':[{'text':'Правда'}]}, {'sections':[{'title':'Задачи'}]})
        self.assertEqual(r['exact_quotes'],1);self.assertIsNone(r['semantic_quality'])

    def test_preparation_freezes_private_input_and_is_non_destructive(self):
        with tempfile.TemporaryDirectory() as td:
            project=Path(td);source=project/'benchmarks/staged-v2'
            (source/'stage2/assets/meeting_kit').mkdir(parents=True)
            (source/'stage2/private').mkdir()
            (source/'stage2/assets/meeting_kit/templates.json').write_text(json.dumps({'a':{},'b':{}}))
            (source/'runner.py').write_text('# frozen runner')
            limits="limits = {'map': 3072, 'reduce': 3072, 'verify': 512, 'render': 2048, 'judge': 1536}"
            (source/'staged.py').write_text(limits)
            (source/'models.json').write_text(json.dumps({'models':[{'name':'qwen3.6:27b','digest':'frozen','think':False,'profile':'production','options':{'num_ctx':32768}}]}))
            (source/'stage2/private/real-input.json').write_text(json.dumps({'input_hash':'test','segments':[{'text':'private'}]}))
            dst=project/'benchmarks/qwen_lab';dst.mkdir();(dst/'lab.py').write_text((ROOT/'lab.py').read_text())
            command=[sys.executable,str(ROOT/'prepare.py'),'--project',str(project)]
            r=subprocess.run(command,capture_output=True,text=True);self.assertEqual(r.returncode,0,r.stderr)
            target=dst/'run01';m=json.loads((target/'manifest.json').read_text())
            self.assertEqual(m['profiles'][0]['stage_limits']['map'],6144)
            self.assertEqual(m['profiles'][1]['stage_limits']['reduce'],3072)
            self.assertEqual(m['profiles'][2]['stage_limits']['reduce'],6144)
            for name,expected in m['files'].items():self.assertEqual(hashlib.sha256((target/name).read_bytes()).hexdigest(),expected)
            self.assertEqual((target/'private').stat().st_mode&0o777,0o700)
            self.assertNotIn('private',r.stdout)
            self.assertEqual((source/'staged.py').read_text(),limits)
            self.assertNotEqual(subprocess.run(command,capture_output=True).returncode,0)

if __name__=='__main__':unittest.main()

class DatasetTests(unittest.TestCase):
    def test_ten_distinct_challenges_for_each_template(self):
        from build_cases import build
        rows=build({'protocol':{},'tldr':{}})
        self.assertEqual(len(rows),20)
        for template in ['protocol','tldr']:
            group=[r for r in rows if r['type']==template]
            self.assertEqual(len({r['scenario'] for r in group}),10)
            self.assertEqual(len({r['input_hash'] for r in group}),10)
            self.assertTrue(all(r['checks']['gold_status']!='adjudicated' for r in group))
