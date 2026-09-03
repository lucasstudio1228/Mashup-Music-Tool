"""Mix lifecycle: tạo job, theo dõi SSE, tải file, xóa."""
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from sqlmodel import Session, select

from backend import core_bridge
from backend.database import engine, get_session
from backend.job_manager import job_manager
from backend.models import Mix, Project, Track
from backend.routers.projects import get_project_or_404
from backend.schemas import MixCreate, MixResponse

router = APIRouter(tags=["mixes"])

_OUTPUTS_ROOT = Path(__file__).parent.parent.parent / "outputs"
DB_PATH = str(Path(__file__).parent.parent.parent / "data" / "app.db")
MIN_TRACKS = 15
DOWNLOAD_FILES = {
    "wav": ("mix.wav", "audio/wav"),
    "cue": ("mix.cue", "text/plain"),
    "tracklist": ("mix_tracklist.txt", "text/plain"),
    "json": ("mix_info.json", "application/json"),
}


def to_mix_response(mix: Mix) -> MixResponse:
    """Merge DB record với progress realtime từ job_manager (nếu đang chạy)."""
    job = job_manager.get_status(mix.id)
    percent = job.percent if job else (100.0 if mix.status == "completed" else 0.0)
    return MixResponse(
        id=mix.id, project_id=mix.project_id, title=mix.title,
        status=(job.status if job else mix.status),
        created_at=mix.created_at, completed_at=mix.completed_at,
        duration_minutes=mix.duration_minutes,
        crossfade_seconds=mix.crossfade_seconds, sample_rate=mix.sample_rate,
        bit_depth=mix.bit_depth, output_dir=mix.output_dir,
        total_duration_seconds=mix.total_duration_seconds,
        track_count=mix.track_count, error_message=mix.error_message,
        progress_percent=percent,
        progress_step=job.step if job else 0,
        progress_step_name=job.step_name if job else "",
    )


def _get_mix_or_404(session: Session, mix_id: int) -> Mix:
    mix = session.get(Mix, mix_id)
    if mix is None:
        raise HTTPException(404, f"Mix {mix_id} không tồn tại")
    return mix


# ---------- Job callbacks (chạy trong worker thread → session riêng) ----------
def _on_success(mix_id: int, result: dict) -> None:
    with Session(engine) as session:
        mix = session.get(Mix, mix_id)
        if mix:
            mix.status = "completed"
            mix.completed_at = datetime.now(timezone.utc)
            mix.total_duration_seconds = result.get("total_duration_seconds")
            mix.track_count = result.get("track_count")
            mix.output_dir = result.get("output_dir")
            session.add(mix)
            session.commit()


def _on_failure(mix_id: int, error: str) -> None:
    with Session(engine) as session:
        mix = session.get(Mix, mix_id)
        if mix:
            mix.status = "failed"
            mix.error_message = error
            mix.completed_at = datetime.now(timezone.utc)
            session.add(mix)
            session.commit()


# ---------- Endpoints ----------
@router.get("/projects/{project_id}/mixes", response_model=list[MixResponse])
def list_mixes(project_id: int, session: Session = Depends(get_session)):
    get_project_or_404(session, project_id)
    mixes = session.exec(
        select(Mix).where(Mix.project_id == project_id)
        .order_by(Mix.created_at.desc())).all()
    return [to_mix_response(m) for m in mixes]


@router.post("/projects/{project_id}/mixes", response_model=MixResponse)
def create_mix(project_id: int, data: MixCreate,
               session: Session = Depends(get_session)):
    project = get_project_or_404(session, project_id)

    tracks = session.exec(
        select(Track).where(Track.project_id == project_id)).all()
    if len(tracks) < MIN_TRACKS:
        raise HTTPException(
            400, f"Cần tối thiểu {MIN_TRACKS} track, hiện có {len(tracks)}")
    if job_manager.is_busy():
        raise HTTPException(409, "Đang có 1 mix render. Vui lòng đợi hoàn tất.")

    title = data.title or f"Mix {datetime.now():%Y-%m-%d %H:%M}"
    mix = Mix(
        project_id=project_id, title=title, status="pending",
        duration_minutes=data.duration_minutes,
        crossfade_seconds=data.crossfade_seconds,
        sample_rate=data.sample_rate, bit_depth=data.bit_depth,
    )
    session.add(mix)
    session.commit()
    session.refresh(mix)

    output_dir = _OUTPUTS_ROOT / str(project_id) / str(mix.id)
    mix.output_dir = str(output_dir)
    session.add(mix)
    session.commit()
    session.refresh(mix)

    track_paths = [t.filepath for t in tracks]

    job_manager.submit(
        mix.id, core_bridge.run_mix_job,
        track_paths, str(output_dir), data.duration_minutes,
        data.crossfade_seconds, data.sample_rate, data.bit_depth, DB_PATH,
        on_success=_on_success, on_failure=_on_failure,
    )
    return to_mix_response(mix)


@router.get("/mixes/{mix_id}", response_model=MixResponse)
def get_mix(mix_id: int, session: Session = Depends(get_session)):
    return to_mix_response(_get_mix_or_404(session, mix_id))


@router.delete("/mixes/{mix_id}")
def delete_mix(mix_id: int, session: Session = Depends(get_session)):
    mix = _get_mix_or_404(session, mix_id)
    if job_manager.is_busy() and mix.status in ("pending", "running"):
        job = job_manager.get_status(mix_id)
        if job and job.status in ("pending", "running"):
            raise HTTPException(409, "Mix đang render, không thể xóa.")
    if mix.output_dir and Path(mix.output_dir).exists():
        shutil.rmtree(mix.output_dir, ignore_errors=True)
    session.delete(mix)
    session.commit()
    return {"ok": True}


@router.get("/mixes/{mix_id}/progress")
async def mix_progress(mix_id: int):
    async def event_generator():
        async for event in job_manager.stream(mix_id):
            data = json.dumps(event["data"])
            yield f"event: {event['type']}\ndata: {data}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@router.get("/mixes/{mix_id}/download/{file_type}")
def download_mix_file(mix_id: int, file_type: str,
                      session: Session = Depends(get_session)):
    mix = _get_mix_or_404(session, mix_id)
    if file_type not in DOWNLOAD_FILES:
        raise HTTPException(400, f"file_type không hợp lệ: {file_type}")
    if not mix.output_dir:
        raise HTTPException(404, "Mix chưa có output")

    filename, media_type = DOWNLOAD_FILES[file_type]
    filepath = Path(mix.output_dir) / filename
    if not filepath.exists():
        raise HTTPException(404, f"File không tồn tại: {filename}")

    safe_title = "".join(c if c.isalnum() or c in " -_" else "_"
                         for c in mix.title).strip() or "mix"
    return FileResponse(
        path=str(filepath),
        filename=f"{safe_title}_{filepath.name}",
        media_type=media_type,
    )
