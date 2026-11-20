"""Install checked official amd64 packages. No user data or build credentials."""
import argparse,hashlib,json,os,subprocess,tempfile,urllib.request
from pathlib import Path
from urllib.parse import urlsplit

def selected(manifest,kind,pins):
 rows=manifest['files'];assert len({r['binary'] for r in rows})==len(rows)
 if kind=='runtime':
  wanted={tuple(line.split('=',1)) for line in pins.splitlines() if line.strip()}
  rows=[r for r in rows if (r['binary'],r['version']) in wanted]
  assert {(r['binary'],r['version']) for r in rows}==wanted,'missing_runtime_pin'
 else:assert kind=='recorder' and any(r['binary']=='google-chrome-stable' for r in rows)
 for r in rows:
  u=urlsplit(r['url'])
  assert u.scheme=='https' and u.hostname in {'security.debian.org','deb.debian.org','dl.google.com'}
  assert Path(r['filename']).name==r['filename'] and len(r['sha256'])==64 and 0<r['bytes']<200*1024**2
 return rows

def main(kind,root):
 assert subprocess.check_output(['dpkg','--print-architecture'],text=True).strip()=='amd64','release_platform_must_be_amd64'
 rows=selected(json.loads((root/'os-downloads.json').read_text()),kind,(root/'runtime-os.binary.lock').read_text())
 with tempfile.TemporaryDirectory(prefix='minutes-os-') as folder:
  folder=Path(folder);paths=[]
  for row in rows:
   path=folder/row['filename'];h=hashlib.sha256();size=0
   with urllib.request.urlopen(row['url'],timeout=60) as source,path.open('xb') as target:
    while True:
     chunk=source.read(1024**2)
     if not chunk:break
     size+=len(chunk);assert size<=row['bytes'],'package_exceeds_bound';h.update(chunk);target.write(chunk)
   assert size==row['bytes'] and h.hexdigest()==row['sha256'],'package_integrity_mismatch';paths.append(str(path))
  env={**os.environ,'DEBIAN_FRONTEND':'noninteractive'}
  subprocess.run(['apt-get','update'],check=True,env=env,timeout=180)
  subprocess.run(['apt-get','install','-y','--no-install-recommends',*paths],check=True,env=env,timeout=600)
  subprocess.run(['dpkg','--audit'],check=True,timeout=30)
  subprocess.run(['apt-get','check'],check=True,timeout=30)
  for row in rows:
   installed=subprocess.check_output(['dpkg-query','-W','-f=${Version}',row['binary']],text=True)
   assert installed==row['version'],'installed_version_mismatch'
 print(json.dumps({'verified_official_packages':len(rows),'kind':kind}))
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('kind',choices=['runtime','recorder']);p.add_argument('--root',type=Path,default=Path('/tmp/minutes-release'));a=p.parse_args();main(a.kind,a.root)
