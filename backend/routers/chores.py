from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from typing import Optional
import sys, os; sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from database import get_db, get_setting_value
from datetime import datetime, timedelta
import pytz

router = APIRouter()


class ChoreCreate(BaseModel):
    title: str
    description: str = ""
    assigned_to: Optional[int] = None
    assign_to_members: Optional[list] = None
    due_date: Optional[str] = None
    recurrence: Optional[str] = None
    recurrence_days: Optional[str] = None
    recurrence_interval: int = 1
    points: int = 1
    time_of_day: Optional[str] = None


class ChoreUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    assigned_to: Optional[int] = None
    due_date: Optional[str] = None
    completed: Optional[bool] = None
    recurrence: Optional[str] = None
    recurrence_days: Optional[str] = None
    recurrence_interval: Optional[int] = None
    points: Optional[int] = None
    time_of_day: Optional[str] = None


def _today_str() -> str:
    tz_val = get_setting_value("timezone", "America/New_York") or "America/New_York"
    try:
        tz = pytz.timezone(tz_val)
    except Exception:
        tz = pytz.timezone("America/New_York")
    return datetime.now(tz).date().isoformat()


@router.get("/")
def get_chores(
    assigned_to: Optional[int] = Query(None),
    completed: Optional[bool] = Query(None),
    hide_future: Optional[bool] = Query(False),
    since_days: Optional[int] = Query(None),
):
    today = _today_str()
    query = """
        SELECT c.*, m.name as member_name, m.color as member_color
        FROM chores c
        LEFT JOIN members m ON c.assigned_to = m.id
        WHERE 1=1
    """
    params = []
    if assigned_to is not None:
        query += " AND c.assigned_to = %s"
        params.append(assigned_to)
    if completed is not None:
        query += " AND c.completed = %s"
        params.append(1 if completed else 0)
    if hide_future:
        query += " AND (c.due_date IS NULL OR c.due_date <= %s)"
        params.append(today)
    if since_days is not None:
        cutoff = (datetime.fromisoformat(today).date() - timedelta(days=since_days)).isoformat()
        query += " AND (c.due_date IS NULL OR c.due_date >= %s)"
        params.append(cutoff)
    query += " ORDER BY c.completed ASC, c.due_date ASC NULLS LAST, c.created_at DESC"
    with get_db() as conn:
        chores = conn.execute(query, params).fetchall()
    return [dict(c) for c in chores]


