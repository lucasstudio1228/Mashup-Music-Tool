"""Global API settings (singleton row id=1)."""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlmodel import Session

from backend.database import get_session
from backend.models import AppSettings
from backend.schemas import AppSettingsRead, AppSettingsUpdate

router = APIRouter(prefix="/api/settings", tags=["settings"])


def _get_or_create_settings(db: Session) -> AppSettings:
    """Singleton: luôn trả về row id=1, tạo nếu chưa có."""
    settings = db.get(AppSettings, 1)
    if not settings:
        settings = AppSettings(id=1)
        db.add(settings)
        db.commit()
        db.refresh(settings)
    return settings


@router.get("", response_model=AppSettingsRead)
def get_settings(db: Session = Depends(get_session)):
    s = _get_or_create_settings(db)
    return AppSettingsRead(
        api_base_url=s.api_base_url,
        api_model=s.api_model,
        has_api_key=bool(s.api_key),
    )


@router.patch("", response_model=AppSettingsRead)
def update_settings(
    data: AppSettingsUpdate,
    db: Session = Depends(get_session),
):
    s = _get_or_create_settings(db)

    if data.clear_api_key:
        s.api_key = None
    elif data.api_key is not None:
        stripped = data.api_key.strip()
        if stripped:                       # Bỏ qua nếu gửi lên empty string
            s.api_key = stripped

    if data.api_base_url is not None:
        url = data.api_base_url.strip().rstrip("/")
        if url:
            s.api_base_url = url

    if data.api_model is not None:
        model = data.api_model.strip()
        if model:
            s.api_model = model

    s.updated_at = datetime.now(timezone.utc)
    db.add(s)
    db.commit()
    db.refresh(s)

    return AppSettingsRead(
        api_base_url=s.api_base_url,
        api_model=s.api_model,
        has_api_key=bool(s.api_key),
    )
