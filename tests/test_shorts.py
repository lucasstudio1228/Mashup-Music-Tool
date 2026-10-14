"""YouTube Short: chọn đoạn giữa, nội dung ngắn, tự đăng nháp sau video dài."""
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.video import shorts, service  # noqa: E402


class PickSegmentTest(unittest.TestCase):
    def test_stays_inside_middle_track(self):
        tl = [{"name": f"T{i}", "start_seconds": i * 160.0} for i in range(45)]
        start, length, name = shorts.pick_segment(7200.0, tl, 45)
        self.assertEqual(name, "T22")              # 3520–3680 chứa mốc giữa 3600
        self.assertGreaterEqual(start, 3520 + shorts._TRACK_PAD)
        self.assertLessEqual(start + length, 3680 - shorts._TRACK_PAD)

    def test_track_too_short_falls_back_to_exact_middle(self):
        tl = [{"name": "A", "start_seconds": 0.0},
              {"name": "B", "start_seconds": 590.0},
              {"name": "C", "start_seconds": 620.0}]
        start, length, name = shorts.pick_segment(1200.0, tl, 45)
        self.assertEqual(name, "")
        self.assertAlmostEqual(start + length / 2, 600.0, places=1)

    def test_no_tracklist_and_short_video(self):
        start, length, name = shorts.pick_segment(20.0, [], 45)
        self.assertEqual(name, "")
        self.assertGreaterEqual(start, 0.0)
        self.assertLessEqual(start + length, 20.0)
        self.assertLessEqual(length, 19.0)


class MetaTextTest(unittest.TestCase):
    def test_tags_always_have_shorts_and_max_six(self):
        tags = shorts._clean_tags("#Piano, relax #piano #a #b #c #d #e")
        parts = tags.split()
        self.assertEqual(parts[0], "#shorts")
        self.assertLessEqual(len(parts), 6)
        self.assertEqual(len(parts), len(set(parts)))

    def test_title_has_single_shorts_suffix(self):
        t = shorts._finish_title("Calm Piano #shorts for Sleep " + "x" * 120)
        self.assertTrue(t.endswith(" #Shorts"))
        self.assertEqual(t.lower().count("#shorts"), 1)
        self.assertLessEqual(len(t), 100)

    def test_fallback_meta_is_complete(self):
        m = shorts.fallback_meta("Lucas Healing Music", "piano", "lofi")
        self.assertTrue(m["title"].endswith("#Shorts"))
        self.assertTrue(m["hook"] and m["description"])
        self.assertIn("#shorts", m["hashtags"])

    def test_ai_failure_returns_fallback(self):
        m = shorts.generate_short_meta(None, "Ch", "piano", "lofi", "Long", "",
                                       [], api_config=None)
        self.assertTrue(m["title"].endswith("#Shorts"))

    def test_description_links_full_session(self):
        d = shorts.compose_description(
            {"description": "Breathe.", "hashtags": "#shorts #piano"},
            "Ch", "https://youtu.be/abc")
        self.assertIn("Full session: https://youtu.be/abc", d)
        self.assertTrue(d.endswith("#shorts #piano"))
        self.assertNotIn("Full session",
                         shorts.compose_description({"description": "x"}, "Ch", ""))

    def test_hook_png_is_vertical_canvas(self):
        tmp = Path(tempfile.mkdtemp())
        try:
            out = tmp / "hook.png"
            self.assertTrue(shorts.render_hook_png("Breathe and let go", out))
            from PIL import Image
            with Image.open(out) as im:
                self.assertEqual(im.size, (shorts.SHORT_W, shorts.SHORT_H))
                self.assertEqual(im.mode, "RGBA")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class _FakeOpenAI:
    """Giả client OpenAI: trả lần lượt các hook trong `hooks` (lặp lại cái cuối)."""
    hooks: list[str] = []
    prompts: list[str] = []

    def __init__(self, **kw):
        self.chat = self
        self.completions = self

    def create(self, **kw):
        import json as _json
        from types import SimpleNamespace as NS
        content = kw["messages"][-1]["content"]
        text = content if isinstance(content, str) else content[0]["text"]
        _FakeOpenAI.prompts.append(text)
        i = min(len(_FakeOpenAI.prompts) - 1, len(_FakeOpenAI.hooks) - 1)
        body = {"hook": _FakeOpenAI.hooks[i]}
        if "description" in text and "title" in text and "Return JSON only: {\"title\"" in text:
            body.update(title="Soft Piano for a Quiet Mind", description="Rain on a window.",
                        hashtags="#shorts #piano")
        msg = NS(content=_json.dumps(body))
        return NS(choices=[NS(message=msg)])


