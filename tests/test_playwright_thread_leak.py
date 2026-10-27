"""Phiên Playwright sync bị RÒ không được đầu độc luồng worker dùng chung.

2026-10-25: _GPMSession.__enter__ nối CDP xong rồi mở tab Muse lỗi → __exit__
không chạy → Playwright còn sống trong luồng video_worker → mọi job sau (clip
Muse, Suno…) lỗi «It looks like you are using Playwright Sync API inside the
asyncio loop». Test chạy Playwright THẬT (chỉ driver, không mở trình duyệt) và
các phiên giả; không gọi backend :8000, không bật GPM.
"""
import asyncio
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import threading
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent))
from backend.video import browser_base as bb          # noqa: E402 - nạp .runtime_packages311
from backend.video import muse_video as mv            # noqa: E402
from backend.video import youtube_driver as yd        # noqa: E402
from backend.video.job_manager import VideoJobManager  # noqa: E402


def _in_thread(fn):
    """Chạy fn trong 1 luồng mới (giống worker) → (kết quả, lỗi)."""
    box = {}

    def run():
        if sys.platform == "win32":
            asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
        try:
            box["result"] = fn()
        except BaseException as e:      # noqa: BLE001
            box["error"] = e
        finally:
            bb.reap_leaked_playwright("dọn sau test")

    t = threading.Thread(target=run, name="test_worker")
    t.start()
    t.join(120)
    return box.get("result"), box.get("error")


class RealPlaywrightLeakTests(unittest.TestCase):
    def test_leaked_session_is_reaped_before_next_start(self):
        def job():
            bb.start_playwright()                    # rò: không stop
            leaked_loop = asyncio._get_running_loop()
            fresh = bb.start_playwright()            # trước đây: ném lỗi asyncio loop
            fresh.stop()
            return leaked_loop, asyncio._get_running_loop()
        res, err = _in_thread(job)
        self.assertIsNone(err)
        leaked_loop, loop_after = res
        self.assertIsNotNone(leaked_loop, "phiên rò giữ loop của luồng (đúng cơ chế lỗi)")
        self.assertTrue(leaked_loop.is_closed(), "phiên rò phải được dừng THẬT (loop đóng)")
        self.assertIsNone(loop_after, "luồng phải sạch sau khi dừng phiên mới")

    def test_raw_second_start_still_fails_without_helper(self):
        """Đối chứng: đúng lỗi người dùng gặp nếu không đi qua start_playwright."""
        from playwright.sync_api import sync_playwright

        def job():
            bb.start_playwright()                    # rò
            sync_playwright().start()
        _, err = _in_thread(job)
        self.assertIsNotNone(err)
        self.assertIn("Sync API inside the asyncio loop", str(err))

    def test_worker_survives_job_that_leaks(self):
        """Job 1 bỏ ngỏ Playwright rồi lỗi; job 2 cùng worker vẫn mở được."""
        mgr = VideoJobManager()
        leaked = []

        def bad_job(pid, cb):
            leaked.append(bb.start_playwright())
            raise RuntimeError("tab Muse không mở được")

        def good_job(pid, cb):
            pw = bb.start_playwright()
            pw.stop()
            return {"ok": True}

        with patch("backend.video.job_manager.keep_awake"):
            mgr.submit(1, "clips", bad_job)
            mgr.submit(2, "suno", good_job)
            deadline = time.time() + 90
            while time.time() < deadline and mgr.get(2).status in ("pending", "running"):
                time.sleep(0.2)
        mgr._executor.shutdown(wait=True)
        self.assertEqual(mgr.get(1).status, "failed")
        self.assertEqual(mgr.get(2).status, "completed", mgr.get(2).error)
        self.assertEqual(bb._live_sessions(), [])   # luồng test không dính gì


class _FakePW:
    def __init__(self, connect_exc=None):
        self.stopped = 0
        self.connect_exc = connect_exc
        outer = self

        class _Chromium:
            def connect_over_cdp(self, ep, timeout=None):
                raise outer.connect_exc

        self.chromium = _Chromium()

    def stop(self):
        self.stopped += 1


