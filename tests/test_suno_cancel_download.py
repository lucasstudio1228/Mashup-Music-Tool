"""Huỷ Suno phải có hiệu lực NGAY trong các vòng chờ dài; resume không được
chạm trần tải vì bản probe/dự phòng; bài chỉ lỗi tải (menu chập chờn) ở lượt
chạy trước được thử lại 1 lần."""
import threading
import time
import unittest

from backend.video import browser_base as bb
from backend.video import suno_service as svc
from backend.video.job_manager import JobCancelled, VideoJobManager


class _Page:
    def wait_for_timeout(self, ms):
        time.sleep(ms / 1000.0)


class CancelHelpersTest(unittest.TestCase):
    def tearDown(self):
        bb.set_cancel_check(None)

    def test_sleep_and_wait_stop_fast_when_cancelled(self):
        ev = threading.Event()
        bb.set_cancel_check(ev.is_set)
        threading.Timer(0.3, ev.set).start()
        t = time.time()
        with self.assertRaises(bb.OperationCancelled):
            bb.cancellable_sleep(60)
        self.assertLess(time.time() - t, 1.5)
        ev.clear()
        threading.Timer(0.3, ev.set).start()
        t = time.time()
        with self.assertRaises(bb.OperationCancelled):
            bb.wait_ms(_Page(), 60_000)
        self.assertLess(time.time() - t, 1.5)

    def test_cancel_not_swallowed_by_except_exception(self):
        bb.set_cancel_check(lambda: True)
        with self.assertRaises(bb.OperationCancelled):
            try:
                bb.check_cancel()
            except Exception:       # noqa: BLE001 — mô phỏng code thử-lại của driver
                self.fail("OperationCancelled bị except Exception nuốt")

    def test_no_check_registered_is_noop(self):
        bb.set_cancel_check(None)
        bb.cancellable_sleep(0.01)
        bb.wait_ms(_Page(), 10)

    def test_check_is_per_thread(self):
        bb.set_cancel_check(lambda: True)
        out = []

        def other():
            try:
                bb.check_cancel()
                out.append("ok")
            except bb.OperationCancelled:
                out.append("cancelled")
        th = threading.Thread(target=other); th.start(); th.join()
        self.assertEqual(out, ["ok"])


class JobManagerCancelFlagTest(unittest.TestCase):
    def test_progress_cb_exposes_is_cancelled_and_job_stops_fast(self):
        jm = VideoJobManager()
        started = threading.Event()

        def job(pid, cb):
            bb.set_cancel_check(cb.is_cancelled)
            try:
                started.set()
                bb.cancellable_sleep(60)     # không gọi cb → trước đây kẹt 60s
            except bb.OperationCancelled:
                raise JobCancelled() from None
            finally:
                bb.set_cancel_check(None)
        jm.submit(1, "suno", job)
        self.assertTrue(started.wait(3))
        t = time.time()
        self.assertTrue(jm.cancel(1))
        while jm.get(1).status == "running" and time.time() - t < 5:
            time.sleep(0.05)
        self.assertEqual(jm.get(1).status, "cancelled")
        self.assertLess(time.time() - t, 2)


class _C:
    def __init__(self, song_id, download_status="pending", validation_status="not_verified"):
        self.song_id = song_id
        self.download_status = download_status
        self.validation_status = validation_status


class SkipSpareTest(unittest.TestCase):
    def test_rules(self):
        self.assertTrue(svc._skip_spare(None, set()))
        self.assertTrue(svc._skip_spare(_C("a", "discarded"), set()))
        self.assertTrue(svc._skip_spare(_C("a", "downloaded", "invalid"), set()))
        self.assertFalse(svc._skip_spare(_C("a", "pending"), set()))
        # lỗi tải ở lượt TRƯỚC → thử lại; đã thử trong lượt NÀY → bỏ
        self.assertFalse(svc._skip_spare(_C("a", "failed"), set()))
        self.assertTrue(svc._skip_spare(_C("a", "failed"), {"a"}))
        # gọi không có `tried` (Create thay thế) → giữ hành vi cũ: bỏ bài lỗi
        self.assertTrue(svc._skip_spare(_C("a", "failed"), None))


if __name__ == "__main__":
    unittest.main()
