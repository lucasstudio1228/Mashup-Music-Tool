"""Vòng Create Suno: giãn lượt đang render + bù lượt Create bị Suno bỏ qua.
Không mở trình duyệt, không tốn credit (driver giả, SQLite trong RAM)."""
from datetime import timedelta
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent))
from sqlmodel import Session, SQLModel, create_engine, select

from backend.models import Project, SunoBatch, SunoCandidate
from backend.video import suno_service as ss
from backend.video.suno_driver import NewSong


class FakeDriver:
    serial = 0                         # song_id duy nhất xuyên các driver

    def __init__(self, lose=()):
        self.lose = set(lose)          # số thứ tự lượt bấm Create bị Suno bỏ qua
        self.clicks = 0
        self.styles = []
        self.n = 0

    def read_credits(self):
        return 1000

    def snapshot_song_ids(self):
        return set()

    def fill_styles(self, s):
        self.styles.append(s)

    def fill_exclusions(self, e):
        pass

    def click_create(self):
        self.clicks += 1

    def wait_for_new_songs(self, before, expected=2, timeout_sec=300):
        if self.clicks in self.lose:
            return []
        out = []
        for _ in range(2):
            FakeDriver.serial += 1
            out.append(NewSong(song_id=f"song-{FakeDriver.serial}", duration_seconds=200))
        return out


class GenerateTests(unittest.TestCase):
    def setUp(self):
        eng = create_engine("sqlite://")
        SQLModel.metadata.create_all(eng)
        self.s = Session(eng)
        self.addCleanup(self.s.close)
        p = Project(name="P")
        self.s.add(p)
        self.s.commit()
        self.s.refresh(p)
        self.batch = SunoBatch(project_id=p.id, idempotency_key="k")
        self.s.add(self.batch)
        self.s.commit()
        self.s.refresh(self.batch)
        self.cfg = {"max_create_actions": 5, "max_replacement_creates": 2,
                    "prompt_plan": [{"styles": f"style {i}", "exclusions": ""}
                                    for i in range(5)]}
        # Không chờ thật khi throttle.
        t = patch.object(ss, "_throttle_inflight", lambda *a, **k: None)
        t.start()
        self.addCleanup(t.stop)
        self.logs = []

    def run_gen(self, drv, target=5):
        ss._generate_until_target(self.s, self.batch, self.cfg, drv, target,
                                  lambda *a, **k: None, self.logs.append)

    def reqs(self):
        return sorted({c.request_id for c in self.s.exec(select(SunoCandidate)).all()})

    def test_lost_create_is_compensated_with_same_prompt(self):
        drv = FakeDriver(lose={3})
        self.run_gen(drv)
        self.assertEqual(self.batch.create_actions_used, 6)
        self.assertIn("req6:p3", self.reqs())
        self.assertNotIn("req3", self.reqs())
        self.assertEqual(drv.styles[-1], "style 2")     # đúng prompt của lượt mất
        self.assertEqual(ss._count(self.s, self.batch, selected=True), 5)

    def test_resume_after_budget_hit_compensates(self):
        # Tình huống lô thật: đã dùng hết 5 lượt, lượt 3 không có bài.
        first = FakeDriver(lose={3})
        self.cfg["max_replacement_creates"] = 0
        with self.assertRaises(ss.SunoBudgetError):
            self.run_gen(first)
        self.assertEqual(self.batch.create_actions_used, 5)
        self.cfg["max_replacement_creates"] = 2
        drv = FakeDriver()
        self.run_gen(drv)
        self.assertEqual(drv.clicks, 1)
        self.assertIn("req6:p3", self.reqs())

    def test_compensation_is_capped(self):
        drv = FakeDriver(lose={3, 6, 7})
        with self.assertRaises(ss.SunoBudgetError):
            self.run_gen(drv)
        self.assertEqual(self.batch.create_actions_used, 7)   # 5 + tối đa 2 lượt bù

    def test_lost_requests_skips_compensated(self):
        for rid, sid in (("req1", "a"), ("req3:p2", "b")):
            self.s.add(SunoCandidate(batch_id=self.batch.id,
                                     project_id=self.batch.project_id,
                                     song_id=sid, request_id=rid))
        self.s.commit()
        self.batch.create_actions_used = 3
        self.assertEqual(ss._lost_requests(self.s, self.batch, 2), [])
        self.batch.create_actions_used = 3
        self.assertEqual(ss._lost_requests(self.s, self.batch, 3), ["req3"])


class ThrottleTests(unittest.TestCase):
    def test_waits_until_window_frees(self):
        eng = create_engine("sqlite://")
        SQLModel.metadata.create_all(eng)
        s = Session(eng)
        batch = SunoBatch(id=1, project_id=1, idempotency_key="k")
        now = ss._now()
        times = [now - timedelta(seconds=5 * i) for i in range(ss._MAX_INFLIGHT_CREATES)]
        clock = {"t": now}
        slept = []

        def fake_sleep(sec):
            slept.append(sec)
            clock["t"] += timedelta(seconds=sec)

        with patch.object(ss, "_now", lambda: clock["t"]), \
             patch.object(ss.time, "sleep", fake_sleep):
            ss._throttle_inflight(s, batch, times, lambda *a, **k: None, lambda m: None)
        self.assertTrue(slept)
        oldest = min(times)
        self.assertGreaterEqual((clock["t"] - oldest).total_seconds(),
                                ss._INFLIGHT_WINDOW_SEC)
        s.close()

    def test_no_wait_when_few_inflight(self):
        s = Session(create_engine("sqlite://"))
        SQLModel.metadata.create_all(s.get_bind())
        batch = SunoBatch(id=1, project_id=1, idempotency_key="k")
        with patch.object(ss.time, "sleep", side_effect=AssertionError("slept")):
            ss._throttle_inflight(s, batch, [ss._now()] * 3, lambda *a, **k: None,
                                  lambda m: None)
        s.close()


if __name__ == "__main__":
    unittest.main()
