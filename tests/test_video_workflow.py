"""Offline regression tests against actual workflow/assembler entry points."""
import json
from pathlib import Path
import sys
import time
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent))
from backend.video import config, assembler, service, flow_driver
from backend.video.gemini_driver import _prompt_for
from backend.video.flow_driver import generate_clips


class VideoWorkflowTests(unittest.TestCase):
    def test_three_images_feed_every_clip_with_both_sheets(self):
        n = config.PARAMS.image_count
        self.assertEqual(n, 3)
        self.assertEqual(config.SHEET_IMAGES,
                         (config.IMG_MAIN_SHEET, config.IMG_PET_SHEET))
        self.assertEqual(set(config.DEFAULT_PROMPTS), {str(i) for i in range(n)})
        plan = config.generate_ingredient_plan(config.PARAMS.total_clips)
        self.assertEqual(len(plan), config.PARAMS.total_clips)
        # Clip 00 (intro) = video thumbnail: ảnh bìa + hai bảng nhân vật.
        self.assertEqual(plan[0], [config.IMG_THUMBNAIL, *config.SHEET_IMAGES])
        # Các clip còn lại dùng cùng hai bảng nhân vật; ảnh 0 không vào clip nào khác.
        self.assertTrue(all(ings == list(config.SHEET_IMAGES) for ings in plan[1:]))
        # Hai bảng phân tích được vẽ TRƯỚC, ảnh bìa vẽ sau cùng.
        self.assertEqual(config.IMAGE_ORDER, (1, 2, 0))

    def test_clean_thumbnail_and_locked_continuity_in_every_prompt(self):
        for i in range(config.PARAMS.image_count):
            prompt = _prompt_for(i, "Zen Lake", config.PARAMS, config.DEFAULT_PROMPTS)
            for value in ("Zen Lake", "THE SAME", "sage-green hoodie", "wooden cabin", "LOCKED COMPOSITION"):
                self.assertIn(value, prompt)
        # Ảnh 0 cấm chữ tuyệt đối; hai bảng phân tích thì BẮT BUỘC có nhãn chữ.
        self.assertIn("NO TEXT AT ALL", _prompt_for(0, "Zen", config.PARAMS, config.DEFAULT_PROMPTS))
        for i in config.SHEET_IMAGES:
            sheet = _prompt_for(i, "Zen", config.PARAMS, config.DEFAULT_PROMPTS, "real")
            self.assertNotIn("MUST CONTAIN NO TEXT AT ALL", sheet)
            self.assertIn("FRONT VIEW", sheet)
            # Phải MỞ ĐẦU bằng "bảng phân tích nhân vật", KHÔNG phải prefix
            # điện ảnh — prefix đó từng khiến Gemini vẽ ra một cảnh phim.
            self.assertTrue(sheet.startswith("CHARACTER MODEL SHEET"))
            self.assertNotIn("CINEMATIC PHOTOREALISTIC IMAGE", sheet)
        # Ảnh bìa thì vẫn giữ nguyên prefix phong cách.
        self.assertTrue(_prompt_for(0, "Zen", config.PARAMS, config.DEFAULT_PROMPTS,
                                    "real").startswith("CINEMATIC PHOTOREALISTIC IMAGE"))
        with self.assertRaisesRegex(ValueError, "ngoài bộ"):
            _prompt_for(40, "Zen", config.PARAMS, {"0": "one shot"})
        with self.assertRaises(ValueError):
            _prompt_for(0, "Zen", config.PARAMS, {})

    def test_gemini_uses_bundled_chromium_while_flow_keeps_external_browser(self):
        exe = r"C:\fake\CocCoc\browser.exe"
        with patch.object(config, "SITE_BROWSER", {"gemini": ""}), \
                patch.object(config, "load_overrides",
                             return_value={"browser_executable": exe}), \
                patch.object(config.Path, "exists", return_value=True):
            # Gemini: ÉP Chromium bundled (Cốc Cốc đóng cửa sổ ở bước tải ảnh).
            self.assertIsNone(config.resolve_browser_exe("gemini"))
            # Flow: giữ trình duyệt ngoài, nếu không sẽ bị chặn "hoạt động bất thường".
            self.assertEqual(config.resolve_browser_exe("flow"), exe)
            self.assertEqual(config.resolve_browser_exe(), exe)
            # video_overrides.json vẫn ghi đè được lựa chọn theo site.
            with patch.object(config, "load_overrides", return_value={
                    "browser_executable_by_site": {"gemini": exe}}):
                self.assertEqual(config.resolve_browser_exe("gemini"), exe)

    def test_image_job_reopens_browser_after_it_closes_midway(self):
        """Cửa sổ chết giữa chừng → mở phiên mới, chỉ làm nốt ảnh còn thiếu."""
        from backend.video import gemini_driver as gd
        calls: list[list[int]] = []

        with TemporaryDirectory() as tmp, patch.object(config, "MEDIA_ROOT", Path(tmp)), \
                patch.object(gd, "BrowserSession", side_effect=AssertionError):
            out = config.images_dir(21, "Reopen")
            out.mkdir(parents=True, exist_ok=True)

            def fake_session(todo, mark_done):
                calls.append(list(todo))
                # Phiên 1: lưu được ảnh đầu rồi trình duyệt tự đóng giữa chừng.
                for k, i in enumerate(todo):
                    (out / f"{i}.png").write_bytes(b"x" * 20000)
                    mark_done(i)
                    if len(calls) == 1 and k == 0:
                        raise RuntimeError(
                            "Page.wait_for_timeout: Target page, context or "
                            "browser has been closed")

            paths = gd.generate_images(
                21, "Reopen", config.PARAMS, None, resume=True,
                project_name="Reopen", prompts_override=config.DEFAULT_PROMPTS,
                _session_impl=fake_session)

            # Phiên 1 nhận cả 3 ảnh, phiên 2 CHỈ nhận 2 ảnh còn thiếu.
            self.assertEqual(calls, [[1, 2, 0], [2, 0]])
            self.assertEqual(len(paths), config.PARAMS.image_count)

        # Lỗi KHÔNG phải "cửa sổ đóng" thì phải nổi lên ngay, không thử lại.
        with TemporaryDirectory() as tmp, patch.object(config, "MEDIA_ROOT", Path(tmp)), \
                patch.object(gd, "BrowserSession", side_effect=AssertionError):
            def boom(todo, mark_done):
                raise RuntimeError("Gemini từ chối tạo ảnh")
            with self.assertRaisesRegex(RuntimeError, "từ chối"):
                gd.generate_images(22, "Boom", config.PARAMS, None, resume=True,
                                   project_name="Boom",
                                   prompts_override=config.DEFAULT_PROMPTS,
                                   _session_impl=boom)

    def test_human_pace_waits_between_steps_and_is_overridable(self):
        """Mọi bước phải có nhịp nghỉ ngẫu nhiên (Flow gắn cờ khi thao tác quá
        nhanh/đều); chỉ ô prompt là dán thẳng như người copy-paste."""
        waited: list[int] = []
        page = SimpleNamespace(wait_for_timeout=waited.append)

        for kind, (lo, hi) in config.FLOW_HUMAN_PACE.items():
            waited.clear()
            flow_driver._pace(page, kind)
            self.assertEqual(len(waited), 1)
            self.assertGreaterEqual(waited[0], int(lo * 1000))
            self.assertLessEqual(waited[0], int(hi * 1000) + 1)
        # Nghỉ giữa hai clip phải dài hơn hẳn nhịp bấm nút.
        self.assertGreater(config.FLOW_HUMAN_PACE["between_clips"][0],
                           config.FLOW_HUMAN_PACE["ui"][1])

        waited.clear()
        with patch.object(config, "load_overrides",
                          return_value={"flow_human_pace": {"ui": [7, 7]}}):
            flow_driver._pace(page, "ui")
        self.assertEqual(waited, [7000])

    def test_ingredients_cleared_until_composer_is_empty(self):
        """Còn sót chip nguyên liệu ⇒ Flow ẩn chính ảnh đó khỏi picker → phải
        xoá tới khi ô soạn RỖNG, không chỉ bấm 'Xoá câu lệnh' một lần."""
        clicks: list[int] = []
        counts = [2, 1, 0]         # mỗi lần bấm xoá mới bớt 1 chip

        class FakePage:
            def wait_for_timeout(self, ms):
                pass

        with patch.object(flow_driver, "_composer_imgs",
                          side_effect=lambda page: counts.pop(0)), \
                patch.object(flow_driver, "_click",
                             side_effect=lambda *a, **k: (clicks.append(1), True)[1]):
            flow_driver._clear_ingredients(FakePage(), config.FLOW_SELECTORS)
        self.assertEqual(len(clicks), 2)        # bấm xoá 2 lần rồi mới sạch

        # Ô soạn đã rỗng → không bấm gì cả.
        clicks.clear()
        with patch.object(flow_driver, "_composer_imgs", return_value=0), \
                patch.object(flow_driver, "_click",
                             side_effect=lambda *a, **k: (clicks.append(1), True)[1]):
            flow_driver._clear_ingredients(FakePage(), config.FLOW_SELECTORS)
        self.assertEqual(clicks, [])

    def test_clip_downloaded_straight_from_tile_media_url(self):
        """Tải clip = GET thẳng URL media biến thể chất lượng cao, không dùng
        download manager của trình duyệt (Cốc Cốc crash ở bước đó)."""
        src = "https://flow.google.com/asb/AB-nOU_abc123=mm,22,15"
        asked: list[str] = []

        class FakeResp:
            status = 200

            def body(self):
                return b"v" * 50_000

        class FakePage:
            mouse = SimpleNamespace(move=lambda *a, **k: None)
            request = SimpleNamespace(
                get=lambda url, **k: (asked.append(url), FakeResp())[1])

            def wait_for_timeout(self, ms):
                pass

            def evaluate(self, js):
                return src

        with TemporaryDirectory() as tmp:
            dest = Path(tmp) / "clip_00.mp4"
            ok = flow_driver._fetch_tile_media(
                FakePage(), {"x": 10, "y": 20}, dest)
            self.assertTrue(ok)
            self.assertEqual(dest.stat().st_size, 50_000)
            # Đổi hậu tố sang biến thể nét hơn (mm,37,15), giữ nguyên id.
            self.assertEqual(
                asked, [f"https://flow.google.com/asb/AB-nOU_abc123"
                        f"={config.FLOW_MEDIA_VARIANT}"])

        # URL CDN ĐÃ KÝ (có ?Expires=…&Signature=…): phải GET NGUYÊN VĂN, cắt
        # theo dấu '=' là ra URL rác và server trả 403.
        src = ("https://lh3.googleusercontent.com/asb/AB-x9?Expires=1790340079"
               "&KeyName=labs-flow-prod-cdn-key&Signature=rR54sqltWI9pk")
        asked.clear()
        with TemporaryDirectory() as tmp:
            dest = Path(tmp) / "clip_01.mp4"
            self.assertTrue(flow_driver._fetch_tile_media(
                FakePage(), {"x": 10, "y": 20}, dest))
            self.assertEqual(asked, [src])

        # Không lấy được URL → trả False để rơi về đường menu ⋯ → Tải xuống.
        class NoVideoPage(FakePage):
            def evaluate(self, js):
                return None

        with TemporaryDirectory() as tmp:
            self.assertFalse(flow_driver._fetch_tile_media(
                NoVideoPage(), {"x": 1, "y": 2}, Path(tmp) / "c.mp4"))

    def test_clip_job_reopens_browser_after_it_closes_midway(self):
        """Flow cũng phải mở lại phiên khi cửa sổ tự đóng (như bước tạo ảnh)."""
        closed = ("Download.save_as: Target page, context or browser "
                  "has been closed")
        with TemporaryDirectory() as tmp, patch.object(config, "MEDIA_ROOT", Path(tmp)), \
                patch.object(flow_driver, "time", SimpleNamespace(sleep=lambda s: None)), \
                patch.object(flow_driver, "BrowserSession",
                             side_effect=RuntimeError(closed)) as sess:
            img = config.images_dir(31, "Reopen")
            img.mkdir(parents=True, exist_ok=True)
            for i in range(config.PARAMS.image_count):
                (img / f"{i}.png").write_bytes(b"x" * 20000)
            with self.assertRaisesRegex(RuntimeError, "has been closed"):
                generate_clips(31, config.PARAMS, None, resume=True,
                               project_name="Reopen")
            # 3 phiên: chết giữa chừng thì mở lại, hết lượt mới ném lỗi.
            self.assertEqual(sess.call_count, 3)

        # Lỗi khác (Flow chặn) phải nổi lên NGAY, không mở lại phiên.
        with TemporaryDirectory() as tmp, patch.object(config, "MEDIA_ROOT", Path(tmp)), \
                patch.object(flow_driver, "BrowserSession",
                             side_effect=RuntimeError("Flow từ chối tạo clip")) as sess2:
            img2 = config.images_dir(32, "Boom")
            img2.mkdir(parents=True, exist_ok=True)
            for i in range(config.PARAMS.image_count):
                (img2 / f"{i}.png").write_bytes(b"x" * 20000)
            with self.assertRaisesRegex(RuntimeError, "từ chối"):
                generate_clips(32, config.PARAMS, None, resume=True,
                               project_name="Boom")
            self.assertEqual(sess2.call_count, 1)

    def test_blocked_flow_waits_then_reopens_a_new_session(self):
        """Thẻ «hoạt động bất thường» = cờ TẠM THỜI (tự hết sau ~20–30 phút):
        nghỉ dài rồi mở phiên mới, tối đa FLOW_MAX_BLOCK_RETRIES lần. Hết
        lượt/credit thì nghỉ vô ích → ném ra ngay."""
        slept: list[float] = []
        fake_time = SimpleNamespace(sleep=lambda s: slept.append(s),
                                    time=time.time)
        blocked = flow_driver.FlowBlocked(
            "Google Flow từ chối tạo clip: “… hoạt động bất thường …”")
        with TemporaryDirectory() as tmp, patch.object(config, "MEDIA_ROOT", Path(tmp)), \
                patch.object(flow_driver, "time", fake_time), \
                patch.object(flow_driver.random, "uniform", lambda a, b: 0.0), \
                patch.object(flow_driver, "BrowserSession",
                             side_effect=blocked) as sess:
            img = config.images_dir(33, "Blocked")
            img.mkdir(parents=True, exist_ok=True)
            for i in range(config.PARAMS.image_count):
                (img / f"{i}.png").write_bytes(b"x" * 20000)
            with self.assertRaisesRegex(RuntimeError, "hoạt động bất thường"):
                generate_clips(33, config.PARAMS, None, resume=True,
                               project_name="Blocked")
            self.assertEqual(sess.call_count,
                             config.FLOW_MAX_BLOCK_RETRIES + 1)

        # Hết lượt/credit: KHÔNG nghỉ, KHÔNG mở lại — báo người dùng ngay.
        with TemporaryDirectory() as tmp, patch.object(config, "MEDIA_ROOT", Path(tmp)), \
                patch.object(flow_driver, "BrowserSession",
                             side_effect=flow_driver.FlowBlocked(
                                 "Google Flow từ chối tạo clip: “hết lượt”")) as s2:
            img = config.images_dir(34, "NoCredit")
            img.mkdir(parents=True, exist_ok=True)
            for i in range(config.PARAMS.image_count):
                (img / f"{i}.png").write_bytes(b"x" * 20000)
            with self.assertRaisesRegex(RuntimeError, "hết lượt"):
                generate_clips(34, config.PARAMS, None, resume=True,
                               project_name="NoCredit")
            self.assertEqual(s2.call_count, 1)

    def test_session_quota_is_smaller_than_the_block_threshold(self):
        """Flow gắn cờ sau ~5–6 lượt/phiên ⇒ hạn mức phải THẤP HƠN, và nghỉ
        giữa hai phiên phải đủ dài để cờ tự hết (>= 15 phút)."""
        self.assertLessEqual(config.FLOW_CLIPS_PER_SESSION, 5)
        self.assertGreaterEqual(config.FLOW_SESSION_COOLDOWN[0], 900)
        self.assertGreaterEqual(config.FLOW_BLOCK_COOLDOWN[0],
                                config.FLOW_SESSION_COOLDOWN[0])
        # Đủ phiên để làm hết 40 clip (40/4 = 10 phiên) + dư cho lỗi.
        self.assertGreaterEqual(
            config.FLOW_MAX_SESSIONS,
            config.PARAMS.total_clips / config.FLOW_CLIPS_PER_SESSION)

    def test_media_folder_uses_name_and_id(self):
        self.assertEqual(config.project_dir(2, "Bamboo Flute").name, "Bamboo Flute (#2)")
        self.assertEqual(config.project_dir(2, 'Zen: Focus / Study?').name, "Zen_ Focus _ Study_ (#2)")

    def test_flow_resume_with_full_clip_set_does_not_open_browser(self):
        total = config.PARAMS.total_clips
        with TemporaryDirectory() as tmp, patch.object(config, "MEDIA_ROOT", Path(tmp)), \
                patch("backend.video.flow_driver.BrowserSession") as browser:
            images = config.images_dir(12, "Flow Test")
            clips = config.clips_dir(12, "Flow Test")
            images.mkdir(parents=True)
            clips.mkdir(parents=True)
            for i in range(config.PARAMS.image_count):
                (images / f"{i}.png").write_bytes(b"test image")
            for k in range(total):
                (clips / f"clip_{k:02d}.mp4").write_bytes(b"x" * 10001)
            result = generate_clips(12, resume=True, project_name="Flow Test")
            saved = json.loads((clips / "_plan.json").read_text(encoding="utf-8"))
            self.assertEqual(len(saved["plan"]), total)
            self.assertEqual(saved["plan"][0], list(config.INTRO_INGREDIENTS))
            self.assertTrue(all(p == list(config.SHEET_IMAGES) for p in saved["plan"][1:]))
            self.assertEqual(len(result), total)
            browser.assert_not_called()

    def test_flow_requires_both_sheets_but_not_the_thumbnail(self):
        with TemporaryDirectory() as tmp, patch.object(config, "MEDIA_ROOT", Path(tmp)), \
                patch("backend.video.flow_driver.BrowserSession") as browser:
            images = config.images_dir(14, "Sheets")
            clips = config.clips_dir(14, "Sheets")
            images.mkdir(parents=True)
            clips.mkdir(parents=True)
            # Thiếu bảng linh thú (ảnh 2) → phải dừng ngay, không mở trình duyệt.
            (images / "0.png").write_bytes(b"x")
            (images / "1.png").write_bytes(b"x")
            with self.assertRaises(FileNotFoundError):
                generate_clips(14, resume=True, project_name="Sheets")
            # Đủ hai bảng, thiếu ảnh bìa mà clip intro 00 chưa có → phải dừng
            # (intro dựng từ ảnh bìa).
            (images / "2.png").write_bytes(b"x")
            (images / "0.png").unlink()
            for k in range(1, config.PARAMS.total_clips):
                (clips / f"clip_{k:02d}.mp4").write_bytes(b"x" * 10001)
            with self.assertRaisesRegex(FileNotFoundError, "0.png"):
                generate_clips(14, resume=True, project_name="Sheets")
            # Intro đã có → thiếu ảnh bìa vẫn resume được.
            (clips / "clip_00.mp4").write_bytes(b"x" * 10001)
            generate_clips(14, resume=True, project_name="Sheets")
            browser.assert_not_called()

    def test_production_sequence_intro_once_and_window_across_cycles(self):
        with TemporaryDirectory() as tmp, patch.object(config, "MEDIA_ROOT", Path(tmp)), \
                patch.object(service, "probe_duration", return_value=7200), \
                patch.object(service, "overlay_title_on_clip", return_value=False), \
                patch.object(service, "assemble_video_blend", return_value={}) as assemble:
            clips = config.clips_dir(13, "Sequence Test")
            clips.mkdir(parents=True)
            for i in range(config.PARAMS.total_clips):
                (clips / f"clip_{i:02d}.mp4").touch()
            service.step_assemble(13, lambda *args: None, audio_path="mock.wav",
                                  seed=7, project_name="Sequence Test")
            sequence = assemble.call_args.args[0]
            self.assertEqual(Path(sequence[0]).name, "clip_00.mp4")
            rest = sequence[1:]
            self.assertEqual(len(rest), len(set(rest)))
            self.assertNotIn(sequence[0], rest)
            expanded = [sequence[0], *rest, *rest, *rest]
            self.assertEqual(expanded.count(sequence[0]), 1)
            for idx, clip in enumerate(expanded):
                self.assertNotIn(clip, expanded[max(0, idx-config.PARAMS.t_window):idx])

    def test_actual_assembler_keeps_intro_out_of_loop_and_bounded_command(self):
        calls, lists = [], []
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            audio = root / "audio.wav"
            audio.touch()
            def run(cmd):
                calls.append(cmd)
                script = Path(cmd[cmd.index("-filter_complex_script") + 1])
                self.assertIn("xfade=transition=fade", script.read_text())
                return SimpleNamespace(returncode=0, stderr="")
            def mux(path, *args):
                lists.extend(path.read_text(encoding="utf-8").splitlines())
            with patch.object(assembler, "_run", side_effect=run), \
                    patch.object(assembler, "probe_duration", return_value=8), \
                    patch.object(assembler, "_mux_visual_list", side_effect=mux):
                assembler.assemble_video_blend([f"clip_{i:02d}.mp4" for i in range(41)],
                    str(audio), str(root / "final.mp4"), target_seconds=86400)
            self.assertIn("clip_00.mp4", calls[0])
            self.assertNotIn("clip_00.mp4", calls[1])
            self.assertEqual(sum("_visual_intro.mp4" in line for line in lists), 1)
            self.assertTrue(all("_visual_cycle.mp4" in line for line in lists[1:]))
            self.assertLess(max(len(" ".join(cmd)) for cmd in calls), 32767)


