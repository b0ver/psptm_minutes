"""Actual empty PostgreSQL bootstrap with the final restore tmpfs/UID contract."""
import json,os,subprocess,time
from pathlib import Path
B=Path('/home/epopov/projects/psptm_minutes-ops/main-release-20261119')
PG='postgres:16.10-alpine@sha256:029660641a0cfc575b14f336ba448fb8a75fd595d42e1fa316b9fb4378742297'
def main():
 os.umask(0o077);d=B/'postgres-readonly-template-probe-v2';assert not d.exists();d.mkdir(mode=0o700)
 password=d/'password';password.write_text('synthetic-readonly-template-only\n');password.chmod(0o444)
 name='minutes-pg-ro-template-20261120-v2';owner='minutes-pg-ro-template-20261120-v2';cid=None;image=None
 with (d/'execution.log').open('xb') as log:
  def run(args,data=None,timeout=30):
   r=subprocess.run(args,input=data,stdout=subprocess.PIPE,stderr=log,timeout=timeout)
   if r.returncode:raise RuntimeError('readonly_postgres_template_failed')
   return r.stdout
  try:
   identity=['docker','run','--rm','--log-driver','none','--runtime','runc','--network','none','--read-only','--cap-drop','ALL','--security-opt','no-new-privileges','--user','postgres','--entrypoint','id',PG]
   uid=int(run(identity+['-u']));gid=int(run(identity+['-g']));assert uid>0 and gid>0
   image=run(['docker','image','inspect',PG,'--format','{{.Id}}']).decode().strip()
   cid=run(['docker','run','-d','--name',name,'--label','minutes.restore.owner='+owner,'--log-driver','none','--runtime','runc','--network','none','--user','postgres','--read-only','--cpus','.5','--memory','1g','--cap-drop','ALL','--security-opt','no-new-privileges','--tmpfs','/tmp:size=64m,mode=1777','--tmpfs',f'/var/lib/postgresql/data:rw,nosuid,size=768m,uid={uid},gid={gid},mode=0700','--tmpfs',f'/var/run/postgresql:rw,nosuid,size=16m,uid={uid},gid={gid},mode=0770','-e','POSTGRES_USER=minutes','-e','POSTGRES_DB=minutes_restore_template','-e','POSTGRES_PASSWORD_FILE=/run/probe-password','--mount','type=bind,src='+str(password)+',dst=/run/probe-password,readonly',PG]).decode().strip()
   for _ in range(40):
    r=subprocess.run(['docker','exec',cid,'sh','-c','[ "$(cat /proc/1/comm)" = postgres ] && exec psql -X -qAt -U minutes -d minutes_restore_template -c "SELECT 1"'],stdout=subprocess.DEVNULL,stderr=log,timeout=5)
    if r.returncode==0:break
    time.sleep(1)
   else:raise RuntimeError('readonly_postgres_template_unready')
   assert run(['docker','exec',cid,'psql','-qAt','-U','minutes','-d','minutes_restore_template','-c',"SELECT current_database(),version() LIKE 'PostgreSQL 16.10 %'"]).strip()==b'minutes_restore_template|t'
   proof={'status':'PASSED','scope':'empty_synthetic_postgres_template','uid':uid,'gid':gid,'readonly_root':True,'tmpfs_data_bytes':768*1024**2,'network':'none','production_mounts':False,'production_services_modified':False}
   (d/'receipt.json').write_text(json.dumps(proof,indent=2)+'\n');print(json.dumps(proof),flush=True)
  finally:
   if cid:
    actual=json.loads(run(['docker','inspect',cid]))[0];assert actual['Image']==image and actual['Config']['Labels'].get('minutes.restore.owner')==owner
    run(['docker','stop','-t','10',cid]);run(['docker','rm',cid])
if __name__=='__main__':main()
