"""Router phần Video: tạo ảnh, tạo clip, ghép video, theo dõi tiến độ."""
import json
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel
from sqlmodel import Session

from backend.database import get_session
from backend.models import Project
from backend.video import config, service
from backend.video.job_manager import video_job_manager

router = APIRouter(prefix="/api/projects/{project_id}/video", tags=["video"])


class RunImagesBody(BaseModel):
    topic: Optional[str] = None
    idea: Optional[str] = None      # ý tưởng người dùng → Claude viết prompt
    mode: str = "restart"          # "restart" = làm lại từ đầu | "resume" = tạo tiếp

class RunClipsBody(BaseModel):
    mode: str = "restart"          # "restart" | "resume"

class AssembleBody(BaseModel):
    audio_path: Optional[str] = None
    seed: Optional[int] = None

class RunAllBody(BaseModel):
    topic: Optional[str] = None
    idea: Optional[str] = None      # ý tưởng người dùng → Claude viết prompt
    audio_path: Optional[str] = None
    seed: Optional[int] = None

class RebuildBody(BaseModel):
    audio_path: Optional[str] = None
    seed: Optional[int] = None
    mode: str = "restart"          # clip mode: "restart" (tạo lại clip) | "resume"


def _project_or_404(db: Session, project_id: int) -> Project:
    p = db.get(Project, project_id)
    if not p:
        raise HTTPException(404, f"Project {project_id} không tồn tại")
    return p

def _guard_busy():
    if video_job_manager.is_busy():
        raise HTTPException(409, "Đang có 1 tác vụ video chạy. Vui lòng đợi.")


@router.get("/styles")
def list_styles(project_id: int):
    """Danh sách phong cách hình ảnh/video cho bộ chọn ở UI (cuộn được)."""
    return {
        "default": config.DEFAULT_STYLE,
        "styles": [
            {
                "key": key,
                "icon": s.get("icon", ""),
                "label": s.get("label", key),
                "desc": s.get("desc", ""),
            }
            for key, s in config.STYLES.items()
        ],
    }


@router.get("/status")
def status(project_id: int, db: Session = Depends(get_session)):
    project = _project_or_404(db, project_id)
    media = service.list_media(project_id, project.name)
    job = video_job_manager.get(project_id)
    p = config.PARAMS
    media["job"] = None if not job else {
        "kind": job.kind, "status": job.status,
        "percent": job.percent, "message": job.message, "error": job.error,
    }
    media["params"] = {
        "image_count": p.image_count, "clip_seconds": p.clip_seconds,
        "rest_clips": p.rest_clips, "total_clips": p.total_clips,
        "t_window": p.t_window, "model": p.flow_model,
        "aspect_ratio": p.aspect_ratio, "flow_mode": p.flow_mode,
        "blend_seconds": p.blend_seconds, "slow_speed": p.slow_speed,
    }
    return media


@router.get("/prompts")
def prompt_manifest(project_id: int, db: Session = Depends(get_session)):
    from backend.video.prompt_workflow import read_manifest
    project = _project_or_404(db, project_id)
    try:
        return {"manifest": read_manifest(project_id, project.name)}
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("/prompts/prepare")
def prepare_prompts(project_id: int, body: RunImagesBody = RunImagesBody(),
                    db: Session = Depends(get_session)):
    project = _project_or_404(db, project_id)
    _guard_busy()
    idea = body.idea if body.idea is not None else project.video_idea
    video_job_manager.submit(project_id, "prompts", service.step_prepare_prompts,
                             project.name, idea, project.video_style)
    return {"status": "submitted", "kind": "prompts"}


@router.post("/images")
def gen_images(project_id: int, body: RunImagesBody,
               db: Session = Depends(get_session)):
    project = _project_or_404(db, project_id)
    _guard_busy()
    topic = (body.topic or project.name or "meditation").strip()
    mode = "resume" if body.mode == "resume" else "restart"
    idea = (body.idea if body.idea is not None else project.video_idea).strip() or None
    video_job_manager.submit(project_id, "images", service.step_images,
                             topic, mode, project.name, idea, project.video_style)
    return {"status": "submitted", "kind": "images", "topic": topic,
            "mode": mode, "has_idea": bool(idea)}


