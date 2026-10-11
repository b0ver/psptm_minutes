"""Local A/B review packets; identities never appear in blinded packets."""
import argparse
import hashlib
import json
import os
import random
from pathlib import Path

def build(records, output, seed=42):
    if output.exists():raise ValueError('output_already_exists')
    groups={}
    for r in records:groups.setdefault((r['input_hash'],r['template']),[]).append(r)
    pairs=[];keys=[];rng=random.Random(seed)
    for (source,template),group in sorted(groups.items()):
        if len(group)!=2 or group[0]['candidate']==group[1]['candidate']:
            raise ValueError('exactly_two_distinct_candidates_required')
        rng.shuffle(group)
        identifier=hashlib.sha256((source+template).encode()).hexdigest()[:16]
        pairs.append({'id':identifier,'input_hash':source,'template':template,'A':group[0]['report'],'B':group[1]['report'],
                      'review_status':'pending','preference':None,'source_required':True})
        keys.append({'id':identifier,'A':group[0]['candidate'],'B':group[1]['candidate']})
    os.umask(0o077);output.mkdir(parents=True,mode=0o700)
    (output/'blinded.json').write_text(json.dumps(pairs,ensure_ascii=False))
    (output/'identities.json').write_text(json.dumps(keys,ensure_ascii=False))
    # Reversed packets permit an explicit position-bias check, not an independent sample.
    (output/'position-audit.json').write_text(json.dumps([{**p,'A':p['B'],'B':p['A'],'audit_of':p['id']} for p in pairs],ensure_ascii=False))
    return len(pairs)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('records',type=Path);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    print(json.dumps({'blinded_pairs':build(json.loads(a.records.read_text()),a.output)}))
