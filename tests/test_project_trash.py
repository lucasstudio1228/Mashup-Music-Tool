"""Thùng rác project (giữ 7 ngày): DB + thư mục tạm, không đụng dữ liệu thật."""
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlmodel import Session, SQLModel, create_engine, select

from backend.models import Mix, Project, Track
from backend.project_cleanup import CleanupRoots, ProjectBusyError, ProjectCleanupError
from backend import project_trash as trash

NOW = datetime(2026, 10, 8, 12, 0, tzinfo=timezone.utc)


class ProjectTrashTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory(prefix="mixer-trash-test-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.roots = CleanupRoots(self.root)
        self.engine = create_engine(f"sqlite:///{self.root / 'test.sqlite'}")
        SQLModel.metadata.create_all(self.engine)
        self.addCleanup(self.engine.dispose)
        self.session = Session(self.engine)
        self.addCleanup(self.session.close)
        self.audio = SimpleNamespace(_lock=threading.Lock(), _jobs={})
        self.stem = SimpleNamespace(_lock=threading.Lock(), _statuses={}, _futures={})
        self.video = SimpleNamespace(_lock=threading.Lock(), _jobs={})
        for target, value in (
                ("backend.project_cleanup._managers",
                 lambda: (self.audio, self.stem, self.video)),
                ("backend.project_cleanup._sleep", lambda _s: None),
                ("backend.win_locks.lockers", lambda *_a, **_k: [])):
            p = patch(target, value)
            p.start()
            self.addCleanup(p.stop)
        self.project = Project(id=7, name="Zen Flute")
        self.other = Project(id=8, name="Other")
        self.session.add_all([self.project, self.other])
        self.session.commit()
        self.session.add(Track(project_id=7, filename="a.wav", filepath="x/a.wav",
                               duration_seconds=180, sample_rate=48000, channels=2,
                               format="WAV", is_lossy=False))
        self.session.add(Mix(project_id=7, title="mix", status="completed"))
        self.session.commit()
        self.owned = [self.roots.media / "Zen Flute (#7)", self.roots.outputs / "7",
                      self.roots.stems / "7", self.roots.suno / "project_7"]
        self.protected = [self.roots.media / "Other (#8)", self.roots.outputs / "8"]
        for folder in (*self.owned, *self.protected):
            folder.mkdir(parents=True)
            (folder / "asset.dat").write_bytes(b"data " + folder.name.encode())

    def _trash(self, when=NOW):
        return trash.move_to_trash(self.session, self.project, self.roots, now=when)

    def test_move_keeps_db_and_data_in_trash_folder(self):
        info = self._trash()
        self.assertTrue(info["trashed"])
        self.assertEqual(info["in_place"], [])
        for folder in self.owned:
            self.assertFalse(folder.exists(), folder)
        entry = Path(info["trash_dir"])
        self.assertEqual(entry.parent, self.root / "Trash")
        self.assertTrue(entry.name.startswith("Zen Flute (#7)__20261008-120000"))
        self.assertTrue((entry / "media" / "Zen Flute (#7)" / "asset.dat").exists())
        self.assertTrue((entry / "outputs" / "7" / "asset.dat").exists())
        self.assertTrue((entry / "suno_batches" / "project_7" / "asset.dat").exists())
        manifest = json.loads((entry / trash.MANIFEST).read_text(encoding="utf-8"))
        self.assertEqual(manifest["phase"], "trashed")
        self.assertEqual(len(manifest["paths"]), 4)
        self.session.refresh(self.project)
        self.assertIsNotNone(self.project.deleted_at)
        self.assertEqual(len(self.session.exec(
            select(Track).where(Track.project_id == 7)).all()), 1)
        for folder in self.protected:
            self.assertTrue((folder / "asset.dat").exists())

    def test_busy_project_is_not_trashed(self):
        self.video._jobs[7] = SimpleNamespace(status="running")
        with self.assertRaises(ProjectBusyError):
            self._trash()
        for folder in self.owned:
            self.assertTrue(folder.exists())
        self.assertFalse((self.root / "Trash").exists()
                         and any((self.root / "Trash").iterdir()))
        self.session.refresh(self.project)
        self.assertIsNone(self.project.deleted_at)

    def test_restore_puts_everything_back(self):
        info = self._trash()
        trash.restore_from_trash(self.session, self.project, self.roots)
        for folder in self.owned:
            self.assertTrue((folder / "asset.dat").exists(), folder)
        self.assertFalse(Path(info["trash_dir"]).exists())
        self.session.refresh(self.project)
        self.assertIsNone(self.project.deleted_at)
        self.assertIsNone(self.project.trash_dir)

    def test_restore_refuses_to_overwrite_new_data(self):
        self._trash()
        (self.roots.outputs / "7").mkdir(parents=True)
        with self.assertRaises(ProjectCleanupError):
            trash.restore_from_trash(self.session, self.project, self.roots)
        # Không khôi phục nửa vời: media vẫn nằm trong thùng rác.
        self.assertFalse((self.roots.media / "Zen Flute (#7)").exists())
        self.session.refresh(self.project)
        self.assertIsNotNone(self.project.deleted_at)

    def test_locked_folder_stays_in_place_and_is_purged_later(self):
        real_rename = Path.rename

        def rename(path, target):
            if Path(path) == self.roots.outputs / "7":
                raise PermissionError(5, "Access is denied")
            return real_rename(path, target)

        with patch.object(Path, "rename", rename):
            info = self._trash()
        self.assertEqual(info["in_place"], [str(self.roots.outputs / "7")])
        self.assertTrue(info["warnings"])
        self.assertTrue((self.roots.outputs / "7").exists())
        trash.purge_trashed(self.session, self.project, self.roots)
        self.assertFalse((self.roots.outputs / "7").exists())
        self.assertFalse(Path(info["trash_dir"]).exists())
        self.assertIsNone(self.session.get(Project, 7))

    def test_purge_expired_only_after_seven_days(self):
        info = self._trash(NOW - timedelta(days=6, hours=23))
        result = trash.purge_expired(self.session, self.roots, now=NOW)
        self.assertEqual(result["purged"], [])
        self.assertTrue(Path(info["trash_dir"]).exists())
        later = NOW + timedelta(hours=2)
        result = trash.purge_expired(self.session, self.roots, now=later)
        self.assertEqual(result["purged"], ["Zen Flute"])
        self.assertGreater(result["freed_bytes"], 0)
        self.assertFalse(Path(info["trash_dir"]).exists())
        self.assertIsNone(self.session.get(Project, 7))
        self.assertEqual(self.session.exec(
            select(Track).where(Track.project_id == 7)).all(), [])
        self.assertIsNotNone(self.session.get(Project, 8))
        for folder in self.protected:
            self.assertTrue((folder / "asset.dat").exists())

    def test_naive_deleted_at_from_sqlite_is_treated_as_utc(self):
        self._trash(NOW - timedelta(days=8))
        self.project.deleted_at = self.project.deleted_at.replace(tzinfo=None)
        self.session.add(self.project)
        self.session.commit()
        self.assertEqual(trash.purge_expired(self.session, self.roots, now=NOW)["purged"],
                         ["Zen Flute"])

    def test_list_trash_reports_days_left_and_size(self):
        self._trash(NOW - timedelta(days=2))
        items = trash.list_trash(self.session, self.roots, now=NOW)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["id"], 7)
        self.assertAlmostEqual(items[0]["days_left"], 5.0, places=1)
        self.assertGreater(items[0]["size_bytes"], 0)

    def test_orphan_entry_without_project_is_swept_but_unknown_is_kept(self):
        info = self._trash(NOW - timedelta(days=1))
        entry = Path(info["trash_dir"])
        # Project bị xoá cứng theo đường khác → mục Trash/ thành mồ côi.
        self.session.delete(self.session.get(Project, 7))
        for row in self.session.exec(select(Track)).all():
            self.session.delete(row)
        for row in self.session.exec(select(Mix)).all():
            self.session.delete(row)
        self.session.commit()
        stray = self.root / "Trash" / "no-manifest"
        stray.mkdir()
        (stray / "keep.dat").write_bytes(b"?")
        trash.purge_expired(self.session, self.roots, now=NOW)
        self.assertFalse(entry.exists())
        self.assertTrue((stray / "keep.dat").exists())

    def test_cannot_trash_twice(self):
        self._trash()
        with self.assertRaises(ProjectCleanupError):
            self._trash()


if __name__ == "__main__":
    unittest.main()
