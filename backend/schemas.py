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
    # Nhạc cụ chủ đạo (khoá cho bảng nhân vật, cảnh clip và Styles Suno) +
    # thể loại nhạc. Rỗng = AI tự suy từ tên/mô tả như trước.
    instrument: str = ""
    music_style: str = ""


class ProjectRename(BaseModel):
    name: str
    # none = chỉ đổi tên · text = vẽ lại chữ thumbnail + ghép lại video ·
    # regen = tạo lại ảnh bìa Gemini + clip 00 Flow rồi như text
    refresh: Optional[str] = "none"


class ProjectUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    video_idea: Optional[str] = None
    auto_video: Optional[bool] = None
    video_style: Optional[str] = None
    instrument: Optional[str] = None
    music_style: Optional[str] = None
    auto_upload: Optional[bool] = None
    music_source: Optional[str] = None       # "local" | "suno"
    suno_idea: Optional[str] = None          # ý tưởng/nhạc cụ cho STEP 0 (Suno)
    # Tổng thời lượng mix/video (phút) + crossfade (giây) — dùng cả cho auto.
    mix_duration_minutes: Optional[float] = Field(default=None, ge=1, le=720)
    mix_crossfade_seconds: Optional[float] = Field(default=None, ge=1, le=60)


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
    instrument: str = ""
    music_style: str = ""
    auto_upload: bool = False
    music_source: str = "local"
    suno_idea: str = ""
    batch_id: Optional[str] = None
    mix_duration_minutes: float = 120.0
    mix_crossfade_seconds: float = 5.0


# ---------- Batch (sản xuất hàng loạt) ----------
class BatchCreate(BaseModel):
    count: int = Field(ge=1, le=50)
    # Nhạc cụ + thể loại nhạc: giữ nguyên cả lô, là đầu vào của mọi prompt
    # (Suno/Gemini/Flow) VÀ là khoá tra ChannelMapping khi đăng nháp YouTube.
    instrument: str = Field(min_length=1)
    music_style: str = Field(min_length=1)
    channel_name: str = ""
    # Style ảnh giữ nguyên cả lô — key trong video/config.STYLES (2d/3d/…/real).
    video_style: str = "2d"
    # Chủ đề tuỳ chọn (giữ mạch); rỗng = AI tự chọn 1 mạch chung.
    theme: str = ""
    # Ngân sách Suno mỗi project (mặc định theo SunoConfig nếu bỏ trống).
    target_tracks: Optional[int] = None
    max_create_actions: Optional[int] = None
    # Tổng thời lượng mix/video (phút) cho MỖI project trong lô.
    mix_duration_minutes: float = Field(default=120.0, ge=1, le=720)


class BatchProjectItem(BaseModel):
    project_id: int
    name: str
    suno_phase: Optional[str] = None
    suno_message: Optional[str] = None


class BatchRunResponse(BaseModel):
    batch_key: str
    total: int
    completed: int
    failed: int
    status: str
    current_index: int
    current_project_id: Optional[int] = None
    message: str = ""
    params: dict = {}
    created_at: datetime
    updated_at: datetime
    projects: list[BatchProjectItem] = []


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
    duration_minutes: float = 120.0
    crossfade_seconds: float = 5.0
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
