from sqlalchemy.orm import Session
import models


def log_event(
    db: Session,
    actor_name: str,
    event: str,
    ok: bool = True,
    user_id: int | None = None,
    event_type: str | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
):
    """
    Existing calls like log_event(db, name, "logged in") keep working
    unchanged. New optional fields let auth/admin code attach a structured
    event_type plus request metadata for security auditing, without ever
    logging passwords, hashes, tokens, secrets, or codes.
    """
    entry = models.AccessLog(
        actor_name=actor_name,
        event=event,
        ok=ok,
        user_id=user_id,
        event_type=event_type,
        ip_address=ip_address,
        user_agent=user_agent,
    )
    db.add(entry)
    db.commit()
