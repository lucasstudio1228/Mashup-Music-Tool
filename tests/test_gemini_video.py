"""Tạo clip bằng Gemini Video — không mở trình duyệt (chỉ ffmpeg cục bộ)."""
import json
import shutil
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent))
from backend.video import config, gemini_video as gv, service  # noqa: E402

HAS_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


class PromptSpecTests(unittest.TestCase):
    def test_spec_appended_once_and_english(self):
        p = gv.with_spec("A calm scene by the lake")
        self.assertTrue(p.startswith(config.GEMINI_VIDEO_SPEC))
        self.assertTrue(p.endswith("A calm scene by the lake"))
        self.assertIn("no on-screen text", p)
        self.assertIn("1080p", p)
        self.assertIn("1920x1080", p)
        self.assertIn("8 seconds", p)
        self.assertEqual(gv.with_spec(p), p)
        self.assertTrue(p.isascii())

    def test_classify_reply(self):
        self.assertEqual(gv.classify_reply(
            "Sorry, something went wrong. Please try your request again."), "transient")
        self.assertEqual(gv.classify_reply("I can't generate that video."), "refused")
        self.assertEqual(gv.classify_reply(
            "You've reached your daily limit for video generation."), "limit")
        self.assertEqual(gv.classify_reply("Bạn đã đạt đến giới hạn tạo video"), "limit")
        self.assertIsNone(gv.classify_reply(
            "I'm generating your video. This could take a few minutes"))
        self.assertIsNone(gv.classify_reply("Your video is ready!"))


class DisabledToolTests(unittest.TestCase):
    """Hết lượt/ngày: mục «Tạo video» còn đó nhưng aria-disabled=true."""
    class _Loc:
        def __init__(self, attrs): self.attrs = attrs
        @property
        def first(self): return self
        def count(self): return 1 if self.attrs is not None else 0
        def get_attribute(self, name, timeout=None): return (self.attrs or {}).get(name)

    class _Page:
        def __init__(self, attrs): self.attrs = attrs
        def locator(self, sel): return DisabledToolTests._Loc(self.attrs)

    def test_detects_disabled_item(self):
        sels = config.get_selectors("gemini_video")
        self.assertTrue(gv._video_tool_disabled(self._Page({"aria-disabled": "true"}), sels))
        self.assertTrue(gv._video_tool_disabled(
            self._Page({"class": "mat-mdc-list-item disabled mdc-list-item--disabled"}), sels))
        self.assertFalse(gv._video_tool_disabled(self._Page({"aria-disabled": "false"}), sels))
        self.assertFalse(gv._video_tool_disabled(self._Page(None), sels))

    def test_limit_message_stops_batch(self):
        from backend import batch_service
        msg = "Gemini báo hết lượt/giới hạn tạo video ở clip 09: ..."
        self.assertTrue(any(m in msg.lower() for m in batch_service._ACCOUNT_BLOCK_MARKERS))


class EngineSelectionTests(unittest.TestCase):
    def test_default_engine_is_muse(self):
        from backend.video import flow_driver, muse_video
        with patch.object(config, "load_overrides", return_value={}):
            self.assertEqual(config.clip_engine(), "muse")
            self.assertIs(service._clip_generator(), muse_video.generate_clips_muse)
        with patch.object(config, "load_overrides", return_value={"clip_engine": "xyz"}):
            self.assertEqual(config.clip_engine(), "muse")
        with patch.object(config, "load_overrides", return_value={"clip_engine": "flow"}):
            self.assertIs(service._clip_generator(), flow_driver.generate_clips)

    def test_gemini_engine_override(self):
        with patch.object(config, "load_overrides", return_value={"clip_engine": "gemini"}):
            self.assertEqual(config.clip_engine(), "gemini")
            self.assertIs(service._clip_generator(), gv.generate_clips_gemini)

    def test_video_selectors_registered(self):
        sels = config.get_selectors("gemini_video")
        for key in ("tools_menu", "video_tool", "mode_button", "mode_thinking",
                    "upload_button", "aspect_button", "send_button", "download_button"):
            self.assertTrue(sels.get(key), key)


@unittest.skipUnless(HAS_FFMPEG, "cần ffmpeg")
class FinalizeClipTests(unittest.TestCase):
    def test_strips_audio_and_scales_to_1080p(self):
        with TemporaryDirectory(prefix="gv-final-") as tmp:
            raw = Path(tmp) / "raw.mp4"
            subprocess.run(
                ["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=size=1280x720:rate=24:duration=2",
                 "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
                 "-c:v", "libx264", "-c:a", "aac", "-shortest", str(raw)],
                capture_output=True, check=True)
            self.assertTrue(gv.probe_streams(raw)["audio"])
            dest = Path(tmp) / "clip_00.mp4"
            info = gv.finalize_clip(raw, dest)
            self.assertFalse(info["audio"])
            self.assertEqual((info["width"], info["height"]), (1920, 1080))
            self.assertAlmostEqual(info["duration"], 2.0, delta=0.2)
            self.assertFalse(any(Path(tmp).glob("*.part.mp4")))


class GenerateFlowTests(unittest.TestCase):
    """Luồng resume/only/restart — thay phiên trình duyệt bằng _session_impl."""

    def setUp(self):
        self._tmp = TemporaryDirectory(prefix="gv-gen-")
        root = Path(self._tmp.name)
        self.p = patch.object(config, "MEDIA_ROOT", root / "media")
        self.p.start()
        self.img = config.images_dir(1, "P")
        self.img.mkdir(parents=True)
        for i in range(3):
            (self.img / f"{i}.png").write_bytes(b"png")
        (self.img / "prompts.json").write_text(json.dumps(
            {"motions": {str(i): f"Scene {i} by the river." for i in range(40)}}),
            encoding="utf-8")
        self.clips = config.clips_dir(1, "P")
        self.clips.mkdir(parents=True)

    def tearDown(self):
        self.p.stop()
        self._tmp.cleanup()

    def _run(self, **kw):
        seen = []
        gv.generate_clips_gemini(1, config.PARAMS, None, project_name="P", style="2d",
                                 _session_impl=lambda pending: seen.extend(pending), **kw)
        return seen

    def test_resume_only_missing(self):
        (self.clips / "clip_00.mp4").write_bytes(b"0" * 20_000)
        (self.clips / "clip_02.mp4").write_bytes(b"0" * 20_000)
        seen = self._run(resume=True)
        self.assertNotIn(0, seen)
        self.assertNotIn(2, seen)
        self.assertEqual(len(seen), config.PARAMS.total_clips - 2)

    def test_only_forces_given_clip(self):
        (self.clips / "clip_03.mp4").write_bytes(b"0" * 20_000)
        self.assertEqual(self._run(only=[3]), [3])
        self.assertTrue((self.clips / "clip_03.mp4").exists())

    def test_restart_deletes_old_clips(self):
        (self.clips / "clip_05.mp4").write_bytes(b"0" * 20_000)
        seen = self._run(resume=False)
        self.assertFalse((self.clips / "clip_05.mp4").exists())
        self.assertEqual(seen, list(range(config.PARAMS.total_clips)))

    def test_missing_sheet_raises(self):
        (self.img / "2.png").unlink()
        with self.assertRaises(FileNotFoundError):
            self._run(resume=True)


if __name__ == "__main__":
    unittest.main()
