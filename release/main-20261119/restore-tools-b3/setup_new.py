"""Create only new isolated restore storage. Never modify old restore/NAS data."""
import os,json,subprocess,sys,hashlib
from pathlib import Path
D=Path('/home/epopov/projects/psptm_minutes-ops/main-release-20261119/restore-b3')
TARGET=Path('/mnt/models/psptm_minutes-ops/main-release-20261119/restore-b3')
IMAGE='sha256:24e3f8bacc4bd7af6f6dc56f8bdc0e1effd3f094756b52a3d9c7d7c1140800b8'
V={'data':'minutes-restore-data-20261119-b3','state':'minutes-restore-state-20261119-b3'}
def run(args):
 r=subprocess.run(args,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=40)
 if r.returncode:raise RuntimeError('setup_command_failed_'+args[1])
 return r.stdout
os.umask(0o077)
assert not TARGET.exists(),'new_target_required';assert not D.exists(),'new_control_required'
for name in V.values():assert subprocess.run(['docker','volume','inspect',name],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL).returncode!=0,'volume_already_exists'
TARGET.mkdir(parents=True,mode=0o755);TARGET.chmod(0o755);D.mkdir(mode=0o700)
(D/'control').mkdir(mode=0o755);(D/'control').chmod(0o755);(D/'progress').mkdir(mode=0o755);(D/'progress').chmod(0o755)
run(['docker','volume','create','--label','minutes.restore.attempt=20261119-b3','--opt','type=none','--opt','o=bind','--opt','device='+str(TARGET),V['data']])
run(['docker','volume','create','--label','minutes.restore.attempt=20261119-b3',V['state']])
init="import os;from pathlib import Path\nfor p in ['/restore','/state','/progress']:\n assert not os.listdir(p);os.chown(p,10001,10001);os.chmod(p,0o700 if p!='/progress' else 0o755)"
run(['docker','run','--rm','--network','none','--read-only','--cpus','.5','--memory','128m','--cap-drop','ALL','--cap-add','CHOWN','--cap-add','FOWNER','--security-opt','no-new-privileges','--user','0','-v',V['data']+':/restore','-v',V['state']+':/state','-v',str(D/'progress')+':/progress','--entrypoint','python',IMAGE,'-c',init])
code="""from pathlib import Path
import json,hashlib,sys,resource,stat
sys.path[:0]=['/next','/writer-code'];from restore_controller import validate_spec
p=Path('/spec/spec.json');s=json.loads(p.read_text());h=validate_spec(s)
assert len(s['archives'])==85
print(json.dumps({'spec_sha256':h,'target_entries':len(s['target']),'logical_output_bytes':sum(r[2] for r in s['target'] if stat.S_ISREG(r[1])),'peak_rss_kib':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss}))
"""
old=json.loads(run(['docker','volume','inspect','minutes-restore-spec-20261118-a2']))[0];assert old.get('Labels')=={'minutes.restore.attempt':'20261118-a2'}
result=json.loads(run(['docker','run','--rm','--network','none','--read-only','--cpus','.5','--memory','512m','--cap-drop','ALL','--security-opt','no-new-privileges','--user','10001:10001','-v','minutes-restore-spec-20261118-a2:/spec:ro','-v',str(D.parent/'code-b3')+':/next:ro','-v',str(D.parent/'code-b3')+':/writer-code:ro','--entrypoint','python',IMAGE,'-c',code]))
free=__import__('shutil').disk_usage(TARGET).free
assert free-result['logical_output_bytes']-2*1024**3>=60*1024**3,'projected_disk_reserve'
(D/'setup.json').write_text(json.dumps({'target':str(TARGET),'volumes':V,'image':IMAGE,**result},indent=2)+'\n')
print(json.dumps({'isolated_storage':'created','spec':result,'target_free_bytes':free,'projected_reserve_bytes':free-result['logical_output_bytes']-2*1024**3}))
