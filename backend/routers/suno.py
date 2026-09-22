"""
Router STEP 0 — tạo nhạc nền Suno cho 1 project.

Tích hợp cùng video_job_manager (kind="suno") nên được serialize với các job
video/audio khác. GIỮ NGUYÊN khả năng import nhạc thủ công (không đụng tracks.py).

An toàn (mục K của spec):
  • /dry-run: điền form + đọc trạng thái, KHÔNG tạo/tải → KHÔNG tiêu credit.
  • /start: LIVE — chỉ chạy khi người dùng chủ động bấm (kèm ngân sách override).
  • /pause: dừng ở checkpoint gần nhất, giữ state để /resume.
  • /cancel: huỷ hẳn batch (terminal).
"""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlmodel import Session, select

from backend.database import get_session
from backend.models import Project
from backend.video import config as vconfig
from backend.video import suno_selfcheck
from backend.video import suno_service
from backend.video.job_manager import video_job_manager

router = APIRouter(prefix="/api/projects/{project_id}/suno", tags=["suno"])


class StartBody(BaseModel):
    preset: Optional[str] = None
    # Styles/Exclusions tuỳ chọn (AI viết từ ý tưởng, hoặc người dùng tự sửa).
    # Nếu bỏ trống → dùng nguyên văn preset.
    styles: Optional[str] = None
    exclusions: Optional[str] = None
    suno_idea: Optional[str] = None
    target_tracks: Optional[int] = None
    preferred_model: Optional[str] = None
    allow_model_fallback: Optional[bool] = None
    max_mode: Optional[bool] = None
    preferred_duration_seconds_min: Optional[int] = None
    preferred_duration_seconds_max: Optional[int] = None
    minimum_duration_seconds: Optional[int] = None
    # Ngân sách — người dùng xác nhận trước khi chạy live.
    max_create_actions: Optional[int] = None
    max_generation_credits: Optional[int] = None
    max_new_song_downloads: Optional[int] = None
    auto_continue_workflow: Optional[bool] = None

    def overrides(self) -> dict:
        return {k: v for k, v in self.model_dump().items() if v is not None}


def _project_or_404(db: Session, project_id: int) -> Project:
    p = db.get(Project, project_id)
    if not p:
        raise HTTPException(404, f"Project {project_id} không tồn tại")
    return p


def _guard_busy():
    if video_job_manager.is_busy():
        raise HTTPException(409, "Đang có 1 tác vụ (video/suno) chạy. Vui lòng đợi.")


@router.get("/config")
def suno_config(project_id: int, db: Session = Depends(get_session)):
    """Cấu hình mặc định + danh sách preset (kèm Styles/Exclusions để UI xem
    trước). Không tiêu credit."""
    _project_or_404(db, project_id)
    base = vconfig.SUNO
    presets = [
        {"key": k, "label": v["label"], "styles": v["styles"],
         "exclusions": v["exclusions"]}
        for k, v in vconfig.SUNO_PRESETS.items()
    ]
    return {
        "defaults": {
            "source": base.source,
            "preset": base.preset,
            "target_tracks": base.target_tracks,
            "preferred_model": base.preferred_model,
            "allow_model_fallback": base.allow_model_fallback,
            "instrumental": base.instrumental,
            "max_mode": base.max_mode,
            "preferred_duration_seconds_min": base.preferred_duration_seconds_min,
            "preferred_duration_seconds_max": base.preferred_duration_seconds_max,
            "minimum_duration_seconds": base.minimum_duration_seconds,
            "max_create_actions": base.max_create_actions,
            "max_generation_credits": base.max_generation_credits,
            "max_new_song_downloads": base.max_new_song_downloads,
            "auto_continue_workflow": base.auto_continue_workflow,
        },
        "presets": presets,
    }


class GenStylesBody(BaseModel):
    idea: str = ""
    preset: Optional[str] = None
    save: bool = True   # lưu idea vào project.suno_idea để textbox nhớ


