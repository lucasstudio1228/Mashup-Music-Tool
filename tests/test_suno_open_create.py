"""open_create_page: session-recovery treo/rơi về trang chủ «Log in» → tải lại
rồi mới kết luận; trang chủ đăng xuất có nút Create KHÔNG được coi là sẵn sàng.
Trang giả lập — không mở trình duyệt."""
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent))

from backend.video import suno_driver
from backend.video.suno_driver import (SunoDriver, SunoError, SunoLoginRequired,
                                       suno_logged_out)


class TimeoutError_(Exception):
    pass


TimeoutError_.__name__ = "TimeoutError"


class _Loc:
    def __init__(self, n):
        self.n = n

    def count(self):
        return self.n

    def nth(self, i):
        return self

    def is_visible(self):
        return self.n > 0


class FakePage:
    """Mỗi goto lấy 1 kịch bản: 'timeout' | 'home' (đăng xuất) | 'create'."""

    def __init__(self, script):
        self.script = list(script)
        self.url = "about:blank"
        self.gotos = 0

    def goto(self, url, wait_until=None, timeout=None):
        self.gotos += 1
        step = self.script.pop(0)
        if step == "timeout":
            self.url = "https://suno.com/auth/session-recovery?return_to=%2Fcreate"
            raise TimeoutError_("Page.goto: Timeout 45000ms exceeded.")
        self.url = {"home": "https://suno.com/",
                    "create": "https://suno.com/create"}[step]

    def locator(self, sel):
        return _Loc(1 if self.url == "https://suno.com/" else 0)


def _fake_query_first(page, sels, timeout_ms=0):
    # Trang chủ đăng xuất CŨNG có nút Create → query_first vẫn trả phần tử.
    return object() if page.url.startswith("https://suno.com/") and "/auth/" not in page.url else None


class OpenCreatePageTests(unittest.TestCase):
    def setUp(self):
        for target, value in (("backend.video.suno_driver.query_first", _fake_query_first),
                              ("backend.video.suno_driver.wait_ms", lambda p, ms: None)):
            p = patch(target, value)
            p.start()
            self.addCleanup(p.stop)
        self.logs = []

    def _driver(self, script):
        page = FakePage(script)
        return SunoDriver(page, selectors={"create_button": ["x"]},
                          log=self.logs.append), page

    def test_recovers_after_timeout_and_logged_out_home(self):
        d, page = self._driver(["timeout", "home", "create"])
        with patch("backend.video.suno_driver.time.time", side_effect=range(0, 10**6, 30)):
            d.open_create_page()
        self.assertEqual(page.gotos, 3)
        self.assertIn("sẵn sàng", self.logs[-1])

    def test_logged_out_home_is_not_ready(self):
        d, page = self._driver(["home", "home", "home"])
        with self.assertRaises(SunoLoginRequired) as cm:
            d.open_create_page()
        self.assertIn("Mở UI Suno để đăng nhập", str(cm.exception))
        self.assertEqual(page.gotos, 3)

    def test_all_timeouts_reports_unresponsive_not_login(self):
        d, _ = self._driver(["timeout"] * 3)
        with self.assertRaises(SunoError) as cm:
            d.open_create_page()
        self.assertNotIsInstance(cm.exception, SunoLoginRequired)
        self.assertIn("không phản hồi", str(cm.exception))

    def test_non_timeout_error_propagates(self):
        d, page = self._driver(["create"])
        page.goto = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("Target closed"))
        with self.assertRaises(RuntimeError):
            d.open_create_page()

    def test_logged_out_detection(self):
        page = FakePage([])
        page.url = "https://suno.com/"
        self.assertTrue(suno_logged_out(page))
        page.url = "https://suno.com/create"
        self.assertFalse(suno_logged_out(page))
        page.url = "https://suno.com/sign-in?redirect=create"
        self.assertTrue(suno_logged_out(page))
        page.url = "https://suno.com/auth/session-recovery"
        self.assertFalse(suno_logged_out(page))      # đang khôi phục, chưa kết luận


class IsLoggedInTests(unittest.TestCase):
    def test_home_page_with_create_button_is_not_logged_in(self):
        from backend.video import suno_selfcheck
        page = FakePage([])
        with patch("backend.video.suno_selfcheck.query_first", _fake_query_first):
            page.url = "https://suno.com/"
            self.assertFalse(suno_selfcheck.is_logged_in(page, {"create_button": ["x"]}))
            page.url = "https://suno.com/create"
            self.assertTrue(suno_selfcheck.is_logged_in(page, {"create_button": ["x"]}))



class CountdownTests(unittest.TestCase):
    def test_countdown_reports_remaining_and_sleeps_total(self):
        from backend.video import suno_service
        clock = {"t": 1000.0}
        slept, msgs = [], []

        def fake_sleep(sec):
            slept.append(sec)
            clock["t"] += sec
        with patch.object(suno_service.time, "time", lambda: clock["t"]), \
                patch.object(suno_service, "cancellable_sleep", fake_sleep):
            suno_service._countdown(40, "Chờ Suno", msgs.append, step=15)
        self.assertAlmostEqual(sum(slept), 40)
        self.assertEqual([m.split("~")[1].split("s")[0] for m in msgs], ["40", "25", "10"])
        self.assertIn("không phải treo", msgs[0])


if __name__ == "__main__":
    unittest.main()
