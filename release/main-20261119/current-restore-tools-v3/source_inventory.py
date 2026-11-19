"""Read-only private source reconciliation. Prints aggregate counters only."""
import hashlib,json,os,re
from pathlib import Path
import sys
sys.path.insert(0,'/app')
from sqlalchemy import text
from app.db import engine,Base

assert os.environ.get('MINUTES_SOURCE_READONLY')=='1'
assert os.getuid()==10001
plan=json.loads(Path('/source-access-plan.json').read_text())
env=plan['environment'];users=set(env['TELEGRAM_ALLOWED_USERS'].split(','))
assert len(users)==2 and all(x.isdigit() for x in users)
mapping=json.loads(env['MINUTES_CALENDAR_RECOVERY_OWNERS_JSON'])
reconciled=json.loads(Path('/historical-partials.json').read_text())['rows']
historical={r['session_id']:r for r in reconciled}
assert len(historical)==len(reconciled)==14
for r in historical.values():assert r['status']=='partial_saved' and not r['pending']
terminal={'uploaded','failed','cancelled','partial_saved'}
state_hashes=[];partials=0;events=0
for path in sorted(Path('/data/telemost').glob('*/state.json')):
 raw=path.read_bytes();state=json.loads(raw);sid=path.parent.name
 assert re.fullmatch('[a-f0-9-]{36}',sid)
 assert state.get('status') in terminal and not state.get('recovery_pending') and not state.get('repair_requested'),'source_agent_not_reconciled'
 digest=hashlib.sha256(raw).hexdigest();state_hashes.append([sid,digest])
 if state['status']=='partial_saved':
  assert sid in historical and digest==historical[sid]['state_sha256'],'new_or_changed_partial_recording'
  partials+=1
 events+=bool(state.get('recovery_event'))
assert partials==14 and events==0,'historical_state_or_event_drift_requires_review'
with engine.connect() as c:
 c.execute(text('SET TRANSACTION READ ONLY'))
 for user in users:
  row=c.execute(text('SELECT user_id,chat_id,active FROM telegram_users WHERE user_id=:u'),{'u':user}).one()
  assert row.active and row.user_id==row.chat_id,'operator_private_chat_binding_changed'
 for subscription,user in mapping.items():
  assert user in users
  assert c.scalar(text('SELECT count(*) FROM calendar_subscriptions WHERE id=:s'),{'s':subscription})==1,'calendar_owner_binding_missing'
 queues={}
 for table in ['meetings','meeting_analysis','meeting_reports']:
  busy=c.scalar(text("SELECT count(*) FROM "+table+" WHERE status='processing'"))
  assert busy==0,'source_work_pending'
  queues[table]=busy
 assert c.scalar(text("SELECT count(*) FROM meetings WHERE status='queued'"))==0,'transcription_work_pending'
 eligible=c.scalar(text("SELECT count(*) FROM meeting_analysis a JOIN meetings m ON m.id=a.meeting_id WHERE a.status='queued' AND json_typeof(m.transcript)='array' AND m.status IN ('ready','failed')"))
 assert eligible==0,'eligible_analysis_work_pending'
 blocked_analysis=c.scalar(text("SELECT count(*) FROM meeting_analysis a JOIN meetings m ON m.id=a.meeting_id WHERE a.status='queued' AND a.step='waiting_transcript' AND m.status='failed' AND m.transcript IS NULL"))
 total_queued_analysis=c.scalar(text("SELECT count(*) FROM meeting_analysis WHERE status='queued'"))
 assert blocked_analysis==total_queued_analysis,'unknown_queued_analysis_requires_review'
 blocked_reports=c.scalar(text("SELECT count(*) FROM meeting_reports r JOIN meeting_analysis a ON a.meeting_id=r.meeting_id JOIN meetings m ON m.id=r.meeting_id WHERE r.status='queued' AND a.status='queued' AND a.step='waiting_transcript' AND m.status='failed' AND m.transcript IS NULL"))
 assert blocked_reports==c.scalar(text("SELECT count(*) FROM meeting_reports WHERE status='queued'")),'unknown_queued_report_requires_review'
 assert c.scalar(text("SELECT count(*) FROM calendar_occurrences WHERE status IN ('dispatching','uncertain','started')"))==0,'calendar_intent_unsettled'
 assert c.scalar(text("SELECT count(*) FROM telegram_commands WHERE status NOT IN ('done','failed')"))==0,'telegram_command_pending'
 assert c.scalar(text("SELECT count(*) FROM telegram_events WHERE status NOT IN ('sent','failed')"))==0,'telegram_delivery_pending'
 imminent=c.scalar(text("SELECT count(*) FROM calendar_occurrences o JOIN calendar_subscriptions s ON s.id=o.subscription_id WHERE s.enabled AND s.connected AND s.recording_authorized AND o.status IN ('scheduled','dispatching','uncertain','started') AND NOT o.skipped AND o.starts_at BETWEEN now()-interval '5 minutes' AND now()+interval '15 minutes'"))
 assert imminent==0,'scheduled_recording_near_cutover'
 counts={}
 for name in sorted(Base.metadata.tables):
  assert re.fullmatch('[a-z][a-z0-9_]*',name)
  counts[name]=c.scalar(text('SELECT count(*) FROM "'+name+'"'))
print(json.dumps({'status':'READONLY_SOURCE_RECONCILED','terminal_sessions':len(state_hashes),'historical_partials_preserved':partials,'pending_recovery_events':events,'processing_jobs':queues,'eligible_analysis_jobs':eligible,'preserved_waiting_transcript_analysis':blocked_analysis,'preserved_waiting_transcript_reports':blocked_reports,'imminent_authorized_meetings':imminent,'operators_verified_private':len(users),'calendar_owner_bindings':len(mapping),'table_counts':counts,'all_state_hashes_sha256':hashlib.sha256(json.dumps(state_hashes,separators=(',',':')).encode()).hexdigest(),'source_modified':False}))
