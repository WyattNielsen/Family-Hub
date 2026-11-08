from fastapi import APIRouter, HTTPException, UploadFile, File
from fastapi.responses import FileResponse
from pydantic import BaseModel
from typing import Optional
import sys, os, uuid, shutil
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from database import get_db

router = APIRouter()

MEMBER_SELECT = (
    "SELECT id, name, color, avatar, avatar_photo, is_admin, write_calendar_source_id, "
    "school_id, school_name, sort_order FROM members"
)
MEMBER_ORDER = "ORDER BY sort_order, name, id"

ALLOWED_AVATAR_TYPES = {"image/jpeg", "image/png", "image/gif", "image/webp"}
ALLOWED_AVATAR_EXT = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
MAX_AVATAR_BYTES = 8 * 1024 * 1024


def _avatars_dir():
    data_dir = os.environ.get("DATA_DIR")
    if data_dir:
        root = data_dir
    else:
        db = os.environ.get("DB_PATH") or ""
        parent = os.path.dirname(db)
        root = parent if parent else os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")
    path = os.path.join(root, "avatars")
    os.makedirs(path, exist_ok=True)
    return path


def _remove_avatar_file(name):
    if not name:
        return
    path = os.path.join(_avatars_dir(), os.path.basename(name))
    if os.path.isfile(path):
        try:
            os.remove(path)
        except OSError:
            pass


class MemberCreate(BaseModel):
    name: str
    color: str = "#4A90D9"
    avatar: Optional[str] = None
    is_admin: bool = False
    school_id: Optional[str] = None
    school_name: Optional[str] = None

class MemberOrder(BaseModel):
    ids: list[int]

class MemberUpdate(BaseModel):
    name: Optional[str] = None
    color: Optional[str] = None
    avatar: Optional[str] = None
    is_admin: Optional[bool] = None
    write_calendar_source_id: Optional[int] = None
    school_id: Optional[str] = None
    school_name: Optional[str] = None

def _validate_write_source(conn, member_id: int, source_id: Optional[int]):
    if source_id is None:
        return
    source = conn.execute(
        "SELECT id, member_id, kind FROM calendar_sources WHERE id=?", (source_id,)
    ).fetchone()
    if not source:
        raise HTTPException(status_code=400, detail="Write calendar source not found")
    if source["member_id"] != member_id:
        raise HTTPException(
            status_code=400,
            detail="Write calendar must be assigned to this member",
        )
    if source["kind"] not in ("google", "caldav"):
        raise HTTPException(
            status_code=400,
            detail="Write calendar must be Google or iCloud CalDAV",
        )

@router.get("/")
def get_members():
    conn = get_db()
    members = conn.execute(f"{MEMBER_SELECT} {MEMBER_ORDER}").fetchall()
    conn.close()
    return [dict(m) for m in members]

@router.put("/order")
def set_member_order(body: MemberOrder):
    conn = get_db()
    existing = {row["id"] for row in conn.execute("SELECT id FROM members").fetchall()}
    if len(body.ids) != len(set(body.ids)) or set(body.ids) != existing:
        conn.close()
        raise HTTPException(status_code=400, detail="Order must include every family member once")
    for index, member_id in enumerate(body.ids):
        conn.execute("UPDATE members SET sort_order=? WHERE id=?", (index, member_id))
    conn.commit()
    conn.close()
    return {"ok": True}

@router.post("/")
def create_member(member: MemberCreate):
    conn = get_db()
    school_id = (member.school_id or "").strip() or None
    school_name = (member.school_name or "").strip() or None
    if not school_id:
        school_name = None
    next_order = conn.execute("SELECT COALESCE(MAX(sort_order), -1) + 1 FROM members").fetchone()[0]
    cur = conn.execute(
        "INSERT INTO members (name, color, avatar, is_admin, school_id, school_name, sort_order) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (member.name, member.color, member.avatar, 1 if member.is_admin else 0, school_id, school_name, next_order)
    )
    conn.commit()
    new_id = cur.lastrowid
    row = conn.execute(f"{MEMBER_SELECT} WHERE id=?", (new_id,)).fetchone()
    conn.close()
    return dict(row)

