"""Chờ ảnh Gemini: ảnh vẽ lâu hơn mốc thường (Pro · Mở rộng ~200s) KHÔNG bị cắt
khi Gemini vẫn đang tạo; Gemini trả CHỮ thì dừng sớm và giữ nguyên văn."""
import unittest
from unittest import mock

from backend.video import gemini_driver as gd


class _Clock:
    def __init__(self):
        self.t = 1000.0

    def time(self):
        return self.t


class _Page:
    """Giả lập trang: ảnh xuất hiện ở giây `img_at`; đang tạo tới `busy_until`."""
    def __init__(self, clock, img_at=None, busy_until=0.0, reply=""):
        self.c, self.img_at, self.busy_until, self.reply = clock, img_at, busy_until, reply
        self.t0 = clock.t

    def evaluate(self, js, *a):
        el = self.c.t - self.t0
        if js is gd._JS_LARGE_IMAGES:
            if self.img_at is not None and el >= self.img_at:
                return [{"src": "blob:new", "w": 1024, "h": 572}]
            return []
        if js is gd._JS_GEN_STATE:
            busy = el < self.busy_until
            return {"busy": busy, "reply": "" if busy else self.reply}
        return None

    def wait_for_timeout(self, ms):
        self.c.t += ms / 1000.0


class WaitNewImageTest(unittest.TestCase):
    def _run(self, page, clock, timeout, max_sec):
        info = {}
        with mock.patch.object(gd.time, "time", clock.time):
            src = gd._wait_new_image(page, set(), timeout, max_sec, info)
        return src, info, clock.t - page.t0

    def test_slow_image_still_generating_is_not_cut(self):
        c = _Clock()
        page = _Page(c, img_at=400, busy_until=400)       # vượt mốc 300s
        src, _, _ = self._run(page, c, 300, 720)
        self.assertEqual(src, "blob:new")

    def test_image_appearing_just_after_busy_ends_is_caught(self):
        # Dò live: ảnh blob hiện vài giây SAU khi nút «Ngừng» biến mất.
        c = _Clock()
        page = _Page(c, img_at=610, busy_until=600, reply="Here is the character sheet")
        src, _, _ = self._run(page, c, 300, 720)
        self.assertEqual(src, "blob:new")

    def test_prompt_starts_with_generate_directive(self):
        self.assertTrue(gd.IMAGE_DIRECTIVE.startswith("Generate an image"))

    def test_text_reply_stops_early_with_reply(self):
        c = _Clock()
        page = _Page(c, busy_until=30, reply="I can't create that image.")
        src, info, el = self._run(page, c, 300, 720)
        self.assertIsNone(src)
        self.assertTrue(info.get("text_only"))
        self.assertIn("can't create", info["reply"])
        self.assertLess(el, 90)

    def test_hard_cap_when_forever_busy(self):
        c = _Clock()
        page = _Page(c, busy_until=10**9)
        src, info, el = self._run(page, c, 300, 720)
        self.assertIsNone(src)
        self.assertTrue(info.get("busy"))
        self.assertLessEqual(el, 725)

    def test_not_busy_no_reply_gives_up_at_timeout(self):
        c = _Clock()
        page = _Page(c)
        src, _, el = self._run(page, c, 300, 720)
        self.assertIsNone(src)
        self.assertLess(el, 305)


if __name__ == "__main__":
    unittest.main()
