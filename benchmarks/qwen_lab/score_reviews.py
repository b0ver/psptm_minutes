"""Score adjudicated source reviews, not model self-ratings. Output contains no text."""
import argparse
import json
from pathlib import Path

VERDICTS={'supported','unsupported','contradicted','uncertain'}
CRITICAL={'decision','action','assignee','deadline','amount'}

def score(review, source=None):
    expected=review.get('expected',[]);claims=review.get('claims',[])
    known={x['id'] for x in expected}
    if len(known)!=len(expected):raise ValueError('duplicate_expected_id')
    matched=set();counts={v:0 for v in VERDICTS};critical_errors=0;pending=0
    source_lines={f'L{i+1:05d}':row['text'] for i,row in enumerate(source or [])}
    for claim in claims:
        verdict=claim['verdict']
        if verdict not in VERDICTS:raise ValueError('unknown_verdict')
        ids=set(claim.get('matched_expected_ids',[]))
        if not ids<=known:raise ValueError('unknown_expected_id')
        counts[verdict]+=1
        if verdict=='supported':
            if not claim.get('source_spans'):raise ValueError('supported_claim_without_source')
            if source is not None:
                for span in claim['source_spans']:
                    if not isinstance(span,dict) or not span.get('quote') or span.get('segment_id') not in source_lines or span['quote'] not in source_lines[span['segment_id']]:
                        raise ValueError('source_span_not_exact')
            matched.update(ids)
        elif verdict in {'unsupported','contradicted'}:
            critical_errors+=int(claim['category'] in CRITICAL)
        else:pending+=1
    reviewed=counts['supported']+counts['unsupported']+counts['contradicted']
    final=(review.get('gold_status')=='adjudicated' and review.get('review_status')=='adjudicated' and pending==0 and source is not None)
    precision=counts['supported']/reviewed if reviewed else None
    recall=len(matched)/len(known) if known else None
    return {'case_id':review['case_id'],'template':review['template'],'profile':review['profile'],
            'gold_items':len(known),'matched_gold_items':len(matched),'claims_reviewed':reviewed,
            'supported':counts['supported'],'unsupported':counts['unsupported'],'contradicted':counts['contradicted'],
            'critical_errors':critical_errors,'pending_claims':pending,'precision':precision,'recall':recall,
            'quality_scores':review.get('quality_scores'),'eligible_for_selection':final,
            'passes_factual_gate':final and critical_errors==0 and (precision is None or precision>=0.95)
              and (recall is None or recall>=0.90),
            'note':'No-claim/no-gold denominators stay null; source spans require independent source validation.'}

def main():
    p=argparse.ArgumentParser();p.add_argument('reviews',type=Path);p.add_argument('--inputs',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    values=json.loads(a.reviews.read_text());inputs=json.loads(a.inputs.read_text())
    if isinstance(inputs,dict):inputs=[inputs]
    sources={x.get('case_id',x.get('id')):x['segments'] for x in inputs}
    rows=[score(x,sources.get(x['case_id'])) for x in values]
    a.output.write_text(json.dumps({'rows':rows,'quality_winner':None,'adjudicated_reports':sum(x['eligible_for_selection'] for x in rows)},indent=2))
if __name__=='__main__':main()