@router.post("/clips")
def gen_clips(project_id: int, body: RunClipsBody = RunClipsBody(),
              db: Session = Depends(get_session)):
    project = _project_or_404(db, project_id)
    _guard_busy()
    mode = "resume" if body.mode == "resume" else "restart"
    video_job_manager.submit(project_id, "clips", service.step_clips,
                             mode, project.name, project.video_style)
    return {"status": "submitted", "kind": "clips", "mode": mode}


@router.post("/assemble")
def assemble(project_id: int, body: AssembleBody,
             db: Session = Depends(get_session)):
    project = _project_or_404(db, project_id)
    _guard_busy()
    video_job_manager.submit(project_id, "assemble", service.step_assemble,
                             body.audio_path, body.seed, project.name)
    return {"status": "submitted", "kind": "assemble"}


@router.post("/run-all")
def run_all(project_id: int, body: RunAllBody,
            db: Session = Depends(get_session)):
    project = _project_or_404(db, project_id)
    _guard_busy()
    topic = (body.topic or project.name or "meditation").strip()
    idea = (body.idea if body.idea is not None else project.video_idea).strip() or None
    video_job_manager.submit(project_id, "full", service.run_full_video,
                             topic, body.audio_path, body.seed, project.name,
                             idea, project.video_style)
    return {"status": "submitted", "kind": "full", "topic": topic,
            "has_idea": bool(idea)}


@router.post("/rebuild")
def rebuild(project_id: int, body: RebuildBody = RebuildBody(),
            db: Session = Depends(get_session)):
    """Tạo lại VIDEO từ ảnh ĐÃ CÓ (không tạo lại ảnh): clip → ghép."""
    project = _project_or_404(db, project_id)
    _guard_busy()
    media = service.list_media(project_id, project.name)
    if media["image_count"] < config.PARAMS.image_count:
        raise HTTPException(
            400, f"Chưa đủ {config.PARAMS.image_count} ảnh để tạo lại video "
                 f"(hiện có {media['image_count']}). Hãy tạo ảnh trước.")
    mode = "resume" if body.mode == "resume" else "restart"
    video_job_manager.submit(project_id, "rebuild", service.run_video_from_images,
                             body.audio_path, body.seed, project.name,
                             project.video_style, mode)
    return {"status": "submitted", "kind": "rebuild", "mode": mode}


@router.post("/images/{index}/regenerate")
def regen_image(project_id: int, index: int,
                db: Session = Depends(get_session)):
    """Tạo lại ĐÚNG 1 ảnh (ghi đè), giữ nguyên ảnh khác + dùng lại prompt đã lưu."""
    project = _project_or_404(db, project_id)
    _guard_busy()
    if not (0 <= index < config.PARAMS.image_count):
        raise HTTPException(400, f"Chỉ số ảnh {index} không hợp lệ "
                                 f"(0..{config.PARAMS.image_count - 1}).")
    video_job_manager.submit(project_id, "images", service.regen_image,
                             index, project.name, project.video_style)
    return {"status": "submitted", "kind": "images", "index": index,
            "single": True}


@router.post("/clips/{index}/regenerate")
def regen_clip(project_id: int, index: int,
               db: Session = Depends(get_session)):
    """Tạo lại ĐÚNG 1 clip (ghi đè), giữ nguyên clip khác (cần _plan.json)."""
    project = _project_or_404(db, project_id)
    _guard_busy()
    if not (0 <= index < config.PARAMS.image_count):
        raise HTTPException(400, f"Chỉ số clip {index} không hợp lệ "
                                 f"(0..{config.PARAMS.image_count - 1}).")
    video_job_manager.submit(project_id, "clips", service.regen_clip,
                             index, project.name, project.video_style)
    return {"status": "submitted", "kind": "clips", "index": index,
            "single": True}


@router.post("/motions/regenerate")
def regen_motions(project_id: int, db: Session = Depends(get_session)):
    """Sinh lại CHỈ prompt chuyển động (motions) từ prompt ảnh đã lưu."""
    project = _project_or_404(db, project_id)
    _guard_busy()
    video_job_manager.submit(project_id, "motions", service.regen_motions,
                             project.name, project.video_style)
    return {"status": "submitted", "kind": "motions"}


