"""Prepare private immutable-image Compose. Does not create application services."""
import copy, hashlib, json, os, subprocess
from pathlib import Path

B=Path('/home/epopov/projects/psptm_minutes-ops/main-release-20261119')
SOURCE='c5d0059162ba2eae72c3c56dd378affb53a34aa8'
ROLES={'api':'runtime','telegram':'runtime','calendar-scheduler':'runtime','analysis-worker':'runtime','worker':'gpu','telemost-agent':'telemost'}

def main():
 os.umask(0o077)
 out=B/'candidate-c5';assert not out.exists(),'candidate_directory_already_exists'
 original=json.loads((B/'baseline-metadata/baseline/compose.yaml').read_text())
 spec=copy.deepcopy(original);plan=json.loads((B/'access-plan.private.json').read_text())
 env=plan['environment'];assert plan['operator_count']==2 and env['TELEGRAM_ALLOW_ALL']=='0'
 users=env['TELEGRAM_ALLOWED_USERS'].split(',');assert len(set(users))==2 and all(x.isdigit() for x in users)
 assert set(env['MINUTES_RECOVERY_CONTROL_ALLOWED_USERS'].split(','))==set(users)
 assert env['MINUTES_RECOVERY_CONTROL_ACTOR'] in users
 bindings=json.loads(env['MINUTES_CALENDAR_RECOVERY_OWNERS_JSON']);assert bindings and set(bindings.values())<=set(users)
 receipts={kind:json.loads((B/('registry-images/'+kind+'-installed.json')).read_text()) for kind in set(ROLES.values())}
 for r in receipts.values():assert r['source_commit']==SOURCE and r['exact_registry_ref_resolves_locally'] and r['tested_config_and_layers_identical']
 out.mkdir(mode=0o700);secrets=out/'secrets';secrets.mkdir(mode=0o700)
 key=secrets/'recovery_control_key';key.write_bytes((B/'future-recovery-control-key').read_bytes());key.chmod(0o600)
 assert key.stat().st_size>=32
 runtime=receipts['runtime']['registry_ref']
 utility=['docker','run','--rm','--log-driver','none','--runtime','runc','--network','none','--read-only','--cpus','.1','--memory','64m','--cap-drop','ALL','--cap-add','CHOWN','--cap-add','FOWNER','--security-opt','no-new-privileges','--user','0','--mount','type=bind,src='+str(key)+',dst=/key','--entrypoint','python',runtime,'-c',"import os;os.chown('/key',10001,10001);os.chmod('/key',0o400)"]
 subprocess.run(utility,check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=20)
 keydest='/run/secrets/recovery_control_key'
 for role,kind in ROLES.items():
  s=spec['services'][role];s['image']=receipts[kind]['registry_ref'];s['pull_policy']='never'
  assert s['read_only'] and 'ALL' in s['cap_drop'] and any(x.startswith('no-new-privileges') for x in s['security_opt'])
  if role in {'telegram','telemost-agent'}:
   s['volumes'].append({'type':'bind','source':str(key),'target':keydest,'read_only':True,'bind':{'create_host_path':False}})
   s['environment']['MINUTES_RECOVERY_CONTROL_KEY_FILE']=keydest
   s['environment']['MINUTES_RECOVERY_CONTROL_ALLOWED_USERS']=env['MINUTES_RECOVERY_CONTROL_ALLOWED_USERS']
  if role=='telegram':
   for k,v in env.items():
    if k!='MINUTES_RECOVERY_CONTROL_KEY_FILE':s['environment'][k]=v.replace('$','$$')
   # Filled with the actual UTC cutover time before service recreation.
   s['environment']['MINUTES_RECOVERY_NOTICES_SINCE']='2099-01-01T00:00:00+00:00'
 assert spec['services']['db']==original['services']['db'],'database_must_not_change'
 (out/'compose.yaml').write_text(json.dumps(spec,indent=2)+'\n')
 for name in ['compose.gpu.yaml','compose.telemost.yaml','compose.release.yaml']:(out/name).write_text('{"services":{}}\n')
 command=['docker','compose','--project-directory',str(out),'-p','psptm-minutes','-f',str(out/'compose.yaml')]
 for profile in ['ai','telegram','calendar','telemost']:command+=['--profile',profile]
 result=subprocess.run(command+['config','--format','json'],stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=30)
 assert result.returncode==0,'candidate_compose_validation_failed'
 normalized=json.loads(result.stdout)
 for role,kind in ROLES.items():
  s=normalized['services'][role];assert s['image']==receipts[kind]['registry_ref'] and s['pull_policy']=='never'
  assert s['command']==original['services'][role]['command']
  expected=dict(original['services'][role]['environment'])
  for k,v in expected.items():
   if role!='telegram' or k not in env:assert s['environment'][k]==v.replace('$$','$'),'unexpected_environment_change'
  assert s['networks']==original['services'][role]['networks']
 receipt={'status':'PREPARED_NOT_ACCEPTED_OR_DEPLOYED','source_commit':SOURCE,'services':len(ROLES),'database_unchanged':True,'existing_volumes_and_networks_preserved':True,'operator_count':2,'future_notice_cutoff_must_be_replaced':True,'compose_sha256':hashlib.sha256((out/'compose.yaml').read_bytes()).hexdigest(),'production_services_modified':False}
 (out/'prepared.json').write_text(json.dumps(receipt,indent=2)+'\n');print(json.dumps(receipt))

if __name__=='__main__':main()