class _FakeLocator:
    def __init__(self, visible, clicks):
        self._visible, self._clicks = visible, clicks

    def count(self):
        return 1 if self._visible else 0

    @property
    def first(self):
        return self

    def is_visible(self):
        return self._visible

    def click(self):
        self._clicks.append(1)


class _FakeFlowPage:
    """Trang Flow giả: đếm clip xong = 0 mãi (Veo lỗi), có nút Retry, và
    page_text mô phỏng thẻ báo lỗi hiện trên màn hình."""

    def __init__(self, page_text="", retry=True):
        self.page_text, self.retry, self.clicks = page_text, retry, []
        self.waited_ms = 0

    def evaluate(self, script):
        if script == flow_driver._JS_PAGE_TEXT:
            return self.page_text
        if script == flow_driver._JS_BUTTONS:
            return []
        return 0                      # _JS_DONE_CLIPS: chưa có clip nào xong

    def locator(self, sel):
        return _FakeLocator(self.retry and "etry" in sel, self.clicks)

    def wait_for_timeout(self, ms):
        self.waited_ms += ms          # không ngủ thật → test chạy nhanh


_BLOCK_CARD = (
    "Không thành công\n"
    "Chúng tôi nhận thấy có hoạt động bất thường nào đó. Vui lòng truy cập vào "
    "Trung tâm trợ giúp để biết thêm thông tin.\n"
    "Bạn chưa bị tính phí cho lượt tạo này."
)


