"""SQLModel table definitions."""
from datetime import datetime, timezone
from typing import Optional

from sqlmodel import Field, SQLModel


class AppSettings(SQLModel, table=True):
    """Singleton — luôn chỉ có 1 row với id=1."""
    id: int = Field(default=1, primary_key=True)
    api_key: Optional[str] = None           # stored, never returned to client
    api_base_url: str = "https://api.anthropic.com"
    api_model: str = "claude-haiku-4-5-20251001"
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc))


class Project(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(index=True)
    description: str = ""
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc))
    # api_key, api_base_url, api_model ĐÃ XÓA — dùng AppSettings toàn cục


class Track(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    project_id: int = Field(foreign_key="project.id", index=True)
    filename: str
    filepath: str               # absolute path trên disk
    duration_seconds: float
    sample_rate: int
    channels: int
    format: str                 # WAV / FLAC / MP3
    is_lossy: bool
    subtype: Optional[str] = None
    added_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc))


class TrackStem(SQLModel, table=True):
    """
    1-1 với Track. Lưu trạng thái separation + volume settings.
    Tự động tạo khi track được add vào project.
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    track_id:   int   = Field(foreign_key="track.id",
                               unique=True, index=True)
    project_id: int   = Field(foreign_key="project.id", index=True)

    # Trạng thái separation
    status:       str           = Field(default="pending")
    started_at:   Optional[datetime] = None
    completed_at: Optional[datetime] = None
    error_message: Optional[str]    = None

    # Volume mỗi stem — range 0.0 (mute) → 2.0 (double)
    # Default 1.0 = giữ nguyên
    vol_other:  float = Field(default=1.0)   # Âm nhạc
    vol_bass:   float = Field(default=1.0)   # Âm trầm
    vol_drums:  float = Field(default=1.0)   # Trống
    vol_vocals: float = Field(default=1.0)   # Giọng hát

    # Độ mạnh khử noise THỦ CÔNG mỗi stem — range 0.0 → 1.0
    # 0.0 = chỉ khử tự động theo volume (mặc định, giữ hành vi cũ)
    # >0  = ép khử mạnh hơn kể cả khi stem để nguyên
    # Strength thực tế lúc mix = max(clip(1 - volume), den_*)
    den_other:  float = Field(default=0.0)   # Âm nhạc
    den_bass:   float = Field(default=0.0)   # Âm trầm
    den_drums:  float = Field(default=0.0)   # Trống
    den_vocals: float = Field(default=0.0)   # Giọng hát

    # Gain LUFS normalization đã apply (linear scalar)
    # Lưu để reference, không dùng lại
    lufs_gain_applied: Optional[float] = None

    # Waveform thumbnail — JSON string
    # Format: {"other": [0.1, 0.3,...], "bass": [...], ...}
    # 200 RMS points mỗi stem, normalized 0-1
    waveform_data: Optional[str] = None


class Mix(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    project_id: int = Field(foreign_key="project.id", index=True)
    title: str                  # auto-generated or user override
    status: str = "pending"     # pending | running | completed | failed
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc))
    completed_at: Optional[datetime] = None
    # Settings snapshot
    duration_minutes: float = 60.0
    crossfade_seconds: float = 15.0
    # 0 = auto (max native, không upsample giả) — được resolve khi tạo mix.
    sample_rate: int = 0
    # 32-bit float (không nén) = chất lượng tối đa.
    bit_depth: int = 32
    # Output
    output_dir: Optional[str] = None    # absolute path
    total_duration_seconds: Optional[float] = None
    track_count: Optional[int] = None
    error_message: Optional[str] = None