@router.post("/generate-styles")
def suno_generate_styles(project_id: int, body: GenStylesBody,
                         db: Session = Depends(get_session)):
    """AI viết prompt Styles (+ Exclude styles) từ ý tưởng/nhạc cụ người dùng.
    LUÔN nhạc không lời. KHÔNG mở trình duyệt, KHÔNG tiêu credit Suno (chỉ gọi
    LLM). Nếu không dùng được AI (thiếu key/lỗi) → fallback về preset.

    Đồng thời PHÂN LOẠI project vào đúng kênh YouTube đã cấu hình (bám sát
    settings), lưu project.instrument/music_style, và BẬT auto_video +
    auto_upload để luồng chạy tự động tới bước lưu nháp YouTube."""
    project = _project_or_404(db, project_id)
    idea = (body.idea or "").strip()

    # Lưu ý tưởng vào project (textbox nhớ giữa các phiên) — giống video_idea.
    if body.save:
        project.suno_idea = idea
        db.add(project)
        db.commit()

    from backend.database import DB_PATH
    from backend.core_bridge import get_api_config_from_db
    from backend.video.suno_prompt import generate_suno_styles

    title = (project.name or "").strip()
    api_config = get_api_config_from_db(str(DB_PATH))

    # AI đoán nhạc cụ/không khí từ Project Title + ý tưởng. Chạy khi có ít nhất
    # một trong hai (thường luôn có Project Title).
    result = None
    if idea or title:
        result = generate_suno_styles(idea, api_config, project_title=title)

    if result is None:
        preset_key = body.preset or vconfig.SUNO.preset
        preset = vconfig.SUNO_PRESETS.get(preset_key) or \
            next(iter(vconfig.SUNO_PRESETS.values()))
        styles = preset["styles"]
        exclusions = preset["exclusions"]
        source = "fallback"
        note = ("Chưa cấu hình API key hoặc AI lỗi — dùng preset. "
                "Cấu hình API ở ⚙️ Settings để AI viết theo ý tưởng.")
    else:
        styles = result["styles"]
        exclusions = result["exclusions"]
        source = "ai"
        note = None

    # ── Phân loại kênh YouTube + bật chuỗi tự động ──
    channel = _classify_and_prepare(db, project, title, idea, styles, api_config)
    return {"source": source, "styles": styles, "exclusions": exclusions,
            "note": note, "channel": channel}


def _classify_and_prepare(db: Session, project: Project, title: str, idea: str,
                          styles: str, api_config) -> dict:
    """Chọn kênh YouTube khớp nhất trong các ChannelMapping đã cấu hình; lưu
    instrument/music_style vào project; bật auto_video + auto_upload để luồng
    một-chạm đi hết tới lưu nháp YouTube. Trả dict cho UI hiển thị (kèm cảnh báo
    nếu chưa cấu hình / không khớp)."""
    from backend.models import ChannelMapping
    from backend.video.channel_match import classify_channel

    mappings = [
        {"instrument": m.instrument, "music_style": m.music_style,
         "channel_name": m.channel_name, "gpm_profile_id": m.gpm_profile_id}
        for m in db.exec(select(ChannelMapping).order_by(ChannelMapping.id)).all()
    ]

    # Luôn bật auto_upload + auto_video: luồng một-chạm phải đi hết tới YouTube.
    project.auto_upload = True
    project.auto_video = True

    matched: Optional[dict] = None
    warning: Optional[str] = None
    if not mappings:
        warning = ("Chưa cấu hình kênh YouTube nào. Vào ⚙️ Settings → "
                   "YouTube/GPMLogin thêm dòng (nhạc cụ + phong cách → profile) "
                   "thì tool mới đăng nháp đúng kênh. Suno/Mix/Video vẫn chạy.")
    else:
        matched = classify_channel(title, idea, styles, mappings, api_config)
        if matched:
            project.instrument = matched.get("instrument", "")
            project.music_style = matched.get("music_style", "")
        else:
            warning = ("Không xác định chắc chắn kênh YouTube cho project này. "
                       "Kiểm tra nhạc cụ trong Tên project, hoặc thêm dòng ánh xạ "
                       "phù hợp ở ⚙️ Settings. Tool sẽ dừng ở bước đăng nháp để "
                       "không đăng nhầm kênh.")

    db.add(project)
    db.commit()

    return {
        "matched": bool(matched),
        "instrument": project.instrument,
        "music_style": project.music_style,
        "channel_name": (matched or {}).get("channel_name", ""),
        "confidence": (matched or {}).get("match_confidence", ""),
        "warning": warning,
    }


