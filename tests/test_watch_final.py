"""Nút «Xem Final»: GET /video/final phát inline (tua được), POST /video/open-final
mở trình phát máy. DB tạm + MEDIA_ROOT tạm — không đụng backend / media thật."""
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from backend.video import config


class WatchFinalTests(unittest.TestCase):
    def setUp(self):
        from fastapi.testclient import TestClient
        from sqlmodel import Session, SQLModel, create_engine
        from backend.database import get_session
        from backend.main import app
        from backend.models import Project
        tmp = TemporaryDirectory(prefix="watch-final-")
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        engine = create_engine(f"sqlite:///{root / 's.sqlite'}")
        SQLModel.metadata.create_all(engine)
        self.addCleanup(engine.dispose)
        with Session(engine) as s:
            proj = Project(name="Moon Lake")
            s.add(proj)
            s.commit()
            self.pid = proj.id

        def _session():
            with Session(engine) as s:
                yield s
        app.dependency_overrides[get_session] = _session
        self.addCleanup(app.dependency_overrides.clear)
        p = mock.patch.object(config, "MEDIA_ROOT", root / "media")
        p.start()
        self.addCleanup(p.stop)
        self.client = TestClient(app)
        self.base = f"/api/projects/{self.pid}/video"

    def _make_final(self, data: bytes = b"\x00" * 4096) -> Path:
        d = config.final_dir(self.pid, "Moon Lake")
        d.mkdir(parents=True, exist_ok=True)
        f = d / "final.mp4"
        f.write_bytes(data)
        return f

    def test_missing_final_404(self):
        self.assertEqual(self.client.get(f"{self.base}/final").status_code, 404)
        with mock.patch("os.startfile", create=True) as sf:
            self.assertEqual(self.client.post(f"{self.base}/open-final").status_code, 404)
        sf.assert_not_called()

    def test_final_streams_inline_with_range(self):
        self._make_final(bytes(range(256)) * 16)
        r = self.client.get(f"{self.base}/final?t=123")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.headers["content-type"], "video/mp4")
        self.assertNotIn("attachment", r.headers.get("content-disposition", ""))
        self.assertEqual(r.headers.get("accept-ranges"), "bytes")
        r = self.client.get(f"{self.base}/final", headers={"Range": "bytes=10-19"})
        self.assertEqual(r.status_code, 206)
        self.assertEqual(r.content, bytes(range(10, 20)))

    def test_open_final_uses_system_player(self):
        f = self._make_final()
        with mock.patch("os.startfile", create=True) as sf:
            r = self.client.post(f"{self.base}/open-final")
        self.assertEqual(r.status_code, 200)
        sf.assert_called_once_with(str(f))

    def test_status_reports_final_mtime(self):
        f = self._make_final()
        from backend.routers import video as V
        with mock.patch.object(V, "video_job_manager") as jm:
            jm.get.return_value = None
            jm.is_busy.return_value = False
            r = self.client.get(f"{self.base}/status")
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertTrue(body["final_exists"])
        self.assertEqual(body["final_mtime"], int(f.stat().st_mtime))
        self.assertEqual(body["final_size"], 4096)


if __name__ == "__main__":
    unittest.main()
