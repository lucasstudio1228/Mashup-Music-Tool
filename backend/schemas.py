"""Pydantic request/response schemas (tách khỏi DB models)."""
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


# ---------- Settings ----------
class AppSettingsRead(BaseModel):
    api_base_url: str
    api_model: str
    has_api_key: bool          # TRUE nếu key đã được set, KHÔNG trả key thật


class AppSettingsUpdate(BaseModel):
    api_key: Optional[str] = None          # None = không thay đổi key
    clear_api_key: bool = False            # True = xóa key hiện tại
    api_base_url: Optional[str] = None
    api_model: Optional[str] = None


class ApiTestRequest(BaseModel):
    """Giá trị tạm để test (nếu bỏ trống → dùng cấu hình đã lưu trong DB).

    Cho phép test TRƯỚC khi lưu: gửi lên key/base_url/model đang gõ trong form.
    """
    api_key: Optional[str] = None
    api_base_url: Optional[str] = None
    api_model: Optional[str] = None


class ApiTestResult(BaseModel):
    ok: bool
    model: str                             # model đã dùng để gọi thử
    base_url: str
    latency_ms: Optional[int] = None       # thời gian phản hồi khi thành công
    reply: Optional[str] = None            # nội dung model trả về (rút gọn)
    error_type: Optional[str] = None       # tên exception khi lỗi
    error: Optional[str] = None            # thông điệp lỗi thân thiện
    retryable: bool = False                # True nếu nên chờ & thử lại (vd 524)


# ---------- Projects ----------
class ProjectCreate(BaseModel):
    name: str = Field(min_length=1)
    description: str = ""


class ProjectUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    video_idea: Optional[str] = None
    auto_video: Optional[bool] = None
    video_style: Optional[str] = None


class ProjectResponse(BaseModel):
    id: int
    name: str
    description: str
    created_at: datetime
    track_count: int = 0
    mix_count: int = 0
    video_idea: str = ""
    auto_video: bool = False
    video_style: str = "2d"


# ---------- Tracks ----------
class TrackResponse(BaseModel):
    id: int
    project_id: int
    filename: str
    filepath: str
    duration_seconds: float
    sample_rate: int
    channels: int
    format: str
    is_lossy: bool
    subtype: Optional[str] = None
    added_at: datetime


class ScanRequest(BaseModel):
    folder_path: str
    recursive: bool = False


class AddFileRequest(BaseModel):
    file_path: str


class ScanResult(BaseModel):
    added: int
    skipped_duplicates: int
    skipped_errors: int
    error_files: list[str]
    total_tracks: int
    warning_lossy: bool


# ---------- Mixes ----------
class MixCreate(BaseModel):
    duration_minutes: float = 60.0
    crossfade_seconds: float = 15.0
    # sample_rate = 0 → auto (max native của library, không upsample giả).
    sample_rate: int = 0
    # bit_depth mặc định 32 (float, không nén) — chất lượng tối đa.
    bit_depth: int = 32
    title: Optional[str] = None


class MixResponse(BaseModel):
    id: int
    project_id: int
    title: str
    status: str
    created_at: datetime
    completed_at: Optional[datetime] = None
    duration_minutes: float
    crossfade_seconds: float
    sample_rate: int
    bit_depth: int
    output_dir: Optional[str] = None
    total_duration_seconds: Optional[float] = None
    track_count: Optional[int] = None
    error_message: Optional[str] = None
    # Trạng thái realtime từ job_manager (nếu đang chạy)
    progress_percent: float = 0.0
    progress_step: int = 0
    progress_step_name: str = ""


# ---------- Detail ----------
class ProjectDetailResponse(ProjectResponse):
    tracks: list[TrackResponse] = []
    mixes: list[MixResponse] = []
