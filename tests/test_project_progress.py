"""Tình trạng project trên trang danh sách: chấm từ DB + file thật (thư mục tạm)."""
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlmodel import Session, SQLModel, create_engine

from backend.models import Mix, Project, SunoBatch, Track, YoutubeUpload
from backend.project_progress import STEPS, project_progress
from backend.video import config as vconfig

PARAMS = SimpleNamespace(image_count=3, total_clips=20)


class ProjectProgressTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory(prefix="progress-test-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.engine = create_engine(f"sqlite:///{self.root / 'db.sqlite'}")
        SQLModel.metadata.create_all(self.engine)
        self.addCleanup(self.engine.dispose)
        self.s = Session(self.engine)
        self.addCleanup(self.s.close)
        self.p = Project(name="Zen", music_source="suno")
        self.s.add(self.p)
        self.s.commit()
        self.s.refresh(self.p)
        base = self.root / "media"
        for name in ("images_dir", "clips_dir", "final_dir"):
            sub = base / name
            pt = patch.object(vconfig, name, lambda pid, pname=None, _d=sub: _d)
            pt.start()
            self.addCleanup(pt.stop)

    # ── helpers ──
    def _progress(self, **kw):
        kw.setdefault("params", PARAMS)
        return project_progress(self.s, self.p, **kw)

    def _step(self, res, key):
        return next(s for s in res["steps"] if s["key"] == key)

    def _tracks(self, n):
        for i in range(n):
            self.s.add(Track(project_id=self.p.id, filename=f"{i}.wav", filepath=f"x/{i}.wav",
                             duration_seconds=200, sample_rate=48000, channels=2,
                             format="WAV", is_lossy=False))
        self.s.commit()

    def _batch(self, phase, imported=0, downloaded=0):
        self.s.add(SunoBatch(project_id=self.p.id, idempotency_key=f"k{phase}", phase=phase,
                             target_tracks=15, imported_tracks=imported,
                             downloaded_tracks=downloaded, staging_dir=str(self.root)))
        self.s.commit()

    def _mix_done(self):
        out = self.root / "mix1"
        out.mkdir()
        (out / "mix.wav").write_bytes(b"RIFF")
        self.s.add(Mix(project_id=self.p.id, title="m", status="completed",
                       output_dir=str(out), total_duration_seconds=7200))
        self.s.commit()

    def _files(self, images=0, clips=0, final=False):
        for sub, n, pat in (("images_dir", images, "img_{}.png"),
                            ("clips_dir", clips, "clip_{:02d}.mp4")):
            d = self.root / "media" / sub
            d.mkdir(parents=True, exist_ok=True)
            for i in range(n):
                (d / pat.format(i)).write_bytes(b"x")
        if final:
            d = self.root / "media" / "final_dir"
            d.mkdir(parents=True, exist_ok=True)
            (d / "final.mp4").write_bytes(b"x")

    def _upload(self, short=False, status="draft", error=None):
        self.s.add(YoutubeUpload(project_id=self.p.id, profile_id="p", channel_name="Kênh A",
                                 video_path="final/short.mp4" if short else "final/final.mp4",
                                 status=status, error_message=error))
        self.s.commit()

    # ── tests ──
    def test_new_project_not_started(self):
        res = self._progress()
        self.assertEqual(res["state"], "new")
        self.assertEqual(res["stage"], "music")
        self.assertEqual([s["key"] for s in res["steps"]], [k for k, _ in STEPS])

    def test_full_chain_done(self):
        self._batch("COMPLETED", imported=15, downloaded=15)
        self._tracks(15)
        self._mix_done()
        self._files(images=3, clips=20, final=True)
        self._upload()
        self._upload(short=True)
        res = self._progress()
        self.assertEqual(res["state"], "done")
        self.assertEqual(res["done_steps"], len(STEPS))
        self.assertIn("Short", res["summary"])

    def test_stopped_after_draft_points_to_short(self):
        self._batch("COMPLETED", imported=15)
        self._tracks(15)
        self._mix_done()
        self._files(images=3, clips=20, final=True)
        self._upload()
        res = self._progress()
        self.assertEqual(res["state"], "stopped")
        self.assertEqual(res["stage"], "short")
        self.assertIn("tiếp theo: Short", res["summary"])

    def test_auto_short_off_counts_as_done(self):
        self._batch("COMPLETED", imported=15)
        self._tracks(15)
        self._mix_done()
        self._files(images=3, clips=20, final=True)
        self._upload()
        res = self._progress(auto_short=False)
        self.assertEqual(res["state"], "done")
        self.assertEqual(self._step(res, "short")["state"], "skipped")

    def test_suno_blocked_shows_reason(self):
        self._batch("INSUFFICIENT_DOWNLOAD_ALLOWANCE", downloaded=4)
        res = self._progress()
        self.assertEqual(res["state"], "blocked")
        self.assertIn("hết lượt tải Suno", res["summary"])

    def test_stale_suno_phase_with_all_tracks_imported_is_done(self):
        # Server tắt khi đang tạo lại bài lẻ → phase cũ kẹt DOWNLOADING.
        self._batch("DOWNLOADING", imported=15, downloaded=22)
        self._tracks(15)
        self._mix_done()
        res = self._progress()
        self.assertEqual(self._step(res, "music")["state"], "done")
        self.assertEqual(res["stage"], "images")

    def test_suno_interrupted_midway_is_stopped_at_music(self):
        self._batch("DOWNLOADING", imported=0, downloaded=6)
        res = self._progress()
        self.assertEqual(res["state"], "stopped")
        self.assertIn("6/15", res["summary"])

    def test_running_video_job_marks_current_step(self):
        self._batch("COMPLETED", imported=15)
        self._tracks(15)
        self._mix_done()
        self._files(images=3, clips=7)
        job = SimpleNamespace(kind="full", status="running", percent=42.0,
                              message="Clip 8/20", error=None)
        res = self._progress(video_job=job)
        self.assertEqual(res["state"], "running")
        self.assertEqual(res["stage"], "clips")
        self.assertEqual(res["percent"], 42.0)
        self.assertIn("Clip 8/20", res["summary"])

    def test_failed_video_job_reports_error_step(self):
        self._batch("COMPLETED", imported=15)
        self._tracks(15)
        self._mix_done()
        job = SimpleNamespace(kind="full", status="failed", percent=0, message="",
                              error="Gemini lỗi máy chủ")
        res = self._progress(video_job=job)
        self.assertEqual(res["state"], "error")
        self.assertEqual(res["stage"], "images")
        self.assertIn("Gemini", res["summary"])

    def test_running_mix_job(self):
        self._batch("COMPLETED", imported=15)
        self._tracks(15)
        self.s.add(Mix(project_id=self.p.id, title="m", status="running"))
        self.s.commit()
        mix_id = self.s.exec(Mix.__table__.select()).first().id
        job = SimpleNamespace(status="running", percent=55.0, step=3, step_name="Crossfade")
        res = self._progress(mix_jobs={mix_id: job})
        self.assertEqual(res["stage"], "mix")
        self.assertEqual(res["state"], "running")
        self.assertIn("Crossfade", res["summary"])

    def test_queued_in_batch_only_before_start(self):
        self.assertEqual(self._progress(queued_in_batch=True)["state"], "queued")
        self._batch("COMPLETED", imported=15)
        self._tracks(15)
        self._mix_done()
        self._files(images=3, clips=20, final=True)
        self._upload()
        self.assertEqual(self._progress(queued_in_batch=True)["state"], "stopped")

    def test_upload_error_is_reported(self):
        self._batch("COMPLETED", imported=15)
        self._tracks(15)
        self._mix_done()
        self._files(images=3, clips=20, final=True)
        self._upload(status="failed", error="Không thấy ô Tiêu đề")
        res = self._progress()
        self.assertEqual(res["state"], "error")
        self.assertEqual(res["stage"], "youtube")


if __name__ == "__main__":
    unittest.main()
