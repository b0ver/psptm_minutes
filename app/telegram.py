from datetime import timedelta
import logging
import os
import time
import threading
from pathlib import Path

import httpx
from sqlalchemy import select, text

from app.db import Session, Outbox, BotCursor, engine, now
from app.registry import locked_action, change_status
from app.settings import secret

log = logging.getLogger("minutes.telegram")
DISPATCHER_LOCK = 90601010  # STT: 90601008; analysis: 90601009.


class DeliveryError(Exception):
    def __init__(self, code='telegram_unavailable', retry_after=0):
        super().__init__(code)
        self.code, self.retry_after = code, retry_after



class Telegram:
    def __init__(self, token):
        self.rate_lock = threading.Lock()
        self.last_send = {}
        self.client = httpx.Client(base_url=f"https://api.telegram.org/bot{token}/", timeout=35, trust_env=False, follow_redirects=False)

    def call(self, method, payload):
        if method in {'sendMessage','sendDocument','editMessageText','editMessageReplyMarkup'}:
            chat = str(payload.get('chat_id',''))
            with self.rate_lock:
                scheduled = max(time.monotonic(), self.last_send.get(chat,0)+1.1)
                self.last_send[chat] = scheduled
            time.sleep(max(0, scheduled-time.monotonic()))
        try:
            if method=='sendDocument':
                data=dict(payload);filename,content=data.pop('_document')
                r=self.client.post(method,data=data,files={'document':(filename,content,'text/markdown; charset=utf-8')})
            else:r = self.client.post(method, json=payload)
            result = r.json()
            if not result.get('ok'):
                code = result.get('error_code', r.status_code)
                description = str(result.get('description','')).lower()
                if code == 409:raise DeliveryError('telegram_poll_conflict')
                if code == 401:raise DeliveryError('telegram_unauthorized')
                if code == 429:
                    raise DeliveryError('rate_limited', min(3600,max(1,int(result.get('parameters',{}).get('retry_after',30)))))
                if code == 403:raise DeliveryError('bot_blocked')
                if 'message is not modified' in description: return True
                if method.startswith('edit') and ('message to edit not found' in description or "message can't be edited" in description):
                    raise DeliveryError('message_unavailable')
                raise DeliveryError('telegram_rejected')
            r.raise_for_status()
            return result['result']
        except (httpx.HTTPError, ValueError, KeyError):
            raise DeliveryError('telegram_unavailable') from None


def dispatch_one(bot, chat_id):
    # Hold row lock across the request. A crash after send can still cause a duplicate.
    with Session.begin() as s:
        o = s.scalar(select(Outbox).where(Outbox.status == "pending", Outbox.next_attempt <= now()).order_by(Outbox.id).with_for_update(skip_locked=True).limit(1))
        if not o:
            return False
        o.attempts += 1
        try:
            result = bot.call("sendMessage", {"chat_id": chat_id, "text": o.payload,
                "reply_markup": {"inline_keyboard": [[
                    {"text": "В работе", "callback_data": f"p:{o.action_id}:{o.version}"},
                    {"text": "Выполнено", "callback_data": f"d:{o.action_id}:{o.version}"},
                ]]}})
            o.message_id, o.status, o.error_code = result["message_id"], "sent", None
        except (DeliveryError, KeyError):
            o.error_code = "telegram_unavailable"
            o.status = "failed" if o.attempts >= 5 else "pending"
            o.next_attempt = now() + timedelta(seconds=min(300, 10 * 2 ** o.attempts))
    return True


def handle_update(s, update, chat_id, allowed_users):
    callback = update.get("callback_query")
    if not callback:
        return None
    user = callback.get("from", {}).get("id")
    chat = callback.get("message", {}).get("chat", {}).get("id")
    if str(chat) != str(chat_id) or str(user) not in allowed_users:
        return (callback["id"], "Нет доступа")
    pieces = callback.get("data", "").split(":")
    if len(pieces) != 3 or pieces[0] not in {"p", "d"} or not pieces[2].isdigit():
        return (callback["id"], "Неверная команда")
    action = locked_action(s, pieces[1])
    if not action:
        return (callback["id"], "Поручение не найдено")
    try:
        change_status(s, action, int(pieces[2]), "in_progress" if pieces[0] == "p" else "done", "telegram:" + str(user))
    except ValueError:
        return (callback["id"], "Статус уже изменён или версия устарела")
    return (callback["id"], "Статус сохранён в реестре")


