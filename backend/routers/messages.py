from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional
import sys, os; sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from database import get_db
from datetime import datetime, timedelta

router = APIRouter()

class MessageCreate(BaseModel):
    body: str
    author_id: Optional[int] = None
    recipient_id: Optional[int] = None

@router.get("/")
def get_messages():
    conn = get_db()
    # Auto-expire messages older than 7 days
    cutoff = (datetime.utcnow() - timedelta(days=7)).isoformat()
    conn.execute("DELETE FROM messages WHERE created_at < ?", (cutoff,))
    conn.commit()
    rows = conn.execute("""
        SELECT msg.id, msg.body, msg.created_at, msg.recipient_id,
               m.id as member_id, m.name, m.color, m.avatar, m.avatar_photo,
               r.name as recipient_name, r.color as recipient_color,
               r.avatar as recipient_avatar, r.avatar_photo as recipient_avatar_photo
        FROM messages msg
        LEFT JOIN members m ON m.id = msg.author_id
        LEFT JOIN members r ON r.id = msg.recipient_id
        ORDER BY msg.created_at DESC
    """).fetchall()
    conn.close()
    return [dict(r) for r in rows]

@router.post("/")
def create_message(payload: MessageCreate):
    if not payload.body.strip():
        raise HTTPException(status_code=400, detail="Message cannot be empty")
    conn = get_db()
    if payload.recipient_id is not None:
        if not conn.execute("SELECT id FROM members WHERE id=?", (payload.recipient_id,)).fetchone():
            conn.close()
            raise HTTPException(status_code=400, detail="Unknown person")
    conn.execute(
        "INSERT INTO messages (author_id, body, recipient_id) VALUES (?, ?, ?)",
        (payload.author_id, payload.body.strip(), payload.recipient_id)
    )
    conn.commit()
    conn.close()
    return {"ok": True}

@router.delete("/{msg_id}")
def delete_message(msg_id: int):
    conn = get_db()
    conn.execute("DELETE FROM messages WHERE id=?", (msg_id,))
    conn.commit()
    conn.close()
    return {"ok": True}
