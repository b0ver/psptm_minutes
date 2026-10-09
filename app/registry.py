from sqlalchemy import select

from app.db import Action, Audit, Outbox


def action_dict(action):
    return {k: getattr(action, k) for k in ("id", "meeting_id", "text", "owner", "due_on", "timezone", "source_seconds", "evidence", "status", "version")}


def audit(session, action, event, actor):
    snapshot = action_dict(action)
    if snapshot["due_on"]:
        snapshot["due_on"] = snapshot["due_on"].isoformat()
    session.add(Audit(action_id=action.id, event=event, actor=actor, snapshot=snapshot))


def locked_action(session, action_id):
    return session.scalar(select(Action).where(Action.id == action_id).with_for_update())


def approve(session, action, version):
    if action.version != version:
        raise ValueError("version_conflict")
    if action.status == "approved":
        return
    if action.status != "draft":
        raise ValueError("not_a_draft")
    action.status = "approved"
    # Snapshot contains only the explicitly approved task card, never transcript/evidence/minutes.
    card = (f"Поручение {action.id} · v{action.version}\n{action.text}\n"
            f"Ответственный: {action.owner or 'не указан'}\n"
            f"Срок: {action.due_on.isoformat() if action.due_on else 'не указан'} ({action.timezone})\n"
            "Статус: утверждено")
    session.add(Outbox(action_id=action.id, version=action.version, payload=card))
    audit(session, action, "approved", "web")


def change_status(session, action, version, status, actor):
    if action.version != version:
        raise ValueError("version_conflict")
    if action.status == status:
        return
    if action.status not in ("approved", "in_progress") or status not in ("in_progress", "done"):
        raise ValueError("invalid_status_transition")
    action.status = status
    audit(session, action, "status_changed", actor)
