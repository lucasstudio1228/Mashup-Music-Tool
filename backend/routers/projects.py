"""CRUD cho /api/projects."""
import shutil
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select

from backend.database import get_session
from backend.models import Mix, Project, Track
from backend.schemas import (ProjectCreate, ProjectDetailResponse,
                             ProjectResponse, ProjectUpdate, MixResponse,
                             TrackResponse)

router = APIRouter(prefix="/projects", tags=["projects"])

_OUTPUTS_ROOT = Path(__file__).parent.parent.parent / "outputs"


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
    )


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
    project = Project(**data.model_dump())
    session.add(project)
    session.commit()
    session.refresh(project)
    return to_response(session, project)


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
    for key, value in data.model_dump(exclude_unset=True).items():
        if key == "video_style":
            from backend.video import config as video_config
            value = video_config.normalize_style(value)
        setattr(project, key, value)
    session.add(project)
    session.commit()
    session.refresh(project)
    return to_response(session, project)


@router.delete("/{project_id}")
def delete_project(project_id: int, session: Session = Depends(get_session)):
    project = get_project_or_404(session, project_id)

    # 1. Xóa output files trên disk
    project_outputs = _OUTPUTS_ROOT / str(project_id)
    if project_outputs.exists():
        shutil.rmtree(project_outputs, ignore_errors=True)

    # 2. Xóa stems directory + TrackStem records
    from backend.models import TrackStem
    from backend.stem_service import STEMS_ROOT
    stems_dir = STEMS_ROOT / str(project_id)
    if stems_dir.exists():
        shutil.rmtree(stems_dir, ignore_errors=True)
    for stem in session.exec(
            select(TrackStem).where(TrackStem.project_id == project_id)).all():
        session.delete(stem)

    # 3. Xóa mixes trong DB
    for mix in session.exec(
            select(Mix).where(Mix.project_id == project_id)).all():
        session.delete(mix)

    # 4. Xóa tracks trong DB
    for track in session.exec(
            select(Track).where(Track.project_id == project_id)).all():
        session.delete(track)

    # 5. Xóa project
    session.delete(project)
    session.commit()
    return {"ok": True}
