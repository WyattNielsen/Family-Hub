from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from database import get_db

router = APIRouter()


class ItemCreate(BaseModel):
    name: str
    quantity: Optional[str] = ""


class ItemPatch(BaseModel):
    name: Optional[str] = None
    quantity: Optional[str] = None
    checked: Optional[bool] = None


def _rows():
    with get_db() as conn:
        rows = conn.execute(
            "SELECT id, name, quantity, checked, created_at FROM shopping_items ORDER BY checked ASC, id ASC"
        ).fetchall()
    return [dict(r) for r in rows]


@router.get("/")
def list_items():
    return _rows()


@router.post("/")
def create_item(body: ItemCreate):
    name = (body.name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="Item name is required")
    quantity = (body.quantity or "").strip()
    with get_db() as conn:
        row = conn.execute(
            """INSERT INTO shopping_items (name, quantity)
               VALUES (%s, %s)
               RETURNING id, name, quantity, checked, created_at""",
            (name, quantity),
        ).fetchone()
        conn.commit()
    return dict(row)


@router.patch("/{item_id}")
def patch_item(item_id: int, body: ItemPatch):
    with get_db() as conn:
        existing = conn.execute("SELECT id FROM shopping_items WHERE id = %s", (item_id,)).fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="Item not found")
        data = body.dict(exclude_unset=True)
        updates = {}
        if "name" in data and data["name"] is not None:
            name = data["name"].strip()
            if not name:
                raise HTTPException(status_code=400, detail="Item name is required")
            updates["name"] = name
        if "quantity" in data and data["quantity"] is not None:
            updates["quantity"] = data["quantity"].strip()
        if "checked" in data and data["checked"] is not None:
            updates["checked"] = 1 if data["checked"] else 0
        if updates:
            clause = ", ".join(f"{k} = %s" for k in updates)
            conn.execute(f"UPDATE shopping_items SET {clause} WHERE id = %s", (*updates.values(), item_id))
            conn.commit()
        row = conn.execute(
            "SELECT id, name, quantity, checked, created_at FROM shopping_items WHERE id = %s",
            (item_id,),
        ).fetchone()
    return dict(row)


@router.delete("/{item_id}")
def delete_item(item_id: int):
    with get_db() as conn:
        conn.execute("DELETE FROM shopping_items WHERE id = %s", (item_id,))
        conn.commit()
    return {"ok": True}


@router.post("/clear-checked")
def clear_checked():
    with get_db() as conn:
        conn.execute("DELETE FROM shopping_items WHERE checked = 1")
        conn.commit()
    return {"ok": True}
