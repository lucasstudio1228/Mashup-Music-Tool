"""«Tạo tiếp ảnh/clip thiếu» → chạy tiếp ghép → nháp YouTube → nháp Short."""
import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.video import service, shorts, config  # noqa: E402


# Sổ công thức thumbnail (data/thumbnail_recipes.json) → file tạm, để test
# không ghi công thức giả vào sổ thật của người dùng.
from backend.video import thumbnail_recipe as _thumb_recipe  # noqa: E402
_LEDGER_DIR = None
_LEDGER_PATCH = None
_AI_PATCH = None


def setUpModule():
    global _LEDGER_DIR, _LEDGER_PATCH, _AI_PATCH
    import tempfile as _tf
    from unittest import mock as _mock
    _LEDGER_DIR = _tf.TemporaryDirectory()
    _LEDGER_PATCH = _mock.patch.object(
        _thumb_recipe, "LEDGER", Path(_LEDGER_DIR.name) / "thumbnail_recipes.json")
    _LEDGER_PATCH.start()
    # Không bao giờ gọi API ảnh thật (tốn phí) trong test, kể cả khi đã lưu key.
    from backend.video import thumbnail_ai as _tai
    _AI_PATCH = _mock.patch.object(_tai, "load_settings",
                                   lambda *a, **k: _tai.ThumbAISettings(engine="pil"))
    _AI_PATCH.start()


def tearDownModule():
    if _AI_PATCH:
        _AI_PATCH.stop()
    if _LEDGER_PATCH:
        _LEDGER_PATCH.stop()
    if _LEDGER_DIR:
        _LEDGER_DIR.cleanup()


class ContinueChainTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        (self.tmp / "clips").mkdir()
        (self.tmp / "final").mkdir()
        self.audio = self.tmp / "mix.wav"
        self.audio.write_bytes(b"a")
        for i in range(config.PARAMS.total_clips):
            (self.tmp / "clips" / f"clip_{i:02d}.mp4").write_bytes(b"c")
        self.calls: list[str] = []
        self.msgs: list[str] = []
        self.drafts = {"long": False, "short": False}
        pats = [
            mock.patch.object(config, "clips_dir", lambda *a, **k: self.tmp / "clips"),
            mock.patch.object(config, "final_dir", lambda *a, **k: self.tmp / "final"),
            mock.patch.object(service, "find_latest_audio", lambda *a, **k: str(self.audio)),
            mock.patch.object(service, "_read_project_fields",
                              lambda pid: {"instrument": "piano", "music_style": "lofi",
                                           "auto_upload": 0}),
            mock.patch.object(service, "step_assemble", side_effect=self._assemble),
            mock.patch.object(service, "step_upload_youtube",
                              side_effect=lambda *a, **k: self.calls.append("upload") or {}),
            mock.patch.object(service, "step_upload_short",
                              side_effect=lambda *a, **k: self.calls.append("short") or {}),
            mock.patch.object(service, "step_clips",
                              side_effect=lambda *a, **k: self.calls.append("clips")),
            mock.patch.object(service, "step_images",
                              side_effect=lambda *a, **k: self.calls.append("images")),
            mock.patch.object(service, "_draft_done_since",
                              lambda pid, ts, shorts: self.drafts["short" if shorts else "long"]),
            mock.patch.object(shorts, "settings", lambda: {"auto_short": True}),
        ]
        for p in pats:
            p.start()
            self.addCleanup(p.stop)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _assemble(self, *a, **k):
        self.calls.append("assemble")
        (self.tmp / "final" / "final.mp4").write_bytes(b"v")
        return {}

    def _cb(self, m, p):
        self.msgs.append(m)

    def test_images_resume_runs_whole_chain_even_without_auto_upload(self):
        service.continue_from_images(1, self._cb, "topic", "P")
        self.assertEqual(self.calls, ["images", "clips", "assemble", "upload", "short"])

    def test_clips_resume_runs_chain(self):
        service.continue_from_clips(1, self._cb, "P")
        self.assertEqual(self.calls, ["clips", "assemble", "upload", "short"])

    def test_skips_assemble_when_final_is_newer(self):
        final = self.tmp / "final" / "final.mp4"
        final.write_bytes(b"v")
        later = time.time() + 60
        os.utime(final, (later, later))
        service.continue_from_clips(1, self._cb, "P")
        self.assertEqual(self.calls, ["clips", "upload", "short"])

    def test_reassembles_when_a_clip_is_newer(self):
        final = self.tmp / "final" / "final.mp4"
        final.write_bytes(b"v")
        old = time.time() - 600
        os.utime(final, (old, old))
        service.continue_from_clips(1, self._cb, "P")
        self.assertIn("assemble", self.calls)

    def test_skips_uploads_already_done(self):
        self.drafts.update(long=True, short=True)
        service.continue_from_clips(1, self._cb, "P")
        self.assertEqual(self.calls, ["clips", "assemble"])

    def test_only_short_missing(self):
        self.drafts["long"] = True
        service.continue_from_clips(1, self._cb, "P")
        self.assertEqual(self.calls, ["clips", "assemble", "short"])

    def test_upload_error_stops_before_short_but_keeps_video(self):
        service.step_upload_youtube.side_effect = RuntimeError("GPM down")
        res = service.continue_from_clips(1, self._cb, "P")
        self.assertNotIn("short", self.calls)
        self.assertIn("upload_error", res)
        self.assertTrue(res["assembled"])

    def test_no_audio_stops_after_clips(self):
        with mock.patch.object(service, "find_latest_audio", lambda *a, **k: None):
            res = service.continue_from_clips(1, self._cb, "P")
        self.assertEqual(self.calls, ["clips"])
        self.assertEqual(res["reason"], "no_audio")

    def test_auto_short_off(self):
        with mock.patch.object(shorts, "settings", lambda: {"auto_short": False}):
            service.continue_from_clips(1, self._cb, "P")
        self.assertEqual(self.calls, ["clips", "assemble", "upload"])


class DraftDoneSinceTest(unittest.TestCase):
    def test_compares_created_at_with_final_mtime(self):
        import sqlite3
        con = sqlite3.connect(":memory:")
        con.row_factory = sqlite3.Row
        con.execute("CREATE TABLE youtubeupload (project_id, status, video_path, created_at)")
        con.execute("INSERT INTO youtubeupload VALUES (1,'draft','x/final.mp4','2026-10-07T10:00:00+00:00')")
        con.execute("INSERT INTO youtubeupload VALUES (1,'failed','x/short.mp4','2026-10-07T12:00:00+00:00')")
        from datetime import datetime, timezone
        ts = datetime(2026, 10, 7, 9, 0, tzinfo=timezone.utc).timestamp()

        class _NoClose:
            def __getattr__(s, n):
                return (lambda: None) if n == "close" else getattr(con, n)
        with mock.patch.object(service, "_db", lambda: _NoClose()):
            self.assertTrue(service._draft_done_since(1, ts, shorts=False))
            self.assertFalse(service._draft_done_since(1, ts + 7200, shorts=False))
            self.assertFalse(service._draft_done_since(1, ts, shorts=True))


if __name__ == "__main__":
    unittest.main()