class CDPSessionCleanupTests(unittest.TestCase):
    def test_cdp_connect_failure_stops_playwright(self):
        pw = _FakePW(connect_exc=ConnectionRefusedError("ECONNREFUSED"))
        with patch.object(bb, "start_playwright", return_value=pw):
            with self.assertRaises(yd.CDPConnectError):
                yd._CDPPlaywright("ws://127.0.0.1:1", connect_sec=0).__enter__()
        self.assertEqual(pw.stopped, 1)

    def test_cdp_cancel_during_connect_stops_playwright(self):
        pw = _FakePW(connect_exc=bb.OperationCancelled())
        with patch.object(bb, "start_playwright", return_value=pw):
            with self.assertRaises(bb.OperationCancelled):
                yd._CDPPlaywright("ws://127.0.0.1:1", connect_sec=0).__enter__()
        self.assertEqual(pw.stopped, 1)


class _Client:
    def __init__(self):
        self.closed = 0

    def start_profile(self, pid):
        return "ws://127.0.0.1:9/devtools"

    def close_profile(self, pid):
        self.closed += 1


class _Page:
    url = "about:blank"

    def goto(self, *a, **k):
        raise TimeoutError("Timeout 90000ms exceeded")


class _Ctx:
    pages: list = []

    def new_page(self):
        return _Page()


class _Browser:
    contexts = [_Ctx()]


class _Cdp:
    exits = 0

    def __init__(self, ep):
        self.browser = _Browser()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        type(self).exits += 1


class GPMSessionCleanupTests(unittest.TestCase):
    def test_muse_tab_failure_closes_cdp_and_profile(self):
        client = _Client()
        _Cdp.exits = 0
        with patch("backend.video.gpm_client.GPMClient", return_value=client), \
             patch.object(yd, "_CDPPlaywright", _Cdp):
            with self.assertRaises(TimeoutError):
                mv._GPMSession("pid-x").__enter__()
        self.assertEqual(_Cdp.exits, 1, "phải dừng Playwright khi __enter__ lỗi")
        self.assertEqual(client.closed, 1, "phải đóng profile GPM khi __enter__ lỗi")


class BrowserSessionCleanupTests(unittest.TestCase):
    def _session(self, tmp, ctx=None, launch_exc=None):
        pw = _FakePW()

        class _Chromium:
            executable_path = __file__

            def launch_persistent_context(self, **kw):
                if launch_exc:
                    raise launch_exc
                return ctx

        pw.chromium = _Chromium()
        return pw, bb.BrowserSession(headless=True, profile_dir=Path(tmp) / "p")

    def test_enter_failure_after_launch_stops_playwright(self):
        class _BadCtx:
            closed = 0

            def set_default_timeout(self, ms):
                raise RuntimeError("Target closed")

            def close(self):
                type(self).closed += 1

        with TemporaryDirectory() as tmp:
            pw, sess = self._session(tmp, ctx=_BadCtx())
            with patch.object(bb, "start_playwright", return_value=pw), \
                 patch.object(bb.config, "resolve_browser_exe", return_value=None):
                with self.assertRaises(RuntimeError):
                    sess.__enter__()
        self.assertEqual(pw.stopped, 1)
        self.assertEqual(_BadCtx.closed, 1)

    def test_exit_stops_playwright_even_if_context_close_fails(self):
        class _DeadCtx:
            def set_default_timeout(self, ms): pass
            def set_default_navigation_timeout(self, ms): pass
            def close(self): raise RuntimeError("Browser has been closed")

        with TemporaryDirectory() as tmp:
            pw, sess = self._session(tmp, ctx=_DeadCtx())
            with patch.object(bb, "start_playwright", return_value=pw), \
                 patch.object(bb.config, "resolve_browser_exe", return_value=None):
                with sess:
                    pass                       # cửa sổ bị đóng tay giữa chừng
                sess.__exit__(None, None, None)  # gọi lại (vd _SunoBrowser.close) vô hại
        self.assertEqual(pw.stopped, 1)


if __name__ == "__main__":
    unittest.main()
