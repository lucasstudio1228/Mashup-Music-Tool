"""2026-10-06: Gemini phải BẬT công cụ «Tạo hình ảnh» (không bật → trả chữ «image
generation tool has been disabled»); lô không kẹt khi phase Suno bị khâu rà soát
ghi đè sau khi đã nhập đủ bài."""
import unittest

from backend import batch_service as bs
from backend.video import config, gemini_driver as gd
from backend.video import suno_service as ss


class _Loc:
    def __init__(self, page, sel):
        self.p, self.sel = page, sel

    @property
    def first(self):
        return self

    def _exists(self):
        if self.sel in self.p.sels["image_tool_chip"]:
            return self.p.tool_on
        if self.sel in self.p.sels["image_tool_item"]:
            return self.p.menu_open
        return False

    def count(self):
        return 1 if self._exists() else 0

    def is_visible(self):
        return self._exists()

    def get_attribute(self, name):
        return "true" if self.p.tool_on else "false"

    def click(self, timeout=None):
        self.p.clicks.append("tool")
        self.p.tool_on = True
        self.p.menu_open = False


class _Keyboard:
    def press(self, k):
        pass


class _Page:
    def __init__(self, tool_on=False):
        self.sels = config.get_selectors("gemini")
        self.tool_on, self.menu_open, self.clicks = tool_on, False, []
        self.keyboard = _Keyboard()

    def locator(self, sel):
        return _Loc(self, sel)

    def wait_for_timeout(self, ms):
        pass


def _fake_click_first(page, selectors, timeout_ms=0):
    if selectors is page.sels["upload_menu"]:
        page.menu_open = True
        page.clicks.append("menu")
        return True
    if selectors is page.sels["image_ratio_button"]:
        page.clicks.append("ratio")
        return True
    if any("16:9" in s for s in selectors):
        page.clicks.append("16:9")
        return True
    return False


class EnableImageToolTests(unittest.TestCase):
    def setUp(self):
        self._orig = gd.click_first
        gd.click_first = _fake_click_first

    def tearDown(self):
        gd.click_first = self._orig

    def test_turns_tool_on_and_sets_16_9(self):
        page = _Page()
        self.assertTrue(gd._enable_image_tool(page, page.sels))
        self.assertEqual(page.clicks, ["menu", "tool", "ratio", "16:9"])

    def test_already_on_does_not_toggle_off(self):
        page = _Page(tool_on=True)
        self.assertTrue(gd._enable_image_tool(page, page.sels))
        self.assertNotIn("tool", page.clicks)

    def test_disabled_tool_reply_counts_as_server_error(self):
        reply = ("I cannot generate the image because my image generation tool "
                 "has been disabled for this request.")
        self.assertTrue(any(k in reply.lower() for k in gd._SERVER_ERR))
        reply2 = "I seem to be encountering an error. Can I try something else for you?"
        self.assertTrue(any(k in reply2.lower() for k in gd._SERVER_ERR))


class SunoDoneTests(unittest.TestCase):
    def test_completed(self):
        self.assertTrue(bs._suno_done({"phase": ss.COMPLETED}))

    def test_stale_downloading_but_all_imported(self):
        self.assertTrue(bs._suno_done({"phase": ss.DOWNLOADING,
                                       "imported_tracks": 15, "target_tracks": 15}))

    def test_not_enough_imported(self):
        self.assertFalse(bs._suno_done({"phase": ss.DOWNLOADING,
                                        "imported_tracks": 9, "target_tracks": 15}))

    def test_blocking_or_cancelled_never_done(self):
        for ph in (ss.WAITING_FOR_LOGIN, ss.FAILED, ss.CANCELLED, None):
            self.assertFalse(bs._suno_done({"phase": ph, "imported_tracks": 15,
                                            "target_tracks": 15}), ph)


if __name__ == "__main__":
    unittest.main()
