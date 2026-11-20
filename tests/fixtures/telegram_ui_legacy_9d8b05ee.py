"""Telegram control interface. Private chats; every resource operation is owner scoped.
Content exports require an explicit owner request in a private chat; ordinary views contain metadata only.
"""
from datetime import timedelta
import hashlib
import json
import os
import re
from uuid import uuid4, UUID

import httpx
from sqlalchemy import select, and_, exists, cast, Date, DateTime, func, literal

from app import reports
from app.db import (Session, Meeting, Action, Outbox, TelegramUser, TelegramResource,
                    TelegramDialog, TelegramEvent, TelegramCommand, TelegramCard, now)
from app.registry import locked_action, change_status

API = os.getenv('MINUTES_API_URL', 'http://api:8080').rstrip('/')
TERMINAL = {'uploaded', 'failed', 'cancelled', 'partial_saved', 'upload_uncertain'}
SESSION_LABELS = {'starting':'Запуск', 'opening':'Открываем Телемост', 'preparing':'Готовим вход',
    'waiting_host':'Ожидаем допуска — впустите «Ассистента протокола · запись»', 'joined':'Ассистент вошёл',
    'recording':'Идёт запись', 'stopping':'Останавливаем запись', 'finishing':'Сохраняем запись',
    'saved':'Файл сохранён', 'uploading':'Передаём на обработку', 'uploaded':'Файл принят приложением',
    'partial_saved':'Сохранена часть записи', 'upload_uncertain':'Запись сохранена; передачу нужно повторить',
    'failed':'Ошибка подключения или записи', 'cancelled':'Отменено'}
MEETING_LABELS={'queued':'В очереди на транскрибацию', 'processing':'Транскрибация', 'ready':'Транскрипт готов', 'failed':'Ошибка транскрибации'}
REPORT_LABELS={'queued':'в очереди','processing':'готовится','ready':'готов','failed':'ошибка'}


def button(text, data):
    assert len(data.encode()) <= 64
    return {'text':text, 'callback_data':data}


def keyboard(rows):
    return {'inline_keyboard':rows}


def menu():
    return keyboard([[button('Мои встречи','v1:meetings:0'),button('Текущая обработка','v1:status')],
        [button('Мои поручения','v1:tasks:open:0')], [button('Подключить к Телемосту','v1:new')],
        [button('Помощь','v1:help')]])


def owned(s, user, kind, rid):
    return s.scalar(select(TelegramResource).where(TelegramResource.user_id==user,
        TelegramResource.kind==kind, TelegramResource.resource_id==rid)) is not None


def dialog(s, user):
    d=s.get(TelegramDialog,user)
    if d and d.expires_at <= now():
        s.delete(d);s.flush();return None
    return d


def set_dialog(s, user, step, data):
    d=s.get(TelegramDialog,user)
    if not d:
        d=TelegramDialog(user_id=user);s.add(d)
    d.step,d.data,d.revision,d.expires_at=step,data,str(uuid4()),now()+timedelta(minutes=15)
    return d


def emit(s, user, key, text, markup=None, message_id=None, card=None, fingerprint=None, stage=None):
    if s.scalar(select(TelegramEvent.id).where(TelegramEvent.event_key==key)):
        return
    u=s.get(TelegramUser,user)
    payload={'chat_id':u.chat_id,'text':text[:4000]}
    if markup is not None:payload['reply_markup']=markup
    if message_id:payload['message_id']=message_id
    s.add(TelegramEvent(event_key=key,user_id=user,payload=payload,
        method='editMessageText' if message_id else 'sendMessage',card_id=card,card_fingerprint=fingerprint,card_stage=stage))


def meeting_scope(user):
    return exists().where(TelegramResource.kind=='meeting',TelegramResource.resource_id==Meeting.id,TelegramResource.user_id==user)


def action_scope(user):
    return exists().where(TelegramResource.kind=='meeting',TelegramResource.resource_id==Action.meeting_id,TelegramResource.user_id==user)