class FlowFailureHandlingTests(unittest.TestCase):
    def test_block_reason_detects_card_and_ignores_normal_page(self):
        self.assertIn("hoạt động bất thường",
                      flow_driver._flow_block_reason(_FakeFlowPage(_BLOCK_CARD)))
        self.assertIsNone(flow_driver._flow_block_reason(
            _FakeFlowPage("Dự án mới\nBắt đầu tạo\nThành phần")))

    def test_blocked_card_stops_pipeline_without_clicking_retry(self):
        page = _FakeFlowPage(_BLOCK_CARD)
        with self.assertRaises(flow_driver.FlowBlocked) as ctx:
            flow_driver._wait_and_download_clip(
                page, {}, Path("clip_00.mp4"), 900, 0)
        self.assertIn("hoạt động bất thường", str(ctx.exception))
        self.assertIn("TẠO LẠI", str(ctx.exception))
        self.assertEqual(page.clicks, [])      # KHÔNG bấm Retry khi bị chặn

    def test_plain_failure_retries_then_gives_up_long_before_wait_sec(self):
        page = _FakeFlowPage("Không thành công\nBạn chưa bị tính phí cho lượt tạo này.")
        started = time.time()
        self.assertFalse(flow_driver._wait_and_download_clip(
            page, {}, Path("clip_00.mp4"), 900, 0))
        self.assertEqual(len(page.clicks), flow_driver._CLIP_MAX_RETRIES)
        self.assertLess(time.time() - started, 30)   # không nằm chờ hết 900s

    def test_wait_loop_reports_progress_so_cancel_can_land(self):
        page, ticks = _FakeFlowPage(retry=False), []
        with patch.object(flow_driver.time, "time",
                          side_effect=[0, 0, 0, 31, 31, 31, 31, 10_000]):
            flow_driver._wait_and_download_clip(
                page, {}, Path("clip_00.mp4"), 900, 0,
                on_tick=lambda msg: ticks.append(msg))
        self.assertTrue(ticks and "đang render" in ticks[0])


if __name__ == "__main__":
    unittest.main()
