"""CRUD cho /api/projects."""
from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select

from backend.database import get_session
from backend.models import Mix, Project, Track
from backend.schemas import (BatchCreate, BatchRunResponse, ProjectCreate,
                             ProjectDetailResponse, ProjectResponse,
                             ProjectRename, ProjectUpdate, MixResponse,
                             TrackResponse)

router = APIRouter(prefix="/projects", tags=["projects"])

def _counts(session: Session, project_id: int) -> tuple[int, int]:
    tracks = session.exec(
        select(Track).where(Track.project_id == project_id)).all()
    mixes = session.exec(
        select(Mix).where(Mix.project_id == project_id)).all()
    return len(tracks), len(mixes)


def to_response(session: Session, project: Project) -> ProjectResponse:
    track_count, mix_count = _counts(session, project.id)
    return ProjectResponse(
        id=project.id, name=project.name, description=project.description,
        created_at=project.created_at,
        track_count=track_count, mix_count=mix_count,
        video_idea=project.video_idea, auto_video=project.auto_video,
        video_style=project.video_style,
        instrument=project.instrument, music_style=project.music_style,
        auto_upload=project.auto_upload,
        music_source=getattr(project, "music_source", "local"),
        suno_idea=getattr(project, "suno_idea", ""),
        batch_id=getattr(project, "batch_id", None),
        mix_duration_minutes=project.mix_duration_minutes or 120.0,
        mix_crossfade_seconds=project.mix_crossfade_seconds or 5.0,
    )


_IDEA_FIELDS = ("video_idea", "suno_idea")


def _ideas_to_english(values: dict) -> dict:
    """Ý tưởng người dùng nhập được lưu bằng TIẾNG ANH để mọi prompt đồng nhất.
    Dịch lỗi → giữ nguyên bản gốc (generator vẫn dịch lại lúc chạy)."""
    from backend.video.translate import needs_translation, to_english_or_none
    for key in _IDEA_FIELDS:
        text = values.get(key)
        if isinstance(text, str) and needs_translation(text):
            translated = to_english_or_none(
                text, log=lambda m: print(f"[translate] {m}", flush=True))
            if translated:
                values[key] = translated
    return values


def _canonical_instrument(name: str) -> str:
    """Nhạc cụ trong danh mục → nhãn chuẩn (vd 'piano' → 'Piano'); tên tự nhập giữ nguyên."""
    from backend.video.music_spec import find_instrument
    spec = find_instrument(name or "")
    if not spec:
        return ""
    return spec["label"] if spec["key"] != "custom" else name.strip()


def get_project_or_404(session: Session, project_id: int) -> Project:
    project = session.get(Project, project_id)
    if project is None:
        raise HTTPException(404, f"Project {project_id} không tồn tại")
    return project


@router.get("", response_model=list[ProjectResponse])
def list_projects(session: Session = Depends(get_session)):
    projects = session.exec(select(Project).order_by(Project.created_at.desc())).all()
    return [to_response(session, p) for p in projects]


@router.post("", response_model=ProjectResponse)
def create_project(data: ProjectCreate, session: Session = Depends(get_session)):
    values = _ideas_to_english(data.model_dump())
    values["instrument"] = _canonical_instrument(values.get("instrument", ""))
    values["music_style"] = (values.get("music_style") or "").strip()
    project = Project(**values)
    session.add(project)
    session.commit()
    session.refresh(project)
    return to_response(session, project)


# ---------- Batch: sản xuất hàng loạt N sản phẩm YouTube ----------
@router.post("/batch", response_model=BatchRunResponse)
def create_batch(data: BatchCreate):
    """Sinh N ý tưởng phân biệt (AI, KHÔNG tốn credit Suno) → tạo N project cùng
    batch_id → chạy TUẦN TỰ trọn pipeline (Suno→Mix→Video→đăng nháp) cho từng cái.
    Cảnh báo ngân sách + xác nhận đã thực hiện ở phía UI trước khi gọi."""
    from backend import batch_service
    try:
        return batch_service.create_batch(
            count=data.count, instrument=data.instrument,
            music_style=data.music_style, video_style=data.video_style,
            theme=data.theme, target_tracks=data.target_tracks,
            max_create_actions=data.max_create_actions,
            channel_name=data.channel_name,
            mix_duration_minutes=data.mix_duration_minutes,
        )
    except RuntimeError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("/instruments")
def instrument_catalog():
    """Danh mục nhạc cụ + thể loại cho form New Project (đơn lẻ và hàng loạt)."""
    from backend.video import music_spec
    return {
        "instruments": music_spec.instrument_options(),
        "genres": [{"key": g["key"], "label": g["label"],
                    "bpm": list(g["bpm"]), "beatless": g["beatless"]}
                   for g in music_spec.GENRES],
    }


@router.get("/batch/styles")
def batch_styles():
    """Danh sách phong cách ảnh cho form tạo lô (chưa có project nên không dùng
    được /api/projects/{id}/video/styles). Cùng nguồn video/config.STYLES."""
    from backend.video import config as video_config
    return {
        "default": video_config.DEFAULT_STYLE,
        "styles": [
            {"key": key, "icon": s.get("icon", ""),
             "label": s.get("label", key), "desc": s.get("desc", "")}
            for key, s in video_config.STYLES.items()
        ],
    }


@router.get("/batch/active")
def active_batch():
    """Lô đang chạy (thread orchestrator còn sống) — để UI theo dõi đúng lô kể cả
    khi request tạo lô bị timeout phía trình duyệt nên chưa nhận được batch_key."""
    from backend import batch_service
    return {"batch_key": batch_service._active_batch_key()}