def meetings_view(s,user,page=0):
    items=list(s.scalars(select(Meeting).where(meeting_scope(user)).order_by(Meeting.created_at.desc(),Meeting.id).offset(page*5).limit(6)))
    rows=[[button(m.title[:50],f'v1:m:{m.id}')] for m in items[:5]]
    navigation=[]
    if page:navigation.append(button('Назад',f'v1:meetings:{page-1}'))
    if len(items)>5:navigation.append(button('Далее',f'v1:meetings:{page+1}'))
    if navigation:rows.append(navigation)
    rows.append([button('Главное меню','v1:menu')])
    return ('Мои встречи' if items else 'У вас пока нет обработанных встреч. Отправьте ссылку Телемоста, чтобы начать.'),keyboard(rows)


def meeting_view(s,user,rid):
    if not owned(s,user,'meeting',rid):return 'Встреча недоступна.',menu()
    m=s.get(Meeting,rid)
    if not m:return 'Встреча недоступна.',menu()
    analysis=reports.state(s,rid)
    lines=[m.title,MEETING_LABELS.get(m.status,'Обработка'),'', 'Разборы:']
    lines += [f"• {r['name']}: {REPORT_LABELS.get(r['status'],'ожидание')}" for r in analysis['reports']]
    lines.append('\nMarkdown можно скачать по разделам в личном чате. Файл будет передан через Telegram.')
    from app.db import RecordingImport
    receipt=s.scalar(select(RecordingImport).where(RecordingImport.meeting_id==rid))
    if receipt and not receipt.complete:lines.insert(2,'Неполная запись: выводы относятся только к сохранённой части.')
    return '\n'.join(lines),keyboard([[button('Выбрать разборы',f'v1:types:{rid}')],
        [button('Скачать Markdown',f'v1:files:{rid}')],
        [button('Обновить',f'v1:m:{rid}'),button('Мои встречи','v1:meetings:0')]])


def tasks_view(s,user,mode,page):
    query=select(Action).where(action_scope(user),Action.status.in_(['approved','in_progress','done']))
    if mode=='open':query=query.where(Action.status.in_(['approved','in_progress']))
    elif mode=='overdue':query=query.where(Action.status!='done',Action.due_on < cast(func.timezone(Action.timezone,literal(now(),type_=DateTime(timezone=True))),Date))
    elif mode=='without_due':query=query.where(Action.status!='done',Action.due_on.is_(None))
    elif mode=='done':query=query.where(Action.status=='done')
    else:return 'Неизвестный фильтр.',menu()
    items=list(s.scalars(query.order_by(Action.id).offset(page*4).limit(5)))
    lines=['Мои утверждённые поручения']
    rows=[]
    for i,a in enumerate(items[:4],1):
        lines += [f'\n{i}. {a.text[:450]}',f"Ответственный: {a.owner or 'не указан'} · Срок: {a.due_on or 'не указан'} ({a.timezone})",f"Статус: {a.status}"]
        if a.status!='done':rows.append([button(f'{i}: В работе',f'p:{a.id}:{a.version}'),button(f'{i}: Выполнено',f'd:{a.id}:{a.version}')])
    if not items:lines.append('Поручений по этому фильтру нет. Черновики утверждаются в локальном приложении.')
    nav=[]
    if page:nav.append(button('Назад',f'v1:tasks:{mode}:{page-1}'))
    if len(items)>4:nav.append(button('Далее',f'v1:tasks:{mode}:{page+1}'))
    if nav:rows.append(nav)
    rows += [[button('Открытые','v1:tasks:open:0'),button('Выполненные','v1:tasks:done:0')],
        [button('Просроченные','v1:tasks:overdue:0'),button('Без срока','v1:tasks:without_due:0')],
        [button('Главное меню','v1:menu')]]
    return '\n'.join(lines),keyboard(rows)


def recording_form(d):
    names={r['id']:r['name'] for r in reports.catalog()}
    data=d.data
    text=(f"Телемост\nНазвание: {data['title']}\nДлительность: до {data['duration_minutes']} минут\n"
          'Разборы: '+', '.join(names[t] for t in data['types'])+'\n\n'
          'Ассистент будет видимым участником с выключенными камерой и микрофоном. '
          'Запуск подтверждает, что организатор и участники разрешили запись. '
          'Аудио и AI-обработка остаются на сервере.')
    return text,keyboard([[button('Разрешение получено — начать',f'v1:launch:{d.revision}')],
        [button('Название',f'v1:title:{d.revision}'),button('Длительность',f'v1:duration:{d.revision}')],
        [button('Типы разборов',f'v1:choose:{d.revision}:0')],[button('Отмена','v1:cancel')]])


