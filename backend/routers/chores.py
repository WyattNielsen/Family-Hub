from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from typing import Optional
import asyncio
import sys, os; sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from database import get_db, get_setting_value
from datetime import datetime, timedelta
from calendar import monthrange
import pytz
import jobs

router = APIRouter()

class ChoreCreate(BaseModel):
    title: str
    description: str = ""
    assigned_to: Optional[int] = None
    assign_to_members: Optional[list] = None  # list of member IDs for multi-assign
    due_date: Optional[str] = None
    recurrence: Optional[str] = None  # daily, weekdays, weekly, monthly, None
    recurrence_days: Optional[str] = None  # comma-separated days e.g. "Mon,Tue,Wed"
    recurrence_interval: int = 1
    points: int = 1
    time_of_day: Optional[str] = None  # morning, afternoon, evening

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

@router.get("/")
def get_chores(
    assigned_to: Optional[int] = Query(None),
    completed: Optional[bool] = Query(None),
    hide_future: Optional[bool] = Query(False),
    since_days: Optional[int] = Query(None)
):
    conn = get_db()
    try:
        today = datetime.now().date().isoformat()
        query = """
            SELECT c.*, m.name as member_name, m.color as member_color
            FROM chores c
            LEFT JOIN members m ON c.assigned_to = m.id
            WHERE 1=1
        """
        params = []
        if assigned_to is not None:
            query += " AND c.assigned_to = ?"
            params.append(assigned_to)
        if completed is not None:
            query += " AND c.completed = ?"
            params.append(1 if completed else 0)
        if hide_future:
            query += " AND (c.due_date IS NULL OR c.due_date <= ?)"
            params.append(today)
        if since_days is not None:
            cutoff = (datetime.now().date() - timedelta(days=since_days)).isoformat()
            query += " AND (c.due_date IS NULL OR c.due_date >= ?)"
            params.append(cutoff)
        query += " ORDER BY c.completed ASC, c.due_date ASC NULLS LAST, c.created_at DESC"
        chores = conn.execute(query, params).fetchall()
        return [dict(c) for c in chores]
    finally:
        conn.close()

def _needs_catch_up(recurrence, due_date, today: str) -> bool:
    return bool(recurrence and due_date and str(due_date)[:10] < today)