@router.post("/")
def create_chore(chore: ChoreCreate):
    results = []
    today = _today_str()
    due_date = chore.due_date or (today if chore.recurrence else None)
    member_ids = chore.assign_to_members if chore.assign_to_members else [chore.assigned_to]
    with get_db() as conn:
        for mid in member_ids:
            inserted = conn.execute(
                """
                INSERT INTO chores
                    (title, description, assigned_to, due_date, recurrence,
                     recurrence_days, recurrence_interval, points, time_of_day)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (
                    chore.title, chore.description, mid, due_date,
                    chore.recurrence, chore.recurrence_days,
                    chore.recurrence_interval, chore.points, chore.time_of_day,
                ),
            ).fetchone()
            row = conn.execute(
                """
                SELECT c.*, m.name as member_name, m.color as member_color
                FROM chores c LEFT JOIN members m ON c.assigned_to = m.id WHERE c.id=%s
                """,
                (inserted["id"],),
            ).fetchone()
            results.append(dict(row))
        conn.commit()
    return results[0] if len(results) == 1 else results


@router.put("/{chore_id}")
def update_chore(chore_id: int, chore: ChoreUpdate):
    with get_db() as conn:
        existing = conn.execute("SELECT * FROM chores WHERE id=%s", (chore_id,)).fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="Chore not found")

        updates = {}
        data = chore.dict()
        for k, v in data.items():
            if v is not None:
                updates[k] = v

        if "completed" in updates:
            was_completed = bool(existing["completed"])
            now_completed = updates["completed"]
            updates["completed"] = 1 if now_completed else 0
            if now_completed and not was_completed:
                updates["completed_at"] = datetime.now().isoformat()
                if existing["recurrence"]:
                    _create_next_recurrence(conn, dict(existing))
            elif not now_completed:
                updates["completed_at"] = None

        if updates:
            set_clause = ", ".join(f"{k}=%s" for k in updates)
            conn.execute(f"UPDATE chores SET {set_clause} WHERE id=%s", (*updates.values(), chore_id))
            conn.commit()

        row = conn.execute(
            """
            SELECT c.*, m.name as member_name, m.color as member_color
            FROM chores c LEFT JOIN members m ON c.assigned_to = m.id WHERE c.id=%s
            """,
            (chore_id,),
        ).fetchone()
    return dict(row)


@router.delete("/{chore_id}")
def delete_chore(chore_id: int):
    with get_db() as conn:
        conn.execute("DELETE FROM chores WHERE id=%s", (chore_id,))
        conn.commit()
    return {"ok": True}


@router.post("/reset")
def reset_chores(mode: str = Query("points")):
    with get_db() as conn:
        if mode == "all":
            conn.execute("DELETE FROM chores")
        else:
            conn.execute("DELETE FROM chores WHERE completed=1")
        conn.commit()
    return {"ok": True}


@router.get("/leaderboard")
def get_leaderboard():
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT m.id, m.name, m.color, m.avatar, m.avatar_photo,
                   COALESCE(SUM(CASE WHEN c.completed=1 THEN c.points ELSE 0 END), 0) as points
            FROM members m
            LEFT JOIN chores c ON c.assigned_to = m.id
            GROUP BY m.id
            ORDER BY points DESC, m.sort_order, m.name
            """
        ).fetchall()

        done_days: dict[int, set[str]] = {}
        for row in conn.execute(
            """SELECT assigned_to, SUBSTRING(due_date FROM 1 FOR 10) AS due
               FROM chores
               WHERE completed=1 AND assigned_to IS NOT NULL AND due_date IS NOT NULL
               GROUP BY assigned_to, SUBSTRING(due_date FROM 1 FOR 10)"""
        ):
            done_days.setdefault(row["assigned_to"], set()).add(row["due"])

    tz_val = get_setting_value("timezone", "America/New_York") or "America/New_York"
    try:
        tz = pytz.timezone(tz_val)
    except Exception:
        tz = pytz.timezone("America/New_York")
    today = datetime.now(tz).date()

    result = []
    for r in rows:
        d = dict(r)
        days = done_days.get(d["id"], ())
        streak = 0
        check = today
        while check.isoformat() in days:
            streak += 1
            check -= timedelta(days=1)
        d["streak"] = streak
        result.append(d)
    return result


def advance_recurring_chores():
    """Create missed recurring occurrences. Runs from worker.py."""
    with get_db() as conn:
        _advance_recurring_to_today(conn)


