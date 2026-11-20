import json,subprocess
from pathlib import Path

def stop_owned(name,cidfile,token,log,expected_image=None,run=subprocess.run):
    p=Path(cidfile);cid=p.read_text().strip() if p.exists() else None
    obj=run(['docker','inspect',cid or name],capture_output=True,text=True,timeout=10)
    if obj.returncode:
        # Missing container is harmless; other engine failures are not.
        if any(x in obj.stderr.lower() for x in ('no such object','no such container')):return 'absent'
        raise RuntimeError('cleanup_inspect_failed')
    data=json.loads(obj.stdout)
    if len(data)!=1:raise RuntimeError('cleanup_identity_count')
    o=data[0]
    if (expected_image and o.get('Image')!=expected_image) or (cid and o['Id']!=cid) or o['Name']!='/'+name or o['Config'].get('Labels',{}).get('minutes.restore.owner')!=token:
        raise RuntimeError('cleanup_identity_mismatch')
    if not o['State']['Running']:return 'stopped'
    if o['State'].get('Paused'):run(['docker','unpause',o['Id']],stdout=log,stderr=log,timeout=15,check=True)
    run(['docker','stop','-t','5',o['Id']],stdout=log,stderr=log,timeout=20,check=True)
    return 'stopped'

def set_paused_owned(name,cidfile,token,image,paused,run=subprocess.run):
    cid=Path(cidfile).read_text().strip()
    obj=run(['docker','inspect',cid],capture_output=True,text=True,timeout=10)
    if obj.returncode:raise RuntimeError('pause_inspect_failed')
    rows=json.loads(obj.stdout)
    if len(rows)!=1:raise RuntimeError('pause_identity_count')
    o=rows[0]
    if o['Id']!=cid or o['Name']!='/'+name or o['Image']!=image or o['Config'].get('Labels',{}).get('minutes.restore.owner')!=token:raise RuntimeError('pause_identity_mismatch')
    if not o['State']['Running']:raise RuntimeError('pause_container_not_running')
    if bool(o['State'].get('Paused'))!=paused:
        run(['docker','pause' if paused else 'unpause',cid],capture_output=True,text=True,timeout=15,check=True)
