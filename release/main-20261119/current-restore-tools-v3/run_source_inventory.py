"""Launch readonly source reconciliation with only data/DB-secret/plan mounts."""
import json,os,re,subprocess,sys
from pathlib import Path
B=Path('/home/epopov/projects/psptm_minutes-ops/main-release-20261119')
def main(label):
 assert re.fullmatch('[a-z][a-z0-9]{0,15}',label);os.umask(0o077)
 d=B/('source-inventory-'+label);assert not d.exists();d.mkdir(mode=0o700)
 source=json.loads((B/'baseline-metadata/running-services.json').read_text())['services'];api=source['api'];db=source['db']
 networks=set(api['NetworkSettings']['Networks'])&set(db['NetworkSettings']['Networks']);assert len(networks)==1
 env=dict(v.split('=',1) for v in api['Config']['Env']);secret=next(m['Source'] for m in api['Mounts'] if m['Destination']==env['DB_PASSWORD_FILE'] and m['Type']=='bind');volume=next(m['Name'] for m in api['Mounts'] if m['Destination']=='/data' and m['Type']=='volume')
 for name in ['access-plan.private.json','historical-partial-reconciliation.private.json']:
  p=d/name;p.write_bytes((B/name).read_bytes());p.chmod(0o444)
 image=json.loads((B/'registry-images/runtime-installed.json').read_text())['registry_ref']
 args=['docker','run','--rm','--log-driver','none','--runtime','runc','--network',next(iter(networks)),'--read-only','--cpus','.5','--memory','256m','--cap-drop','ALL','--security-opt','no-new-privileges','-e','MINUTES_SOURCE_READONLY=1','-e','DB_HOST='+env.get('DB_HOST','db'),'-e','DB_PASSWORD_FILE=/run/source-password','--mount','type=bind,src='+secret+',dst=/run/source-password,readonly','-v',volume+':/data:ro','-v',str(d/'access-plan.private.json')+':/source-access-plan.json:ro','-v',str(d/'historical-partial-reconciliation.private.json')+':/historical-partials.json:ro','-v',str(B/'source_inventory.py')+':/source_inventory.py:ro','--entrypoint','python',image,'/source_inventory.py']
 r=subprocess.run(args,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=45)
 (d/'execution.private.log').write_bytes(r.stderr)
 assert r.returncode==0,'source_reconciliation_failed_see_protected_log'
 receipt=json.loads(r.stdout);assert receipt['status']=='READONLY_SOURCE_RECONCILED'
 (d/'receipt.json').write_text(json.dumps(receipt,indent=2)+'\n');print(json.dumps(receipt))
if __name__=='__main__':main(*sys.argv[1:])
