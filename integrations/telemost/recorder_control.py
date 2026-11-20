"""Authenticated, deny-by-default recovery requests. Never process/container restart."""
import os
from pathlib import Path
from fastapi import HTTPException, Request
try:
    from .signed_control import verify, Rejected
except ImportError:
    from signed_control import verify, Rejected


def install(app, namespace):
    def configuration():
        path=os.getenv('MINUTES_RECOVERY_CONTROL_KEY_FILE','')
        try:key=Path(path).read_bytes() if path else b''
        except OSError:key=b''
        users={v.strip() for v in os.getenv('MINUTES_RECOVERY_CONTROL_ALLOWED_USERS','').split(',') if v.strip()}
        return key,users

    async def control(request:Request):
        key,users=configuration()
        if len(key)<32 or not users:raise HTTPException(503,'recovery_control_unconfigured')
        try:
            body=await request.json()
            verify(key,'POST',request.url.path,body,request.headers.get('X-Minutes-Control-Signature',''),users)
        except (ValueError,TypeError,AttributeError):
            raise HTTPException(403,'recovery_control_denied') from None
        # No await from authoritative snapshot through durable persist. All
        # recorder state mutation and admission checks share this event loop.
        if request.url.path=='/control/events':
            return namespace['recovery_events'](after='' if body['session_id']=='-' else body['session_id'], limit=50)
        sid=body['session_id'];sessions=namespace['sessions']
        if sid not in sessions:raise HTTPException(404,'session_not_found')
        state=sessions[sid];generation=str(state.get('created_at',''))
        pending=bool(state.get('recovery_pending'))
        watch=namespace['capture_watchdogs'].get(sid)
        stale=bool(watch and watch.expired(namespace['time'].monotonic()))
        snapshot={'session_id':sid,'generation':generation,'status':state['status'],
                  'stale':stale,'recovery_pending':pending}
        operation=state.get('recovery_control_operation')
        path=request.url.path
        if path=='/control/prepare':return snapshot
        if path=='/control/status':
            if not operation or operation['request_id']!=body['request_id'] or operation['user_id']!=body['user_id']:
                raise HTTPException(404,'operation_not_found')
            return {**snapshot,'operation_id':operation['request_id'],
                    'result':'pending' if state.get('repair_requested') or pending else state['status']}
        if path=='/control/cancel':
            # Confirmation lives at Telegram dispatcher. Accepted requests cannot
            # race-cancel a recovery already underway.
            if operation and operation['request_id']==body['request_id']:
                raise HTTPException(409,'already_accepted')
            return {'result':'not_submitted'}
        if operation and operation['request_id']==body['request_id']:
            if operation['user_id']!=body['user_id'] or operation['generation']!=body['generation']:
                raise HTTPException(409,'operation_conflict')
            return {**snapshot,'operation_id':operation['request_id'],'result':'accepted'}
        if generation!=body['generation']:raise HTTPException(409,'session_changed')
        if pending or state.get('repair_requested'):raise HTTPException(409,'recovery_in_progress')
        if state['status']!='recording' or not stale or sid not in namespace['tasks']:
            raise HTTPException(409,'active_recording_protected')
        namespace['persist'](sid,repair_requested=True,recovery_control_operation={
            'request_id':body['request_id'],'user_id':body['user_id'],'generation':generation})
        return {**snapshot,'operation_id':body['request_id'],'result':'accepted'}

    for path in ('prepare','confirm','cancel','status','events'):
        app.add_api_route('/control/'+path,control,methods=['POST'],status_code=202 if path=='confirm' else 200)
