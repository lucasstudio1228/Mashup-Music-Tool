"""Quản lý track của 1 project: scan folder, add file, upload, xóa."""
import re
import shutil
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlmodel import Session, select

from backend import core_bridge
from backend.database import get_session
from backend.models import Track
from backend.routers.projects import get_project_or_404
from backend.schemas import (AddFileRequest, ScanRequest, ScanResult,
                             TrackResponse)
from backend.video import config as video_config

router = APIRouter(prefix="/projects", tags=["tracks"])


def _safe_filename(name: str) -> str:
    """Bỏ ký tự nguy hiểm khỏi tên file upload (chống path traversal)."""
    base = Path(name or "").name                       # bỏ mọi thành phần thư mục
    base = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", base).strip(" .")
    return base or "upload"


def _delete_track_stems(session: Session, track: Track) -> None:
    """Xóa stem files + TrackStem record của 1 track (FK track_id → track.id)."""
    import shutil
    from backend.models import TrackStem
    from backend.stem_service import STEMS_ROOT

    stem_dir = STEMS_ROOT / str(track.project_id) / str(track.id)
    if stem_dir.exists():
        shutil.rmtree(stem_dir, ignore_errors=True)
    stem = session.exec(
        select(TrackStem).where(TrackStem.track_id == track.id)).first()
    if stem:
        session.delete(stem)


def _existing_paths(session: Session, project_id: int) -> set[str]:
    rows = session.exec(
        select(Track.filepath).where(Track.project_id == project_id)).all()
    return {str(Path(p).resolve()) for p in rows}


def _persist(session: Session, project_id: int, core_track) -> Track:
    track = Track(
        project_id=project_id,
        filename=Path(core_track.path).name,
        filepath=str(Path(core_track.path).resolve()),
        duration_seconds=core_track.duration_seconds,
        sample_rate=core_track.sample_rate,
        channels=core_track.channels,
        format=core_track.format,
        is_lossy=core_track.is_lossy,
        subtype=core_track.subtype,
    )
    session.add(track)
    return track


@router.get("/{project_id}/tracks", response_model=list[TrackResponse])
def list_tracks(project_id: int, session: Session = Depends(get_session)):
    get_project_or_404(session, project_id)
    return session.exec(
        select(Track).where(Track.project_id == project_id)
        .order_by(Track.added_at)).all()


@router.post("/{project_id}/tracks/scan", response_model=ScanResult)
def scan_folder(project_id: int, data: ScanRequest,
                session: Session = Depends(get_session)):
    get_project_or_404(session, project_id)
    folder = Path(data.folder_path)
    if not folder.exists() or not folder.is_dir():
        raise HTTPException(404, f"Folder không tồn tại: {data.folder_path}")

    core_tracks, error_files = core_bridge.scan_folder(
        str(folder), recursive=data.recursive)
    existing = _existing_paths(session, project_id)

    added = 0
    skipped_duplicates = 0
    warning_lossy = False
    for core_track in core_tracks:
        resolved = str(Path(core_track.path).resolve())
        if resolved in existing:
            skipped_duplicates += 1
            continue
        _persist(session, project_id, core_track)
        existing.add(resolved)
        added += 1
        warning_lossy = warning_lossy or core_track.is_lossy
    session.commit()

    total = len(session.exec(
        select(Track).where(Track.project_id == project_id)).all())
    return ScanResult(
        added=added, skipped_duplicates=skipped_duplicates,
        skipped_errors=len(error_files), error_files=error_files,
        total_tracks=total, warning_lossy=warning_lossy,
    )


@router.post("/{project_id}/tracks/add-file", response_model=TrackResponse)
def add_file(project_id: int, data: AddFileRequest,
             session: Session = Depends(get_session)):
    get_project_or_404(session, project_id)
    path = Path(data.file_path)
    if not path.exists() or not path.is_file():
        raise HTTPException(404, f"File không tồn tại: {data.file_path}")

    resolved = str(path.resolve())
    if resolved in _existing_paths(session, project_id):
        raise HTTPException(409, "File đã có trong project")

    core_track = core_bridge.probe_file(str(path))
    if core_track is None:
        raise HTTPException(400, f"Không đọc được file audio: {path.name}")

    track = _persist(session, project_id, core_track)
    session.commit()
    session.refresh(track)
    return track


@router.post("/{project_id}/tracks/upload", response_model=TrackResponse)
def upload_track(project_id: int,
                 file: UploadFile = File(...),
                 session: Session = Depends(get_session)):
    """Upload 1 file audio TỪ MÁY người dùng → lưu vào media/<project>/uploads/,
    đăng ký thành track VÀ dùng luôn làm nhạc nền video (find_latest_audio quét
    thư mục uploads). Cho phép 'upload track dài rồi tạo video' không cần mix."""
    project = get_project_or_404(session, project_id)

    fname = _safe_filename(file.filename or "upload")
    ext = Path(fname).suffix.lower()
    if ext not in video_config.AUDIO_EXTS:
        raise HTTPException(
            400, f"Định dạng '{ext or '?'}' không hỗ trợ. Chấp nhận: "
                 f"{', '.join(video_config.AUDIO_EXTS)}")

    up_dir = video_config.uploads_dir(project_id, project.name)
    up_dir.mkdir(parents=True, exist_ok=True)
    dest = up_dir / fname
    # Tránh ghi đè file trùng tên: thêm hậu tố _1, _2, ...
    if dest.exists():
        stem, suffix = Path(fname).stem, Path(fname).suffix
        i = 1
        while (up_dir / f"{stem}_{i}{suffix}").exists():
            i += 1
        dest = up_dir / f"{stem}_{i}{suffix}"

    try:
        with dest.open("wb") as out:
            shutil.copyfileobj(file.file, out)
    finally:
        file.file.close()

    resolved = str(dest.resolve())
    if resolved in _existing_paths(session, project_id):
        raise HTTPException(409, "File đã có trong project")

    core_track = core_bridge.probe_file(str(dest))
    if core_track is None:
        dest.unlink(missing_ok=True)                   # dọn file rác nếu không đọc được
        raise HTTPException(400, f"Không đọc được file audio: {fname}")

    track = _persist(session, project_id, core_track)
    session.commit()
    session.refresh(track)
    return track


@router.delete("/{project_id}/tracks/{track_id}")
def delete_track(project_id: int, track_id: int,
                 session: Session = Depends(get_session)):
    track = session.get(Track, track_id)
    if track is None or track.project_id != project_id:
        raise HTTPException(404, f"Track {track_id} không tồn tại trong project")
    _delete_track_stems(session, track)   # cascade: stems trước
    session.delete(track)
    session.commit()
    return {"ok": True}


@router.delete("/{project_id}/tracks")
def delete_all_tracks(project_id: int, session: Session = Depends(get_session)):
    get_project_or_404(session, project_id)
    tracks = session.exec(
        select(Track).where(Track.project_id == project_id)).all()
    for track in tracks:
        _delete_track_stems(session, track)   # cascade: stems trước
        session.delete(track)
    session.commit()
    return {"deleted": len(tracks)}
