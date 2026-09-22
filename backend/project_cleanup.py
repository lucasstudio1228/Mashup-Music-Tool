"""Delete only a project's owned files, with rollback before DB commit.

Imported Track.filepath/Mix.output_dir/SunoBatch.staging_dir values are never
used as deletion targets. A failed purge leaves a clearly reported quarantine;
the small recovery journal under data/project_deletions records its location.
"""
from __future__ import annotations

from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import stat
import uuid

from sqlmodel import Session, select

from backend.models import (Mix, Project, SunoBatch, SunoCandidate, Track,
                            TrackStem, YoutubeUpload)


class ProjectCleanupError(RuntimeError):
    """Deletion stopped without silently discarding failed filesystem work."""


class ProjectBusyError(ProjectCleanupError):
    """A project is still being used by a worker."""


@dataclass(frozen=True)
class CleanupRoots:
    workspace: Path

    @property
    def media(self) -> Path:
        return self.workspace / "media"

    @property
    def outputs(self) -> Path:
        return self.workspace / "outputs"

    @property
    def stems(self) -> Path:
        return self.workspace / "stems"

    @property
    def suno(self) -> Path:
        return self.workspace / "data" / "suno_batches"

    @property
    def journals(self) -> Path:
        return self.workspace / "data" / "project_deletions"


DEFAULT_ROOTS = CleanupRoots(Path(__file__).resolve().parent.parent)
_ACTIVE = {"pending", "running"}


def _managers():
    # Lazy imports keep path/DB tests independent of the audio runtime.
    from backend.job_manager import job_manager
    from backend.stem_job_manager import stem_job_manager
    from backend.video.job_manager import video_job_manager
    return job_manager, stem_job_manager, video_job_manager


@contextmanager
def _guard_idle(session: Session, project_id: int):
    """Serialize this check/delete against worker state updates and submits.

Managers expose no shared project lifecycle API yet, so keep their existing
locks for the short rename + DB transaction, never for the expensive purge.
"""
    mixes = session.exec(select(Mix).where(Mix.project_id == project_id)).all()
    stems = session.exec(
        select(TrackStem).where(TrackStem.project_id == project_id)).all()
    audio, stem, video = _managers()
    with ExitStack() as stack:
        for manager in (audio, stem, video):
            stack.enter_context(manager._lock)
        video_job = video._jobs.get(project_id)
        busy = bool(video_job and video_job.status in _ACTIVE)
        busy = busy or any(
            (job := audio._jobs.get(mix.id)) and job.status in _ACTIVE
            for mix in mixes)
        busy = busy or any(
            stem._statuses.get(item.id) in _ACTIVE
            or ((future := stem._futures.get(item.id)) is not None
                and not future.done())
            for item in stems)
        now = datetime.now(timezone.utc)
        for batch in session.exec(
                select(SunoBatch).where(SunoBatch.project_id == project_id)).all():
            expiry = batch.lock_expires_at
            if expiry and expiry.tzinfo is None:
                expiry = expiry.replace(tzinfo=timezone.utc)
            busy = busy or bool(batch.lock_token and expiry and expiry > now)
        if busy:
            raise ProjectBusyError(
                "Project đang có tác vụ chạy/chờ. Hãy dừng tác vụ và chờ dừng "
                "hoàn toàn trước khi xoá project.")
        yield


def _absolute(path: Path) -> Path:
    return Path(os.path.abspath(path))


def _reject_links(path: Path) -> None:
    """Reject Windows junctions/reparse points as well as POSIX symlinks."""
    path = _absolute(path)
    for part in (*reversed(path.parents), path):
        try:
            info = part.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or (
                getattr(info, "st_file_attributes", 0)
                & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)):
            raise ProjectCleanupError(f"Không xoá qua symlink/junction: {part}")


def _validate(path: Path, root: Path, *, inspect_tree: bool = False) -> None:
    path, root = _absolute(path), _absolute(root)
    _reject_links(root)
    _reject_links(path)
    try:
        relative = path.resolve().relative_to(root.resolve())
    except ValueError as exc:
        raise ProjectCleanupError(f"Đường dẫn nằm ngoài vùng project: {path}") from exc
    if not relative.parts:
        raise ProjectCleanupError(f"Không được xoá thư mục gốc: {root}")
    if path.exists() and not path.is_dir():
        raise ProjectCleanupError(f"Đường dẫn project không phải thư mục: {path}")
    if inspect_tree and path.exists():
        # os.walk(followlinks=False) plus explicit checks also catches junctions
        # before rmtree can traverse them on an older Windows/Python runtime.
        for current, directories, files in os.walk(path, followlinks=False):
            for name in (*directories, *files):
                _reject_links(Path(current) / name)


