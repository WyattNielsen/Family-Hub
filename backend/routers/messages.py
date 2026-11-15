from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional
import sys, os; sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from database import get_db
from datetime import datetime, timedelta
import time

router = APIRouter()
_last_expire_check = 0.0


class MessageCreate(BaseModel):
    body: str
    author_id: Optional[int] = None
    recipient_id: Optional[int] = None


def expire_old_messages():
    cutoff = (datetime.utcnow() - timedelta(days=7)).isoformat()
    with get_db() as conn:
        expired = conn.execute(
            "SELECT 1 FROM messages WHERE created_at < %s LIMIT 1", (cutoff,)
        ).fetchone()
        if expired:
            conn.execute("DELETE FROM messages WHERE created_at < %s", (cutoff,))
            conn.commit()


@router.get("/")
def get_messages():
    global _last_expire_check
    now = time.monotonic()
    if now - _last_expire_check > 300:
        _last_expire_check = now
        expire_old_messages()

    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT msg.id, msg.body, msg.created_at, msg.recipient_id,
                   m.id as member_id, m.name, m.color, m.avatar, m.avatar_photo,
                   r.name as recipient_name, r.color as recipient_color,
                   r.avatar as recipient_avatar, r.avatar_photo as recipient_avatar_photo
            FROM messages msg
            LEFT JOIN members m ON m.id = msg.author_id
            LEFT JOIN members r ON r.id = msg.recipient_id
            ORDER BY msg.created_at DESC
            """
        ).fetchall()
    return [dict(r) for r in rows]


@router.post("/")
def create_message(payload: MessageCreate):
    if not payload.body.strip():
        raise HTTPException(status_code=400, detail="Message cannot be empty")
    with get_db() as conn:
        if payload.recipient_id is not None:
            if not conn.execute("SELECT id FROM members WHERE id=%s", (payload.recipient_id,)).fetchone():
                raise HTTPException(status_code=400, detail="Unknown person")
        conn.execute(
            "INSERT INTO messages (author_id, body, recipient_id) VALUES (%s, %s, %s)",
            (payload.author_id, payload.body.strip(), payload.recipient_id),
        )
        conn.commit()
    return {"ok": True}


@router.delete("/{msg_id}")
def delete_message(msg_id: int):
    with get_db() as conn:
        conn.execute("DELETE FROM messages WHERE id=%s", (msg_id,))
        conn.commit()
    return {"ok": True}
