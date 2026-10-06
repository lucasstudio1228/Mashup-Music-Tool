"""
tasks.py — Thanh tác vụ TOÀN CỤC: gom mọi tác vụ đang chạy/xếp hàng (worker
Video/Suno/Upload, worker Mix, tách stem, lô sản xuất) + vài tác vụ vừa xong,
để UI luôn hiện 1 thanh ở dưới dù đang xem project nào. Chỉ đọc, không đổi gì.
"""
from __future__ import annotations

import time

from fastapi import APIRouter, Depends
from sqlmodel import Session, select

from backend.database import get_session
from backend.models import BatchRun, Mix, Project, TrackStem

router = APIRouter(prefix="/api/tasks", tags=["tasks"])

RECENT_SECONDS = 15 * 60
_ACTIVE = ("pending", "running", "paused")

# kind của video_job_manager → (nhãn, tab trong trang project)
_VIDEO_KIND = {
    "suno": ("Suno — tạo & tải WAV", "suno"),
    "suno-login": ("Suno — đăng nhập / kiểm tra selector", "suno"),
    "suno-fix": ("Tạo lại bài Suno lỗi", "audio"),
    "remux": ("Thay nhạc trong final.mp4", "audio"),
    "upload": ("Đăng nháp YouTube", "video"),
    "full": ("Dựng video (ảnh → clip → ghép)", "video"),
    "rebuild": ("Dựng video từ ảnh có sẵn (clip → ghép)", "video"),
    "prompts": ("Chuẩn bị prompt video", "video"),
    "motions": ("Tạo lại prompt chuyển động", "video"),
    "clips-manual": ("Clip thủ công", "video"),
    "retitle": ("Đổi tên — cập nhật chữ thumbnail/intro", "video"),
    "images": ("Tạo ảnh", "video"),
    "clips": ("Tạo clip", "video"),
    "assemble": ("Ghép video", "video"),
}


def _name(db: Session, cache: dict, pid) -> str:
    if pid is None:
        return ""
    if pid not in cache:
        p = db.get(Project, pid)
        cache[pid] = p.name if p else f"(project #{pid} đã xoá)"
    return cache[pid]


def _suno_counts(pid: int) -> str:
    try:
        from backend.video import suno_service
        b = suno_service.batch_status(pid) or {}
        if b.get("target_tracks"):
            return (f"tạo {b.get('selected_tracks', 0)} · tải "
                    f"{b.get('downloaded_tracks', 0)} · import "
                    f"{b.get('imported_tracks', 0)}/{b['target_tracks']}")
    except Exception:       # noqa: BLE001
        pass
    return ""


@router.get("/active")
def active_tasks(db: Session = Depends(get_session)):
    from backend.job_manager import job_manager
    from backend.stem_job_manager import stem_job_manager
    from backend.video.job_manager import video_job_manager

    now = time.time()
    names: dict = {}
    tasks, recent = [], []

    # 1) Worker Video/Suno/Upload (1 luồng — pending = đang xếp hàng)
    for j in video_job_manager.all_jobs():
        label, tab = _VIDEO_KIND.get(j.kind, (j.kind, "video"))
        item = {
            "id": f"video:{j.project_id}", "source": "video",
            "project_id": j.project_id,
            "project_name": _name(db, names, j.project_id),
            "kind": j.kind, "label": label, "tab": tab,
            "status": j.status, "percent": round(j.percent or 0, 1),
            "message": (j.message or "")[:300],
            "error": (j.error or "").split("\n")[0][:300] or None,
            "detail": _suno_counts(j.project_id)
                      if j.kind == "suno" and j.status in _ACTIVE else "",
        }
        if j.status in _ACTIVE:
            tasks.append(item)
        elif j.finished_at and now - j.finished_at <= RECENT_SECONDS:
            item["ago_seconds"] = int(now - j.finished_at)
            recent.append(item)

    # 2) Worker Mix (riêng)
    for j in job_manager.all_jobs():
        active = j.status in _ACTIVE
        if not active and not (j.finished_at and now - j.finished_at <= RECENT_SECONDS):
            continue
        mix = db.get(Mix, j.mix_id)
        pid = mix.project_id if mix else None
        item = {
            "id": f"mix:{j.mix_id}", "source": "mix", "project_id": pid,
            "project_name": _name(db, names, pid),
            "kind": "mix", "label": f"Mix audio · {mix.title}" if mix and mix.title
                                    else f"Mix audio #{j.mix_id}",
            "tab": "audio", "status": j.status,
            "percent": round(j.percent or 0, 1),
            "message": (j.message or j.step_name or "")[:300],
            "error": (j.error or "")[:300] or None, "detail": "",
        }
        if active:
            tasks.append(item)
        else:
            item["ago_seconds"] = int(now - j.finished_at)
            recent.append(item)

    # 3) Tách stem (Demucs)
    for sid, st, msg in stem_job_manager.active():
        stem = db.get(TrackStem, sid)
        pid = stem.project_id if stem else None
        tasks.append({
            "id": f"stem:{sid}", "source": "stem", "project_id": pid,
            "project_name": _name(db, names, pid), "kind": "stem",
            "label": "Tách stem (Demucs)", "tab": "audio", "status": st,
            "percent": None, "message": msg[:300], "error": None, "detail": "",
        })

    # 4) Lô sản xuất hàng loạt đang chạy (thread orchestrator còn sống)
    try:
        from backend import batch_service
        for run in db.exec(select(BatchRun).where(BatchRun.status == "running")).all():
            t = batch_service._threads.get(run.batch_key)
            if t is None or not t.is_alive():
                continue
            done = run.completed + run.failed
            tasks.append({
                "id": f"batch:{run.batch_key}", "source": "batch",
                "project_id": run.current_project_id,
                "project_name": _name(db, names, run.current_project_id),
                "kind": "batch",
                "label": f"Lô sản xuất — sản phẩm "
                         f"{min(run.current_index + 1, run.total)}/{run.total}",
                "tab": "suno", "status": "running",
                "percent": round(100 * done / run.total, 1) if run.total else 0,
                "message": (run.message or "")[:300], "error": None,
                "detail": f"{run.failed} lỗi" if run.failed else "",
            })
    except Exception:       # noqa: BLE001
        pass

    order = {"batch": 0, "video": 1, "mix": 2, "stem": 3}
    tasks.sort(key=lambda t: (order.get(t["source"], 9), t["status"] == "pending"))
    recent.sort(key=lambda t: t["ago_seconds"])
    return {"tasks": tasks, "recent": recent[:3]}
