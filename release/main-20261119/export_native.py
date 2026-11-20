"""Export native CI images for authenticated draft-only transport, never public artifacts."""
import gzip,hashlib,json,os,subprocess,sys
from pathlib import Path
PART=1024**3

def digest(path):
 with path.open('rb') as source:return hashlib.file_digest(source,'sha256').hexdigest()

def main(out):
 commit=os.environ['GITHUB_SHA'];run_id=os.environ['GITHUB_RUN_ID'];images={}
 assert len(commit)==40 and all(c in '0123456789abcdef' for c in commit)
 for kind in ('runtime','gpu','telemost'):
  ref=f'minutes-main-{kind}:{commit}';image=json.loads(subprocess.check_output(['docker','image','inspect',ref]))[0]
  assert image['Architecture']=='amd64' and image['Os']=='linux'
  labels=image['Config'].get('Labels') or {};assert labels.get('org.opencontainers.image.revision')==commit
  assert not labels.get('org.opencontainers.image.source'),'public_repository_package_autolink_forbidden'
  archive=out/f'native-{run_id}-{kind}.tar.gz';producer=subprocess.Popen(['docker','image','save',ref],stdout=subprocess.PIPE,stderr=subprocess.DEVNULL)
  try:
   with archive.open('xb') as target,gzip.GzipFile(fileobj=target,mode='wb',compresslevel=1,mtime=0) as compressed:
    while True:
     block=producer.stdout.read(1024**2)
     if not block:break
     compressed.write(block)
   assert producer.wait(timeout=30)==0,'image_export_failed'
  finally:
   if producer.poll() is None:producer.kill();producer.wait()
  info={'image_id':image['Id'],'source_commit':commit,'unpacked_size':image['Size'],'archive_sha256':digest(archive),'archive_bytes':archive.stat().st_size,'parts':[]}
  if archive.stat().st_size>1500*1024**2:
   with archive.open('rb') as source:
    index=0
    while True:
     first=source.read(1024**2)
     if not first:break
     part=out/(archive.name+f'.part-{index:03d}')
     with part.open('xb') as target:
      target.write(first);remaining=PART-len(first)
      while remaining:
       block=source.read(min(1024**2,remaining))
       if not block:break
       target.write(block);remaining-=len(block)
     info['parts'].append({'name':part.name,'bytes':part.stat().st_size,'sha256':digest(part)});index+=1
   archive.unlink()  # Own verified temporary file; all bytes remain in hashed parts.
  else:info['parts']=[{'name':archive.name,'bytes':archive.stat().st_size,'sha256':info['archive_sha256']}]
  images[kind]=info
 metadata={'source_commit':commit,'build_run_id':run_id,'platform':'linux/amd64','draft_only_transport':True,'deploy_allowed':False,'images':images}
 (out/f'native-{run_id}-transport.json').write_text(json.dumps(metadata,indent=2)+'\n')
 print(json.dumps({'native_images_exported':3,'source_commit':commit,'transport':'authenticated_existing_draft_only','deploy_allowed':False}))
if __name__=='__main__':main(Path(sys.argv[1]))
