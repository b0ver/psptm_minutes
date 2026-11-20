"""Bounded private metadata archive. Explicit readonly file mounts only.

Secret bytes go from an unlogged Docker stdout directly to a protected file.
The caller must encrypt and verify that file off host before cutover.
"""
import hashlib,json,os,re,subprocess,sys,tarfile
from pathlib import Path
B=Path('/home/epopov/projects/psptm_minutes-ops/main-release-20261119')
def main(label,database_label=None):
 assert re.fullmatch('[a-z][a-z0-9]{0,15}',label);os.umask(0o077)
 d=B/('final-metadata-'+label);assert not d.exists();d.mkdir(mode=0o700)
 old=B/'settings-precutover-20261119.tar'
 old_receipt=json.loads((B/'settings-precutover-receipt.json').read_text())
 assert hashlib.sha256(old.read_bytes()).hexdigest()==old_receipt['sha256']
 with tarfile.open(old) as archive:
  entries=[]
  for member in archive:
   assert member.isfile() and not Path(member.name).is_absolute() and '..' not in Path(member.name).parts
   entries.append({'source':'/'+member.name,'archive':member.name,'sha256':hashlib.sha256(archive.extractfile(member).read()).hexdigest()})
 assert len(entries)==11
 for p in sorted((B/'candidate-c5').glob('*.yaml')):entries.append({'source':str(p),'archive':str(p).lstrip('/'),'sha256':None})
 key=B/'candidate-c5/secrets/recovery_control_key';entries.append({'source':str(key),'archive':str(key).lstrip('/'),'sha256':None})
 if database_label is not None:
  assert re.fullmatch('[a-z][a-z0-9]{0,15}',database_label)
  dump=B/('database-snapshot-'+database_label)/'database.dump';r=json.loads(dump.with_name('receipt.json').read_text());assert r['current_data_attempt']
  entries.append({'source':str(dump),'archive':str(dump).lstrip('/'),'sha256':r['sha256']})
 descriptor=d/'descriptor.private.json';descriptor.write_text(json.dumps(entries));descriptor.chmod(0o444)
 image=json.loads((B/'registry-images/runtime-installed.json').read_text())['registry_ref']
 args=['docker','run','--rm','--log-driver','none','--runtime','runc','--network','none','--read-only','--user','0','--cpus','.25','--memory','256m','--cap-drop','ALL','--cap-add','DAC_READ_SEARCH','--security-opt','no-new-privileges','-v',str(descriptor)+':/descriptor.json:ro']
 for index,row in enumerate(entries):
  assert Path(row['source']).is_file() and not Path(row['source']).is_symlink()
  args+=['--mount','type=bind,src='+row['source']+',dst=/input/'+str(index)+',readonly']
 code="""import hashlib,json,os,stat,sys,tarfile
from pathlib import Path
entries=json.loads(Path('/descriptor.json').read_text());checked=[];total=0
for index,row in enumerate(entries):
 p=Path('/input')/str(index);s=p.stat();assert stat.S_ISREG(s.st_mode) and s.st_size<=256*1024**2
 total+=s.st_size;assert total<=300*1024**2
 with p.open('rb') as f:digest=hashlib.file_digest(f,'sha256').hexdigest()
 if row['sha256'] is not None:assert row['sha256']==digest,'settings_or_dump_changed_before_backup'
 checked.append((p,row,s))
with tarfile.open(fileobj=sys.stdout.buffer,mode='w|') as archive:
 for p,row,s in checked:
  assert p.stat()==s,'metadata_changed_during_backup'
  archive.add(p,arcname=row['archive'],recursive=False)
  assert p.stat()==s,'metadata_changed_during_backup'
"""
 args+=['--entrypoint','python',image,'-c',code]
 with (d/'metadata.tar').open('xb') as out,(d/'execution.private.log').open('xb') as log:
  r=subprocess.run(args,stdout=out,stderr=log,timeout=120)
 assert r.returncode==0,'metadata_capture_failed_see_protected_log'
 p=d/'metadata.tar'
 with p.open('rb') as stream:digest=hashlib.file_digest(stream,'sha256').hexdigest()
 receipt={'status':'AWAITING_ENCRYPTED_OFFHOST_ROUNDTRIP','sha256':digest,'bytes':p.stat().st_size,'original_settings_files_verified':11,'candidate_configuration_included':True,'database_included':database_label is not None,'database_label':database_label,'production_services_modified':False,'candidate_compose_sha256':hashlib.sha256((B/'candidate-c5/compose.yaml').read_bytes()).hexdigest()}
 (d/'receipt.json').write_text(json.dumps(receipt,indent=2)+'\n');print(json.dumps(receipt))
if __name__=='__main__':main(*sys.argv[1:])
