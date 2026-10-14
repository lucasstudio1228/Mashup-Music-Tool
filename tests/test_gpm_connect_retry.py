"""Đăng nháp YouTube: trình duyệt GPMLogin chết ngay sau khi bật (CDP
«WebSocket error: connect ECONNREFUSED») → đóng profile, bật lại, thử lại.
Lỗi SAU khi đã nối YouTube Studio thì KHÔNG thử lại (tránh tải trùng)."""
import unittest

from backend.video import gpm_client, service
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


    def test_kills_leftover_chrome_before_restarting(self):
        """GPM «mất dấu» Chrome cũ (giữ khoá profile) → dọn rồi mới bật lại."""
        class _C(_Client):
            kills = 0

            def kill_leftover_browsers(self, pid):
                self.kills += 1
                return 9 if self.kills == 1 else 0
        c, msgs, seen = _C(), [], []

        def upload(cdp):
            seen.append(cdp)
            if len(seen) < 2:
                raise CDPConnectError("connect ECONNREFUSED")
            return {"status": "draft"}
        r = service._upload_with_gpm_retry(c, "p1", upload, lambda m, p: msgs.append(m),
                                           sleep=lambda s: None)
        self.assertEqual(r, {"status": "draft"})
        self.assertEqual(c.kills, 1)                  # chỉ dọn sau lần nối hỏng
        self.assertTrue(any("9 tiến trình Chrome sót" in m for m in msgs))


class LeftoverBrowserTest(unittest.TestCase):
    def test_rejects_unsafe_profile_ids(self):
        for bad in ("", "*", "abc", "x' ; Stop-Process -Name chrome ; '"):
            self.assertEqual(gpm_client.find_profile_browser_pids(bad), [], bad)
            self.assertEqual(gpm_client.kill_profile_browsers(bad), 0, bad)

    def test_unknown_profile_has_no_processes(self):
        self.assertEqual(gpm_client.find_profile_browser_pids(
            "00000000-0000-0000-0000-000000000000"), [])

    def test_kill_after_wait_only_when_still_alive(self):
        alive = [[11, 12], [11, 12], [11, 12]]
        orig = (gpm_client.find_profile_browser_pids, gpm_client.kill_profile_browsers,
                gpm_client.time.sleep)
        gpm_client.find_profile_browser_pids = lambda pid: alive[0]
        gpm_client.kill_profile_browsers = lambda pid: len(alive[0])
        gpm_client.time.sleep = lambda s: None
        try:
            c = gpm_client.GPMClient("http://127.0.0.1:1")
            self.assertEqual(c.kill_leftover_browsers("p", wait_sec=0), 2)
            alive[0] = []
            self.assertEqual(c.kill_leftover_browsers("p", wait_sec=0), 0)
        finally:
            (gpm_client.find_profile_browser_pids, gpm_client.kill_profile_browsers,
             gpm_client.time.sleep) = orig


if __name__ == "__main__":
    unittest.main()
