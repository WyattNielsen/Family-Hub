from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional
import sys, os; sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from database import get_db

router = APIRouter()

class MemberCreate(BaseModel):
    name: str
    color: str = "#4A90D9"
    avatar: Optional[str] = None
    is_admin: bool = False

class MemberUpdate(BaseModel):
    name: Optional[str] = None
    color: Optional[str] = None
    avatar: Optional[str] = None
    is_admin: Optional[bool] = None

@router.get("/")
def get_members():
    conn = get_db()
    members = conn.execute(
        "SELECT id, name, color, avatar, is_admin, google_email FROM members ORDER BY name"
    ).fetchall()
    conn.close()
    return [dict(m) for m in members]

@router.post("/")
def create_member(member: MemberCreate):
    conn = get_db()
    cur = conn.execute(
        "INSERT INTO members (name, color, avatar, is_admin) VALUES (?, ?, ?, ?)",
        (member.name, member.color, member.avatar, 1 if member.is_admin else 0)
    )
    conn.commit()
    new_id = cur.lastrowid
    row = conn.execute("SELECT id, name, color, avatar, is_admin, google_email FROM members WHERE id=?", (new_id,)).fetchone()
    conn.close()
    return dict(row)

@router.put("/{member_id}")
def update_member(member_id: int, member: MemberUpdate):
    conn = get_db()
    existing = conn.execute("SELECT * FROM members WHERE id=?", (member_id,)).fetchone()
    if not existing:
        conn.close()
        raise HTTPException(status_code=404, detail="Member not found")
    updates = {}
    if member.name is not None: updates["name"] = member.name
    if member.color is not None: updates["color"] = member.color
    if member.avatar is not None: updates["avatar"] = member.avatar
    if member.is_admin is not None: updates["is_admin"] = 1 if member.is_admin else 0
    if updates:
        set_clause = ", ".join(f"{k}=?" for k in updates)
        conn.execute(f"UPDATE members SET {set_clause} WHERE id=?", (*updates.values(), member_id))
        conn.commit()
    row = conn.execute("SELECT id, name, color, avatar, is_admin, google_email FROM members WHERE id=?", (member_id,)).fetchone()
    conn.close()
    return dict(row)

@router.delete("/{member_id}")
def delete_member(member_id: int):
    conn = get_db()
    conn.execute("DELETE FROM members WHERE id=?", (member_id,))
    conn.commit()
    conn.close()
    return {"ok": True}

@router.get("/{member_id}/chore-stats")
def get_chore_stats(member_id: int):
    conn = get_db()
    stats = conn.execute("""
        SELECT 
            COUNT(*) as total,
            SUM(completed) as completed,
            SUM(CASE WHEN completed=1 THEN points ELSE 0 END) as points
        FROM chores WHERE assigned_to=?
    """, (member_id,)).fetchone()
    conn.close()
    return dict(stats)
