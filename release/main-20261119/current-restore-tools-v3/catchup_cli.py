"""Isolated current-delta entrypoint. Host owns admission lease and all mounts."""
import json,os,sys,stat
from pathlib import Path
sys.path[:0]=['/catchup','/next','/writer-code']
from catchup_core import snapshot_strict,apply_current,BothDisks
from restore_controller import comparable,validate_spec
from runtime_scope import persistent
from plan import plan,canonical,digest
from delta_writer import Guard,package
from next_core import Budget,Halt,atomic_json

def main(mode):
 if os.environ.get('MINUTES_CURRENT_RESTORE')!='1' or mode not in ('package','apply'):raise Halt('current_restore_contract')
 mounts={line.split()[4] for line in Path('/proc/self/mountinfo').read_text().splitlines()}
 if not {'/source','/restore','/parent-state','/spec','/control','/out'}.issubset(mounts):raise Halt('current_restore_mounts')
 s=json.loads(Path('/spec/spec.json').read_text());sha=validate_spec(s)
 parent_state=json.loads(Path('/parent-state/state.json').read_text())
 if parent_state['spec_sha256']!=sha or len(parent_state['completed'])!=85 or parent_state.get('in_progress') is not None:raise Halt('base_archive_restore_incomplete')
 parent=json.loads(Path('/parent-state/file-proof.json').read_text())
 if parent['spec_sha256']!=sha:raise Halt('parent_proof_spec_binding')
 prior=s['target']
 if os.environ.get('MINUTES_CURRENT_PARENT')=='delta':
  if '/current-parent' not in mounts:raise Halt('current_parent_mount')
  prior=json.loads(Path('/current-parent/current-manifest.json').read_text());parent=json.loads(Path('/current-parent/current-file-proof.json').read_text());finished=json.loads(Path('/current-parent/apply-complete.json').read_text())
  if parent['baseline_spec_sha256']!=sha or parent['target_manifest_sha256']!=digest(prior) or finished['proof_canonical_sha256']!=digest(parent):raise Halt('current_parent_chain_binding')
 out=Path('/out');guard=BothDisks(Guard('/out'),Guard('/restore'));budget=Budget(guard.check);guard.check()
 if mode=='package':
  if (out/'package-complete.json').exists():raise Halt('current_package_already_completed')
  exclusions=json.loads(Path('/current-exclusions.json').read_text())
  raw=snapshot_strict('/source',guard.check);current=persistent(raw,exclusions)
  p=plan(prior,current);maximum=int(os.environ.get('MINUTES_CURRENT_MAX_BYTES','536870912'))
  if p['logical_delta_bytes']>maximum:raise Halt('current_delta_exceeds_phase_limit')
  atomic_json(out/'current-target-manifest.json',current);atomic_json(out/'delta-plan.json',p);atomic_json(out/'current-source-exclusions.json',exclusions)
  for selection in p['packages']:package('/source',out,selection,p['plan_sha256'],guard)
  if comparable(raw)!=comparable(snapshot_strict('/source',guard.check)):raise Halt('source_changed_during_current_package')
  atomic_json(out/'package-complete.json',{'plan_sha256':p['plan_sha256'],'source_manifest_sha256':digest(current),'source_metadata_stable':True,'packages':len(p['packages']),'logical_delta_bytes':p['logical_delta_bytes']})
  print(json.dumps({'phase':'current_packaged','packages':len(p['packages']),'logical_delta_bytes':p['logical_delta_bytes'],'source_metadata_stable':True}),flush=True)
 else:
  p=json.loads((out/'delta-plan.json').read_text());new=json.loads((out/'current-target-manifest.json').read_text());receipt=json.loads((out/'package-complete.json').read_text())
  if receipt['plan_sha256']!=p['plan_sha256'] or receipt['source_manifest_sha256']!=digest(new):raise Halt('package_complete_binding')
  packages=[]
  for selection in p['packages']:
   stem=out/f"part-{selection['index']:06d}";packages.append((Path(str(stem)+'.tar'),json.loads(Path(str(stem)+'.json').read_text())))
  proof=apply_current('/restore/data',out,prior,new,parent,p,packages,budget,guard.check)
  guard.check();print(json.dumps({'phase':'current_applied_awaiting_db_and_live_source_check','file_count':len(proof['files']),'target_manifest_sha256':proof['target_manifest_sha256']}),flush=True)
if __name__=='__main__':
 try:main(sys.argv[1])
 except Exception as e:
  print(json.dumps({'phase':'STOPPED','error':type(e).__name__,'code':str(e) if isinstance(e,Halt) else 'current_delta_failed'}),flush=True);raise SystemExit(1)
