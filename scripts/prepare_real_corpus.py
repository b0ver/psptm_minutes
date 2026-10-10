"""Freeze real transcripts locally; never print meeting text or call an AI service."""
import argparse
import hashlib
import json
import os
from pathlib import Path
from datetime import datetime, timezone
from sqlalchemy import select, text
from app.db import Session, Meeting, RecordingImport


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def prepare(output, exposed_hashes, linked_groups):
    if output.exists():
        raise ValueError('output_already_exists')
    rows = []
    with Session() as db:
        db.execute(text('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY'))
        for meeting in db.scalars(select(Meeting).order_by(Meeting.created_at, Meeting.id)):
            if not meeting.transcript:
                continue
            receipt = db.scalar(select(RecordingImport).where(RecordingImport.meeting_id == meeting.id))
            segments = meeting.transcript
            rows.append({'meeting_id': meeting.id, 'title': meeting.title, 'segments': segments,
                         'status': meeting.status, 'input_hash': digest(segments),
                         'complete': receipt.complete if receipt else None,
                         'characters': sum(len(s.get('text', '')) for s in segments),
                         'end_seconds': max(s['end'] for s in segments)})
    parents = {r['meeting_id']: r['meeting_id'] for r in rows}
    def root(identifier):
        while parents[identifier] != identifier:
            identifier = parents[identifier]
        return identifier
    def union(a, b):
        parents[root(b)] = root(a)
    for group in linked_groups:
        members = [identifier for identifier in group if identifier in parents]
        for identifier in members[1:]:
            union(members[0], identifier)
    # Duplicate hashes are always one evaluation unit.
    for i, a in enumerate(rows):
        for b in rows[i+1:]:
            if a['input_hash'] == b['input_hash']:
                union(a['meeting_id'], b['meeting_id'])
    exposed_groups = {root(r['meeting_id']) for r in rows if r['input_hash'] in exposed_hashes}
    eligible = [r for r in rows if root(r['meeting_id']) not in exposed_groups
                and r['status'] == 'ready' and r['complete'] is not False and r['characters'] >= 5000]
    # Deterministic length-stratified provisional holdout; semantic review is still required.
    groups = {}
    for row in eligible:
        groups.setdefault(root(row['meeting_id']), row)
    ordered = sorted(groups, key=lambda g: (groups[g]['characters'], g))
    count = min(5, len(ordered))
    chosen = {ordered[round(i*(len(ordered)-1)/max(1,count-1))] for i in range(count)}
    for index, row in enumerate(rows, 1):
        row['case_id'] = f'M{index:02d}'
        row['group_id'] = root(row['meeting_id'])
        row['previously_exposed'] = row['group_id'] in exposed_groups
        row['split'] = ('holdout_provisional' if row['group_id'] in chosen else
                        'diagnostic' if row['status'] != 'ready' or row['complete'] is False or row['characters'] < 5000
                        else 'development')
    os.umask(0o077)
    output.mkdir(parents=True, mode=0o700)
    (output/'inputs.json').write_text(json.dumps(rows, ensure_ascii=False))
    manifest = {'frozen_at': datetime.now(timezone.utc).isoformat(), 'source': 'production_transcripts_read_only',
                'origin': 'real_meetings_user_confirmed', 'split_status': 'provisional_pending_semantic_duplicate_review',
                'input_file_sha256': hashlib.sha256((output/'inputs.json').read_bytes()).hexdigest(),
                'cases': [{k:v for k,v in r.items() if k not in {'title','segments'}} for r in rows]}
    (output/'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    labels = [{'case_id':r['case_id'], 'input_hash':r['input_hash'], 'review_status':'unreviewed',
               'decisions':[], 'actions':[], 'required_facts':[], 'unsupported_claims':[],
               'source_spans':[], 'reviewer':None} for r in rows]
    (output/'review-labels.json').write_text(json.dumps(labels, ensure_ascii=False, indent=2))
    return {'transcripts':len(rows), 'splits':{s:sum(r['split']==s for r in rows)
            for s in ['development','holdout_provisional','diagnostic']},
            'previously_exposed':sum(r['previously_exposed'] for r in rows),
            'failed_with_transcript':sum(r['status']!='ready' for r in rows),
            'partial':sum(r['complete'] is False for r in rows),
            'holdout_ready_for_scoring':False}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--exposed-hashes', default='[]')
    parser.add_argument('--linked-groups', default='[]')
    args = parser.parse_args()
    print(json.dumps(prepare(args.output, set(json.loads(args.exposed_hashes)), json.loads(args.linked_groups))))
