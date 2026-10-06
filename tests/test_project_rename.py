"""Đổi tên project: thư mục media + đường dẫn DB + chữ ký prompt đi cùng tên
mới; project khác không bị đụng; tác vụ đang chạy → từ chối; lỗi → trả lại cũ.
Chỉ dùng DB/thư mục tạm."""
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

from backend.models import Project, Track, YoutubeUpload
from backend.project_cleanup import ProjectBusyError
from backend.project_rename import RenameError, _prompt_signature, rename_project
from backend.video import prompt_catalog


class ProjectRenameTests(unittest.TestCase):
    def setUp(self):
        tmp = TemporaryDirectory(prefix="mixer-rename-test-")
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.media = self.root / "media"
        self.engine = create_engine(f"sqlite:///{self.root / 't.sqlite'}")
        SQLModel.metadata.create_all(self.engine)
        self.addCleanup(self.engine.dispose)
        self.s = Session(self.engine)
        self.addCleanup(self.s.close)
        self.video = SimpleNamespace(_lock=threading.Lock(), _jobs={})
        p = patch("backend.project_cleanup._managers", return_value=(
            SimpleNamespace(_lock=threading.Lock(), _jobs={}),
            SimpleNamespace(_lock=threading.Lock(), _statuses={}, _futures={}),
            self.video))
        p.start()
        self.addCleanup(p.stop)

        self.p9 = Project(id=9, name="Old Title")
        self.p90 = Project(id=90, name="Old Title")          # cùng tên, id khác
        self.s.add_all([self.p9, self.p90])
        self.s.commit()
        self.old = self.media / "Old Title (#9)"
        (self.old / "images").mkdir(parents=True)
        (self.old / "images" / "0.png").write_bytes(b"png")
        other = self.media / "Old Title (#90)"
        other.mkdir(parents=True)
        self.s.add_all([
            Track(project_id=9, filename="a.wav", duration_seconds=1, sample_rate=1,
                  channels=2, format="WAV", is_lossy=False,
                  filepath=str(self.old / "suno_tracks" / "a.wav")),
            Track(project_id=90, filename="b.wav", duration_seconds=1, sample_rate=1,
                  channels=2, format="WAV", is_lossy=False,
                  filepath=str(other / "suno_tracks" / "b.wav")),
            YoutubeUpload(project_id=9, profile_id="x",
                          video_path=str(self.old / "final" / "final.mp4")),
        ])
        self.s.commit()
        inputs = {"context": {"title": "Old Title", "visual_idea": "v"}, "style": "2d",
                  "count": 3, "scene_count": 20, "style_version": 1,
                  "workflow_version": 1}
        sig = prompt_catalog.prompt_hash(json.dumps(inputs, ensure_ascii=False,
                                                    sort_keys=True))
        (self.old / "images" / "prompts.json").write_text(json.dumps(
            {**inputs, "input_signature": sig, "title": "Old Title",
             "prompts": {"0": "cover"}}), encoding="utf-8")

    def _rename(self, name):
        return rename_project(self.s, self.p9, name, media_root=self.media)

    def test_moves_folder_updates_paths_and_signature(self):
        info = self._rename("  New   Title ")
        new = self.media / "New Title (#9)"
        self.assertEqual(info["name"], "New Title")
        self.assertTrue(info["moved"])
        self.assertFalse(self.old.exists())
        self.assertTrue((new / "images" / "0.png").exists())
        tracks = {t.project_id: t.filepath for t in self.s.exec(select(Track)).all()}
        self.assertTrue(tracks[9].startswith(str(new)))
        self.assertIn("Old Title (#90)", tracks[90])     # project khác giữ nguyên
        up = self.s.exec(select(YoutubeUpload)).one()
        self.assertEqual(up.video_path, str(new / "final" / "final.mp4"))
        m = json.loads((new / "images" / "prompts.json").read_text(encoding="utf-8"))
        self.assertEqual(m["context"]["title"], "New Title")
        self.assertEqual(m["title"], "New Title")
        self.assertEqual(m["input_signature"], _prompt_signature(m))
        self.assertEqual(self.s.get(Project, 9).name, "New Title")

    def test_busy_project_is_refused_and_untouched(self):
        self.video._jobs[9] = SimpleNamespace(status="running")
        with self.assertRaises(ProjectBusyError):
            self._rename("New Title")
        self.assertTrue(self.old.exists())
        self.assertEqual(self.s.get(Project, 9).name, "Old Title")

    def test_empty_name_rejected(self):
        with self.assertRaises(RenameError):
            self._rename("   ")

    def test_target_folder_exists_rejected(self):
        (self.media / "Taken (#9)").mkdir()
        with self.assertRaises(RenameError):
            self._rename("Taken")
        self.assertTrue(self.old.exists())

    def test_folder_locked_gives_clear_error(self):
        with patch("backend.project_rename.os.rename",
                   side_effect=PermissionError("in use")):
            with self.assertRaises(RenameError) as cm:
                self._rename("New Title")
        self.assertIn("đang", str(cm.exception))
        self.assertEqual(self.s.get(Project, 9).name, "Old Title")

    def test_same_name_is_noop(self):
        info = self._rename("Old Title")
        self.assertFalse(info["changed"])
        self.assertTrue(self.old.exists())


