"""Selector YouTube Studio: không được có selector chữ KHÔNG giới hạn vùng.

Lỗi 2026-10-03: fallback `tp-yt-paper-radio-button:has-text('Có')` của mục
«Sử dụng AI» khớp radio «Có, nội dung này dành cho trẻ em» → bản nháp bị đặt
nhầm là video cho trẻ em và bỏ trống khai báo AI.
"""
import re
import unittest

from backend.video import config
from backend.video import youtube_driver as yd


class YoutubeSelectorSafetyTests(unittest.TestCase):
    def setUp(self):
        self.sel = config.get_selectors("youtube")

    def test_no_unscoped_wildcard_text_selectors(self):
        for key, items in self.sel.items():
            for css in items:
                self.assertFalse(css.lstrip().startswith("*:"),
                                 f"{key}: selector '{css}' khớp cả <html>")

    def test_ai_yes_only_targets_altered_content_radios(self):
        for css in self.sel["altered_content_yes"]:
            scoped = ("ALTERED_CONTENT" in css or "altered-content" in css
                      or "ytkp-altered-content-select" in css)
            self.assertTrue(scoped, f"selector AI không giới hạn vùng: {css}")
            self.assertIsNone(re.search(r"has-text\('(Có|Yes)'\)", css), css)
        self.assertIn("VIDEO_HAS_ALTERED_CONTENT_YES",
                      self.sel["altered_content_yes"][0])

    def test_mfk_no_never_matches_kids_yes(self):
        for css in self.sel["mfk_no"]:
            self.assertNotIn("nth-of-type", css)
            self.assertNotIn("'VIDEO_MADE_FOR_KIDS_MFK'", css)

    def test_language_and_category_use_test_ids(self):
        self.assertIn("test-id='{lang}'", self.sel["video_language_option"][0])
        self.assertIn("CREATOR_VIDEO_CATEGORY_MUSIC", self.sel["category_music"][0])
        self.assertIn("tp-yt-paper-item:text-is('Nhạc')", self.sel["category_music"])


class _FakeLoc:
    def __init__(self, page, name):
        self.page, self.name = page, name

    def count(self):
        return 1 if self.name in self.page.radios else 0

    def get_attribute(self, attr):
        if attr == "aria-checked":
            return "true" if self.page.radios.get(self.name) else "false"
        return None

    @property
    def first(self):
        return self


class _FakePage:
    def __init__(self, radios):
        self.radios = radios

    def locator(self, css):
        m = re.search(r"name='([^']+)'", css)
        return _FakeLoc(self, m.group(1) if m else "")

    def wait_for_timeout(self, ms):
        pass


class EnsureRadioTests(unittest.TestCase):
    def test_reads_aria_checked_and_reclicks(self):
        page = _FakePage({"VIDEO_MADE_FOR_KIDS_MFK": True,
                          "VIDEO_MADE_FOR_KIDS_NOT_MFK": False})
        clicks = []

        def fake_click(p, selectors, timeout_ms=0):
            clicks.append(selectors[0])
            p.radios["VIDEO_MADE_FOR_KIDS_MFK"] = False
            p.radios["VIDEO_MADE_FOR_KIDS_NOT_MFK"] = True
            return True

        orig = yd._bb.click_first
        yd._bb.click_first = fake_click
        try:
            ok = yd._ensure_radio(page, ["x[name='VIDEO_MADE_FOR_KIDS_NOT_MFK']"],
                                  yd._MFK_NO_NAMES)
        finally:
            yd._bb.click_first = orig
        self.assertTrue(ok)
        self.assertEqual(len(clicks), 1)

    def test_missing_radio_returns_none(self):
        page = _FakePage({})
        orig = yd._bb.click_first
        yd._bb.click_first = lambda *a, **k: False
        try:
            self.assertIsNone(yd._ensure_radio(page, ["nope"], yd._AI_YES_NAMES))
        finally:
            yd._bb.click_first = orig


if __name__ == "__main__":
    unittest.main()