@router.get("/batch/{batch_key}", response_model=BatchRunResponse)
def get_batch(batch_key: str):
    from backend import batch_service
    run = batch_service.get_batch(batch_key)
    if run is None:
        raise HTTPException(404, f"Không tìm thấy lô {batch_key}")
    return run


@router.post("/batch/{batch_key}/cancel", response_model=BatchRunResponse)
def cancel_batch(batch_key: str):
    from backend import batch_service
    run = batch_service.cancel_batch(batch_key)
    if run is None:
        raise HTTPException(404, f"Không tìm thấy lô {batch_key}")
    return run


@router.post("/batch/{batch_key}/resume", response_model=BatchRunResponse)
def resume_batch(batch_key: str):
    """Chạy tiếp lô đã dừng: bỏ qua sản phẩm đã xong, nối lại chỗ dừng."""
    from backend import batch_service
    try:
        run = batch_service.resume_batch(batch_key)
    except ValueError as e:
        raise HTTPException(409, str(e))
    if run is None:
        raise HTTPException(404, f"Không tìm thấy lô {batch_key}")
    return run


@router.get("/{project_id}", response_model=ProjectDetailResponse)
def get_project(project_id: int, session: Session = Depends(get_session)):
    from backend.routers.mixes import to_mix_response
    project = get_project_or_404(session, project_id)
    base = to_response(session, project)
    tracks = session.exec(
        select(Track).where(Track.project_id == project_id)
        .order_by(Track.added_at)).all()
    mixes = session.exec(
        select(Mix).where(Mix.project_id == project_id)
        .order_by(Mix.created_at.desc())).all()
    return ProjectDetailResponse(
        **base.model_dump(),
        tracks=[TrackResponse.model_validate(t, from_attributes=True) for t in tracks],
        mixes=[to_mix_response(m) for m in mixes],
    )


@router.patch("/{project_id}", response_model=ProjectResponse)
def update_project(project_id: int, data: ProjectUpdate,
                   session: Session = Depends(get_session)):
    project = get_project_or_404(session, project_id)
    values = data.model_dump(exclude_unset=True)
    new_name = values.pop("name", None)
    if new_name is not None and new_name.strip() != (project.name or ""):
        # Tên gắn với thư mục media + chữ ký prompt → đi qua luồng đổi tên an toàn.
        _do_rename(session, project, new_name)
    for key, value in _ideas_to_english(values).items():
        if key == "video_style":
            from backend.video import config as video_config
            value = video_config.normalize_style(value)
        elif key == "instrument" and (value or "").strip() != (project.instrument or ""):
            # Chỉ chuẩn hoá khi THẬT SỰ đổi — giữ nguyên chuỗi cũ (vd lấy từ ánh xạ
            # kênh) để chữ ký hồ sơ prompt của project đã có ảnh không bị lệch.
            value = _canonical_instrument(value or "")
        setattr(project, key, value)
    session.add(project)
    session.commit()
    session.refresh(project)
    return to_response(session, project)


def _do_rename(session: Session, project: Project, new_name: str) -> dict:
    from backend.project_cleanup import ProjectBusyError
    from backend.project_rename import RenameError, rename_project
    try:
        return rename_project(session, project, new_name)
    except ProjectBusyError as exc:
        raise HTTPException(409, str(exc)) from exc
    except RenameError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/{project_id}/rename")
def rename(project_id: int, data: ProjectRename,
           session: Session = Depends(get_session)):
    """Đổi tên project (kèm thư mục media, đường dẫn DB, tiêu đề prompt), rồi
    tuỳ `refresh` cập nhật CHỮ tiêu đề trên thumbnail + intro video:
    none = chỉ đổi tên · text = vẽ lại chữ + ghép lại video ·
    regen = tạo lại ảnh bìa (Gemini) + clip 00 (Flow) rồi như text."""
    from backend.video import config as vconfig
    from backend.video import service as vservice
    from backend.video.job_manager import video_job_manager
    project = get_project_or_404(session, project_id)
    refresh = data.refresh or "none"
    if refresh not in ("none", "text", "regen"):
        raise HTTPException(400, "refresh phải là none | text | regen.")
    if refresh != "none" and video_job_manager.is_busy():
        raise HTTPException(409, "Đang có 1 tác vụ video chạy — chờ xong rồi "
                                 "đổi tên kèm cập nhật thumbnail.")
    if refresh == "regen" and not (
            vconfig.images_dir(project_id, project.name) / "prompts.json").exists():
        raise HTTPException(400, "Project chưa có bộ prompt/ảnh — chưa thể tạo "
                                 "lại ảnh bìa. Chọn «Chỉ đổi tên».")
    info = _do_rename(session, project, data.name)
    job = None
    if refresh != "none":
        video_job_manager.submit(project_id, "retitle", vservice.retitle_video,
                                 project.name, project.video_style,
                                 refresh == "regen")
        job = "retitle"
    return {**info, "job": job, "project": to_response(session, project)}


@router.delete("/{project_id}")
def delete_project(project_id: int, session: Session = Depends(get_session)):
    project = get_project_or_404(session, project_id)
    from backend.project_cleanup import (ProjectBusyError, ProjectCleanupError,
                                         delete_project_data)
    try:
        return delete_project_data(session, project)
    except ProjectBusyError as exc:
        raise HTTPException(409, str(exc)) from exc
    except (ProjectCleanupError, OSError) as exc:
        raise HTTPException(500, str(exc)) from exc