def poll(bot, chat_id, allowed_users):
    with Session() as s:
        cursor = s.get(BotCursor, 1)
        offset = cursor.offset if cursor else 0
    updates = bot.call("getUpdates", {"offset": offset, "timeout": 20, "allowed_updates": ["callback_query"]})
    for update in updates:
        with Session.begin() as s:
            cursor = s.get(BotCursor, 1)
            if not cursor:
                cursor = BotCursor(id=1, offset=0)
                s.add(cursor)
            if update["update_id"] < cursor.offset:
                continue
            result = handle_update(s, update, chat_id, allowed_users)
            cursor.offset = update["update_id"] + 1
        if result:
            bot.call("answerCallbackQuery", {"callback_query_id": result[0], "text": result[1]})



def telegram_token():
    value=os.getenv('TELEGRAM_TOKEN','').strip()
    path=os.getenv('TELEGRAM_TOKEN_FILE')
    file_value=Path(path).read_text().strip() if path and Path(path).is_file() else ''
    if file_value=='DISABLED':file_value=''
    if value and file_value and value!=file_value:
        raise RuntimeError('telegram_token_sources_conflict')
    token=value or file_value
    if not token:raise RuntimeError('telegram_token_missing')
    return token


def poll_interface(bot,allow_all,allowed_users):
    from app.telegram_ui import handle
    from app.telegram_stats import observe,record_private_usage
    with Session() as s:
        cursor=s.get(BotCursor,1);offset=cursor.offset if cursor else 0
    updates=bot.call('getUpdates',{'offset':offset,'timeout':20,'allowed_updates':['message','callback_query','my_chat_member']})
    for update in updates:
        with Session.begin() as s:
            cursor=s.get(BotCursor,1)
            if not cursor:cursor=BotCursor(id=1,offset=0);s.add(cursor)
            if update['update_id']<cursor.offset:continue
            is_group,answer=observe(s,update)
            membership=update.get('my_chat_member')
            if is_group:pass
            elif membership:
                from app.db import TelegramUser
                chat=membership.get('chat',{})
                if chat.get('type')=='private':
                    u=s.scalar(select(TelegramUser).where(TelegramUser.chat_id==str(chat.get('id'))))
                    if u and membership.get('new_chat_member',{}).get('status') in {'kicked','left'}:u.active=False
                answer=None
            else:
                record_private_usage(s,update)
                answer=handle(s,update,allow_all,allowed_users)
            cursor.offset=update['update_id']+1
        if answer:
            try:bot.call('answerCallbackQuery',{'callback_query_id':answer[0],'text':answer[1]})
            except DeliveryError:pass


def dispatch_interface(bot):
    from app.db import TelegramEvent,TelegramUser,TelegramCard
    with Session.begin() as s:
        e=s.scalar(select(TelegramEvent).where(TelegramEvent.status=='pending',TelegramEvent.next_attempt<=now()).order_by(TelegramEvent.id).with_for_update(skip_locked=True).limit(1))
        if not e:return False
        user=s.get(TelegramUser,e.user_id)
        if not user or not user.active:e.status='cancelled';return True
        if str(e.payload.get('chat_id'))!=user.chat_id:e.status='failed';return True
        if e.method=='sendDocument':
            from app.telegram_exports import document
            try:
                filename,content=document(s,e.user_id,e.payload['meeting_id'],e.payload['section'])
                outgoing={'chat_id':user.chat_id,'_document':(filename,content)}
            except (ValueError,KeyError):e.status='failed';return True
        else:outgoing=e.payload
        e.attempts+=1
        try:
            try:result=bot.call(e.method,outgoing)
            except DeliveryError as error:
                if error.code!='message_unavailable':raise
                payload={k:v for k,v in e.payload.items() if k!='message_id'}
                result=bot.call('sendMessage',payload)
            e.status='sent'
            if e.card_id:
                card=s.get(TelegramCard,e.card_id)
                if card and card.user_id==e.user_id:
                    if isinstance(result,dict) and result.get('message_id'):card.message_id=result['message_id']
                    card.fingerprint,card.stage,card.updated_at=e.card_fingerprint,e.card_stage,now()
        except DeliveryError as error:
            if error.code=='bot_blocked':user.active=False;e.status='failed'
            elif error.code=='rate_limited':
                e.attempts-=1;e.next_attempt=now()+timedelta(seconds=error.retry_after)
            else:
                e.status='failed' if e.attempts>=5 else 'pending'
                e.next_attempt=now()+timedelta(seconds=min(300,10*2**e.attempts))
        return True