def _advance_recurring_to_today(conn):
    today_str = _today_str()
    today = datetime.fromisoformat(today_str).date()
    rows = conn.execute(
        """
        SELECT DISTINCT ON (title, COALESCE(assigned_to, -1)) *
        FROM chores
        WHERE recurrence IS NOT NULL AND due_date IS NOT NULL
        ORDER BY title, COALESCE(assigned_to, -1), SUBSTRING(due_date FROM 1 FOR 10) DESC
        """
    ).fetchall()

    created = False
    for row in rows:
        chore = dict(row)
        max_due_prefix = str(chore["due_date"])[:10]
        if max_due_prefix >= today_str:
            continue
        chore["due_date"] = max_due_prefix
        for _ in range(60):
            due = datetime.fromisoformat(chore["due_date"]).date()
            interval = chore.get("recurrence_interval") or 1
            rec = chore["recurrence"]
            if rec == "daily":
                next_due = due + timedelta(days=interval)
            elif rec == "weekdays":
                next_due = due + timedelta(days=1)
                while next_due.weekday() >= 5:
                    next_due += timedelta(days=1)
            elif rec == "weekly":
                next_due = due + timedelta(weeks=interval)
            elif rec == "monthly":
                month = due.month + interval
                year = due.year + (month - 1) // 12
                month = ((month - 1) % 12) + 1
                next_due = due.replace(year=year, month=month)
            elif rec == "custom_days":
                day_map = {"Mon": 0, "Tue": 1, "Wed": 2, "Thu": 3, "Fri": 4, "Sat": 5, "Sun": 6}
                selected = sorted(
                    [day_map[d.strip()] for d in (chore.get("recurrence_days") or "").split(",") if d.strip() in day_map]
                )
                if not selected:
                    break
                current_wd = due.weekday()
                next_wd = next((d for d in selected if d > current_wd), selected[0])
                next_due = due + timedelta(days=(next_wd - current_wd) % 7 or 7)
            else:
                break
            if next_due > today:
                break
            next_due_str = next_due.isoformat()
            existing = conn.execute(
                "SELECT id FROM chores WHERE title=%s AND assigned_to IS NOT DISTINCT FROM %s AND due_date=%s",
                (chore["title"], chore["assigned_to"], next_due_str),
            ).fetchone()
            if not existing:
                conn.execute(
                    """
                    INSERT INTO chores (title, description, assigned_to, due_date, recurrence,
                                        recurrence_days, recurrence_interval, points, time_of_day)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        chore["title"], chore.get("description"), chore["assigned_to"],
                        next_due_str, chore["recurrence"], chore.get("recurrence_days"),
                        interval, chore["points"], chore.get("time_of_day"),
                    ),
                )
                created = True
            chore["due_date"] = next_due_str
    if created:
        conn.commit()


def _create_next_recurrence(conn, chore: dict):
    if not chore["due_date"] or not chore["recurrence"]:
        return
    try:
        due = datetime.fromisoformat(str(chore["due_date"])[:10])
        interval = chore.get("recurrence_interval", 1) or 1
        rec = chore["recurrence"]
        if rec == "daily":
            next_due = due + timedelta(days=interval)
        elif rec == "weekdays":
            next_due = due + timedelta(days=1)
            while next_due.weekday() >= 5:
                next_due += timedelta(days=1)
        elif rec == "weekly":
            next_due = due + timedelta(weeks=interval)
        elif rec == "monthly":
            month = due.month + interval
            year = due.year + (month - 1) // 12
            month = ((month - 1) % 12) + 1
            next_due = due.replace(year=year, month=month)
        elif rec == "custom_days":
            day_map = {"Mon": 0, "Tue": 1, "Wed": 2, "Thu": 3, "Fri": 4, "Sat": 5, "Sun": 6}
            days_str = chore.get("recurrence_days") or ""
            selected = sorted([day_map[d.strip()] for d in days_str.split(",") if d.strip() in day_map])
            if not selected:
                return
            current_wd = due.weekday()
            next_wd = next((d for d in selected if d > current_wd), selected[0])
            days_ahead = (next_wd - current_wd) % 7 or 7
            next_due = due + timedelta(days=days_ahead)
        else:
            return
        next_due_str = next_due.date().isoformat()
        existing = conn.execute(
            """
            SELECT id FROM chores
            WHERE title=%s AND assigned_to IS NOT DISTINCT FROM %s AND due_date=%s AND completed=0
            """,
            (chore["title"], chore["assigned_to"], next_due_str),
        ).fetchone()
        if not existing:
            conn.execute(
                """
                INSERT INTO chores (title, description, assigned_to, due_date, recurrence, recurrence_days, recurrence_interval, points, time_of_day)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    chore["title"], chore["description"], chore["assigned_to"], next_due_str,
                    chore["recurrence"], chore.get("recurrence_days"), interval,
                    chore["points"], chore.get("time_of_day"),
                ),
            )
    except Exception as e:
        print(f"Recurrence error: {e}")
