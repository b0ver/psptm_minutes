"""User-requested Markdown exports, checked against ownership again at delivery."""
from sqlalchemy import select
from app.db import Meeting,MeetingReport,RecordingImport,TelegramResource
from app import reports


def document(s,user,meeting_id,section):
    owner=s.get(TelegramResource,('meeting',meeting_id))
    if not owner or owner.user_id!=user:raise ValueError('export_unavailable')
    meeting=s.get(Meeting,meeting_id)
    if not meeting:raise ValueError('export_unavailable')
    title=meeting.title.replace('\r',' ').replace('\n',' ')
    pieces=[f'# {title}',f'\nID встречи: {meeting.id}']
    from app.meeting_context import data,annotate,roster
    context=data(s,meeting.id)
    if context.get('participants'):
        pieces+=['\n## Участники Телемоста и наблюдаемые роли',roster(context),'\n> Привязка реплик по активной плитке предварительная, не идентификация голоса. Должности и бизнес-роли не определены.']
    receipt=s.scalar(select(RecordingImport).where(RecordingImport.meeting_id==meeting.id))
    if receipt and not receipt.complete:pieces.append('\n> Неполная запись: выводы относятся только к сохранённой части.')
    chosen=[]
    if section in {'transcript','all'}:
        if section=='transcript' and not meeting.transcript:raise ValueError('export_not_ready')
        if meeting.transcript:
            segments=annotate(meeting.transcript,context)
            pieces += ['\n## Транскрипт','\n'.join(f'[{reports.timestamp(x["start"])}] '+(f'[{x["speaker_hint"]} — вероятный спикер] ' if x.get('speaker_hint') else '')+x['text'] for x in segments)]
    if section=='all':
        chosen=list(s.scalars(select(MeetingReport).where(MeetingReport.meeting_id==meeting.id,MeetingReport.status=='ready',MeetingReport.content.is_not(None)).order_by(MeetingReport.template_id)))
        if not chosen and not meeting.transcript:raise ValueError('export_not_ready')
        pieces.append('\n> В файл включены только готовые разделы на момент скачивания.')
    elif section!='transcript':
        report=s.get(MeetingReport,section)
        if not report or report.meeting_id!=meeting.id or report.status!='ready' or not report.content:raise ValueError('export_not_ready')
        chosen=[report]
    for report in chosen:
        pieces += ['\n## '+reports.CATALOG.get(report.template_id,{}).get('name',report.template_id),report.content]
    content=('\n\n'.join(pieces)+'\n').encode('utf-8')
    if len(content)>45*1024*1024:raise ValueError('export_too_large')
    suffix=section if section in {'all','transcript'} else chosen[0].template_id
    return f'meeting-{meeting.id}-{suffix}.md',content