def _owned_paths(project: Project, roots: CleanupRoots) -> list[tuple[Path, Path]]:
    project_id = project.id
    if not isinstance(project_id, int) or project_id <= 0:
        raise ProjectCleanupError("Project ID không hợp lệ để xoá dữ liệu.")
    from backend.video.config import project_folder_name

    for root in (roots.media, roots.outputs, roots.stems, roots.suno, roots.journals):
        _validate(root, roots.workspace)
    media_names = {
        str(project_id), f"project_{project_id}",
        project_folder_name(project.name, project_id),
    }
    if roots.media.exists():
        # ID suffix is an ownership marker, so old names after a rename remain
        # owned. Bare legacy names are ambiguous and deliberately not guessed.
        media_names.update(
            item.name for item in roots.media.iterdir()
            if item.name.endswith(f" (#{project_id})"))
    candidates = [(roots.outputs, roots.outputs / str(project_id)),
                  (roots.stems, roots.stems / str(project_id)),
                  (roots.suno, roots.suno / f"project_{project_id}")]
    candidates.extend((roots.media, roots.media / name) for name in sorted(media_names))
    result = []
    for root, path in candidates:
        _validate(path, root, inspect_tree=True)
        if path.exists():
            result.append((root, path))
    return result


def _write_journal(path: Path, document: dict) -> None:
    # Atomic replacement avoids leaving an unreadable recovery record.
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(document, ensure_ascii=False, indent=2),
                         encoding="utf-8")
    temporary.replace(path)


def _delete_records(session: Session, project: Project) -> None:
    # Explicit flushes preserve FK ordering even when SQLite FK checks are on.
    for model in (SunoCandidate, TrackStem, YoutubeUpload, SunoBatch, Mix, Track):
        for record in session.exec(
                select(model).where(model.project_id == project.id)).all():
            session.delete(record)
        session.flush()
    session.delete(project)
    session.flush()


def delete_project_data(session: Session, project: Project,
                        roots: CleanupRoots = DEFAULT_ROOTS) -> dict:
    """Delete DB + owned files; restore staged files if the DB commit fails."""
    token = f"project_{project.id}_{uuid.uuid4().hex}"
    moved: list[tuple[Path, Path, Path]] = []
    warnings: list[str] = []
    pending: list[str] = []
    with _guard_idle(session, project.id):
        targets = _owned_paths(project, roots)
        journal_dir = roots.journals / token
        _validate(journal_dir, roots.journals)
        journal_dir.mkdir(parents=True)
        journal_path = journal_dir / "manifest.json"
        document = {
            "project_id": project.id, "project_name": project.name,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "phase": "staging", "paths": [
                {"original": str(path),
                 "quarantine": str(root / ".project-trash" / token / path.name)}
                for root, path in targets],
        }
        _write_journal(journal_path, document)
        try:
            for root, source in targets:
                destination = root / ".project-trash" / token / source.name
                _validate(destination, root)
                destination.parent.mkdir(parents=True, exist_ok=True)
                # Both endpoints are verified and within the same owned root;
                # rename is recoverable and does not copy large media files.
                source.rename(destination)
                moved.append((root, source, destination))
            document["phase"] = "staged"
            _write_journal(journal_path, document)
            _delete_records(session, project)
            session.commit()
        except Exception as exc:
            session.rollback()
            restore_errors = []
            for root, source, destination in reversed(moved):
                try:
                    _validate(source, root)
                    _validate(destination, root)
                    if source.exists():
                        raise ProjectCleanupError(f"Đích khôi phục đã tồn tại: {source}")
                    destination.rename(source)
                except Exception as restore_exc:
                    restore_errors.append(str(restore_exc))
            document["phase"] = "restore_pending" if restore_errors else "restored"
            document["error"] = str(exc)
            document["restore_errors"] = restore_errors
            try:
                _write_journal(journal_path, document)
            except OSError:
                pass  # Initial staging journal still contains recovery paths.
            detail = f"Không xoá project; giao dịch đã rollback. Nhật ký: {journal_path}."
            if restore_errors:
                detail += " Cần khôi phục thư mục quarantine: " + "; ".join(restore_errors)
            raise ProjectCleanupError(detail) from exc
        document["phase"] = "committed"
        try:
            _write_journal(journal_path, document)
        except OSError as exc:
            warnings.append(f"Không cập nhật được nhật ký sau commit: {exc}")

    # All DB records are now gone. Purge errors must never masquerade as a
    # rollback: retain the quarantined files and report their exact locations.
    deleted = []
    for root, source, destination in moved:
        try:
            _validate(destination, root, inspect_tree=True)
            shutil.rmtree(destination)
            deleted.append(str(source))
            if destination.parent.exists() and not any(destination.parent.iterdir()):
                destination.parent.rmdir()
        except OSError as exc:
            pending.append(str(destination))
            warnings.append(f"Chưa dọn xong quarantine {destination}: {exc}")
        except ProjectCleanupError as exc:
            pending.append(str(destination))
            warnings.append(str(exc))
    document.update(phase="cleanup_pending" if pending else "completed",
                    cleanup_pending=pending, warnings=warnings)
    try:
        _write_journal(journal_path, document)
    except OSError as exc:
        warnings.append(f"Không cập nhật được nhật ký dọn file: {exc}")
    return {"ok": True, "deleted_paths": deleted, "cleanup_pending": pending,
            "warnings": warnings, "cleanup_journal": str(journal_path)}
