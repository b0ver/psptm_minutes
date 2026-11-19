"""Stream private metadata to encrypted off-host storage and verify decryption."""
import hashlib,json,os,re,signal,subprocess,sys,time
from pathlib import Path
ROOT=Path('/Users/evgenypopov/.config/psptm_minutes')
BASE='/home/epopov/projects/psptm_minutes-ops/main-release-20261119'
SSH=['ssh','-o','BatchMode=yes','-o','StrictHostKeyChecking=yes','-o','ConnectTimeout=10','-o','ProxyCommand=tailscale nc %h %p','epopov@100.111.184.29']
def main(label):
 assert re.fullmatch('[a-z][a-z0-9]{0,15}',label);os.umask(0o077)
 signal.signal(signal.SIGALRM,lambda *_:(_ for _ in ()).throw(RuntimeError('offhost_metadata_deadline')));signal.alarm(180)
 d=ROOT/'backups/main-release-20261119';d.mkdir(mode=0o700,parents=True,exist_ok=True)
 target=d/('metadata-'+label+'.tar.gpg');assert not target.exists(),'encrypted_destination_already_exists'
 remote=BASE+'/final-metadata-'+label
 receipt=json.loads(subprocess.check_output([*SSH,'cat '+remote+'/receipt.json'],timeout=20));assert receipt['status']=='AWAITING_ENCRYPTED_OFFHOST_ROUNDTRIP' and receipt['bytes']<=301*1024**2
 gpg=['gpg','--homedir',str(ROOT/'backup-keys/gpg-recorder-20261107'),'--batch','--pinentry-mode','loopback','--passphrase-file',str(ROOT/'backup-keys/recorder-20261107.pass')]
 processes=[]
 try:
  with (d/('metadata-'+label+'.private.log')).open('xb') as log:
   source=subprocess.Popen([*SSH,'cat '+remote+'/metadata.tar'],stdout=subprocess.PIPE,stderr=log);processes.append(source)
   encrypt=subprocess.Popen(gpg+['--symmetric','--cipher-algo','AES256','--compress-algo','none','--output',str(target)],stdin=source.stdout,stdout=subprocess.DEVNULL,stderr=log);processes.append(encrypt);source.stdout.close()
   assert encrypt.wait(timeout=120)==0 and source.wait(timeout=15)==0,'encrypted_metadata_copy_failed';target.chmod(0o600)
   decrypt=subprocess.Popen(gpg+['--decrypt',str(target)],stdout=subprocess.PIPE,stderr=log);processes.append(decrypt)
   h=hashlib.sha256();size=0
   while block:=decrypt.stdout.read(1024**2):
    size+=len(block);assert size<=receipt['bytes'],'offhost_plaintext_exceeds_source';h.update(block)
   assert decrypt.wait(timeout=15)==0 and size==receipt['bytes'] and h.hexdigest()==receipt['sha256'],'offhost_decryption_binding_failed'
  proof={'status':'ENCRYPTED_OFFHOST_ROUNDTRIP_VERIFIED','sha256':h.hexdigest(),'plaintext_bytes':size,'encrypted_bytes':target.stat().st_size,'decryption_hash_verified':True,'plaintext_saved_offhost':False,'source_label':label,'time':time.time()}
  (d/('metadata-'+label+'-verified.json')).write_text(json.dumps(proof,indent=2)+'\n')
  code="import json,sys,pathlib;v=json.load(sys.stdin);p=pathlib.Path('"+remote+"/offhost-confirmed.json');assert not p.exists();p.write_text(json.dumps(v,indent=2)+'\\n');p.chmod(0o600)"
  subprocess.run([*SSH,'python3 -c '+__import__('shlex').quote(code)],input=json.dumps(proof).encode(),stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=20,check=True)
  print(json.dumps(proof))
 finally:
  for p in reversed(processes):
   if p.poll() is None:p.terminate();p.wait(timeout=10)
if __name__=='__main__':main(*sys.argv[1:])