def dispatch_owned_action(bot):
    from app.db import TelegramResource,TelegramUser,Action
    with Session.begin() as s:
        row=s.execute(select(Outbox,TelegramUser).join(Action,Action.id==Outbox.action_id)
            .join(TelegramResource,(TelegramResource.kind=='meeting') & (TelegramResource.resource_id==Action.meeting_id))
            .join(TelegramUser,TelegramUser.user_id==TelegramResource.user_id)
            .where(Outbox.status=='pending',Outbox.next_attempt<=now(),TelegramUser.active.is_(True))
            .order_by(Outbox.id).with_for_update(of=Outbox,skip_locked=True).limit(1)).first()
        if not row:return False
        o,user=row;o.attempts+=1
        try:
            result=bot.call('sendMessage',{'chat_id':user.chat_id,'text':o.payload[:4000],
                'reply_markup':{'inline_keyboard':[[
                    {'text':'В работе','callback_data':f'p:{o.action_id}:{o.version}'},
                    {'text':'Выполнено','callback_data':f'd:{o.action_id}:{o.version}'}]]}})
            o.message_id,o.status,o.error_code=result['message_id'],'sent',None
        except (DeliveryError,KeyError) as error:
            o.error_code='telegram_unavailable'
            if isinstance(error,DeliveryError) and error.code=='bot_blocked':user.active=False;o.status='failed'
            elif isinstance(error,DeliveryError) and error.code=='rate_limited':
                o.attempts-=1;o.next_attempt=now()+timedelta(seconds=error.retry_after)
            else:
                o.status='failed' if o.attempts>=5 else 'pending'
                o.next_attempt=now()+timedelta(seconds=min(300,10*2**o.attempts))
        return True


def background(bot,stopped):
    from app.telegram_ui import command_one,watch_cards
    from app.telegram_stats import dispatch as dispatch_stats
    with httpx.Client(timeout=20,trust_env=False,follow_redirects=False) as client:
        while not stopped.is_set():
            try:
                command_one(client)
                watch_cards(client)
                for _ in range(5):
                    if not dispatch_interface(bot):break
                dispatch_owned_action(bot)
                dispatch_stats(bot)
                stopped.wait(2)
            except Exception:
                # Never log HTTP exceptions: Telegram URLs contain the token.
                log.warning('telegram_background_unavailable');stopped.wait(5)


def main():
    logging.basicConfig(level=logging.WARNING,format='%(levelname)s %(message)s')
    logging.getLogger('httpx').setLevel(logging.ERROR)
    logging.getLogger('httpcore').setLevel(logging.ERROR)
    allowed={v.strip() for v in os.getenv('TELEGRAM_ALLOWED_USERS','').split(',') if v.strip()}
    allow_all=os.getenv('TELEGRAM_ALLOW_ALL')=='1'
    if os.getenv('TELEGRAM_ENABLED')!='1' or (not allow_all and not allowed):
        log.error('telegram_not_configured');return
    try:bot=Telegram(telegram_token())
    except RuntimeError:log.error('telegram_token_not_configured');return
    with engine.connect().execution_options(isolation_level='AUTOCOMMIT') as conn:
        if not conn.scalar(text('SELECT pg_try_advisory_lock(:key)'),{'key':DISPATCHER_LOCK}):
            log.error('dispatcher_already_active');return
        stopped=threading.Event();worker=threading.Thread(target=background,args=(bot,stopped),daemon=True);worker.start()
        try:
            while True:
                conn.execute(text('SELECT 1'))
                try:poll_interface(bot,allow_all,allowed)
                except DeliveryError as error:log.warning('%s',error.code);time.sleep(10)
                except Exception:log.warning('telegram_update_unavailable');time.sleep(5)
        finally:
            stopped.set();worker.join(timeout=25);bot.client.close()


if __name__=='__main__':main()
