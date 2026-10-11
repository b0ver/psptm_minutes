"""Bounded local Qwen experiments. Frozen source kit; private texts stay on host."""
import argparse
import fcntl
import hashlib
import json
import os
import time
from pathlib import Path
import runner
import staged

DEADLINE=0
ROOT=Path(__file__).resolve().parent

class BudgetReached(runner.ProcessingError):
    def __init__(self):super().__init__('run_budget_reached')

def ensure_budget():
    if time.monotonic()>=DEADLINE:raise BudgetReached()

def gate(model):
    while True:
        ensure_budget()
        try:
            g=json.loads((ROOT.parent.parent/'staged-v2/gate.json').read_text())
            loaded=runner.api('/api/ps',timeout=10)['models']
            if g.get('ready') and 0<=time.time()-g['time']<15 and g.get('gpu_utilization',100)<15 and not any(m['digest']!=model['digest'] for m in loaded):return g
        except BudgetReached:raise
        except Exception:pass
        runner.atomic(ROOT/'progress.json',{'phase':'waiting_resources','time':time.time()})
        time.sleep(3)

class LabLLM(staged.StageLLM):
    def chat(self,system,user,spec=None):
        ensure_budget()
        tags=runner.api('/api/tags',timeout=10)['models']
        if next((m['digest'] for m in tags if m['name']==self.model['name']),None)!=self.model['digest']:
            raise runner.ProcessingError('model_digest_changed')
        return super().chat(system,user,spec)

def evidence_checks(extraction,report,case,template):
    source='\n'.join(s['text'] for s in case['segments'])
    facts=extraction['facts']
    quotes=[i.get('evidence','') for k in ('action_items','decisions') for i in facts[k]]
    headers=[s['title'] for s in template['sections']]
    return {'quoted_items':len(quotes),'exact_quotes':sum(bool(q) and q in source for q in quotes),
            'required_sections':len(headers),'section_matches_proxy':sum(h.casefold() in report.casefold() for h in headers),
            'semantic_quality':None,'human_review_status':'pending',
            'warning':'Exact quotes and headings do not establish factuality or completeness.'}

def summarize(records,phase):
    runner.atomic(ROOT/'summary.json',{'phase':phase,'completed':sum(r['status']=='completed' for r in records),
        'failed':sum(r['status']=='failed' for r in records),'records':records,'quality_winner':None,'time':time.time()})

def execute(case,model,kit,assets,templates,records):
    exkey=runner.digest({'input':case['input_hash'],'digest':model['digest'],'limits':model['stage_limits'],'assets':assets,'think':model['think']})
    exfile=ROOT/'private/extractions'/f'{exkey}.json'
    llm=LabLLM(model,kit,assets)
    try:
        if exfile.exists():extraction=json.loads(exfile.read_text())
        else:
            extraction=runner.extract(case,llm,kit)
            runner.atomic(exfile,extraction)
    except BudgetReached:raise
    except Exception as e:
        record={'case':case['id'],'profile':model['lab_profile'],'status':'failed','stage':'extraction',
                'error':e.code if isinstance(e,runner.ProcessingError) else type(e).__name__,'affected_templates':templates,
                'calls':llm.calls}
        runner.atomic(ROOT/'private/extraction-failures'/f'{exkey}.json',record)
        records.append(record);summarize(records,'running');return
    runner.atomic(ROOT/'private/review'/f'{exkey}.json',{'case':case,'extraction':extraction,'annotation_status':'unreviewed'})
    for name in templates:
        ensure_budget()
        key=runner.digest({'extraction':exkey,'template':name})
        target=ROOT/'private/results'/f'{key}.json'
        if target.exists():
            old=json.loads(target.read_text());records.append(old['public']);continue
        start=len(llm.calls);llm.stage='render'
        public={'case':case['id'],'profile':model['lab_profile'],'template':name,'key':key}
        try:
            report=kit['render_report'](name,extraction['facts'],llm)
            public.update(status='completed',checks=evidence_checks(extraction,report,case,kit['CATALOG'][name]))
            runner.atomic(target,{'public':public,'report':report,'extraction_key':exkey,'input_hash':case['input_hash']})
        except BudgetReached:raise
        except Exception as e:
            public.update(status='failed',stage='render',error=e.code if isinstance(e,runner.ProcessingError) else type(e).__name__)
            runner.atomic(target,{'public':public,'input_hash':case['input_hash']})
        public['calls']=llm.calls[start:];records.append(public);summarize(records,'running')

def main():
    global DEADLINE,ROOT
    parser=argparse.ArgumentParser();parser.add_argument('--run',type=Path,required=True);args=parser.parse_args()
    ROOT=args.run.resolve();os.umask(0o077)
    lock=(ROOT.parent/'lab.lock').open('a')
    fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    manifest=json.loads((ROOT/'manifest.json').read_text())
    for name,sha in manifest['files'].items():
        if hashlib.sha256((ROOT/name).read_bytes()).hexdigest()!=sha:raise ValueError('frozen_asset_changed')
    assets=runner.digest(manifest);DEADLINE=time.monotonic()+manifest['maximum_seconds']
    runner.ROOT=staged.ROOT=ROOT;runner.PRIVATE=ROOT/'private';runner.await_gate=gate
    original_api=runner.api
    def bounded_api(path,payload=None,timeout=300):
        return original_api(path,payload,timeout=min(timeout,300))
    runner.api=bounded_api
    kit=runner.load_kit();case=json.loads((ROOT/'private/input.json').read_text())
    if runner.digest(case['segments'])!=case['input_hash']:raise ValueError('input_changed')
    records=[]
    try:
        for model in manifest['profiles']:execute(case,model,kit,assets,manifest['templates'],records)
    except BudgetReached:
        summarize(records,'budget_reached');return
    summarize(records,'generation_finished_review_required')

if __name__=='__main__':
    main()
