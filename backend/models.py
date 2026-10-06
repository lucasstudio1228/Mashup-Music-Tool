"""SQLModel table definitions."""
from datetime import datetime, timezone
from typing import Optional

from sqlmodel import Field, SQLModel


class AppSettings(SQLModel, table=True):
    """Singleton — luôn chỉ có 1 row với id=1."""
    id: int = Field(default=1, primary_key=True)
    api_key: Optional[str] = None           # stored, never returned to client
    api_base_url: str = "https://api.openai.com/v1"
    api_model: str = "gpt-4o-mini"
    # GPMLogin Local Automation API — dùng để bật profile + lấy CDP endpoint.
    # Cổng mặc định 9495 (user xác nhận); có thể chỉnh trong UI nếu máy khác.
    gpm_api_base_url: str = "http://localhost:9495"
    # (Tùy chọn) đường dẫn GPMLoginGlobal.exe để tự mở app nếu chưa chạy.
    gpm_exe_path: Optional[str] = None
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc))


class Project(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(index=True)
    description: str = ""
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc))
    # api_key, api_base_url, api_model ĐÃ XÓA — dùng AppSettings toàn cục

    # Video: ý tưởng lưu sẵn để AI viết prompt (dùng cho cả tạo tay lẫn auto).
    video_idea: str = ""
    # Tự động dựng video ngay sau khi render mix Audio xong. Project MỚI mặc
    # định BẬT (model default True); project CŨ giữ TẮT (migration set 0).
    auto_video: bool = True
    # Phong cách ảnh/video: "2d" (tranh vẽ/anime/cartoon) hoặc "3d" (Pixar CGI).
    # Mặc định 2D cho cả project mới lẫn cũ (migration set '2d').
    video_style: str = "2d"

    # ── Auto-upload YouTube ──
    # Loại nhạc cụ + phong cách nhạc: dùng để tra ChannelMapping → chọn đúng
    # profile GPMLogin (kênh YouTube) khi đăng nháp video.
    instrument: str = ""
    music_style: str = ""
    # Nguồn nhạc nền cho project: "local" (import WAV thủ công như cũ — MẶC ĐỊNH,
    # giữ nguyên hành vi hiện có) hoặc "suno" (tạo 15 bài WAV từ Suno ở STEP 0).
    music_source: str = "local"
    # Ý tưởng / loại nhạc cụ cho STEP 0 (Suno). AI dựa vào đây viết Styles prompt
    # (LUÔN nhạc không lời). Lưu sẵn để textbox nhớ giữa các phiên (giống video_idea).
    suno_idea: str = ""
    # Tự động đăng nháp lên YouTube ngay sau khi render video xong. Mặc định TẮT
    # (an toàn — người dùng bật khi đã cấu hình mapping). Project cũ: migration 0.
    auto_upload: bool = False
    # Nhóm project cùng 1 lô sản xuất hàng loạt (batch). NULL = tạo lẻ như cũ.
    batch_id: Optional[str] = Field(default=None, index=True)
    # Tổng thời lượng mix (= độ dài video) + crossfade. Lưu theo project để CẢ
    # bấm Start Mix tay LẪN chuỗi tự động Suno→Mix→Video dùng đúng 1 con số.
    mix_duration_minutes: float = 120.0
    mix_crossfade_seconds: float = 5.0


class BatchRun(SQLModel, table=True):
    """
    1 lô sản xuất hàng loạt N sản phẩm YouTube. Orchestrator (backend/batch_service.py)
    tạo N Project cùng batch_key rồi chạy TUẦN TỰ trọn pipeline từng project
    (Suno → Mix → Video → đăng nháp). Bảng này là state tiến độ + tham số để UI
    theo dõi i/N và để resume/hủy an toàn.
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    batch_key: str = Field(index=True, unique=True)   # UUID lô
    total: int = 0                                    # N sản phẩm
    completed: int = 0                                # số project đã xong chuỗi
    failed: int = 0                                   # số project lỗi/blocking
    # running | completed | cancelled | blocked | failed
    status: str = Field(default="running")
    current_index: int = 0                            # đang chạy project thứ (0-based)
    current_project_id: Optional[int] = None
    params_json: str = "{}"                           # snapshot tham số lô
    message: str = ""                                 # mô tả trạng thái cho UI
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc))


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


class ChannelMapping(SQLModel, table=True):
    """
    Bảng ánh xạ (nhạc cụ + phong cách nhạc) → 1 profile GPMLogin (kênh YouTube).
    Khi đăng nháp video, tool đọc project.instrument + project.music_style, tra
    bảng này để biết bật profile nào + tên kênh + hashtag mặc định + ngôn ngữ.
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    instrument: str = Field(index=True)          # vd "piano", "guitar"
    music_style: str = Field(index=True)         # vd "lofi", "ambient"
    gpm_profile_id: str                          # UUID profile GPMLogin
    channel_name: str = ""                       # tên kênh (đưa vào description)
    default_hashtags: str = ""                   # vd "#relax #healing #sleep"
    language: str = "en-US"                      # ngôn ngữ video (Anh–Mỹ)
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc))


