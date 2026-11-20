"""Run ONLY in old API isolated restore container. Aggregate output, no secrets.
Actual source DB/data are never mounted here. Calendar key read only by Fernet.
"""
import json,os,re,stat
from pathlib import Path
from sqlalchemy import create_engine,text
from cryptography.fernet import Fernet
from app.db import engine

name=os.environ.get('RESTORE_DB_NAME','')
assert re.fullmatch(r'minutes_restore_[a-z0-9_]+',name)
assert os.environ.get('DB_HOST')=='restore-db'
assert os.environ.get('MINUTES_ISOLATED_RESTORE')=='1'
assert os.getuid()==10001
assert all(stat.S_ISDIR(Path(p).lstat().st_mode) for p in ('/restore','/restore/data','/restore/data/recordings'))
proof=json.loads(Path('/state/file-proof.json').read_text())
expected=json.loads(Path('/spec/spec.json').read_text())
assert proof['spec_sha256']==expected['spec_sha256']
files={r[0]:(r[1],r[2]) for r in proof['files']}
restore=create_engine(engine.url.set(database=name))
with restore.connect() as c:
 c.execute(text('SET TRANSACTION READ ONLY'))
 rows=c.execute(text('SELECT m.recording,r.recording_sha256 FROM meetings m LEFT JOIN recording_imports r ON r.meeting_id=m.id')).all()
 checked=0
 for filename,digest in rows:
  assert filename and Path(filename).name==filename
  relative='recordings/'+filename;p=Path('/restore/data')/relative
  assert relative in files
  s=p.lstat();assert stat.S_ISREG(s.st_mode) and s.st_size>0
  assert [s.st_dev,s.st_ino,s.st_mode,s.st_size,s.st_mtime_ns,s.st_ctime_ns]==files[relative][1]
  with p.open('rb') as f:assert f.read(1) # UID readability smoke, not whole-file hash claim.
  if digest:assert files[relative][0]==digest;checked+=1
 credentials=c.execute(text('SELECT credential FROM calendar_subscriptions')).scalars().all()
 key=Fernet(Path('/backup-calendar-key').read_bytes().strip())
 for encrypted in credentials:assert key.decrypt(encrypted.encode())
 expected_counts=json.loads(Path('/spec/database-counts.json').read_text())
 for table,count in expected_counts.items():
  assert re.fullmatch(r'[a-z][a-z0-9_]*',table)
  assert c.execute(text('SELECT count(*) FROM "'+table+'"')).scalar_one()==count
print(json.dumps({'restored_meetings':len(rows),'recording_import_hashes_matched_to_bound_extraction_proof':checked,'calendar_credentials_decrypted':len(credentials),'table_counts_matched':len(expected_counts),'uid':os.getuid(),'independent_full_disk_reread':False}))