def _apply_member_update(member_id: int, member: MemberUpdate):
    conn = get_db()
    existing = conn.execute("SELECT * FROM members WHERE id=?", (member_id,)).fetchone()
    if not existing:
        conn.close()
        raise HTTPException(status_code=404, detail="Member not found")

    data = member.dict(exclude_unset=True)
    updates = {}
    if "name" in data and data["name"] is not None:
        updates["name"] = data["name"]
    if "color" in data and data["color"] is not None:
        updates["color"] = data["color"]
    if "avatar" in data:
        updates["avatar"] = data["avatar"]
    if "is_admin" in data and data["is_admin"] is not None:
        updates["is_admin"] = 1 if data["is_admin"] else 0
    if "write_calendar_source_id" in data:
        _validate_write_source(conn, member_id, data["write_calendar_source_id"])
        updates["write_calendar_source_id"] = data["write_calendar_source_id"]
    if "school_id" in data:
        school_id = (data.get("school_id") or "").strip() or None
        updates["school_id"] = school_id
        if school_id:
            updates["school_name"] = (data.get("school_name") or "").strip() or None
        else:
            updates["school_name"] = None
    elif "school_name" in data:
        updates["school_name"] = (data.get("school_name") or "").strip() or None

    if updates:
        set_clause = ", ".join(f"{k}=?" for k in updates)
        conn.execute(f"UPDATE members SET {set_clause} WHERE id=?", (*updates.values(), member_id))
        conn.commit()
    row = conn.execute(f"{MEMBER_SELECT} WHERE id=?", (member_id,)).fetchone()
    conn.close()
    return dict(row)

@router.put("/{member_id}")
def update_member(member_id: int, member: MemberUpdate):
    return _apply_member_update(member_id, member)

@router.patch("/{member_id}")
def patch_member(member_id: int, member: MemberUpdate):
    return _apply_member_update(member_id, member)

@router.delete("/{member_id}")
def delete_member(member_id: int):
    conn = get_db()
    row = conn.execute("SELECT avatar_photo FROM members WHERE id=?", (member_id,)).fetchone()
    conn.execute("DELETE FROM members WHERE id=?", (member_id,))
    conn.commit()
    conn.close()
    if row:
        _remove_avatar_file(row["avatar_photo"])
    return {"ok": True}


@router.get("/avatar/{filename}")
def serve_avatar(filename: str):
    filename = os.path.basename(filename)
    path = os.path.join(_avatars_dir(), filename)
    if not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="Avatar not found")
    return FileResponse(path)


@router.post("/{member_id}/avatar")
async def upload_avatar(member_id: int, file: UploadFile = File(...)):
    ext = os.path.splitext(file.filename or "")[1].lower()
    if file.content_type not in ALLOWED_AVATAR_TYPES and ext not in ALLOWED_AVATAR_EXT:
        raise HTTPException(status_code=400, detail="Use a JPEG, PNG, GIF, or WebP image")
    if ext not in ALLOWED_AVATAR_EXT:
        ext = {
            "image/jpeg": ".jpg",
            "image/png": ".png",
            "image/gif": ".gif",
            "image/webp": ".webp",
        }.get(file.content_type, ".jpg")
    conn = get_db()
    row = conn.execute("SELECT avatar_photo FROM members WHERE id=?", (member_id,)).fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="Member not found")
    filename = f"{uuid.uuid4().hex}{ext}"
    dest = os.path.join(_avatars_dir(), filename)
    try:
        with open(dest, "wb") as out:
            shutil.copyfileobj(file.file, out)
    except Exception as e:
        conn.close()
        raise HTTPException(status_code=500, detail=str(e))
    if os.path.getsize(dest) > MAX_AVATAR_BYTES:
        os.remove(dest)
        conn.close()
        raise HTTPException(status_code=400, detail="Image must be under 8 MB")
    conn.execute("UPDATE members SET avatar_photo=? WHERE id=?", (filename, member_id))
    conn.commit()
    conn.close()
    _remove_avatar_file(row["avatar_photo"])
    return {"ok": True, "avatar_photo": filename}


@router.delete("/{member_id}/avatar")
def delete_avatar(member_id: int):
    conn = get_db()
    row = conn.execute("SELECT avatar_photo FROM members WHERE id=?", (member_id,)).fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="Member not found")
    conn.execute("UPDATE members SET avatar_photo=NULL WHERE id=?", (member_id,))
    conn.commit()
    conn.close()
    _remove_avatar_file(row["avatar_photo"])
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