class HookCheckinTest(unittest.TestCase):
    """Hook Short = lời hỏi thăm tiếng Anh, KHÔNG trùng/na ná hook đã dùng."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        p = mock.patch.object(shorts, "_HOOK_LEDGER", self.tmp / "short_hooks_used.json")
        p.start()
        self.addCleanup(p.stop)
        self.addCleanup(shutil.rmtree, self.tmp, True)
        _FakeOpenAI.hooks, _FakeOpenAI.prompts = [], []
        from types import SimpleNamespace as NS
        self.api = NS(api_key="k", model="gpt-4o-mini", to_client_kwargs=lambda: {})

    def _meta(self, used):
        with mock.patch("openai.OpenAI", _FakeOpenAI):
            return shorts.generate_short_meta(None, "Ch", "piano", "lofi", "Long", "",
                                              [], api_config=self.api, used=used)

    def test_pool_hooks_valid_and_mutually_distinct(self):
        pool = shorts._POOL_HOOKS
        for h in pool:
            self.assertTrue(shorts.valid_hook(h), h)
            self.assertTrue(shorts.is_fresh_hook(h, [x for x in pool if x != h]), h)
        for q in shorts._POOL_QUESTIONS:
            for i in shorts._POOL_INVITES:
                self.assertTrue(shorts.valid_hook(f"{q} {i}"), f"{q} {i}")

    def test_pool_never_repeats_over_many_shorts(self):
        import random
        random.seed(7)
        used: list[str] = []
        for _ in range(150):                      # vượt 36 câu soạn sẵn → sang câu ghép
            h = shorts.pool_hook(used)
            self.assertTrue(shorts.valid_hook(h), h)
            self.assertTrue(shorts.is_fresh_hook(h, used), h)
            used.append(h)
        self.assertEqual(len({shorts._norm_hook(h) for h in used}), 150)

    def test_duplicates_and_near_duplicates_are_stale(self):
        used = ["How are you feeling today?"]
        self.assertFalse(shorts.is_fresh_hook("how are you feeling TODAY", used))
        self.assertFalse(shorts.is_fresh_hook("How are you feeling today, friend?", used))
        self.assertTrue(shorts.is_fresh_hook("Did today ask too much of you?", used))

    def test_valid_hook_rules(self):
        self.assertTrue(shorts.valid_hook("Rough day? Stay here and breathe a little."))
        self.assertTrue(shorts.valid_hook("Isn’t it time you rested a little?"))
        self.assertFalse(shorts.valid_hook("Bạn cảm thấy hôm nay thế nào?"))   # không phải EN
        self.assertFalse(shorts.valid_hook("How are you today? 🌿"))
        self.assertFalse(shorts.valid_hook("How are you? #relax"))
        self.assertFalse(shorts.valid_hook("Breathe."))
        self.assertFalse(shorts.valid_hook("One. Two words. Three now."))
        self.assertFalse(shorts.valid_hook(" ".join(["word"] * 17) + "?"))

    def test_ledger_roundtrip_keeps_newest(self):
        with mock.patch.object(shorts, "_LEDGER_CAP", 5):
            for i in range(7):
                shorts.remember_hook(f"Hook number {i} here?", "a", i)
        rows = shorts.used_hooks()
        self.assertEqual([r["project_id"] for r in rows], [2, 3, 4, 5, 6])
        self.assertEqual(shorts.used_hooks(2)[-1]["hook"], "Hook number 6 here?")

    def test_fallback_uses_unused_pool_hook(self):
        used = shorts._POOL_HOOKS[:-1]
        m = shorts.fallback_meta("Ch", "piano", "lofi", used=used)
        self.assertEqual(m["hook"], shorts._POOL_HOOKS[-1])

    def test_ai_fresh_hook_kept_and_prompt_lists_used_hooks(self):
        _FakeOpenAI.hooks = ["Did the day leave you a little tired? Rest here."]
        m = self._meta(["How are you feeling today?"])
        self.assertEqual(m["hook"], _FakeOpenAI.hooks[0])
        self.assertIn(m["hook_angle"], shorts._CHECKIN_ANGLES)
        p = _FakeOpenAI.prompts[0]
        self.assertIn("caring check-in", p)
        self.assertIn("- How are you feeling today?", p)
        self.assertIn(m["hook_angle"], p)

    def test_ai_duplicate_is_retried(self):
        _FakeOpenAI.hooks = ["How are you feeling today?",
                             "Is your heart carrying something heavy right now?"]
        m = self._meta(["How are you feeling today?"])
        self.assertEqual(m["hook"], "Is your heart carrying something heavy right now?")
        self.assertEqual(len(_FakeOpenAI.prompts), 2)
        self.assertIn("Rejected", _FakeOpenAI.prompts[1])

    def test_ai_keeps_repeating_falls_back_to_unused_pool(self):
        used = ["How are you feeling today?"]
        _FakeOpenAI.hooks = ["How are you feeling today?"]
        m = self._meta(used)
        self.assertEqual(m["hook_angle"], "pool")
        self.assertIn(m["hook"], shorts._POOL_HOOKS)
        self.assertTrue(shorts.is_fresh_hook(m["hook"], used))
        self.assertEqual(len(_FakeOpenAI.prompts), 3)      # 1 lần đầu + 2 lần hỏi lại

    def test_two_sentence_hook_breaks_line_at_sentence(self):
        from PIL import Image, ImageDraw
        from backend.video.thumbnail import _load_font
        draw = ImageDraw.Draw(Image.new("RGBA", (10, 10)))
        font = _load_font(shorts._HOOK_FONTS, 60)
        text = "Is life weighing on your shoulders? Take a moment right here."
        _, lines = shorts._sentence_layout(text, draw, font, 907, start_size=84)
        q = [i for i, ln in enumerate(lines) if ln.endswith("?")]
        self.assertEqual(len(q), 1)
        self.assertTrue(lines[q[0] + 1].startswith("Take"))
        self.assertIsNone(shorts._sentence_layout("How are you feeling today?",
                                                  draw, font, 907))
        self.assertTrue(shorts.render_hook_png(text, self.tmp / "h.png"))

    def test_build_short_records_hook_in_ledger(self):
        meta = {"title": "T #Shorts", "hook": "Has today been kind to you?",
                "hook_angle": "pool", "description": "d", "hashtags": "#shorts"}
        with mock.patch.object(shorts, "probe_duration", return_value=600.0), \
             mock.patch.object(shorts, "extract_frame", return_value=False), \
             mock.patch.object(shorts, "generate_short_meta", return_value=meta), \
             mock.patch.object(shorts, "render_hook_png", return_value=True), \
             mock.patch.object(shorts, "render_short", return_value=self.tmp / "short.mp4"), \
             mock.patch.object(shorts, "settings",
                               return_value={"seconds": 45, "crop_x": 0.5, "hook": True}):
            shorts.build_short("final.mp4", self.tmp, [], channel_name="Ch",
                               instrument="piano", music_style="lofi", long_title="L",
                               past_titles=[], api_config=None,
                               progress_cb=lambda m, p: None, project_id=42)
        rows = shorts.used_hooks()
        self.assertEqual((rows[-1]["hook"], rows[-1]["project_id"]),
                         ("Has today been kind to you?", 42))


class AutoShortTriggerTest(unittest.TestCase):
    def _run(self, auto_short: bool, long_ok: bool = True, short_exc=None):
        calls, msgs = [], []
        long = mock.Mock(side_effect=None if long_ok else RuntimeError("boom"))
        short = mock.Mock(side_effect=short_exc)
        with mock.patch.object(service, "_read_project_fields",
                               return_value={"auto_upload": True, "instrument": "piano",
                                             "music_style": "lofi"}), \
             mock.patch.object(service, "step_upload_youtube", long), \
             mock.patch.object(service, "step_upload_short", short), \
             mock.patch.object(shorts, "settings",
                               return_value={"auto_short": auto_short, "seconds": 45,
                                             "crop_x": 0.5, "hook": True}):
            service._maybe_auto_upload(1, lambda m, p: msgs.append(m), "P")
        calls.append((long.called, short.called))
        return long, short, msgs

    def test_short_follows_successful_draft(self):
        long, short, _ = self._run(True)
        self.assertTrue(long.called and short.called)

    def test_no_short_when_disabled(self):
        _, short, _ = self._run(False)
        self.assertFalse(short.called)

    def test_no_short_when_long_draft_failed(self):
        _, short, _ = self._run(True, long_ok=False)
        self.assertFalse(short.called)

    def test_short_error_is_swallowed(self):
        _, short, msgs = self._run(True, short_exc=RuntimeError("yt down"))
        self.assertTrue(short.called)
        self.assertTrue(any("Auto Short lỗi" in m for m in msgs))

    def test_manual_button_chains_short(self):
        with mock.patch.object(service, "step_upload_youtube",
                               return_value={"status": "draft"}) as long, \
             mock.patch.object(service, "step_upload_short",
                               side_effect=RuntimeError("x")) as short, \
             mock.patch.object(shorts, "settings",
                               return_value={"auto_short": True}):
            res = service.step_upload_youtube_and_short(1, lambda m, p: None, "P")
        self.assertTrue(long.called and short.called)
        self.assertEqual(res["status"], "draft")
        self.assertIn("short_error", res)


class LongDraftInfoTest(unittest.TestCase):
    def test_roundtrip(self):
        tmp = Path(tempfile.mkdtemp())
        try:
            self.assertEqual(service._load_long_draft_info(tmp), {})
            service._save_long_draft_info(tmp, "Title", "https://youtu.be/x")
            info = service._load_long_draft_info(tmp)
            self.assertEqual((info["title"], info["url"]), ("Title", "https://youtu.be/x"))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