class RenameEndpointTests(ProjectRenameTests):
    """POST /api/projects/{id}/rename: đổi tên + gửi job cập nhật chữ thumbnail
    (job video được mock — không mở Gemini/Flow)."""

    def setUp(self):
        super().setUp()
        from fastapi.testclient import TestClient
        from backend.database import get_session
        from backend.main import app

        def _session():
            with Session(self.engine) as s:
                yield s
        app.dependency_overrides[get_session] = _session
        self.addCleanup(app.dependency_overrides.clear)
        for target, value in (("backend.video.config.MEDIA_ROOT", self.media),):
            p = patch(target, value)
            p.start()
            self.addCleanup(p.stop)
        self.jobs = SimpleNamespace(is_busy=lambda: False, submitted=[])
        self.jobs.submit = lambda *a: self.jobs.submitted.append(a)
        p = patch("backend.video.job_manager.video_job_manager", self.jobs)
        p.start()
        self.addCleanup(p.stop)
        self.client = TestClient(app)       # không `with` → không chạy startup

    def test_endpoint_text_refresh_submits_retitle_job(self):
        r = self.client.post("/api/projects/9/rename",
                             json={"name": "New Title", "refresh": "text"})
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertEqual(body["project"]["name"], "New Title")
        self.assertEqual(body["job"], "retitle")
        (pid, kind, _fn, name, _style, regen), = self.jobs.submitted
        self.assertEqual((pid, kind, name, regen), (9, "retitle", "New Title", False))
        self.assertTrue((self.media / "New Title (#9)" / "images" / "0.png").exists())

    def test_endpoint_regen_and_none(self):
        r = self.client.post("/api/projects/9/rename",
                             json={"name": "Regen Title", "refresh": "regen"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertTrue(self.jobs.submitted[-1][-1])            # regen_cover=True
        r = self.client.post("/api/projects/9/rename",
                             json={"name": "Plain", "refresh": "none"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertIsNone(r.json()["job"])
        self.assertEqual(len(self.jobs.submitted), 1)

    def test_endpoint_busy_video_worker_409(self):
        self.jobs.is_busy = lambda: True
        r = self.client.post("/api/projects/9/rename",
                             json={"name": "New Title", "refresh": "text"})
        self.assertEqual(r.status_code, 409)
        self.assertTrue(self.old.exists())

    def test_endpoint_empty_name_400(self):
        r = self.client.post("/api/projects/9/rename",
                             json={"name": "  ", "refresh": "none"})
        self.assertEqual(r.status_code, 400)


if __name__ == "__main__":
    unittest.main()
