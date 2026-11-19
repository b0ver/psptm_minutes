import os,sys,json,stat
from pathlib import Path
sys.path[:0]=['/next','/writer-code']
from restore_controller import make_spec
from plan import canonical,digest
for p in ['/restore','/state','/spec']:
 assert not os.listdir(p);os.chmod(p,0o700);os.chown(p,10001,10001)
assert not os.listdir('/progress');os.chmod('/progress',0o755);os.chown('/progress',10001,10001)
old=json.load(open('/old-manifest.json'));target=json.load(open('/target-manifest.json'));ex=json.load(open('/exclusions.json'))['rows'];p=json.load(open('/archives/plan.json'));plan=p['plan_sha256'];assert plan=='c7f83a04b9efd9de2e84bc0ed7175707ff1fffbabb310a3e70cda30c88cc6a99'
complete=json.load(open('/archives/archive-set-complete.json'));assert complete['packages']==84 and complete['plan_sha256']==plan
archives=[{'id':'baseline-20261107','kind':'baseline','strip_prefix':'data','size':36442726400,'sha256':'ae9625578da28ae995f061f2dc8a7787dd0b7efd3eb48ac2906e11df33089bb1','plan_sha256':plan}]
for i in range(84):
 r=json.load(open(f'/archives/part-{i:06d}.json'));assert r['plan_sha256']==plan and r['selection_sha256']==p['packages'][i]['selection_sha256']
 archives.append({'id':f'delta-{i:06d}','kind':'delta','size':r['archive_bytes'],'sha256':r['archive_sha256'],'plan_sha256':plan})
s=make_spec(old,target,ex,archives,plan)
with open('/spec/spec.json','xb') as f:f.write(canonical(s));f.flush();os.fsync(f.fileno())
os.chmod('/spec/spec.json',0o600);os.chown('/spec/spec.json',10001,10001)
print(json.dumps({'spec_sha256':s['spec_sha256'],'archives':len(archives),'target_entries':len(target),'logical_output_bytes':sum(r[2] for r in target if stat.S_ISREG(r[1]))}))