@router.get("/status")
def suno_status(project_id: int, db: Session = Depends(get_session)):
    """Trạng thái batch gần nhất + job hiện tại. KHÔNG báo '15/15 hoàn thành' khi
    mới chỉ submit — các counter phản ánh đúng generated/selected/downloaded/
    validated/imported."""
    _project_or_404(db, project_id)
    batch = suno_service.batch_status(project_id)
    job = video_job_manager.get(project_id)
    job_info = None
    if job and job.kind in ("suno", "suno-login"):
        job_info = {"kind": job.kind, "status": job.status,
                    "percent": job.percent, "message": job.message,
                    "error": job.error}
    return {"batch": batch, "job": job_info,
            "selector_check": suno_selfcheck.get_last_check(project_id)}


@router.post("/dry-run")
def suno_dry_run(project_id: int, body: StartBody = StartBody(),
                 db: Session = Depends(get_session)):
    """Điền form + đọc credits/plan, KHÔNG Create/tải. An toàn, không tốn credit."""
    _project_or_404(db, project_id)
    _guard_busy()
    video_job_manager.submit(project_id, "suno", suno_service.run_suno_step0,
                             dry_run=True, overrides=body.overrides())
    return {"status": "submitted", "kind": "suno", "mode": "dry-run"}


@router.post("/start")
def suno_start(project_id: int, body: StartBody = StartBody(),
               db: Session = Depends(get_session)):
    """LIVE — tạo đủ 15 bài, tải WAV, xác thực, import. Người dùng chủ động bấm."""
    _project_or_404(db, project_id)
    _guard_busy()
    video_job_manager.submit(project_id, "suno", suno_service.run_suno_step0,
                             dry_run=False, overrides=body.overrides())
    return {"status": "submitted", "kind": "suno", "mode": "live"}


@router.post("/resume")
def suno_resume(project_id: int, db: Session = Depends(get_session)):
    """Tiếp tục batch đang dở (PAUSED/blocking). KHÔNG tạo lại/tải lại/import lại
    những gì đã xong — resume dựa trên manifest bền vững."""
    _project_or_404(db, project_id)
    _guard_busy()
    video_job_manager.submit(project_id, "suno", suno_service.run_suno_step0,
                             dry_run=False, resume=True)
    return {"status": "submitted", "kind": "suno", "mode": "resume"}


@router.post("/pause")
def suno_pause(project_id: int, db: Session = Depends(get_session)):
    """Tạm dừng: dừng ở checkpoint gần nhất, giữ state để resume."""
    _project_or_404(db, project_id)
    ok = video_job_manager.cancel(project_id)   # → JobCancelled → PAUSED
    return {"status": "pausing" if ok else "no-active-job"}


@router.post("/cancel")
def suno_cancel(project_id: int, db: Session = Depends(get_session)):
    """Huỷ hẳn batch (terminal). Nếu job đang chạy → yêu cầu huỷ; nếu không →
    đánh dấu batch CANCELLED trực tiếp."""
    _project_or_404(db, project_id)
    suno_service.request_cancel(project_id)
    if video_job_manager.cancel(project_id):
        return {"status": "cancelling"}
    result = suno_service.mark_cancelled(project_id)
    return {"status": "cancelled" if result else "no-active-batch",
            "batch": result}


@router.post("/open-browser")
def suno_open_browser(project_id: int, db: Session = Depends(get_session)):
    """Mở trình duyệt Suno (profile riêng) để người dùng ĐĂNG NHẬP, rồi tự KIỂM
    TRA + tự sửa/lưu selector khi giao diện Suno đổi. KHÔNG tạo/tải gì → không
    tốn credit. Giữ cửa sổ mở tới khi bấm 'Đóng trình duyệt'."""
    _project_or_404(db, project_id)
    _guard_busy()
    video_job_manager.submit(project_id, "suno-login",
                             suno_selfcheck.run_login_and_check)
    return {"status": "submitted", "kind": "suno-login"}


@router.post("/close-browser")
def suno_close_browser(project_id: int, db: Session = Depends(get_session)):
    """Đóng trình duyệt đăng nhập/kiểm tra selector (huỷ job giữ cửa sổ)."""
    _project_or_404(db, project_id)
    ok = video_job_manager.cancel(project_id)
    return {"status": "closing" if ok else "no-active-job"}


@router.get("/progress")
async def suno_progress(project_id: int):
    async def gen():
        async for event in video_job_manager.stream(project_id):
            yield event
    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})