def _insert_chore(chore: ChoreCreate):
    conn = get_db()
    try:
        results = []
        today = datetime.now().date().isoformat()
        # Recurring chores must have a due_date so recurrence engine works correctly
        due_date = chore.due_date or (today if chore.recurrence else None)
        # Determine list of members to assign to
        member_ids = chore.assign_to_members if chore.assign_to_members else [chore.assigned_to]
        for mid in member_ids:
            cur = conn.execute("""
                INSERT INTO chores (title, description, assigned_to, due_date, recurrence, recurrence_days, recurrence_interval, points, time_of_day)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (chore.title, chore.description, mid, due_date,
                  chore.recurrence, chore.recurrence_days, chore.recurrence_interval, chore.points, chore.time_of_day))
            new_id = cur.lastrowid
            row = conn.execute("""
                SELECT c.*, m.name as member_name, m.color as member_color
                FROM chores c LEFT JOIN members m ON c.assigned_to = m.id WHERE c.id=?
            """, (new_id,)).fetchone()
            results.append(dict(row))
        conn.commit()
        payload = results[0] if len(results) == 1 else results
        return payload, _needs_catch_up(chore.recurrence, due_date, today)
    finally:
        conn.close()


async def _run_chore_catch_up():
    """Missed occurrences. Opens its own connection; the save already closed its."""
    await asyncio.to_thread(advance_recurring_chores)


@router.post("/")
async def create_chore(chore: ChoreCreate):
    payload, catch_up = await asyncio.to_thread(_insert_chore, chore)
    if catch_up:
        await jobs.run_background("chore-catch-up", lambda: _run_chore_catch_up())
    return payload

def _update_chore_row(chore_id: int, chore: ChoreUpdate):
    conn = get_db()
    try:
        existing = conn.execute("SELECT * FROM chores WHERE id=?", (chore_id,)).fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="Chore not found")

        updates = {}
        data = chore.dict()
        for k, v in data.items():
            if v is not None:
                updates[k] = v

        catch_up = False
        # Handle completion
        if "completed" in updates:
            was_completed = bool(existing["completed"])
            now_completed = updates["completed"]
            updates["completed"] = 1 if now_completed else 0
            if now_completed and not was_completed:
                updates["completed_at"] = datetime.now().isoformat()
                # Auto-create next occurrence for recurring chores
                if existing["recurrence"]:
                    _create_next_recurrence(conn, dict(existing))
            elif not now_completed:
                updates["completed_at"] = None

        if updates:
            set_clause = ", ".join(f"{k}=?" for k in updates)
            conn.execute(f"UPDATE chores SET {set_clause} WHERE id=?", (*updates.values(), chore_id))
            conn.commit()
            recurrence = updates.get("recurrence", existing["recurrence"])
            due_date = updates.get("due_date", existing["due_date"])
            schedule_changed = any(
                k in updates for k in ("due_date", "recurrence", "recurrence_days", "recurrence_interval")
            )
            today = datetime.now().date().isoformat()
            catch_up = schedule_changed and _needs_catch_up(recurrence, due_date, today)

        row = conn.execute("""
            SELECT c.*, m.name as member_name, m.color as member_color
            FROM chores c LEFT JOIN members m ON c.assigned_to = m.id WHERE c.id=?
        """, (chore_id,)).fetchone()
        return dict(row), catch_up
    finally:
        conn.close()


@router.put("/{chore_id}")
async def update_chore(chore_id: int, chore: ChoreUpdate):
    row, catch_up = await asyncio.to_thread(_update_chore_row, chore_id, chore)
    if catch_up:
        await jobs.run_background("chore-catch-up", lambda: _run_chore_catch_up())
    return row

@router.delete("/{chore_id}")
def delete_chore(chore_id: int):
    conn = get_db()
    try:
        conn.execute("DELETE FROM chores WHERE id=?", (chore_id,))
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()

@router.post("/reset")
def reset_chores(mode: str = Query("points")):
    """Reset chore points for a new season.
    mode=points: delete completed chore rows only (zeroes scores, keeps chore list)
    mode=all: delete every chore row (full fresh start)
    """
    conn = get_db()
    try:
        if mode == "all":
            conn.execute("DELETE FROM chores")
        else:
            conn.execute("DELETE FROM chores WHERE completed=1")
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()

@router.get("/leaderboard")
def get_leaderboard():
    conn = get_db()
    try:
        rows = conn.execute("""
            SELECT m.id, m.name, m.color, m.avatar, m.avatar_photo,
                   SUM(CASE WHEN c.completed=1 THEN c.points ELSE 0 END) as points
            FROM members m
            LEFT JOIN chores c ON c.assigned_to = m.id
            GROUP BY m.id
            ORDER BY points DESC, m.sort_order, m.name
        """).fetchall()

        # One read of completed days, then walk each streak in memory.
        # The old loop queried once per member per day.
        done_days = {}
        for row in conn.execute(
            """SELECT assigned_to, date(due_date) AS due FROM chores
               WHERE completed=1 AND assigned_to IS NOT NULL AND due_date IS NOT NULL
               GROUP BY assigned_to, date(due_date)"""
        ):
            done_days.setdefault(row["assigned_to"], set()).add(row["due"])

        result = []
        tz_val = get_setting_value("timezone", "America/New_York") or "America/New_York"
        tz = pytz.timezone(tz_val)
        today = datetime.now(tz).date()
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
    finally:
        conn.close()

def advance_recurring_chores():
    """Create missed recurring occurrences.

    The daily loop and the chore-catch-up job both call this. Each opens its
    own connection. A save does not run it inline.
    """
    conn = get_db()
    try:
        _advance_recurring_to_today(conn)
    finally:
        conn.close()


def _advance_recurring_to_today(conn):
    """Create any missing recurring chore occurrences up to today.
    Handles the case where a chore was missed (not completed) so
    _create_next_recurrence was never called."""
    today = datetime.now().date()
    # Compare against the local date. SQLite's date('now') is UTC.
    rows = conn.execute("""
        SELECT *, MAX(due_date) as max_due FROM chores
        WHERE recurrence IS NOT NULL AND due_date IS NOT NULL
        GROUP BY title, COALESCE(assigned_to, -1)
        HAVING date(MAX(due_date)) < ?
    """, (today.isoformat(),)).fetchall()
    for row in rows:
        chore = dict(row)
        try:
            _catch_up_chore(conn, chore, today)
            conn.commit()
        except Exception as e:
            # One bad row must not stop the catch-up for every chore after it.
            conn.rollback()
            print(f"Chore catch-up failed for {chore['title']!r}: {e}")


def _catch_up_chore(conn, chore: dict, today):
    chore["due_date"] = chore["max_due"]
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
            next_due = _add_months(due, interval)
        elif rec == "custom_days":
            day_map = {"Mon": 0, "Tue": 1, "Wed": 2, "Thu": 3, "Fri": 4, "Sat": 5, "Sun": 6}
            selected = sorted([day_map[d.strip()] for d in (chore.get("recurrence_days") or "").split(",") if d.strip() in day_map])
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
            "SELECT id FROM chores WHERE title=? AND assigned_to IS ? AND due_date=?",
            (chore["title"], chore["assigned_to"], next_due_str)
        ).fetchone()
        if not existing:
            conn.execute("""
                INSERT INTO chores (title, description, assigned_to, due_date, recurrence,
                                   recurrence_days, recurrence_interval, points, time_of_day)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (chore["title"], chore.get("description"), chore["assigned_to"],
                  next_due_str, chore["recurrence"], chore.get("recurrence_days"),
                  interval, chore["points"], chore.get("time_of_day")))
        chore["due_date"] = next_due_str


def _add_months(d, months: int):
    """Same day N months later, clamped to the month's last day (Jan 31 -> Feb 28)."""
    month_index = d.month - 1 + months
    year = d.year + month_index // 12
    month = month_index % 12 + 1
    return d.replace(year=year, month=month, day=min(d.day, monthrange(year, month)[1]))

def _create_next_recurrence(conn, chore: dict):
    """When a recurring chore is completed, create the next one."""
    if not chore["due_date"] or not chore["recurrence"]:
        return
    try:
        due = datetime.fromisoformat(chore["due_date"])
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
            next_due = _add_months(due, interval)
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
        # Dedup: don't create if a matching incomplete occurrence already exists
        existing = conn.execute("""
            SELECT id FROM chores
            WHERE title=? AND assigned_to IS ? AND due_date=? AND completed=0
        """, (chore["title"], chore["assigned_to"], next_due_str)).fetchone()
        if existing:
            return
        conn.execute("""
            INSERT INTO chores (title, description, assigned_to, due_date, recurrence, recurrence_days, recurrence_interval, points, time_of_day)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (chore["title"], chore["description"], chore["assigned_to"],
              next_due_str, chore["recurrence"],
              chore.get("recurrence_days"), interval, chore["points"],
              chore.get("time_of_day")))
    except Exception as e:
        print(f"Error creating recurrence: {e}")
