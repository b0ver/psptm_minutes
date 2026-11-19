"""Actual current dump/data/key restore on isolated network and tmpfs PostgreSQL."""
import hashlib,json,os,re,secrets,signal,subprocess,sys,tarfile,time
from pathlib import Path
B=Path('/home/epopov/projects/psptm_minutes-ops/main-release-20261119')
PG='postgres:16.10-alpine@sha256:029660641a0cfc575b14f336ba448fb8a75fd595d42e1fa316b9fb4378742297'
def main(label,current_attempt):
 for value in (label,current_attempt):assert re.fullmatch(r'[a-z][a-z0-9]{0,15}',value)
 os.umask(0o077);signal.signal(signal.SIGALRM,lambda *_:(_ for _ in ()).throw(RuntimeError('isolated_restore_deadline')));signal.alarm(480)
 snapshot=B/('database-snapshot-'+label);receipt=json.loads((snapshot/'receipt.json').read_text());dump=snapshot/'database.dump'
 with dump.open('rb') as f:assert hashlib.file_digest(f,'sha256').hexdigest()==receipt['sha256']
 assert receipt['current_data_attempt']==current_attempt
 source=B/('catchup-20261120-'+current_attempt);assert (source/'controller-complete.json').exists()
 setup=json.loads((B/'restore-b4/setup.json').read_text());volume=setup['volumes']['data']
 d=B/('restore-current-database-'+label);assert not d.exists();d.mkdir(mode=0o700);secrets_dir=d/'secrets';secrets_dir.mkdir(mode=0o700)
 password=secrets_dir/'db_password';password.write_text(secrets.token_urlsafe(36)+'\n');password.chmod(0o444)
 baseline=json.loads((B/'baseline-metadata/running-services.json').read_text())['services']['api'];env=dict(x.split('=',1) for x in baseline['Config']['Env']);keypath=next(m['Source'] for m in baseline['Mounts'] if m['Destination']==env['CALENDAR_KEY_FILE'])
 small=B/'settings-precutover-20261119.tar';small_receipt=json.loads((B/'settings-precutover-receipt.json').read_text())
 assert hashlib.sha256(small.read_bytes()).hexdigest()==small_receipt['sha256']
 with tarfile.open(small) as archive:
  member=archive.getmember(keypath.lstrip('/'));assert member.isfile() and member.size<1024;key=(archive.extractfile(member).read())
 backup_key=secrets_dir/'calendar_key';backup_key.write_bytes(key);del key;backup_key.chmod(0o600)
 counts=d/'expected-counts.json';counts.write_bytes((snapshot/'source-counts.private.json').read_bytes());counts.chmod(0o444)
 runtime=json.loads((B/'registry-images/runtime-installed.json').read_text())['registry_ref'];agent=json.loads((B/'registry-images/telemost-installed.json').read_text())['registry_ref']
 prefix='minutes-current-restore-'+label;net=prefix+'-net';db=prefix+'-db';database='minutes_restore_main_'+label;owner='minutes-restore-current-db-'+label;owned=[];network=False
 log=(d/'execution.private.log').open('xb')
 def run(args,data=None,timeout=60):
  r=subprocess.run(args,input=data,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=timeout)
  if r.returncode:log.write(r.stderr);log.flush();raise RuntimeError('isolated_current_restore_command_failed')
  return r.stdout
 try:
  assert subprocess.run(['docker','inspect',db],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL).returncode!=0
  run(['docker','network','create','--internal','--label','minutes.restore.owner='+owner,net]);network=True
  run(['docker','run','--rm','--log-driver','none','--network','none','--read-only','--cpus','.1','--memory','64m','--cap-drop','ALL','--cap-add','CHOWN','--cap-add','FOWNER','--security-opt','no-new-privileges','--user','0','--mount','type=bind,src='+str(backup_key)+',dst=/backup-key','--entrypoint','python',runtime,'-c',"import os;os.chown('/backup-key',10001,10001);os.chmod('/backup-key',0o400)"])
  identity=['docker','run','--rm','--log-driver','none','--network','none','--read-only','--cap-drop','ALL','--security-opt','no-new-privileges','--user','postgres','--entrypoint','id',PG]
  pguid=int(run(identity+['-u']).strip());pggid=int(run(identity+['-g']).strip());assert 0<pguid<65536 and 0<pggid<65536
  cid=run(['docker','run','-d','--name',db,'--label','minutes.restore.owner='+owner,'--log-driver','none','--runtime','runc','--network',net,'--network-alias','restore-db','--user','postgres','--read-only','--cpus','.5','--memory','1g','--cap-drop','ALL','--security-opt','no-new-privileges','--tmpfs','/tmp:size=64m,mode=1777','--tmpfs',f'/var/lib/postgresql/data:rw,nosuid,size=768m,uid={pguid},gid={pggid},mode=0700','--tmpfs',f'/var/run/postgresql:rw,nosuid,size=16m,uid={pguid},gid={pggid},mode=0770','-e','POSTGRES_USER=minutes','-e','POSTGRES_DB='+database,'-e','POSTGRES_PASSWORD_FILE=/run/probe-password','--mount','type=bind,src='+str(password)+',dst=/run/probe-password,readonly',PG]).decode().strip();image=json.loads(run(['docker','inspect',cid]))[0]['Image'];owned.append((cid,image))
  for _ in range(30):
   r=subprocess.run(['docker','exec',cid,'pg_isready','-U','minutes','-d',database],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
   if r.returncode==0:break
   time.sleep(1)
  else:raise RuntimeError('isolated_current_postgres_unready')
  run(['docker','exec','-i',cid,'pg_restore','--exit-on-error','--no-owner','-U','minutes','-d',database],dump.read_bytes(),timeout=120)
  args=['docker','run','--rm','--log-driver','none','--runtime','runc','--network',net,'--read-only','--cpus','.5','--memory','1g','--cap-drop','ALL','--security-opt','no-new-privileges','--tmpfs','/tmp:size=256m,mode=1777','-e','DB_HOST=restore-db','-e','DB_PASSWORD_FILE=/run/probe-password','-e','RESTORE_DB_NAME='+database,'-e','MINUTES_ISOLATED_RESTORE=1','--mount','type=bind,src='+str(password)+',dst=/run/probe-password,readonly','--mount','type=bind,src='+str(backup_key)+',dst=/backup-calendar-key,readonly','--mount','type=bind,src='+str(counts)+',dst=/expected-counts.json,readonly','-v',volume+':/restore:ro','-v',str(source/'writer')+':/proof:ro','-v',str(B/'verify_current_restore.py')+':/verify_current_restore.py:ro','--entrypoint','python',runtime,'/verify_current_restore.py']
  result=json.loads(run(args,timeout=120));result.update({'source_dump_sha256':receipt['sha256'],'backup_key_from_verified_settings_archive':True,'production_database_modified':False,'source_data_attempt':current_attempt});(d/'data-db-key-proof.json').write_text(json.dumps(result,indent=2)+'\n')
  startup="""import asyncio,json,hashlib
from pathlib import Path
import service
async def main():
 before={p.name+str(p.parent):hashlib.sha256(p.read_bytes()).hexdigest() for p in service.DATA.glob('*/state.json')}
 async with service.lifespan(service.app):
  assert len(service.sessions)==len(before) and not service.tasks
  assert all(s['status'] in {'uploaded','failed','partial_saved','cancelled'} and not s.get('recovery_pending') and not s.get('repair_requested') for s in service.sessions.values())
  count=len(service.sessions)
 after={p.name+str(p.parent):hashlib.sha256(p.read_bytes()).hexdigest() for p in service.DATA.glob('*/state.json')}
 assert before==after
 print(json.dumps({'restored_terminal_sessions_loaded':count,'recovery_tasks_created':0,'state_files_changed':0,'automatic_imports_attempted':0}))
asyncio.run(main())
"""
  args=['docker','run','--rm','--log-driver','none','--runtime','runc','--network','none','--read-only','--cpus','.5','--memory','768m','--cap-drop','ALL','--security-opt','no-new-privileges','--tmpfs','/tmp:size=256m,mode=1777','--tmpfs','/home/recorder/.pki:size=16m,uid=10001,gid=10001,mode=0700','-e','AGENT_DATA_DIR=/restore/data/telemost','-e','MINUTES_API_URL=http://unreachable.invalid','-v',volume+':/restore:ro','--entrypoint','python',agent,'-c',startup]
  boot=json.loads(run(args,timeout=90));(d/'agent-state-startup.json').write_text(json.dumps(boot,indent=2)+'\n');print(json.dumps({'database_restore':result,'agent_restored_state_startup':boot}),flush=True)
 finally:
  for cid,image in reversed(owned):
   c=json.loads(run(['docker','inspect',cid]))[0];assert c['Image']==image and c['Config']['Labels'].get('minutes.restore.owner')==owner
   run(['docker','stop','-t','10',cid]);run(['docker','rm',cid])
  if network:run(['docker','network','rm',net])
  log.close()
if __name__=='__main__':
 try:main(*sys.argv[1:])
 except Exception as e:print(json.dumps({'status':'STOPPED','error':type(e).__name__,'code':str(e) if isinstance(e,(RuntimeError,AssertionError)) else 'isolated_current_restore_failed'}));raise SystemExit(1)
