from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from database import get_db
import schoolcafe

router = APIRouter()


class LineUpdate(BaseModel):
    school_id: str
    breakfast: Optional[str] = None
    lunch: Optional[str] = None


def _district():
    district_id = (schoolcafe.setting("schoolcafe_district_id") or "").strip()
    district_name = schoolcafe.setting("schoolcafe_district_name") or ""
    return district_id, district_name


@router.get("/districts")
async def districts(q: str = ""):
    try:
        return await schoolcafe.search_districts(q)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"SchoolCafe search failed: {e}")


@router.get("/schools")
async def schools():
    district_id, district_name = _district()
    if not district_id:
        return {"configured": False, "district_name": "", "schools": []}
    try:
        rows = await schoolcafe.list_schools(district_id)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"SchoolCafe schools failed: {e}")
    if rows is None:
        raise HTTPException(status_code=502, detail="SchoolCafe did not return a school list")
    return {"configured": True, "district_name": district_name, "schools": rows}


@router.get("/lines")
async def lines(school_id: str):
    school_id = (school_id or "").strip()
    if not school_id:
        raise HTTPException(status_code=400, detail="school_id is required")
    today, _hour = schoolcafe.app_today()
    try:
        breakfast = await schoolcafe.serving_lines(school_id, "Breakfast", today)
        lunch = await schoolcafe.serving_lines(school_id, "Lunch", today)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"SchoolCafe lines failed: {e}")
    selected = schoolcafe.line_overrides().get(school_id) or {}
    return {"breakfast": breakfast, "lunch": lunch, "selected": selected}


@router.put("/lines")
def update_lines(body: LineUpdate):
    school_id = (body.school_id or "").strip()
    if not school_id:
        raise HTTPException(status_code=400, detail="school_id is required")
    saved = schoolcafe.save_line_override(school_id, (body.breakfast or "").strip(), (body.lunch or "").strip())
    return {"ok": True, "selected": saved}
