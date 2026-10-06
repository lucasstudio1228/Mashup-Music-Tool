"""Chống tạo lô trùng: request tạo lô có call AI > 30s (trình duyệt từng báo
timeout trong khi server vẫn tạo lô) → bấm lại không được sinh lô thứ 2."""
from pathlib import Path
import sys
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend import batch_service as bs


class BatchCreateGuardTests(unittest.TestCase):
    def setUp(self):
        self._threads = dict(bs._threads)

    def tearDown(self):
        bs._threads.clear()
        bs._threads.update(self._threads)

    def test_refuses_when_a_batch_thread_is_alive(self):
        stop = threading.Event()
        t = threading.Thread(target=stop.wait, daemon=True)
        t.start()
        bs._threads["k_alive"] = t
        try:
            self.assertEqual(bs._active_batch_key(), "k_alive")
            with patch("backend.video.batch_ideas.generate_batch_ideas") as gen:
                with self.assertRaisesRegex(RuntimeError, "Đang có 1 lô"):
                    bs.create_batch(3, "bamboo flute", "Zen", "2d")
                gen.assert_not_called()          # không tốn call AI
        finally:
            stop.set()
            t.join(2)
        self.assertIsNone(bs._active_batch_key())

    def test_refuses_while_another_create_is_in_progress(self):
        self.assertTrue(bs._create_lock.acquire(blocking=False))
        try:
            with patch("backend.video.batch_ideas.generate_batch_ideas") as gen:
                with self.assertRaisesRegex(RuntimeError, "Đang tạo 1 lô khác"):
                    bs.create_batch(3, "bamboo flute", "Zen", "2d")
                gen.assert_not_called()
        finally:
            bs._create_lock.release()

    def test_lock_released_when_ai_fails(self):
        with patch("backend.core_bridge.get_api_config_from_db", return_value={}), \
             patch("backend.video.batch_ideas.generate_batch_ideas",
                   side_effect=RuntimeError("AI thiếu ý tưởng")):
            with self.assertRaisesRegex(RuntimeError, "AI thiếu"):
                bs.create_batch(3, "bamboo flute", "Zen", "2d")
        self.assertTrue(bs._create_lock.acquire(blocking=False))
        bs._create_lock.release()


if __name__ == "__main__":
    unittest.main()
