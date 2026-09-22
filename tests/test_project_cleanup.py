"""Project deletion regression tests: temporary DB/files only, never real media."""
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

from sqlalchemy import event
from sqlmodel import Session, SQLModel, create_engine, select

from backend.models import (Mix, Project, SunoBatch, SunoCandidate, Track,
                            TrackStem, YoutubeUpload)
from backend.project_cleanup import (CleanupRoots, ProjectBusyError,
                                     ProjectCleanupError, _validate,
                                     delete_project_data)


class ProjectCleanupTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory(prefix="mixer-cleanup-test-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.roots = CleanupRoots(self.root)
        self.engine = create_engine(f"sqlite:///{self.root / 'test.sqlite'}")

        @event.listens_for(self.engine, "connect")
        def enable_foreign_keys(connection, _):
            connection.execute("PRAGMA foreign_keys=ON")

        SQLModel.metadata.create_all(self.engine)
        self.addCleanup(self.engine.dispose)
        self.session = Session(self.engine)
        self.addCleanup(self.session.close)
        self.audio = SimpleNamespace(_lock=threading.Lock(), _jobs={})
        self.stem = SimpleNamespace(_lock=threading.Lock(), _statuses={}, _futures={})
        self.video = SimpleNamespace(_lock=threading.Lock(), _jobs={})
        manager_patch = patch("backend.project_cleanup._managers",
                              return_value=(self.audio, self.stem, self.video))
        manager_patch.start()
        self.addCleanup(manager_patch.stop)
        self.project = Project(id=7, name="New Name")
        self.other = Project(id=17, name="Other")
        self.session.add_all([self.project, self.other])
        self.session.commit()
        self.external = self.root / "external-imports" / "original.wav"
        self.external.parent.mkdir()
        self.external.write_bytes(b"original audio")
        self.track = Track(project_id=7, filename="original.wav",
                           filepath=str(self.external), duration_seconds=180,
                           sample_rate=48000, channels=2, format="WAV", is_lossy=False)
        self.session.add(self.track)
        self.session.commit()
        self.track_stem = TrackStem(project_id=7, track_id=self.track.id)
        self.mix = Mix(project_id=7, title="test mix", status="completed",
                       output_dir=str(self.external.parent))
        self.batch = SunoBatch(project_id=7, idempotency_key="test", phase="COMPLETED",
                               staging_dir=str(self.external.parent))
        self.upload = YoutubeUpload(project_id=7, profile_id="fake")
        self.session.add_all([self.track_stem, self.mix, self.batch, self.upload])
        self.session.commit()
        self.candidate = SunoCandidate(project_id=7, batch_id=self.batch.id,
                                       song_id="test-song", wav_path=str(self.external))
        self.session.add(self.candidate)
        self.session.commit()
        self.owned = [self.roots.outputs / "7", self.roots.stems / "7",
                      self.roots.media / "New Name (#7)",
                      self.roots.media / "Old Name (#7)",
                      self.roots.media / "7", self.roots.media / "project_7",
                      self.roots.suno / "project_7"]
        self.protected = [self.roots.outputs / "17", self.roots.stems / "17",
                          self.roots.media / "Old Name (#17)",
                          self.roots.media / "New Name",  # ambiguous legacy name
                          self.roots.suno / "project_17"]
        for folder in (*self.owned, *self.protected):
            folder.mkdir(parents=True)
            (folder / "asset.dat").write_bytes(b"test data")

    def assert_untouched(self):
        self.assertEqual(self.external.read_bytes(), b"original audio")
        self.assertIsNotNone(self.session.get(Project, 17))
        for folder in self.protected:
            self.assertEqual((folder / "asset.dat").read_bytes(), b"test data")

    def test_deletes_owned_paths_and_all_dependent_records(self):
        result = delete_project_data(self.session, self.project, self.roots)
        self.assertTrue(result["ok"])
        self.assertEqual(result["cleanup_pending"], [])
        self.assertEqual(result["warnings"], [])
        self.assertEqual(set(result["deleted_paths"]), {str(p) for p in self.owned})
        for folder in self.owned:
            self.assertFalse(folder.exists())
        for model in (SunoCandidate, TrackStem, YoutubeUpload, SunoBatch, Mix, Track):
            self.assertEqual(self.session.exec(
                select(model).where(model.project_id == 7)).all(), [])
        self.assertIsNone(self.session.get(Project, 7))
        self.assertEqual(json.loads(Path(result["cleanup_journal"]).read_text(
            encoding="utf-8"))["phase"], "completed")
        self.assert_untouched()

    def test_db_failure_restores_all_files_and_records(self):
        with patch.object(self.session, "commit", side_effect=RuntimeError("DB failure")):
            with self.assertRaisesRegex(ProjectCleanupError, "rollback"):
                delete_project_data(self.session, self.project, self.roots)
        self.assertIsNotNone(self.session.get(Project, 7))
        self.assertEqual(len(self.session.exec(select(SunoCandidate)).all()), 1)
        for folder in self.owned:
            self.assertEqual((folder / "asset.dat").read_bytes(), b"test data")
        manifest = next(self.roots.journals.glob("*/manifest.json"))
        self.assertEqual(json.loads(manifest.read_text(encoding="utf-8"))["phase"],
                         "restored")
        self.assert_untouched()

    def test_rename_failure_restores_prior_moves_without_db_deletion(self):
        original_rename = Path.rename

        def fail_one(path, destination):
            if path == self.roots.stems / "7":
                raise PermissionError("file held open")
            return original_rename(path, destination)

        with patch.object(Path, "rename", fail_one):
            with self.assertRaisesRegex(ProjectCleanupError, "rollback"):
                delete_project_data(self.session, self.project, self.roots)
        self.assertIsNotNone(self.session.get(Project, 7))
        for folder in self.owned:
            self.assertTrue((folder / "asset.dat").exists())
        self.assert_untouched()

    def test_purge_failure_reports_recoverable_quarantine(self):
        with patch("backend.project_cleanup.shutil.rmtree",
                   side_effect=PermissionError("file held open")):
            result = delete_project_data(self.session, self.project, self.roots)
        self.assertTrue(result["ok"])
        self.assertIsNone(self.session.get(Project, 7))
        self.assertEqual(len(result["cleanup_pending"]), len(self.owned))
        for path in result["cleanup_pending"]:
            self.assertEqual((Path(path) / "asset.dat").read_bytes(), b"test data")
        self.assertEqual(json.loads(Path(result["cleanup_journal"]).read_text(
            encoding="utf-8"))["phase"], "cleanup_pending")
        self.assert_untouched()

    def test_busy_video_audio_stems_and_live_suno_lock_refuse_delete(self):
        cases = [
            (self.video._jobs, 7, SimpleNamespace(status="running")),
            (self.audio._jobs, self.mix.id, SimpleNamespace(status="pending")),
            (self.stem._statuses, self.track_stem.id, "running"),
        ]
        for state, key, value in cases:
            with self.subTest(worker=state, key=key):
                state[key] = value
                with self.assertRaises(ProjectBusyError):
                    delete_project_data(self.session, self.project, self.roots)
                state.clear()
                self.assertIsNotNone(self.session.get(Project, 7))
                self.assertTrue(all(folder.exists() for folder in self.owned))
        self.batch.lock_token = "live-worker"
        self.batch.lock_expires_at = datetime.now(timezone.utc) + timedelta(minutes=5)
        self.session.add(self.batch)
        self.session.commit()
        with self.assertRaises(ProjectBusyError):
            delete_project_data(self.session, self.project, self.roots)
        self.assertFalse(self.roots.journals.exists())
        self.assert_untouched()

    def test_other_project_busy_does_not_block_target(self):
        self.video._jobs[17] = SimpleNamespace(status="running")
        result = delete_project_data(self.session, self.project, self.roots)
        self.assertTrue(result["ok"])
        self.assert_untouched()

    def test_rejects_outside_paths_and_container_root(self):
        with self.assertRaises(ProjectCleanupError):
            _validate(self.root / "outside", self.roots.media)
        with self.assertRaises(ProjectCleanupError):
            _validate(self.roots.media, self.roots.media)

    def test_rejects_nested_symlink_without_touching_original(self):
        link = self.owned[0] / "external-link"
        try:
            link.symlink_to(self.external.parent, target_is_directory=True)
        except (NotImplementedError, OSError) as exc:
            self.skipTest(f"OS does not permit test symlinks: {exc}")
        with self.assertRaisesRegex(ProjectCleanupError, "symlink/junction"):
            delete_project_data(self.session, self.project, self.roots)
        self.assertIsNotNone(self.session.get(Project, 7))
        self.assert_untouched()

    def test_rejects_windows_reparse_attribute_without_needing_symlink_privilege(self):
        from backend.project_cleanup import _reject_links
        import stat
        original = Path.lstat
        target = self.owned[0]
        def fake_stat(path):
            if path == target:
                return SimpleNamespace(st_mode=stat.S_IFDIR, st_file_attributes=0x400)
            return original(path)
        with patch.object(Path, "lstat", fake_stat):
            with self.assertRaisesRegex(ProjectCleanupError, "symlink/junction"):
                _reject_links(target)
        self.assert_untouched()


if __name__ == "__main__":
    unittest.main()
