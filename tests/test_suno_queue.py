"""Worker video/Suno bận (vd project khác đang đăng nháp YouTube) → Start Suno
phải XẾP HÀNG rồi tự chạy, hoặc báo 409 nói rõ ai đang bận — không im lặng."""
import threading
import time
import unittest
from unittest import mock

from fastapi import HTTPException

from backend.routers import suno as suno_router
from backend.video.job_manager import VideoJobManager


class _Db:
    def get(self, _model, pid):
        return type("P", (), {"name": f"Proj {pid}"})()


class SunoQueueTest(unittest.TestCase):
    def setUp(self):
        self.jm = VideoJobManager()
        self.release = threading.Event()
        self.ran = []
        p = mock.patch.object(suno_router, "video_job_manager", self.jm)
        p.start()
        self.addCleanup(p.stop)
        self.addCleanup(self.release.set)

    def _blocker(self, pid, cb):
        cb("uploading", 73)
        self.release.wait(5)

    def _suno(self, pid, cb, **kw):
        self.ran.append((pid, kw))

    def _wait(self, cond, t=5):
        end = time.time() + t
        while time.time() < end and not cond():
            time.sleep(0.02)
        return cond()

    def test_idle_returns_empty(self):
        self.assertEqual(suno_router._guard_busy(_Db(), 9, allow_queue=True), "")

    def test_queues_behind_other_project_then_runs(self):
        self.jm.submit(8, "upload", self._blocker)
        self.assertTrue(self._wait(lambda: self.jm.get(8).status == "running"))
        msg = suno_router._guard_busy(_Db(), 9, allow_queue=True)
        self.assertIn("xếp hàng", msg)
        self.assertIn("#8", msg)
        self.assertIn("đăng nháp YouTube", msg)
        self.jm.submit(9, "suno", self._suno, dry_run=False, queued_message=msg)
        self.assertEqual(self.jm.get(9).status, "pending")
        self.assertEqual(self.jm.get(9).message, msg)
        self.assertEqual(self.ran, [])
        # Đã có người xếp hàng → lệnh thứ 3 bị 409 kèm mô tả
        with self.assertRaises(HTTPException) as cm:
            suno_router._guard_busy(_Db(), 10, allow_queue=True)
        self.assertEqual(cm.exception.status_code, 409)
        self.assertIn("#8", cm.exception.detail)
        self.release.set()
        self.assertTrue(self._wait(lambda: self.jm.get(9).status == "completed"))
        self.assertEqual(self.ran, [(9, {"dry_run": False})])

    def test_same_project_or_no_queue_is_409_with_reason(self):
        self.jm.submit(8, "upload", self._blocker)
        self.assertTrue(self._wait(lambda: self.jm.get(8).status == "running"))
        for pid, allow in ((8, True), (9, False)):
            with self.assertRaises(HTTPException) as cm:
                suno_router._guard_busy(_Db(), pid, allow_queue=allow)
            self.assertIn("Proj 8", cm.exception.detail)

    def test_cancel_queued_job_never_runs(self):
        self.jm.submit(8, "upload", self._blocker)
        self.assertTrue(self._wait(lambda: self.jm.get(8).status == "running"))

        def suno_with_cb(pid, cb):
            cb("start", 1)
            self.ran.append(pid)
        self.jm.submit(9, "suno", suno_with_cb, queued_message="q")
        self.assertTrue(self.jm.cancel(9))
        self.release.set()
        self.assertTrue(self._wait(lambda: self.jm.get(9).status == "cancelled"))
        self.assertEqual(self.ran, [])


if __name__ == "__main__":
    unittest.main()
