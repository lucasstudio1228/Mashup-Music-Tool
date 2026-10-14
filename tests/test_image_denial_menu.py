"""2026-10-13 — project 35 «Cherry Blossom Serenity» 0/3 ảnh:
(1) Muse TỪ CHỐI đúng bản mô tả (13 lần gửi y hệt, ~55') → giờ thử 1 bản trung tính
    rồi chuyển Gemini; nhớ bản đã bị từ chối (images/_muse_denied.json).
(2) Gemini đổi menu chế độ (bỏ «Tư duy mở rộng», thêm mức Thấp/Trung bình/Cao) →
    menu còn mở che ô nhập → «không tìm thấy ô nhập prompt». Giờ chọn «Cao», đóng
    menu bằng Escape, báo lỗi THẬT khi ô bị che."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from backend.video import config, gemini_driver as gd, gemini_video, muse_image, service
from backend.video.gemini_driver import _prompt_for

PROMPTS = {"0": "A calm scene of a flute player beside a moonlit bamboo grove.",
           "1": "CHARACTER MODEL SHEET of an adult man in an indigo hanfu.",
           "2": "CHARACTER MODEL SHEET of a small white fox companion."}

DENIALS = [
    "I couldn't make this particular model sheet — the image tool turned it down.",
    "Still denied on this exact spec. It needs to be a genuinely different version.",
    "Sorry, I can't create that particular image.",
    "That request was denied by the image generator.",
]
NOT_DENIALS = [
    "The video tool hit an internal error — let me try again.",
    "Something went wrong while generating; I couldn't make this particular image.",
    "Here is your character model sheet!",
    "",
]


def _final_prompt(i: int, style: str) -> str:
    body = _prompt_for(i, "Moonlit", config.PARAMS, PROMPTS, style)
    if i == config.IMG_THUMBNAIL:
        body += config.THUMBNAIL_REF_NOTE
    return muse_image.image_prompt(body)


class DenialDetectTests(unittest.TestCase):
    def test_spec_denials(self):
        for r in DENIALS:
            self.assertTrue(muse_image.is_spec_denial(r), r)

    def test_tool_errors_and_success_are_not_denials(self):
        for r in NOT_DENIALS:
            self.assertFalse(muse_image.is_spec_denial(r), r)

    def test_our_own_prompts_are_not_denials(self):
        # reply_after có thể dính đuôi prompt → chữ của TA không được khớp.
        self.assertFalse(muse_image.is_spec_denial(muse_image.MUSE_IMAGE_SPEC))
        for k in config.STYLES:
            for i in range(3):
                self.assertFalse(muse_image.is_spec_denial(_final_prompt(i, k)), (k, i))

    def test_neutral_style_drops_studio_names_only(self):
        p = "Soft hand-painted Studio Ghibli-style anime, warm light (Makoto Shinkai style)."
        alt = muse_image.neutral_style(p)
        self.assertNotIn("Ghibli", alt)
        self.assertNotIn("Shinkai", alt)
        self.assertIn("hand-painted Japanese anime", alt)
        self.assertIn("warm light", alt)
        plain = _final_prompt(1, "watercolor")
        self.assertEqual(muse_image.neutral_style(plain), plain)

    def test_denied_ledger_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            self.assertFalse(muse_image._was_denied(d, 1, "abc"))
            muse_image._remember_denied(d, 1, "abc")
            muse_image._remember_denied(d, 1, "abc")
            self.assertTrue(muse_image._was_denied(d, 1, "abc"))
            self.assertFalse(muse_image._was_denied(d, 2, "abc"))
            data = json.loads((d / muse_image._DENIED_FILE).read_text(encoding="utf-8"))
            self.assertEqual(len(data["1"]), 1)


class _Sess:
    def __init__(self, *a, **k):
        self.page = mock.Mock()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class MuseRefusedFlowTests(unittest.TestCase):
    def _run(self, img: Path, waits, style="ghibli", only=None):
        sends = []
        wait_iter = iter(waits)

        def fake_send(page, refs, prompt):
            sends.append(prompt)
            return "test"

        def fake_wait(page, prompt, base, timeout_sec, tick):
            return next(wait_iter)

        def fake_png(raw, dest, mode="pad"):
            Path(dest).write_bytes(b"p" * 5000)
            return 1920, 1080

        with mock.patch.object(config, "images_dir", return_value=img), \
                mock.patch.object(muse_image, "_GPMSession", _Sess), \
                mock.patch.object(muse_image, "profile_id", return_value="x"), \
                mock.patch.object(muse_image, "wait_connected"), \
                mock.patch.object(muse_image, "_baseline", return_value={}), \
                mock.patch.object(muse_image, "_send", side_effect=fake_send), \
                mock.patch.object(muse_image, "_wait_image", side_effect=fake_wait), \
                mock.patch.object(muse_image, "_late_image", return_value=False), \
                mock.patch.object(muse_image, "_download_image"), \
                mock.patch.object(muse_image, "to_png_16x9", side_effect=fake_png):
            try:
                out = muse_image.generate_images_muse(
                    35, "Moonlit", config.PARAMS, None, resume=False,
                    prompts_override=PROMPTS, style=style, only=only)
            except Exception as e:   # noqa: BLE001 — trả lỗi để so
                out = e
        return out, sends

    def test_refused_tries_one_neutral_variant_then_denies(self):
        with tempfile.TemporaryDirectory() as tmp:
            img = Path(tmp)
            refused = ("refused", {"text": DENIALS[0]})
            out, sends = self._run(img, [refused, refused])
            self.assertIsInstance(out, muse_image.MuseImageDenied)
            self.assertIsInstance(out, RuntimeError)      # vẫn chuyển máy dự phòng
            self.assertEqual(len(sends), 2)               # KHÔNG gửi lại y hệt 13 lần
            self.assertIn("Ghibli", sends[0])
            self.assertNotIn("Ghibli", sends[1])
            self.assertTrue(muse_image._was_denied(img, 1, sends[0]))
            self.assertTrue(muse_image._was_denied(img, 1, sends[1]))

    def test_refused_then_neutral_variant_succeeds(self):
        with tempfile.TemporaryDirectory() as tmp:
            img = Path(tmp)
            out, sends = self._run(img, [("refused", {"text": DENIALS[1]}),
                                         ("ready", {})], only=[1])
            self.assertEqual(len(sends), 2)
            self.assertNotIn("Ghibli", sends[1])
            self.assertTrue((img / "1.png").exists())
            self.assertEqual(out, [str(img / "1.png")])

    def test_refused_without_brand_names_denies_after_one_send(self):
        with tempfile.TemporaryDirectory() as tmp:
            out, sends = self._run(Path(tmp), [("refused", {"text": DENIALS[0]})],
                                   style="watercolor")
            self.assertIsInstance(out, muse_image.MuseImageDenied)
            self.assertEqual(len(sends), 1)

    def test_tool_error_still_resends(self):
        with tempfile.TemporaryDirectory() as tmp:
            img = Path(tmp)
            with mock.patch.object(muse_image, "_pause_ticking"):
                out, sends = self._run(img, [("error", {"text": NOT_DENIALS[0]}),
                                             ("ready", {})], only=[1])
            self.assertEqual(len(sends), 2)
            self.assertEqual(sends[0], sends[1])          # lỗi công cụ → gửi lại y hệt
            self.assertFalse((img / muse_image._DENIED_FILE).exists())

    def test_previous_denials_skip_muse_entirely(self):
        with tempfile.TemporaryDirectory() as tmp:
            img = Path(tmp)
            p = _final_prompt(1, "ghibli")
            muse_image._remember_denied(img, 1, p)
            muse_image._remember_denied(img, 1, muse_image.neutral_style(p))
            session = mock.Mock()
            with mock.patch.object(config, "images_dir", return_value=img):
                with self.assertRaises(muse_image.MuseImageDenied):
                    muse_image.generate_images_muse(
                        35, "Moonlit", config.PARAMS, None, resume=False,
                        prompts_override=PROMPTS, style="ghibli", _session_impl=session)
            session.assert_not_called()                   # chưa mở Muse

    def test_previous_denial_of_original_uses_neutral_variant(self):
        with tempfile.TemporaryDirectory() as tmp:
            img = Path(tmp)
            muse_image._remember_denied(img, 1, _final_prompt(1, "ghibli"))
            out, sends = self._run(img, [("ready", {})], only=[1])
            self.assertEqual(len(sends), 1)
            self.assertNotIn("Ghibli", sends[0])

    def test_denial_falls_back_to_gemini(self):
        gem = mock.Mock(return_value=["g"])
        muse = mock.Mock(side_effect=muse_image.MuseImageDenied("Ảnh 1: Muse.ai từ chối"))
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(config, "image_engine", return_value="muse"), \
                mock.patch.object(config, "image_fallback", return_value=True), \
                mock.patch.object(config, "images_dir", return_value=Path(tmp)), \
                mock.patch("backend.video.gemini_driver.generate_images", gem), \
                mock.patch("backend.video.muse_image.generate_images_muse", muse):
            res = service._generate_images(35, "t", mock.Mock(), resume=True, project_name="p",
                                           prompts_override=PROMPTS, style="ghibli")
        self.assertEqual(res, ["g"])
        self.assertFalse(gem.call_args.kwargs["resume"])


# ── Gemini: menu chế độ ─────────────────────────────────────────
class _GLoc:
    def __init__(self, page, sel):
        self.p, self.sel = page, sel

    @property
    def first(self):
        return self

    def _exists(self):
        if self.sel in gd._OPEN_MENU or self.sel == "[role='dialog']":
            return self.p.menu_open and self.sel == "[role='menu']"
        return False

    def count(self):
        return 1 if self._exists() else 0

    def is_visible(self):
        return self._exists()


class _GKeyboard:
    def __init__(self, page):
        self.p = page

    def press(self, key):
        self.p.keys.append(key)
        if key == "Escape" and not self.p.stuck_menu:
            self.p.menu_open = False

    def insert_text(self, text):
        self.p.typed = text


class _GPage:
    def __init__(self, options=("model_option_pro", "model_option_thinking"), stuck_menu=False):
        self.sels = config.get_selectors("gemini")
        self.options, self.stuck_menu = set(options), stuck_menu
        self.menu_open, self.keys, self.picked, self.typed = False, [], [], None
        self.keyboard = _GKeyboard(self)

    def locator(self, sel):
        return _GLoc(self, sel)

    def wait_for_timeout(self, ms):
        pass


def _menu_click_first(page, selectors, timeout_ms=0):
    if selectors is page.sels["model_selector"]:
        page.menu_open = True
        return True
    for key in ("model_option_pro", "model_option_thinking"):
        if selectors is page.sels[key]:
            if page.menu_open and key in page.options:
                page.picked.append(key)
                page.menu_open = False      # bấm mục → menu tự đóng
                return True
            return False
    return False


class GeminiMenuTests(unittest.TestCase):
    def setUp(self):
        p1 = mock.patch.object(gd, "click_first", _menu_click_first)
        p2 = mock.patch.object(gd, "_enable_image_tool", return_value=True)
        p1.start()
        p2.start()
        self.addCleanup(p1.stop)
        self.addCleanup(p2.stop)

    def test_selectors_match_new_menu(self):
        s = config.get_selectors("gemini")
        self.assertIn("bard-mode-menu-button", s["model_selector"][0])
        self.assertIn(":text-is('Cao')", s["model_option_thinking"][0])
        self.assertIn(":text-is('3.1 Pro')", s["model_option_pro"][0])
        # «:has-text('Cao')» khớp cả «Cao» lẫn mục khác → không dùng.
        for k in ("model_option_pro", "model_option_thinking"):
            self.assertFalse(any(x.startswith("*:") for x in s[k]), k)
            self.assertFalse(any(":has-text('Cao')" in x for x in s[k]), k)

    def test_select_model_picks_pro_and_high(self):
        page = _GPage()
        self.assertTrue(gd._select_model(page, page.sels))
        self.assertEqual(page.picked, ["model_option_pro", "model_option_thinking"])
        self.assertFalse(page.menu_open)

    def test_missing_level_closes_menu(self):
        page = _GPage(options=("model_option_pro",))   # không có «Cao»/«Tư duy mở rộng»
        gd._select_model(page, page.sels)
        self.assertFalse(page.menu_open)               # trước đây menu mở che ô nhập
        self.assertIn("Escape", page.keys)

    def test_thinking_label_regex(self):
        rx = gemini_video._THINK_RE
        for ok in ("Mở công cụ chọn chế độ, hiện tại là Pro Cao", "Pro Mở rộng",
                   "Pro Extended", "Pro High"):
            self.assertTrue(rx.search(ok), ok)
        for bad in ("Pro Thấp", "Pro Trung bình", "Pro", "Pro Low"):
            self.assertFalse(rx.search(bad), bad)


class _Box:
    def __init__(self, page, click_exc=None, fill_exc=None):
        self.p, self.click_exc, self.fill_exc = page, click_exc, fill_exc

    def click(self, timeout=None):
        if self.p.menu_open:
            raise TimeoutError("locator.click: Timeout 8000ms exceeded. <div> intercepts pointer events")
        if self.click_exc:
            raise self.click_exc

    def fill(self, text, timeout=None):
        if self.fill_exc:
            raise self.fill_exc
        self.p.typed = text


class GeminiFillPromptTests(unittest.TestCase):
    def test_open_menu_is_closed_before_typing(self):
        page = _GPage()
        page.menu_open = True
        with mock.patch.object(gd, "query_first", return_value=_Box(page)):
            self.assertEqual(gd._fill_prompt(page, page.sels, "hello"), "")
        self.assertEqual(page.typed, "hello")
        self.assertIn("Escape", page.keys)

    def test_stuck_menu_reports_covered_box(self):
        page = _GPage(stuck_menu=True)
        page.menu_open = True
        with mock.patch.object(gd, "query_first", return_value=_Box(page)):
            err = gd._fill_prompt(page, page.sels, "hello")
        self.assertIn("bị menu/hộp thoại che", err)
        self.assertIsNone(page.typed)

    def test_missing_box_reports_not_found(self):
        page = _GPage()
        with mock.patch.object(gd, "query_first", return_value=None):
            self.assertEqual(gd._fill_prompt(page, page.sels, "x"),
                             "không tìm thấy ô nhập prompt")

    def test_fill_failure_falls_back_to_insert_text(self):
        page = _GPage()
        box = _Box(page, fill_exc=RuntimeError("not an <input>"))
        with mock.patch.object(gd, "query_first", return_value=box):
            self.assertEqual(gd._fill_prompt(page, page.sels, "abc"), "")
        self.assertEqual(page.typed, "abc")

    def test_other_click_error_is_reported(self):
        page = _GPage()
        box = _Box(page, click_exc=RuntimeError("Element is detached"))
        with mock.patch.object(gd, "query_first", return_value=box):
            err = gd._fill_prompt(page, page.sels, "abc")
        self.assertIn("RuntimeError", err)
        self.assertIn("detached", err)


if __name__ == "__main__":
    unittest.main()
