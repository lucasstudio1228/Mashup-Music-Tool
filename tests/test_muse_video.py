"""Máy tạo clip Muse.ai (muse_video): prompt có đủ thông số, đọc trạng thái /
câu trả lời, chọn engine, hậu kỳ cắt 8s + bỏ tiếng + 1080p, luồng resume/only.
Không mở GPM / trình duyệt (phiên thay bằng _session_impl / trang giả)."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent))
from backend.video import config, service
from backend.video import muse_video as mv
from backend.video import gemini_video as gv

HAS_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


class PromptSpecTests(unittest.TestCase):
    def test_spec_once_english_and_replaces_gemini_spec(self):
        base = "Calm lake at dawn, the character from ingredient 1 and ingredient 2."
        p = mv.with_spec(gv.with_spec(base))
        self.assertTrue(p.startswith(mv.MUSE_VIDEO_SPEC))
        self.assertNotIn(config.GEMINI_VIDEO_SPEC, p)
        self.assertEqual(mv.with_spec(p), p)                   # không lặp
        for need in ("16:9", "1920x1080", "8 seconds", "silent", "no cuts"):
            self.assertIn(need, p)
        self.assertIn("attached image 1", p)
        self.assertNotIn("ingredient", p.lower())
        self.assertTrue(p.isascii())

    def test_status_kind(self):
        self.assertEqual(mv._status_kind("Muse\nConnected\nToday\nCreate video"), "connected")
        self.assertEqual(mv._status_kind("Muse\nConnecting...\n"), "connecting")
        self.assertEqual(mv._status_kind("Muse\n🎹\nRendering video\nToday"), "working")
        self.assertEqual(mv._status_kind("Muse\nis working\nToday"), "working")
        self.assertEqual(mv._status_kind(""), "")

    def test_reply_after_strips_labels(self):
        page = ("old stuff\nYou:\n... keep identity END-OF-PROMPT\n\n👍\n"
                "Assistant message: Done, Lucas — here's your video:\n"
                "Let me know if you want any motion tweaked.\nMessage")
        r = mv.reply_after(page, "keep identity END-OF-PROMPT")
        self.assertTrue(r.strip().startswith("👍"))
        self.assertNotIn("Assistant message", r)
        self.assertFalse(r.rstrip().endswith("Message"))

    def test_classify_tool_errors(self):
        for msg in ("Couldn't generate that sakura park video — the video tool hit an "
                    "internal error on this one.",
                    "Sorry, something went wrong while rendering.",
                    "I wasn't able to create the video this time.",
                    "The generation failed, want me to retry?"):
            # «transient» (bộ lọc chung) và «error» đều được tạo lại như nhau
            self.assertIn(mv.classify_muse_reply(msg), ("error", "transient"), msg)
        # hết lượt / vi phạm chính sách vẫn ưu tiên như cũ
        self.assertEqual(mv.classify_muse_reply(
            "Couldn't generate it: you've reached your daily limit."), "limit")
        self.assertIsNone(mv.classify_muse_reply(
            "Done — here's your video. Let me know if you want any motion tweaked."))

    def test_classify(self):
        self.assertEqual(mv.classify_muse_reply("You've reached your daily limit."), "limit")
        self.assertEqual(mv.classify_muse_reply("Sorry, I can't help with that."), "refused")
        self.assertEqual(mv.classify_muse_reply("Should it be daytime or night?"), "clarify")
        self.assertIsNone(mv.classify_muse_reply("Rendering your video now"))


class _Page:
    """Trang giả: trả lần lượt các trạng thái cho _wait_video / wait_connected."""

    def __init__(self, states):
        self.states = list(states)
        self.reloads = 0

    def evaluate(self, js, arg=None):
        return self.states.pop(0) if len(self.states) > 1 else self.states[0]

    def wait_for_timeout(self, ms):
        pass

    def reload(self, **kw):
        self.reloads += 1


def _st(boxes=1, ready=1, status="Muse\nConnected", text=""):
    return {"boxes": boxes, "ready": ready, "status": status,
            "undelivered": False, "login": False, "text": text}


class WaitTests(unittest.TestCase):
    def test_new_video_is_ready(self):
        page = _Page([_st(1, 1, "Muse\n🎹\nGenerating video"), _st(2, 2)])
        kind, _ = mv._wait_video(page, "PROMPT", 1, 600, lambda s: None)
        self.assertEqual(kind, "ready")

    def test_limit_reply_stops(self):
        page = _Page([_st(1, 1, text="PROMPT\nAssistant message: You've reached your daily limit.\nMessage")])
        kind, st = mv._wait_video(page, "PROMPT", 1, 600, lambda s: None)
        self.assertEqual(kind, "limit")
        self.assertIn("limit", st["text"])

    def test_old_videos_do_not_count(self):
        """Video CŨ trong cuộc trò chuyện (n0=3) không bị coi là kết quả mới."""
        page = _Page([_st(3, 3, "Muse\nis working")])
        with patch.object(mv.time, "time", side_effect=[0, 0, 0, 10, 10, 700]):
            kind, _ = mv._wait_video(page, "PROMPT", 3, 600, lambda s: None)
        self.assertEqual(kind, "timeout")

    def test_novideo_waits_longer_than_one_render(self):
        """Live: panel vẫn «Connected» khi Muse đang dựng, video tới ~130s →
        không được kết luận «novideo» sớm; đứng yên đủ lâu mới kết luận."""
        idle = _st(1, 1, text="PROMPT\n👍\nMessage")
        clock = iter([0, 0, 0, 130, 130, 130, 200, 200, 200])
        page = _Page([idle, idle, idle, _st(2, 2)])
        with patch.object(mv.time, "time", side_effect=lambda: next(clock, 200)):
            kind, _ = mv._wait_video(page, "PROMPT", 1, 600, lambda s: None)
        self.assertEqual(kind, "ready")
        clock = iter([0, 0, 0, 10, 10, 10, 400])
        page = _Page([idle])
        with patch.object(mv.time, "time", side_effect=lambda: next(clock, 400)):
            kind, _ = mv._wait_video(page, "PROMPT", 1, 600, lambda s: None)
        self.assertEqual(kind, "novideo")

    def test_virtualized_chat_same_box_count(self):
        """Live 2026-10-05: chat ảo hoá giữ 4 khung — video mới vào, video cũ nhất
        bị gỡ ⇒ số khung KHÔNG đổi. Phải nhận ra qua tin nhắn sau tin user mới."""
        base = {"boxes": 4, "last_user": "u1", "has_msgs": True}
        def st(last_user, after_boxes, after_ready):
            d = _st(4, 4, "Muse\nConnected")
            d.update(has_msgs=True, last_user=last_user,
                     after_boxes=after_boxes, after_ready=after_ready)
            return d
        # video cũ sau tin user CŨ (tin mới chưa hiện) không được tính
        self.assertEqual(mv._new_video(st("u1", 1, 1), base), (0, 0))
        page = _Page([st("u2", 0, 0), st("u2", 1, 0), st("u2", 1, 1)])
        kind, _ = mv._wait_video(page, "PROMPT", base, 600, lambda s: None)
        self.assertEqual(kind, "ready")
        self.assertTrue(mv._late_video(_Page([st("u2", 1, 1)]), base))
        self.assertFalse(mv._late_video(_Page([st("u1", 1, 1)]), base))

    def test_late_video_detected(self):
        self.assertTrue(mv._late_video(_Page([_st(1, 1), _st(2, 2)]), 1))
        self.assertFalse(mv._late_video(_Page([_st(1, 1)]), 1))

    def test_wait_connected_passes_and_times_out(self):
        mv.wait_connected(_Page([_st(status="Muse\nConnected")]), 10, lambda s: None)
        page = _Page([_st(status="Muse\nConnecting...")])
        with patch.object(mv.time, "time", side_effect=[0, 5, 100]):
            with self.assertRaises(mv.MuseUnavailable):
                mv.wait_connected(page, 30, lambda s: None)

    def test_login_page_raises(self):
        st = _st(status="")
        st["login"] = True
        with self.assertRaises(mv.MuseUnavailable):
            mv.wait_connected(_Page([st]), 10, lambda s: None)


class EngineSelectionTests(unittest.TestCase):
    def test_muse_engine_override(self):
        with patch.object(config, "load_overrides", return_value={"clip_engine": "muse"}):
            self.assertEqual(config.clip_engine(), "muse")
            self.assertIs(service._clip_generator(), mv.generate_clips_muse)
            self.assertEqual(service.clip_engine_label(), "Muse.ai")

    def test_profile_id_default_and_override(self):
        # Repo public: không hard-code profile cá nhân → chưa cấu hình thì báo rõ.
        with patch.object(config, "load_overrides", return_value={}):
            with self.assertRaises(mv.MuseUnavailable):
                mv.profile_id()
        with patch.object(config, "load_overrides", return_value={"muse_gpm_profile_id": "abc"}):
            self.assertEqual(mv.profile_id(), "abc")

    def test_limit_message_stops_batch(self):
        from backend import batch_service
        msg = "Muse báo hết lượt/giới hạn tạo video ở clip 04 (...)"
        self.assertTrue(any(m in msg.lower() for m in batch_service._ACCOUNT_BLOCK_MARKERS))


@unittest.skipUnless(HAS_FFMPEG, "cần ffmpeg")
class FinalizeTrimTests(unittest.TestCase):
    def test_trims_10s_720p_with_audio_to_8s_1080p_silent(self):
        with TemporaryDirectory(prefix="mv-final-") as tmp:
            raw = Path(tmp) / "raw.mp4"
            subprocess.run(
                ["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=size=1280x720:rate=24:duration=10",
                 "-f", "lavfi", "-i", "sine=frequency=440:duration=10",
                 "-c:v", "libx264", "-c:a", "aac", "-shortest", str(raw)],
                capture_output=True, check=True)
            info = gv.finalize_clip(raw, Path(tmp) / "clip_01.mp4", max_sec=8)
            self.assertFalse(info["audio"])
            self.assertEqual((info["width"], info["height"]), (1920, 1080))
            self.assertAlmostEqual(info["duration"], 8.0, delta=0.15)


class GenerateFlowTests(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory(prefix="mv-gen-")
        root = Path(self._tmp.name)
        self.p = patch.object(config, "MEDIA_ROOT", root / "media")
        self.p.start()
        # Profile GPM giả — repo không hard-code profile, máy mới chưa có overrides.
        self.pp = patch.object(mv, "DEFAULT_MUSE_PROFILE_ID", "test-profile")
        self.pp.start()
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
        self.pp.stop()
        self.p.stop()
        self._tmp.cleanup()

    def _run(self, **kw):
        seen = []
        mv.generate_clips_muse(1, config.PARAMS, None, project_name="P", style="2d",
                               _session_impl=lambda pending: seen.extend(pending), **kw)
        return seen

    def test_resume_only_missing(self):
        (self.clips / "clip_00.mp4").write_bytes(b"0" * 20_000)
        seen = self._run(resume=True)
        self.assertNotIn(0, seen)
        self.assertEqual(len(seen), config.PARAMS.total_clips - 1)
        self.assertTrue((self.clips / "_muse_video.log").exists())

    def test_only_forces_given_clip(self):
        (self.clips / "clip_03.mp4").write_bytes(b"0" * 20_000)
        self.assertEqual(self._run(only=[3]), [3])

    def test_cancel_is_not_swallowed(self):
        """Huỷ job (JobCancelled từ progress callback) phải thoát NGAY — không bị
        coi là «lỗi giao diện» rồi gửi lại prompt (tốn lượt Muse)."""
        from backend.video.job_manager import JobCancelled

        class _Sess:
            def __init__(self, pid): self.page = _Page([_st(status="Muse\nis working")])
            def __enter__(self): return self
            def __exit__(self, *a): return False

        sends = []
        def cb(msg, pct):
            if "kết nối" in msg or "đang tạo" in msg:
                raise JobCancelled()
        with patch.object(mv, "_GPMSession", _Sess),              patch.object(mv, "_send", lambda *a: sends.append(a) or "x"):
            with self.assertRaises(JobCancelled):
                mv.generate_clips_muse(1, config.PARAMS, cb, project_name="P",
                                       style="2d", only=[1])
        self.assertEqual(sends, [])

    def test_tool_errors_retry_until_video(self):
        """Muse báo lỗi công cụ 5 lần liền → vẫn tạo lại (không dừng ở 3 lần
        thường), luân phiên prompt dự phòng từ lỗi thứ 3, ra video thì dừng."""
        class _Sess:
            def __init__(self, pid): self.page = _Page([_st()])
            def __enter__(self): return self
            def __exit__(self, *a): return False

        results = [("error", {"text": "internal error"})] * 5 + [("ready", {})]
        sent = []
        def fake_dl(page, raw):
            raw.parent.mkdir(parents=True, exist_ok=True)
            raw.write_bytes(b"0" * 30_000)
        info = {"width": 1920, "height": 1080, "duration": 8.0, "audio": False}
        with patch.object(mv, "_GPMSession", _Sess),              patch.object(mv, "wait_connected", lambda *a: None),              patch.object(mv, "_baseline", lambda page: {}),              patch.object(mv, "_send", lambda page, paths, prompt: sent.append(prompt) or "x"),              patch.object(mv, "_wait_video", side_effect=results),              patch.object(mv, "_late_video", lambda *a: False),              patch.object(mv, "_pause_ticking", lambda *a: None),              patch.object(mv, "_download", fake_dl),              patch.object(mv, "probe_streams", lambda raw: info),              patch.object(mv, "finalize_clip", lambda raw, dest, max_sec=None:
                          (dest.write_bytes(b"0" * 30_000), info)[1]):
            out = mv.generate_clips_muse(1, config.PARAMS, None, project_name="P",
                                         style="2d", only=[4])
        self.assertEqual(len(sent), 6)
        self.assertTrue((self.clips / "clip_04.mp4").exists())
        self.assertEqual(sent[0], sent[1])                 # lỗi 1–2: gửi lại CÙNG prompt
        self.assertNotEqual(sent[0], sent[3])              # lỗi 3: prompt dự phòng
        self.assertEqual(len(out), 1)

    def test_tool_errors_have_a_ceiling(self):
        class _Sess:
            def __init__(self, pid): self.page = _Page([_st()])
            def __enter__(self): return self
            def __exit__(self, *a): return False
        with patch.object(config, "load_overrides", return_value={"muse_error_retries": 2}),              patch.object(mv, "_GPMSession", _Sess),              patch.object(mv, "wait_connected", lambda *a: None),              patch.object(mv, "_baseline", lambda page: {}),              patch.object(mv, "_send", lambda *a: "x"),              patch.object(mv, "_wait_video", return_value=("error", {"text": "internal error"})),              patch.object(mv, "_late_video", lambda *a: False),              patch.object(mv, "_pause_ticking", lambda *a: None):
            with self.assertRaises(RuntimeError) as cm:
                mv.generate_clips_muse(1, config.PARAMS, None, project_name="P",
                                       style="2d", only=[4])
        self.assertIn("3 lần Muse báo lỗi", str(cm.exception))

    def test_missing_sheet_raises(self):
        (self.img / "2.png").unlink()
        with self.assertRaises(FileNotFoundError):
            self._run(resume=True)


if __name__ == "__main__":
    unittest.main()
