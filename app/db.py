from datetime import datetime, timezone, date
from uuid import uuid4

from sqlalchemy import create_engine, String, Text, DateTime, Date, Integer, Boolean, JSON, ForeignKey, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

from app.settings import database_url


def now():
    return datetime.now(timezone.utc)


def identifier():
    return str(uuid4())


class Base(DeclarativeBase):
    pass


class Meeting(Base):
    __tablename__ = "meetings"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=identifier)
    title: Mapped[str] = mapped_column(String(200))
    recording: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(24), default="queued")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    error_code: Mapped[str | None] = mapped_column(String(80))
    transcript: Mapped[list | None] = mapped_column(JSON)
    minutes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class RecordingImport(Base):
    __tablename__ = "recording_imports"
    session_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    meeting_id: Mapped[str] = mapped_column(ForeignKey("meetings.id"), unique=True)
    recording_sha256: Mapped[str] = mapped_column(String(64))
    request_sha256: Mapped[str] = mapped_column(String(64))
    complete: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Action(Base):
    __tablename__ = "actions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=identifier)
    meeting_id: Mapped[str] = mapped_column(ForeignKey("meetings.id"), index=True)
    text: Mapped[str] = mapped_column(Text)
    owner: Mapped[str | None] = mapped_column(String(200))
    due_on: Mapped[date | None] = mapped_column(Date)
    timezone: Mapped[str] = mapped_column(String(64), default="Europe/Moscow")
    source_seconds: Mapped[int] = mapped_column(Integer)
    evidence: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(24), default="draft")
    version: Mapped[int] = mapped_column(Integer, default=1)


class Audit(Base):
    __tablename__ = "audit"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    action_id: Mapped[str] = mapped_column(ForeignKey("actions.id"))
    event: Mapped[str] = mapped_column(String(40))
    actor: Mapped[str] = mapped_column(String(100))
    snapshot: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Outbox(Base):
    __tablename__ = "outbox"
    __table_args__ = (UniqueConstraint("action_id", "version"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    action_id: Mapped[str] = mapped_column(ForeignKey("actions.id"))
    version: Mapped[int] = mapped_column(Integer)
    payload: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(24), default="pending")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    message_id: Mapped[int | None] = mapped_column(Integer)
    error_code: Mapped[str | None] = mapped_column(String(80))


class BotCursor(Base):
    __tablename__ = "bot_cursor"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    offset: Mapped[int] = mapped_column(Integer, default=0)


engine = create_engine(database_url(), pool_pre_ping=True, hide_parameters=True)
Session = sessionmaker(engine, expire_on_commit=False)


def init():
    Base.metadata.create_all(engine)



class MeetingAnalysis(Base):
    __tablename__ = "meeting_analysis"
    meeting_id: Mapped[str] = mapped_column(ForeignKey("meetings.id"), primary_key=True)
    status: Mapped[str] = mapped_column(String(24), default="queued")
    step: Mapped[str] = mapped_column(String(40), default="waiting_transcript")
    completed: Mapped[int] = mapped_column(Integer, default=0)
    total: Mapped[int] = mapped_column(Integer, default=0)
    error_code: Mapped[str | None] = mapped_column(String(80))
    facts: Mapped[dict | None] = mapped_column(JSON)
    chunk_cache: Mapped[dict] = mapped_column(JSON, default=dict)
    source_hash: Mapped[str | None] = mapped_column(String(64))
    meeting_date: Mapped[date | None] = mapped_column(Date)
    participants: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class MeetingReport(Base):
    __tablename__ = "meeting_reports"
    __table_args__ = (UniqueConstraint("meeting_id", "template_id"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=identifier)
    meeting_id: Mapped[str] = mapped_column(ForeignKey("meetings.id"), index=True)
    template_id: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(24), default="queued")
    content: Mapped[str | None] = mapped_column(Text)
    error_code: Mapped[str | None] = mapped_column(String(80))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)




class TelegramUser(Base):
    __tablename__ = 'telegram_users'
    user_id: Mapped[str] = mapped_column(String(24), primary_key=True)
    chat_id: Mapped[str] = mapped_column(String(24))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class TelegramResource(Base):
    __tablename__ = 'telegram_resources'
    kind: Mapped[str] = mapped_column(String(20), primary_key=True)
    resource_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey('telegram_users.user_id'), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class TelegramDialog(Base):
    __tablename__ = 'telegram_dialogs'
    user_id: Mapped[str] = mapped_column(ForeignKey('telegram_users.user_id'), primary_key=True)
    step: Mapped[str] = mapped_column(String(24))
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    revision: Mapped[str] = mapped_column(String(36), default=identifier)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class TelegramEvent(Base):
    __tablename__ = 'telegram_events'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_key: Mapped[str] = mapped_column(String(150), unique=True)
    user_id: Mapped[str] = mapped_column(ForeignKey('telegram_users.user_id'), index=True)
    method: Mapped[str] = mapped_column(String(30))
    payload: Mapped[dict] = mapped_column(JSON)
    card_id: Mapped[str | None] = mapped_column(String(36))
    card_fingerprint: Mapped[str | None] = mapped_column(String(64))
    card_stage: Mapped[str | None] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(24), default='pending')
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class TelegramCommand(Base):
    __tablename__ = 'telegram_commands'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=identifier)
    update_id: Mapped[int] = mapped_column(Integer, unique=True)
    user_id: Mapped[str] = mapped_column(ForeignKey('telegram_users.user_id'), index=True)
    kind: Mapped[str] = mapped_column(String(24))
    body: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(24), default='pending')
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class TelegramCard(Base):
    __tablename__ = 'telegram_cards'
    session_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey('telegram_users.user_id'), index=True)
    message_id: Mapped[int | None] = mapped_column(Integer)
    fingerprint: Mapped[str | None] = mapped_column(String(64))
    stage: Mapped[str | None] = mapped_column(String(200))
    snapshot: Mapped[dict] = mapped_column(JSON, default=dict)
    settled: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class MeetingContext(Base):
    __tablename__ = 'meeting_context'
    meeting_id: Mapped[str] = mapped_column(ForeignKey('meetings.id'), primary_key=True)
    observations: Mapped[dict] = mapped_column(JSON)


class TelegramStatsGroup(Base):
    __tablename__ = 'telegram_stats_groups'
    chat_id: Mapped[str] = mapped_column(String(24), primary_key=True)
    title: Mapped[str] = mapped_column(String(200))
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class TelegramUsage(Base):
    __tablename__ = 'telegram_usage'
    update_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(String(24))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class TelegramStatsReply(Base):
    __tablename__ = 'telegram_stats_replies'
    update_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    chat_id: Mapped[str] = mapped_column(ForeignKey('telegram_stats_groups.chat_id'))
    period: Mapped[str] = mapped_column(String(12), default='all')
    status: Mapped[str] = mapped_column(String(24), default='pending')
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


if __name__ == "__main__":
    init()
