"""Đăng nháp YouTube: trình duyệt GPMLogin chết ngay sau khi bật (CDP
«WebSocket error: connect ECONNREFUSED») → đóng profile, bật lại, thử lại.
Lỗi SAU khi đã nối YouTube Studio thì KHÔNG thử lại (tránh tải trùng)."""
import unittest

from backend.video import service
from backend.video.youtube_driver import CDPConnectError


class _Client:
    def __init__(self):
        self.starts, self.closes = 0, 0

    def start_profile(self, pid):
        self.starts += 1
        return f"ws://localhost:{50000 + self.starts}/devtools/browser/x"

    def close_profile(self, pid):
        self.closes += 1


class GpmRetryTest(unittest.TestCase):
    def _run(self, upload, attempts=3):
        c, msgs, sleeps = _Client(), [], []
        try:
            r = service._upload_with_gpm_retry(
                c, "p1", upload, lambda m, p: msgs.append(m),
                attempts=attempts, sleep=sleeps.append)
        except Exception as e:      # noqa: BLE001
            r = e
        return r, c, msgs, sleeps

    def test_reconnects_with_new_endpoint_after_connect_failure(self):
        seen = []

        def upload(cdp):
            seen.append(cdp)
            if len(seen) < 2:
                raise CDPConnectError("connect ECONNREFUSED")
            return {"status": "draft"}
        r, c, _, sleeps = self._run(upload)
        self.assertEqual(r, {"status": "draft"})
        self.assertEqual(len(set(seen)), 2)          # cổng MỚI mỗi lần bật
        self.assertEqual((c.starts, c.closes), (2, 2))
        self.assertEqual(len(sleeps), 1)

    def test_gives_up_after_attempts_with_guidance(self):
        def upload(cdp):
            raise CDPConnectError("connect ECONNREFUSED")
        r, c, _, sleeps = self._run(upload)
        self.assertIsInstance(r, RuntimeError)
        self.assertIn("GPMLogin", str(r))
        self.assertIn("3 lần", str(r))
        self.assertEqual((c.starts, c.closes), (3, 3))
        self.assertEqual(len(sleeps), 2)

    def test_error_after_connected_is_not_retried(self):
        def upload(cdp):
            raise RuntimeError("Không thấy nút Tải lên")
        r, c, _, _ = self._run(upload)
        self.assertIn("Tải lên", str(r))
        self.assertEqual((c.starts, c.closes), (1, 1))

    def test_browser_closed_mid_upload_gives_clear_message(self):
        class TargetClosedError(Exception):
            pass

        def upload(cdp):
            raise TargetClosedError("Page.wait_for_timeout: Target page, "
                                    "context or browser has been closed")
        r, c, _, _ = self._run(upload)
        self.assertIn("bị ĐÓNG", str(r))
        self.assertEqual((c.starts, c.closes), (1, 1))


if __name__ == "__main__":
    unittest.main()
