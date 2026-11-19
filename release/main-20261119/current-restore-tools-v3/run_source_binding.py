import json,os,re,subprocess,sys,time
from pathlib import Path
B=Path('/home/epopov/projects/psptm_minutes-ops/main-release-20261119')
def main(attempt):
 assert re.fullmatch('[a-z][a-z0-9]{0,15}',attempt);os.umask(0o077)
 out=B/('source-binding-'+attempt+'.json');assert not out.exists()
 source=B/('catchup-20261120-'+attempt);assert json.loads((source/'controller-complete.json').read_text())['status']=='CURRENT_DELTA_APPLIED_AWAITING_FRESH_DB_AND_LIVE_SOURCE_BINDING'
 baseline=json.loads((B/'baseline-metadata/running-services.json').read_text())['services']['api'];volume=next(m['Name'] for m in baseline['Mounts'] if m['Destination']=='/data' and m['Type']=='volume')
 image=json.loads((B/'registry-images/runtime-installed.json').read_text())['registry_ref']
 args=['docker','run','--rm','--log-driver','none','--runtime','runc','--network','none','--read-only','--cpus','.5','--memory','512m','--cap-drop','ALL','--security-opt','no-new-privileges','-e','PYTHONDONTWRITEBYTECODE=1','-e','MINUTES_SOURCE_BINDING=1','-v',volume+':/source:ro','-v',str(source/'writer')+':/proof:ro','-v',str(B/'code-b4')+':/next:ro','-v',str(B/'code-b4')+':/writer-code:ro','-v',str(B/'catchup-code-v3')+':/catchup:ro','-v',str(B/'verify_source_binding.py')+':/verify.py:ro','--entrypoint','python',image,'/verify.py']
 r=subprocess.run(args,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=120)
 if r.returncode:(B/('source-binding-'+attempt+'.private.log')).write_bytes(r.stderr);raise RuntimeError('final_source_binding_failed')
 receipt=json.loads(r.stdout);receipt.update({'attempt':attempt,'time':time.time()});out.write_text(json.dumps(receipt,indent=2)+'\n');print(json.dumps(receipt))
if __name__=='__main__':main(*sys.argv[1:])
