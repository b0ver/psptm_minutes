"""Run only in the isolated current-restore container; aggregate output only."""
import hashlib,json,os,re,stat
from pathlib import Path
import sys
sys.path.insert(0,'/app')
from sqlalchemy import create_engine,text
from cryptography.fernet import Fernet
from app.db import engine
assert os.environ.get('MINUTES_ISOLATED_RESTORE')=='1'
assert os.environ.get('DB_HOST')=='restore-db' and os.getuid()==10001
name=os.environ.get('RESTORE_DB_NAME','');assert re.fullmatch('minutes_restore_[a-z0-9_]+',name)
proof=json.loads(Path('/proof/current-file-proof.json').read_text());manifest=json.loads(Path('/proof/current-manifest.json').read_text())
def canonical(v):return json.dumps(v,ensure_ascii=True,sort_keys=True,separators=(',',':')).encode()
assert proof['target_manifest_sha256']==hashlib.sha256(canonical(manifest)).hexdigest()
finished=json.loads(Path('/proof/apply-complete.json').read_text());assert finished['proof_canonical_sha256']==hashlib.sha256(canonical(proof)).hexdigest()
files={r[0]:(r[1],r[2]) for r in proof['files']};assert len(files)==len(proof['files'])
expected=json.loads(Path('/expected-counts.json').read_text())['counts'];restore=create_engine(engine.url.set(database=name))
key=Fernet(Path('/backup-calendar-key').read_bytes().strip())
with restore.connect() as c:
 c.execute(text('SET TRANSACTION READ ONLY'))
 for table,count in expected.items():
  assert re.fullmatch('[a-z][a-z0-9_]*',table)
  assert c.execute(text('SELECT count(*) FROM "'+table+'"')).scalar_one()==count
 rows=c.execute(text('SELECT m.recording,r.recording_sha256 FROM meetings m LEFT JOIN recording_imports r ON r.meeting_id=m.id')).all();checked=0
 for filename,digest in rows:
  assert filename and Path(filename).name==filename
  relative='recordings/'+filename;assert relative in files
  p=Path('/restore/data')/relative;s=p.lstat();assert stat.S_ISREG(s.st_mode) and s.st_size>0
  assert [s.st_dev,s.st_ino,s.st_mode,s.st_size,s.st_mtime_ns,s.st_ctime_ns]==files[relative][1]
  with p.open('rb') as stream:assert stream.read(1)
  if digest:assert files[relative][0]==digest;checked+=1
 encrypted=c.execute(text('SELECT credential FROM calendar_subscriptions')).scalars().all()
 for credential in encrypted:assert key.decrypt(credential.encode())
print(json.dumps({'status':'CURRENT_DATA_DB_KEY_RESTORE_VERIFIED','target_manifest_sha256':proof['target_manifest_sha256'],'database_tables_matched':len(expected),'restored_meetings':len(rows),'recording_import_hashes_matched':checked,'calendar_credentials_decrypted':len(encrypted),'uid':os.getuid(),'data_readonly':True,'independent_full_source_reread':False,'models_used':False}))