@router.post("/upload-youtube")
def upload_youtube(project_id: int, db: Session = Depends(get_session)):
    """Đăng NHÁP video final.mp4 lên YouTube qua GPMLogin (thủ công). Chọn kênh
    theo (instrument, music_style) của project. KHÔNG publish — chỉ lưu nháp."""
    project = _project_or_404(db, project_id)
    _guard_busy()
    final = config.final_dir(project_id, project.name) / "final.mp4"
    if not final.exists():
        raise HTTPException(400, "Chưa có final.mp4 — hãy render video trước.")
    if not (project.instrument or "").strip() or not (project.music_style or "").strip():
        raise HTTPException(
            400, "Chưa nhập 'nhạc cụ' và 'phong cách nhạc' cho project.")
    video_job_manager.submit(project_id, "upload", service.step_upload_youtube,
                             project.name, project.instrument, project.music_style)
    return {"status": "submitted", "kind": "upload"}


@router.get("/youtube-history")
def youtube_history(project_id: int, db: Session = Depends(get_session)):
    """Lịch sử các lần đăng nháp của project (title/description/hashtag/thời gian)."""
    from backend.models import YoutubeUpload
    from sqlmodel import select
    _project_or_404(db, project_id)
    rows = db.exec(
        select(YoutubeUpload).where(YoutubeUpload.project_id == project_id)
        .order_by(YoutubeUpload.id.desc())
    ).all()
    return [{
        "id": r.id, "channel_name": r.channel_name, "title": r.title,
        "hashtags": r.hashtags, "status": r.status,
        "error_message": r.error_message,
        "created_at": r.created_at.isoformat() if r.created_at else None,
    } for r in rows]


@router.post("/cancel")
def cancel(project_id: int, db: Session = Depends(get_session)):
    """Huỷ tác vụ video đang chạy (hợp tác — dừng ở ranh giới ảnh/clip kế)."""
    _project_or_404(db, project_id)
    ok = video_job_manager.cancel(project_id)
    if not ok:
        raise HTTPException(409, "Không có tác vụ video nào đang chạy để huỷ.")
    return {"status": "cancelling", "project_id": project_id}


@router.get("/progress")
async def progress(project_id: int):
    async def gen():
        async for event in video_job_manager.stream(project_id):
            yield f"event: {event.get('type','message')}\ndata: {json.dumps(event)}\n\n"
    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no",
                                      "Connection": "keep-alive"})


@router.get("/download")
def download(project_id: int, db: Session = Depends(get_session)):
    project = _project_or_404(db, project_id)
    final = config.final_dir(project_id, project.name) / "final.mp4"
    if not final.exists():
        raise HTTPException(404, "Chưa có video final. Hãy ghép video trước.")
    safe = "".join(c if c.isalnum() or c in " -_" else "_"
                   for c in (project.name or "video")).strip()
    return FileResponse(str(final), media_type="video/mp4",
                        filename=f"{safe or 'video'}_final.mp4")


@router.get("/image/{index}")
def get_image(project_id: int, index: int,
              db: Session = Depends(get_session)):
    """Trả ảnh {index}.png để UI hiển thị gallery (xem trước)."""
    project = _project_or_404(db, project_id)
    img = config.images_dir(project_id, project.name) / f"{index}.png"
    if not img.exists():
        raise HTTPException(404, f"Chưa có ảnh {index}.")
    return FileResponse(str(img), media_type="image/png")


@router.get("/clip/{index}")
def get_clip(project_id: int, index: int,
             db: Session = Depends(get_session)):
    """Trả clip_{index}.mp4 để UI xem trước từng clip."""
    project = _project_or_404(db, project_id)
    clip = config.clips_dir(project_id, project.name) / f"clip_{index:02d}.mp4"
    if not clip.exists():
        raise HTTPException(404, f"Chưa có clip {index}.")
    return FileResponse(str(clip), media_type="video/mp4")


@router.get("/thumbnail")
def thumbnail(project_id: int, db: Session = Depends(get_session)):
    project = _project_or_404(db, project_id)
    thumb = config.final_dir(project_id, project.name) / "thumbnail.png"
    if not thumb.exists():
        raise HTTPException(404, "Chưa có thumbnail. Hãy ghép video (assemble) "
                                 "để tạo thumbnail kèm chữ.")
    safe = "".join(c if c.isalnum() or c in " -_" else "_"
                   for c in (project.name or "video")).strip()
    return FileResponse(str(thumb), media_type="image/png",
                        filename=f"{safe or 'video'}_thumbnail.png")
