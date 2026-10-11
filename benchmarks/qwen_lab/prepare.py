"""Prepare a separate run without changing the parallel benchmark or production."""
import argparse
import hashlib
import json
import os
import shutil
from pathlib import Path

parser=argparse.ArgumentParser();parser.add_argument('--project',type=Path,required=True);parser.add_argument('--run-name',default='run01');parser.add_argument('--context',type=int,default=32768);parser.add_argument('--profile',choices=['all','map6144_reduce6144'],default='all');args=parser.parse_args()
if not args.run_name.isalnum():raise ValueError('invalid_run_name')
if args.context not in {32768,65536}:raise ValueError('unsupported_context')
project=args.project.resolve();source=project/'benchmarks/staged-v2';target=project/'benchmarks/qwen_lab'/args.run_name
if target.exists():raise RuntimeError('run_already_exists')
os.umask(0o077);target.mkdir(parents=True,mode=0o700)
shutil.copytree(source/'stage2/assets',target/'assets')
shutil.copy(source/'runner.py',target/'runner.py')
s= (source/'staged.py').read_text()
old="limits = {'map': 3072, 'reduce': 3072, 'verify': 512, 'render': 2048, 'judge': 1536}"
if s.count(old)!=1:raise RuntimeError('upstream_stage_limits_changed')
s=s.replace(old,"limits = self.model['stage_limits']")
(target/'staged.py').write_text(s)
shutil.copy(project/'benchmarks/qwen_lab/lab.py',target/'lab.py')
base=next(m for m in json.loads((source/'models.json').read_text())['models'] if m['name']=='qwen3.6:27b')
base={k:base[k] for k in ['name','digest','think','profile','options']}
base['options']={**base['options'],'num_ctx':args.context}
limits={'map':6144,'reduce':3072,'verify':512,'render':2048,'judge':1536}
profiles=[]
for name,updates in [('map6144',{}),('map8192',{'map':8192}),('map6144_reduce6144',{'reduce':6144})]:
 profiles.append({**base,'lab_profile':name,'stage_limits':{**limits,**updates}})
if args.profile!='all':profiles=[p for p in profiles if p['lab_profile']==args.profile]
real=json.loads((source/'stage2/private/real-input.json').read_text())
(target/'private').mkdir(mode=0o700);(target/'private/input.json').write_text(json.dumps(real,ensure_ascii=False))
manifest={'revision':'qwen-lab-2026-10-11-'+args.run_name,'maximum_seconds':5400,'profiles':profiles,
 'templates':list(json.loads((target/'assets/meeting_kit/templates.json').read_text())),
 'input_hash':real['input_hash'],'input_role':'development_previously_exposed',
 'baseline':'staged-v2/stage2: original MAP3072 failure retained; not replayed',
 'quality_status':'not_scored','files':{str(p.relative_to(target)):hashlib.sha256(p.read_bytes()).hexdigest() for p in target.rglob('*') if p.is_file()}}
(target/'manifest.json').write_text(json.dumps(manifest,indent=2,ensure_ascii=False))
print(json.dumps({'run':args.run_name,'profiles':len(profiles),'templates':len(manifest['templates']),'input_role':manifest['input_role']}))
