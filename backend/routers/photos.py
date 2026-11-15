import sys, os; sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import asyncio
from fastapi import APIRouter, HTTPException, UploadFile, File
from fastapi.responses import FileResponse
from typing import List
import shutil
import uuid

router = APIRouter()

_data_dir = os.environ.get("DATA_DIR")
if _data_dir:
    PHOTOS_DIR = os.path.join(_data_dir, "photos")
else:
    PHOTOS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "photos")
os.makedirs(PHOTOS_DIR, exist_ok=True)

ALLOWED_TYPES = {"image/jpeg", "image/png", "image/gif", "image/webp"}


@router.get("/")
def get_photos():
    try:
        files = [
            f for f in os.listdir(PHOTOS_DIR)
            if f.lower().endswith((".jpg", ".jpeg", ".png", ".gif", ".webp"))
        ]
        urls = [f"/api/photos/file/{f}" for f in sorted(files)]
        return {"photos": urls, "count": len(urls)}
    except Exception:
        return {"photos": [], "count": 0}


def _copy_upload_sync(src_file, dest: str):
    with open(dest, "wb") as f:
        shutil.copyfileobj(src_file, f)


@router.post("/upload")
async def upload_photos(files: List[UploadFile] = File(...)):
    uploaded = []
    errors = []

    for file in files:
        if file.content_type not in ALLOWED_TYPES:
            errors.append(f"{file.filename}: unsupported type {file.content_type}")
            continue

        ext = os.path.splitext(file.filename or "")[1].lower() or ".jpg"
        filename = f"{uuid.uuid4().hex}{ext}"
        dest = os.path.join(PHOTOS_DIR, filename)

        try:
            await asyncio.to_thread(_copy_upload_sync, file.file, dest)
            uploaded.append(filename)
        except Exception as e:
            errors.append(f"{file.filename}: {str(e)}")

    return {"uploaded": len(uploaded), "files": uploaded, "errors": errors}


@router.get("/file/{filename}")
async def serve_photo(filename: str):
    filename = os.path.basename(filename)
    path = os.path.join(PHOTOS_DIR, filename)
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="Photo not found")
    return FileResponse(
        path,
        headers={"Cache-Control": "public, max-age=31536000, immutable"},
    )


@router.delete("/file/{filename}")
async def delete_photo(filename: str):
    filename = os.path.basename(filename)
    path = os.path.join(PHOTOS_DIR, filename)
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="Photo not found")
    await asyncio.to_thread(os.remove, path)
    return {"deleted": filename}