class YoutubeUpload(SQLModel, table=True):
    """
    Bộ nhớ các lần đăng (nháp) — để AI viết title/description ĐA DẠNG, không
    trùng lặp với các video trước của cùng kênh, và để hiển thị lịch sử ở UI.
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    project_id: int = Field(foreign_key="project.id", index=True)
    profile_id: str = Field(index=True)          # profile GPMLogin đã dùng
    channel_name: str = ""
    title: str = ""
    description: str = ""
    hashtags: str = ""
    video_path: str = ""
    status: str = "draft"                        # luôn "draft" — không publish
    error_message: Optional[str] = None
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc))


class SunoBatch(SQLModel, table=True):
    """
    STEP 0 — 1 batch = 1 lần tạo nhạc nền cho 1 project trên Suno.
    Đây là STATE BỀN VỮNG (manifest) để chống trùng + resume: nếu tool bị
    tắt/lỗi giữa chừng, đọc lại batch + candidates để biết đã Create/tải/import
    tới đâu, KHÔNG tạo lại / tải lại / import lại.

    idempotency_key: khoá duy nhất theo (project, lần chạy) — dùng để không
    khởi tạo 2 batch song song cho cùng project.
    lock_token/lock_expires_at: chốt để 1 profile Suno không bị 2 worker điều
    khiển cùng lúc (concurrency_per_account=1).
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    project_id: int = Field(foreign_key="project.id", index=True)
    idempotency_key: str = Field(index=True)     # vd f"suno:{project_id}:{ts}"

    # ── Vòng đời (phases + blocking states — mục I của spec) ──
    #   PREFLIGHT / GENERATING / DOWNLOADING / VALIDATING / READY_FOR_IMPORT /
    #   IMPORTED / DOWNSTREAM_RUNNING / COMPLETED
    #   + WAITING_FOR_LOGIN / WAITING_FOR_HUMAN / SUBMISSION_UNCERTAIN /
    #     INSUFFICIENT_GENERATION_CREDITS / INSUFFICIENT_DOWNLOAD_ALLOWANCE /
    #     UI_CHANGED / FAILED / PAUSED / CANCELLED
    phase: str = Field(default="PREFLIGHT")
    message: str = ""                            # mô tả trạng thái cho UI
    error_message: Optional[str] = None

    # ── Snapshot cấu hình lúc Start (JSON) — để resume dùng đúng tham số ──
    preset: str = "relaxing_flute"
    model: str = "v6"
    target_tracks: int = 15
    config_json: str = "{}"                      # SunoConfig + styles/exclusions đã gửi

    # ── Bộ đếm (mục J) ──
    generated_candidates: int = 0
    selected_tracks: int = 0
    downloaded_tracks: int = 0
    validated_tracks: int = 0
    imported_tracks: int = 0
    create_actions_used: int = 0                 # số lần đã bấm Create (trần max_create_actions)

    # ── Lock chống 2 worker ──
    lock_token: Optional[str] = None
    lock_expires_at: Optional[datetime] = None

    # Thư mục staging chứa WAV (ngoài vùng media auto-watch cho tới khi import).
    staging_dir: Optional[str] = None

    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc))


class SunoCandidate(SQLModel, table=True):
    """
    1 candidate = 1 bài Suno trả về (mỗi request paired_outputs thường trả 2).
    Lưu song_id thật của Suno để chống trùng tuyệt đối. 'selected' = bài được
    chọn cho project; chỉ bài selected mới được tải WAV + import.
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    batch_id: int = Field(foreign_key="sunobatch.id", index=True)
    project_id: int = Field(foreign_key="project.id", index=True)

    song_id: str = Field(index=True)             # ID bài trên Suno (chống trùng)
    request_id: Optional[str] = None             # gộp candidate cùng 1 request
    title: str = ""
    duration_seconds: Optional[float] = None     # đọc từ UI (chưa xác thực)

    selected: bool = False                       # được chọn cho project chưa
    is_spare: bool = False                        # candidate dư (không tải/import)

    # ── Tải WAV ──
    download_status: str = "pending"             # pending|downloading|downloaded|failed
    wav_path: Optional[str] = None               # file WAV THẬT trong staging
    sha256: Optional[str] = None                 # chống trùng nội dung
    download_error: Optional[str] = None

    # ── Xác thực kỹ thuật (ffprobe) — TÁCH khỏi thẩm định nội dung ──
    validation_status: str = "not_verified"      # not_verified|valid|invalid
    validation_error: Optional[str] = None
    verified_duration_seconds: Optional[float] = None
    verified_sample_rate: Optional[int] = None
    verified_channels: Optional[int] = None
    # Không có analyzer nội dung ⇒ giữ 'not_verified', KHÔNG bịa "đã nghe & chọn".
    content_qc_status: str = "not_verified"

    imported: bool = False                       # đã gọi import vào project chưa
    track_id: Optional[int] = None               # Track.id sau khi import

    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc))
