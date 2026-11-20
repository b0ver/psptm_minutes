"""Final readonly source metadata binding, not an independent full byte reread."""
import hashlib,json,os,sys
from pathlib import Path
sys.path[:0]=['/catchup','/next','/writer-code']
from catchup_core import snapshot_strict
from runtime_scope import persistent
from restore_controller import comparable
from plan import digest
assert os.environ.get('MINUTES_SOURCE_BINDING')=='1' and os.getuid()==10001
expected=json.loads(Path('/proof/current-manifest.json').read_text())
proof=json.loads(Path('/proof/current-file-proof.json').read_text())
assert digest(expected)==proof['target_manifest_sha256']
exclusions=json.loads(Path('/proof/current-source-exclusions.json').read_text())
first=snapshot_strict('/source');assert comparable(persistent(first,exclusions))==comparable(expected),'live_source_differs_from_restored_current_copy'
second=snapshot_strict('/source');assert comparable(first)==comparable(second),'source_metadata_changed_during_final_binding'
print(json.dumps({'status':'CURRENT_SOURCE_METADATA_BOUND','target_manifest_sha256':digest(expected),'metadata_stable':True,'entries':len(expected),'source_readonly':True,'independent_full_source_reread':False}))