def types_view(d,page=0):
    items=reports.catalog();rows=[]
    for item in items[page*6:page*6+6]:
        mark='✓ ' if item['id'] in d.data['types'] else '○ '
        rows.append([button(mark+item['name'],f"v1:pick:{d.revision}:{items.index(item)}")])
    nav=[]
    if page:nav.append(button('Назад',f'v1:choose:{d.revision}:{page-1}'))
    if len(items)>(page+1)*6:nav.append(button('Далее',f'v1:choose:{d.revision}:{page+1}'))
    if nav:rows.append(nav)
    rows += [[button('Готово',f'v1:chosen:{d.revision}')],[button('Отмена','v1:cancel')]]
    return 'Выберите типы разборов. Для запуска нужен хотя бы один.',keyboard(rows)


def handle(s,update,allow_all,allowed_users):
    callback=update.get('callback_query');message=callback.get('message') if callback else update.get('message')
    source=(callback or message or {}).get('from',{})
    if not message or source.get('is_bot'):return None
    chat=message.get('chat',{});user=str(source.get('id',''))
    if not user.isdigit() or chat.get('type')!='private':
        return (callback['id'],'Используйте личный чат с ботом.') if callback else None
    if not allow_all and user not in allowed_users:
        return (callback['id'],'Нет доступа') if callback else None
    u=s.get(TelegramUser,user)
    if not u:u=TelegramUser(user_id=user,chat_id=str(chat['id']),active=True);s.add(u);s.flush()
    if u.chat_id!=str(chat['id']):return (callback['id'],'Недоступный чат') if callback else None
    u.active=True
    key='update:'+str(update['update_id']);d=dialog(s,user)
    def reply(text,markup=None):
        emit(s,user,key,text,markup, message.get('message_id') if callback else None)
    def reset():
        if d:s.delete(d);s.flush()
    if not callback:
        text=(message.get('text') or '').strip()
        command=text.split()[0].split('@')[0] if text else ''
        if command in {'/start','/menu','/cancel'}:
            reset();reply('Протоколы и поручения\nВы видите только свои встречи. Отправьте ссылку Телемоста или выберите действие.',menu());return None
        if command=='/meetings':reply(*meetings_view(s,user));return None
        if command=='/tasks':reply(*tasks_view(s,user,'open',0));return None
        if command=='/status':reply(*status_view(s,user));return None
        if command in {'/telemost','/new'}:
            set_dialog(s,user,'link',{});reply('Отправьте прямую ссылку https://telemost.yandex.ru/j/…',keyboard([[button('Отмена','v1:cancel')]]));return None
        if command=='/help':reply(help_text(),menu());return None
        if message.get('document') or message.get('audio') or message.get('voice') or message.get('video'):
            reply('Записи загружаются через локальное приложение. Бот принимает ссылки Телемоста и команды управления.',menu());return None
        if d and d.step=='title':
            if not text or len(text)>200:reply('Введите название длиной от 1 до 200 символов.');return None
            d=set_dialog(s,user,'record',{**d.data,'title':text,'title_from_telemost':False});reply(*recording_form(d));return None
        if re.fullmatch(r'https://telemost\.yandex\.(ru|com)/j/[A-Za-z0-9_-]{8,128}',text):
            d=set_dialog(s,user,'record',{'url':text,'title':'Название из Телемоста','title_from_telemost':True,'duration_minutes':60,'types':list(reports.DEFAULT_TYPES)})
            reply(*recording_form(d));return None
        reply('Отправьте прямую ссылку Телемоста или выберите действие. '+('Диалог завершён.' if d is None else ''),menu());return None
    data=callback.get('data','');parts=data.split(':')
    try:
        if parts[0] in {'p','d'} and len(parts)==3:
            a=locked_action(s,parts[1])
            if not a or not owned(s,user,'meeting',a.meeting_id):raise ValueError('access')
            change_status(s,a,int(parts[2]),'in_progress' if parts[0]=='p' else 'done','telegram:'+user)
            reply(*tasks_view(s,user,'open',0));return callback['id'],'Статус сохранён'
        if len(parts)<2 or parts[0]!='v1':raise ValueError('command')
        op=parts[1]
        if op in {'menu','cancel'}:reset();reply('Главное меню',menu())
        elif op=='help':reply(help_text(),menu())
        elif op=='new':set_dialog(s,user,'link',{});reply('Отправьте прямую ссылку Телемоста.',keyboard([[button('Отмена','v1:cancel')]]))
        elif op=='meetings':reply(*meetings_view(s,user,page_number(parts[2])))
        elif op=='tasks':reply(*tasks_view(s,user,parts[2],page_number(parts[3])))
        elif op=='status':reply(*status_view(s,user))
        elif op=='session':
            sid=str(UUID(parts[2]))
            if not owned(s,user,'session',sid):raise ValueError('access')
            c=s.get(TelegramCard,sid)
            reply(*card_view(s,user,c.snapshot if c and c.snapshot else {'id':sid,'status':'starting'}))
        elif op=='m':
            rid=str(UUID(parts[2]))
            if not owned(s,user,'meeting',rid):raise ValueError('access')
            reply(*meeting_view(s,user,rid))
        elif op in {'files','mdt','mda','mdr'}:
            from app.db import MeetingReport
            rid=str(UUID(parts[2]))
            report=s.get(MeetingReport,rid) if op=='mdr' else None
            mid=report.meeting_id if report else rid
            if op=='mdr' and not report:raise ValueError('export')
            if not owned(s,user,'meeting',mid):raise ValueError('access')
            if op=='files':
                m=s.get(Meeting,mid);rows=[]
                if m and m.transcript:rows.append([button('Транскрипт .md',f'v1:mdt:{mid}')])
                ready=list(s.scalars(select(MeetingReport).where(MeetingReport.meeting_id==mid,MeetingReport.status=='ready',MeetingReport.content.is_not(None)).order_by(MeetingReport.template_id)))
                rows += [[button(reports.CATALOG[r.template_id]['name']+' .md',f'v1:mdr:{r.id}')] for r in ready]
                if rows:rows.append([button('Все готовые разделы .md',f'v1:mda:{mid}')])
                rows.append([button('Назад к встрече',f'v1:m:{mid}')])
                reply('Выберите раздел для скачивания. Файл будет передан через Telegram; отправляйте его только нужным получателям.' if len(rows)>1 else 'Готовых разделов пока нет.',keyboard(rows))
            else:
                section=rid if op=='mdr' else 'transcript' if op=='mdt' else 'all'
                from app.telegram_exports import document
                document(s,user,mid,section)  # Check readiness before enqueueing; recheck ownership at delivery.
                s.add(TelegramEvent(event_key='export:'+str(update['update_id']),user_id=user,method='sendDocument',payload={'chat_id':u.chat_id,'meeting_id':mid,'section':section}))
                return callback['id'],'Файл готовится к отправке'
        elif op=='types':
            rid=str(UUID(parts[2]))
            if not owned(s,user,'meeting',rid):raise ValueError('access')
            d=set_dialog(s,user,'reports',{'meeting_id':rid,'types':list(reports.DEFAULT_TYPES)})
            reply(*types_view(d))
        elif op in {'stop','stop_yes','retry','retry_yes'}:
            sid=str(UUID(parts[2]))
            if not owned(s,user,'session',sid):raise ValueError('access')
            if op in {'stop','retry'}:
                title='Остановить запись и обработать сохранённое?' if op=='stop' else 'Передать сохранённый файл? Если запись неполная, выводы будут относиться только к сохранённой части.'
                reply(title,keyboard([[button('Подтвердить',f'v1:{op}_yes:{sid}')],[button('Назад','v1:status')]]))
            else:
                if op=='stop_yes':
                    start_cmd=s.scalar(select(TelegramCommand).where(TelegramCommand.id==sid,TelegramCommand.user_id==user).with_for_update())
                    if start_cmd and start_cmd.status=='pending':start_cmd.status='cancelled'
                s.add(TelegramCommand(update_id=update['update_id'],user_id=user,kind='stop' if op=='stop_yes' else 'import',body={'session_id':sid}))
                reply('Команда принята. Статус обновится в карточке.',menu())
        elif op in {'launch','title','duration','minutes','choose','pick','chosen'}:
            if not d or d.revision!=parts[2] or d.step not in {'record','title','duration','reports'}:raise ValueError('expired')
            if op=='launch':
                if d.step!='record':raise ValueError('step')
                types=reports.selected_types(d.data['types']);cid=str(uuid4())
                s.add(TelegramCommand(id=cid,update_id=update['update_id'],user_id=user,kind='start',body={'url':d.data['url'],'title':d.data['title'],'title_from_telemost':d.data.get('title_from_telemost',True),'duration_minutes':d.data['duration_minutes'],'analysis_types':types,'recording_authorized':True,'command_key':cid}))
                s.add(TelegramResource(kind='session',resource_id=cid,user_id=user));s.add(TelegramCard(session_id=cid,user_id=user))
                reset();reply('Запуск поставлен в очередь. Карточка появится после ответа агента.',menu())
            elif op=='title':
                if 'url' not in d.data:raise ValueError('step')
                d.step='title';reply('Напишите название встречи.',keyboard([[button('Отмена','v1:cancel')]]))
            elif op=='duration':
                if 'url' not in d.data:raise ValueError('step')
                d.step='duration';reply('Максимальная длительность записи:',keyboard([[button(f'{n} минут',f'v1:minutes:{d.revision}:{n}')] for n in (15,30,60,90,120)]+[[button('Отмена','v1:cancel')]]))
            elif op=='minutes':
                n=int(parts[3])
                if d.step!='duration' or n not in {15,30,60,90,120}:raise ValueError('minutes')
                d.data={**d.data,'duration_minutes':n};d.step='record';reply(*recording_form(d))
            elif op=='choose':reply(*types_view(d,page_number(parts[3])))
            elif op=='pick':
                index=int(parts[3]);items=reports.catalog()
                if not 0<=index<len(items):raise ValueError('type')
                tid=items[index]['id'];types=list(d.data['types'])
                types.remove(tid) if tid in types else types.append(tid)
                d.data={**d.data,'types':types};reply(*types_view(d,index//6))
            elif op=='chosen':
                types=reports.selected_types(d.data['types'])
                if d.step=='reports':
                    rid=d.data['meeting_id']
                    if not owned(s,user,'meeting',rid):raise ValueError('access')
                    m=s.scalar(select(Meeting).where(Meeting.id==rid).with_for_update())
                    if not m or not m.transcript:reply('Дождитесь готовности транскрипта.',keyboard([[button('Обновить',f'v1:m:{rid}')]]));return callback['id'],'Транскрипт ещё не готов'
                    reports.enqueue(s,m,types)
                    for card in s.scalars(select(TelegramCard).where(TelegramCard.user_id==user,TelegramCard.snapshot['meeting_id'].as_string()==rid)):
                        card.settled=False
                    reset();reply('Выбранные разборы поставлены в очередь.',keyboard([[button('Статус',f'v1:m:{rid}')]]))
                else:d.step='record';reply(*recording_form(d))
        else:raise ValueError('command')
    except (ValueError,IndexError,KeyError):
        return callback['id'],'Кнопка недоступна или устарела. Откройте /menu.'
    return callback['id'],'Готово'


def page_number(value):
    number=int(value)
    if not 0<=number<=1000:raise ValueError('page')
    return number


def help_text():
    return ('Отправьте ссылку Телемоста, выберите название, длительность и разборы, затем подтвердите разрешение записи. '
        'Ассистента нужно впустить во встречу. Один ассистент обслуживает одну встречу одновременно.\n\n'
        'Вход открыт всем, но каждый видит только свои материалы. '
        'Бот показывает статусы и утверждённые поручения; по вашей команде отправляет Markdown своей встречи по разделам. Эти файлы передаются через Telegram. Исходные записи и AI-обработка остаются локальными. '
        'Кнопки управления не запускают отправку материалов другим пользователям.\n\n/menu — меню, /cancel — отменить незавершённый диалог.')


def status_view(s,user):
    rows=list(s.scalars(select(TelegramResource).where(TelegramResource.user_id==user,TelegramResource.kind=='session').order_by(TelegramResource.created_at.desc()).limit(5)))
    buttons=[[button('Сессия '+r.resource_id[:8],f'v1:session:{r.resource_id}')] for r in rows]
    # Status snapshots are rendered by the background loop, never fetched during a DB callback transaction.
    buttons += [[button('Мои встречи','v1:meetings:0'),button('Главное меню','v1:menu')]]
    return 'Ваши сессии записи. Живые карточки обновляются автоматически.' if rows else 'Активных сессий пока нет.',keyboard(buttons)


def card_view(s,user,state):
    sid=state['id'];status=state['status'];rows=[]
    lines=[state.get('title') or 'Встреча из Telegram',SESSION_LABELS.get(status,'Обработка')]
    if status=='recording' and state.get('recording_started_at'):
        lines.append(f"Длительность: {max(0,int((now().timestamp()-state['recording_started_at'])/60))} мин.")
    if state.get('chunks'):lines.append(f"На диске: {state['chunks']} частей аудио")
    if state.get('recording_complete') is False:lines.append('Неполная запись: часть встречи отсутствует.')
    if state.get('capture_health') in {'failed','delayed','unavailable'}:
        lines.append('Аварийное сохранение недоступно или задерживается; состояние штатной записи показано выше.')
    if status=='failed':
        errors={'agent_busy':'Ассистент занят другой встречей. Попробуйте позже.',
                'waiting_room_timeout':'Не дождались допуска. Проверьте встречу и подключите ассистента заново.',
                'import_key_conflict':'Параметры повторного импорта отличаются. Нужна проверка в приложении.',
                'start_result_unknown':'Результат запуска не подтверждён. Проверяем прежнюю сессию; не создавайте новый запуск до проверки.'}
        lines.append(errors.get(state.get('error_code'),'Подключение или запись прерваны. Проверьте встречу и попробуйте новый запуск.'))
    if status not in TERMINAL and status not in {'saved','uploading','finishing','stopping'}:
        rows.append([button('Завершить и обработать',f'v1:stop:{sid}')])
    if status in {'partial_saved','upload_uncertain'}:
        rows.append([button('Обработать сохранённую часть' if status=='partial_saved' else 'Повторить передачу',f'v1:retry:{sid}')])
    mid=state.get('meeting_id')
    if mid and owned(s,user,'meeting',mid):
        m=s.get(Meeting,mid)
        if m:
            lines.append(MEETING_LABELS.get(m.status,'Обработка'))
            analysis=reports.state(s,mid)
            lines.append(f"Разборы: {analysis['completed']}/{analysis['total']} · {REPORT_LABELS.get(analysis['status'],'ожидание')}")
            rows.append([button('Встреча и разборы',f'v1:m:{mid}')])
    rows.append([button('Главное меню','v1:menu')])
    return '\n'.join(lines),keyboard(rows)


def command_one(client):
    with Session.begin() as s:
        cmd=s.scalar(select(TelegramCommand).where(TelegramCommand.status=='pending',TelegramCommand.next_attempt<=now()).order_by(TelegramCommand.next_attempt).with_for_update(skip_locked=True).limit(1))
        if not cmd:return False
        user=s.get(TelegramUser,cmd.user_id)
        if not user or not user.active:cmd.status='cancelled';return True
        sid=cmd.id if cmd.kind=='start' else cmd.body['session_id']
        if not owned(s,cmd.user_id,'session',sid):cmd.status='failed';return True
        cmd.attempts+=1
        try:
            if cmd.kind=='start':
                response=client.get(API+'/api/telemost/sessions/'+sid)
                if response.status_code==404:
                    response=client.post(API+'/api/telemost/sessions',json=cmd.body)
            else:
                response=client.post(API+'/api/telemost/sessions/'+sid+('/stop' if cmd.kind=='stop' else '/import'),json={})
            if cmd.kind=='stop' and response.status_code==404:
                state={'id':sid,'status':'cancelled'}
            else:
                response.raise_for_status();state=response.json()
            if state.get('id')!=sid:raise ValueError('session_mismatch')
            cmd.status='done'
            card=s.get(TelegramCard,sid)
            if card:card.snapshot=state
        except httpx.HTTPStatusError as error:
            if 400<=error.response.status_code<500 and error.response.status_code!=429:
                cmd.status='failed'
                if cmd.kind=='start':
                    card=s.get(TelegramCard,sid)
                    card.snapshot={'id':sid,'status':'failed','error_code':'agent_busy' if error.response.status_code==409 else 'start_rejected'}
                emit(s,cmd.user_id,'command-error:'+cmd.id,'Команда не выполнена. Ассистент может быть занят или состояние записи уже изменилось.',menu())
            else:command_retry(s,cmd)
        except (httpx.RequestError,ValueError,KeyError):command_retry(s,cmd)
        return True


def command_retry(s,cmd):
    # Continue reconciliation of an uncertain start; never issue a new UUID automatically.
    cmd.next_attempt=now()+timedelta(seconds=min(120,5*2**min(cmd.attempts,5)))
    if cmd.attempts==3:
        emit(s,cmd.user_id,'command-delayed:'+cmd.id,'Ответ сервиса задерживается. Проверяем результат прежней команды; новая встреча не создаётся.',menu())
    if cmd.attempts>=10:
        cmd.status='failed'
        card=s.get(TelegramCard,cmd.id)
        if card and cmd.kind=='start':card.snapshot={'id':cmd.id,'status':'failed','error_code':'start_result_unknown'}
        emit(s,cmd.user_id,'command-unknown:'+cmd.id,'Результат команды пока не подтверждён. Проверьте состояние сервиса в приложении.',menu())


def watch_cards(client):
    with Session() as s:
        ids=list(s.scalars(select(TelegramCard.session_id).join(TelegramUser).where(TelegramUser.active.is_(True),TelegramCard.settled.is_(False)).limit(500)))
    for sid in ids:
        with Session() as s:
            card=s.get(TelegramCard,sid);old=card.snapshot or {}
            if old.get('status') in TERMINAL and old.get('error_code')!='start_result_unknown':state=old
            else:
                try:
                    response=client.get(API+'/api/telemost/sessions/'+sid)
                    if response.status_code!=200:continue
                    state=response.json()
                    if state.get('id')!=sid:continue
                except (httpx.RequestError,ValueError,KeyError):continue
        with Session.begin() as s:
            card=s.scalar(select(TelegramCard).where(TelegramCard.session_id==sid).with_for_update())
            if not card or not owned(s,card.user_id,'session',sid):continue
            card.snapshot=state
            mid=state.get('meeting_id')
            if mid and s.get(Meeting,mid):
                resource=s.get(TelegramResource,('meeting',mid))
                if resource and resource.user_id!=card.user_id:continue
                if not resource:s.add(TelegramResource(kind='meeting',resource_id=mid,user_id=card.user_id));s.flush()
            text,markup=card_view(s,card.user_id,state)
            fp=hashlib.sha256(json.dumps([text,markup],ensure_ascii=False).encode()).hexdigest()
            m=s.get(Meeting,mid) if mid else None
            analysis=reports.state(s,mid) if m else None
            stage=json.dumps([state['status'],state.get('capture_health'),m.status if m else None,analysis['status'] if analysis else None])
            pending=s.scalar(select(TelegramEvent.id).where(TelegramEvent.card_id==sid,TelegramEvent.status=='pending'))
            if not pending and fp!=card.fingerprint and (stage!=card.stage or (now()-card.updated_at).total_seconds()>=60):
                key=f'card:{sid}:{int(card.updated_at.timestamp()*1000000)}:{fp[:16]}'
                emit(s,card.user_id,key,text,markup,card.message_id,sid,fp,stage)
            if m and m.status in {'ready','failed'}:
                emit(s,card.user_id,f'transcript:{mid}:{m.status}',
                    'Транскрипт вашей встречи готов. Разборы могут ещё обрабатываться.' if m.status=='ready' else 'Ошибка транскрибации вашей встречи. Проверьте запись в приложении.',
                    keyboard([[button('Статус встречи',f'v1:m:{mid}')]]))
            if analysis and analysis['status'] in {'ready','failed'}:
                emit(s,card.user_id,f'analysis:{mid}:{analysis["status"]}:{hashlib.sha256(json.dumps([r["id"] for r in analysis["reports"]]).encode()).hexdigest()[:12]}',
                    'Разборы вашей встречи готовы в локальном приложении.' if analysis['status']=='ready' else 'Часть разборов не завершилась. Проверьте статусы выбранных типов.',
                    keyboard([[button('Статус разборов',f'v1:m:{mid}')]]))

            if not pending and fp==card.fingerprint and state['status'] in TERMINAL and state.get('error_code')!='start_result_unknown':
                if not mid or (m and (m.status=='failed' or (analysis and analysis['status'] in {'ready','failed'}))):
                    card.settled=True
