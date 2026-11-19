"""Read-only explicit restore admission; never logs rows, stderr, IDs or payloads."""
import json,math,shutil,subprocess
from pathlib import Path
from resource_pause import ResourceDenied
RESERVE=60*1024**3
AGENT_CODE='''from pathlib import Path
import json
rows=[json.loads(p.read_text()) for p in Path('/data/telemost').glob('*/state.json')]
print(json.dumps({'total':len(rows),'blocked':sum(r.get('status') not in {'uploaded','failed','partial_saved','cancelled'} or bool(r.get('recovery_pending')) or bool(r.get('repair_requested')) for r in rows)}))'''
SQL="""SELECT json_build_object('meetings',(SELECT count(*) FROM meetings WHERE status='processing'),'analysis',(SELECT count(*) FROM meeting_analysis WHERE status='processing'),'reports',(SELECT count(*) FROM meeting_reports WHERE status='processing'))"""

def pressure():
 result={}
 for kind in ('io','memory'):
  for line in Path('/proc/pressure',kind).read_text().splitlines():
   fields=line.split();result[kind+'_'+fields[0]]=float(next(x.split('=',1)[1] for x in fields[1:] if x.startswith('avg10=')))
 return result

def validate_resources(canonical,raw,root_free,target_free):
 details={'canonical':canonical,'raw_psi_avg10':raw,'root_free_bytes':root_free,'target_free_bytes':target_free}
 for key in ('io_some','io_full','memory_some','memory_full'):
  if key not in raw or not math.isfinite(raw[key]) or raw[key]<0:raise ResourceDenied('psi_telemetry_invalid',details)
 if canonical.get('light_cpu_allowed') is not True:raise ResourceDenied('canonical_light_cpu_denied',details)
 sample=canonical.get('sample',{})
 if type(sample.get('io_pressure_avg10')) not in (int,float) or not math.isfinite(sample['io_pressure_avg10']) or not 0<=sample['io_pressure_avg10']<=1:raise ResourceDenied('canonical_io_busy',details)
 if type(sample.get('memory_pressure_avg10')) not in (int,float) or sample.get('memory_pressure_avg10')!=0:raise ResourceDenied('canonical_memory_pressure',details)
 if raw['io_some']>1 or raw['io_full']>1:raise ResourceDenied('raw_io_pressure',details)
 if raw['memory_some']!=0 or raw['memory_full']!=0:raise ResourceDenied('raw_memory_pressure',details)
 if root_free<RESERVE:raise ResourceDenied('root_disk_reserve',details)
 if target_free<RESERVE:raise ResourceDenied('target_disk_reserve',details)
 return details

def probe_json(args,label,timeout,run=subprocess.run):
 try:result=run(args,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=timeout)
 except subprocess.TimeoutExpired:raise ResourceDenied(label+'_timeout',{'probe':label,'timeout_seconds':timeout}) from None
 if result.returncode:raise ResourceDenied(label+'_exit',{'probe':label,'returncode':result.returncode})
 try:return json.loads(result.stdout)
 except (ValueError,UnicodeError):raise ResourceDenied(label+'_invalid_json',{'probe':label}) from None

def production(run=subprocess.run):
 agent=probe_json(['docker','exec','psptm-minutes-api-1','python','-c',AGENT_CODE],'agent_states',12,run)
 if set(agent)!={'total','blocked'} or any(type(v) is not int or v<0 for v in agent.values()):raise ResourceDenied('agent_counts_invalid',{'probe':'agent_states'})
 if agent['blocked']:raise ResourceDenied('agent_work_or_recovery_active',{'agent':agent})
 queues=probe_json(['docker','exec','-e','PGOPTIONS=-c statement_timeout=2000 -c default_transaction_read_only=on','psptm-minutes-db-1','psql','-qAt','-U','minutes','-d','minutes','-c',SQL],'queue_processing',8,run)
 if set(queues)!={'meetings','analysis','reports'} or any(type(v) is not int or v<0 for v in queues.values()):raise ResourceDenied('processing_counts_invalid',{'probe':'queue_processing'})
 if sum(queues.values()):raise ResourceDenied('production_processing_active',{'processing':queues})
 return {'agent':agent,'processing':queues}

def observe(canonical,root,target,run=subprocess.run):
 details=validate_resources(canonical,pressure(),shutil.disk_usage(root).free,shutil.disk_usage(target).free)
 details.update(production(run));return details
