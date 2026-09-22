"""
routers/youtube.py — Cấu hình cho tính năng auto-upload YouTube qua GPMLogin:
  • GPMLogin Local API: xem/sửa base URL + exe path; liệt kê profile (test kết nối).
  • Bảng ánh xạ (instrument + music_style → profile GPMLogin + kênh + hashtag + ngôn ngữ).
"""
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, select

from backend.database import get_session
from backend.models import AppSettings, ChannelMapping

router = APIRouter(prefix="/api/youtube", tags=["youtube"])


def _settings(db: Session) -> AppSettings:
    s = db.get(AppSettings, 1)
    if not s:
        s = AppSettings(id=1)
        db.add(s)
        db.commit()
        db.refresh(s)
    return s


# ── GPMLogin config ─────────────────────────────────────────────
class GpmConfigRead(BaseModel):
    gpm_api_base_url: str
    gpm_exe_path: Optional[str] = None


class GpmConfigUpdate(BaseModel):
    gpm_api_base_url: Optional[str] = None
    gpm_exe_path: Optional[str] = None


@router.get("/gpm/config", response_model=GpmConfigRead)
def get_gpm_config(db: Session = Depends(get_session)):
    s = _settings(db)
    return GpmConfigRead(gpm_api_base_url=s.gpm_api_base_url,
                        gpm_exe_path=s.gpm_exe_path)


@router.patch("/gpm/config", response_model=GpmConfigRead)
def update_gpm_config(data: GpmConfigUpdate, db: Session = Depends(get_session)):
    s = _settings(db)
    if data.gpm_api_base_url is not None:
        url = data.gpm_api_base_url.strip().rstrip("/")
        if url:
            s.gpm_api_base_url = url
    if data.gpm_exe_path is not None:
        s.gpm_exe_path = data.gpm_exe_path.strip() or None
    s.updated_at = datetime.now(timezone.utc)
    db.add(s)
    db.commit()
    db.refresh(s)
    return GpmConfigRead(gpm_api_base_url=s.gpm_api_base_url,
                        gpm_exe_path=s.gpm_exe_path)


@router.get("/gpm/profiles")
def list_gpm_profiles(db: Session = Depends(get_session)):
    """Liệt kê profile GPMLogin (đổ dropdown mapping + test kết nối API)."""
    from backend.video import gpm_client as gpmc
    s = _settings(db)
    client = gpmc.GPMClient(s.gpm_api_base_url or None)
    try:
        profiles = client.list_profiles()
        return {"ok": True, "base_url": client.base_url, "profiles": profiles}
    except gpmc.GPMError as e:
        # Fallback đọc DB đã nằm trong list_profiles; tới đây là hỏng hẳn.
        return {"ok": False, "base_url": client.base_url,
                "profiles": [], "error": str(e)}


# ── Channel mapping CRUD ────────────────────────────────────────
class MappingBody(BaseModel):
    instrument: str
    music_style: str
    gpm_profile_id: str
    channel_name: str = ""
    default_hashtags: str = ""
    language: str = "en-US"


def _mapping_out(m: ChannelMapping) -> dict:
    return {
        "id": m.id, "instrument": m.instrument, "music_style": m.music_style,
        "gpm_profile_id": m.gpm_profile_id, "channel_name": m.channel_name,
        "default_hashtags": m.default_hashtags, "language": m.language,
    }


@router.get("/mappings")
def list_mappings(db: Session = Depends(get_session)):
    rows = db.exec(select(ChannelMapping).order_by(ChannelMapping.id)).all()
    return [_mapping_out(m) for m in rows]


@router.post("/mappings")
def create_mapping(body: MappingBody, db: Session = Depends(get_session)):
    if not body.instrument.strip() or not body.music_style.strip():
        raise HTTPException(400, "instrument và music_style là bắt buộc.")
    if not body.gpm_profile_id.strip():
        raise HTTPException(400, "Phải chọn 1 profile GPMLogin.")
    m = ChannelMapping(
        instrument=body.instrument.strip(),
        music_style=body.music_style.strip(),
        gpm_profile_id=body.gpm_profile_id.strip(),
        channel_name=body.channel_name.strip(),
        default_hashtags=body.default_hashtags.strip(),
        language=(body.language or "en-US").strip(),
    )
    db.add(m)
    db.commit()
    db.refresh(m)
    return _mapping_out(m)


@router.patch("/mappings/{mapping_id}")
def update_mapping(mapping_id: int, body: MappingBody,
                   db: Session = Depends(get_session)):
    m = db.get(ChannelMapping, mapping_id)
    if not m:
        raise HTTPException(404, "Không tìm thấy dòng ánh xạ.")
    m.instrument = body.instrument.strip()
    m.music_style = body.music_style.strip()
    m.gpm_profile_id = body.gpm_profile_id.strip()
    m.channel_name = body.channel_name.strip()
    m.default_hashtags = body.default_hashtags.strip()
    m.language = (body.language or "en-US").strip()
    m.updated_at = datetime.now(timezone.utc)
    db.add(m)
    db.commit()
    db.refresh(m)
    return _mapping_out(m)


@router.delete("/mappings/{mapping_id}")
def delete_mapping(mapping_id: int, db: Session = Depends(get_session)):
    m = db.get(ChannelMapping, mapping_id)
    if not m:
        raise HTTPException(404, "Không tìm thấy dòng ánh xạ.")
    db.delete(m)
    db.commit()
    return {"ok": True}
