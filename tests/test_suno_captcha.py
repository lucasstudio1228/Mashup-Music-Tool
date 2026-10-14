"""Create Suno bị chặn bằng captcha (2026-10-12): phát hiện captcha → chờ NGƯỜI tự
giải (không bấm gì vào captcha), báo sớm khi Suno không nhận lượt Create, trả lại
lượt cho ngân sách khi credit không đổi, lô dừng hẳn thay vì tự bấm Create lại.
Không mở trình duyệt, không tốn credit (trang/driver giả, SQLite trong RAM)."""
from pathlib import Path
import sys
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent))
from sqlmodel import Session, SQLModel, create_engine

from backend import batch_service as bsvc
from backend.models import Project, SunoBatch
from backend.video import suno_service as ss
from backend.video.suno_driver import (NewSong, SunoCaptchaRequired, SunoDriver,
                                       SunoSubmissionUncertain, _JS_GEN_STATE)

U = "00000000-0000-0000-0000-{:012d}"


class FakePage:
    """Mỗi wait_for_timeout = 1 «nhịp». timeline(tick) → (số bài mới, state)."""

    def __init__(self, timeline):
        self.timeline = timeline
        self.tick = 0
        self.front = 0
        self.clicks = 0                 # captcha KHÔNG bao giờ được bấm
        self.shots = []

    def _now(self):
        return self.timeline(self.tick)

    def eval_on_selector_all(self, sel, js):
        n, _ = self._now()
        return [f"/song/{U.format(i)}" for i in range(n)]

    def evaluate(self, js, *a):
        assert js == _JS_GEN_STATE
        return self._now()[1]

    def wait_for_timeout(self, ms):
        time.sleep(ms / 1000.0)
        self.tick += 1

    def bring_to_front(self):
        self.front += 1

    def screenshot(self, path=None):
        self.shots.append(path)

    def on(self, *a):
        pass

    def locator(self, *a):
        raise RuntimeError("không cần")


NONE = {"captcha": False, "kind": "", "toast": ""}
CAP = {"captcha": True, "kind": "hCaptcha challenge", "toast": ""}


def drv(timeline):
    d = SunoDriver(FakePage(timeline), dry_run=False, selectors={}, log=lambda m: None)
    d._song_meta = lambda sid: ("t", None)
    return d


class WaitTests(unittest.TestCase):
    def setUp(self):
        # Không phát tiếng bíp khi chạy test.
        p = patch.dict(sys.modules, {"winsound": None})
        p.start()
        self.addCleanup(p.stop)

    def _wait(self, d, **kw):
        args = dict(expected=2, timeout_sec=600, poll_sec=0.001, accept_sec=0.03,
                    captcha_wait_sec=600)
        args.update(kw)
        return d.wait_for_new_songs(set(), **args)

    def test_songs_appear_normally(self):
        d = drv(lambda t: (2 if t >= 2 else 0, NONE))
        self.assertEqual(len(self._wait(d, accept_sec=60)), 2)

    def test_captcha_waits_for_human_then_continues(self):
        msgs = []
        # captcha hiện tới nhịp 50 (người giải) → nhịp 55 có 2 bài.
        d = drv(lambda t: (2 if t >= 55 else 0, CAP if t < 50 else NONE))
        songs = self._wait(d, on_human=msgs.append)
        self.assertEqual(len(songs), 2)
        self.assertTrue(msgs and "CAPTCHA" in msgs[0] and "KHÔNG tự giải" in msgs[0])
        self.assertGreaterEqual(d.page.front, 1)
        self.assertEqual(d.page.clicks, 0)

    def test_captcha_never_solved_raises_captcha_required(self):
        d = drv(lambda t: (0, CAP))
        with self.assertRaises(SunoCaptchaRequired):
            self._wait(d, timeout_sec=0.01, captcha_wait_sec=0.05)

    def test_not_accepted_fails_early_without_captcha(self):
        d = drv(lambda t: (0, NONE))
        with self.assertRaises(SunoSubmissionUncertain) as cm:
            self._wait(d)               # timeout 600s nhưng accept 0.03s
        self.assertIn("KHÔNG được nhận", str(cm.exception))

    def test_generation_failed_toast_fails_immediately(self):
        d = drv(lambda t: (0, {**NONE, "toast": "Generation failed | Something went wrong"}))
        with self.assertRaises(SunoSubmissionUncertain) as cm:
            self._wait(d, accept_sec=60)
        self.assertIn("Generation failed", str(cm.exception))
        self.assertLessEqual(d.page.tick, 1)

    def test_pending_generate_request_is_not_failed_early(self):
        d = drv(lambda t: (2 if t >= 40 else 0, NONE))
        d._gen_events.append("generate_sent")
        self.assertEqual(len(self._wait(d)), 2)

    def test_http_error_on_generate_fails(self):
        d = drv(lambda t: (0, NONE))
        d._gen_events += ["generate_sent", "generate_http_403"]
        with self.assertRaises(SunoSubmissionUncertain) as cm:
            self._wait(d, accept_sec=60)
        self.assertIn("generate_http_403", str(cm.exception))

    def test_legacy_call_without_accept_keeps_old_behaviour(self):
        d = drv(lambda t: (0, CAP))
        with self.assertRaises(SunoSubmissionUncertain):
            d.wait_for_new_songs(set(), expected=2, timeout_sec=0.02, poll_sec=0.001)

    def test_turnstile_pending_in_console_waits_for_human(self):
        # Ca thật 2026-10-12 (project 35): ô Turnstile «Verify you are human» nằm
        # trong shadow DOM đóng → không đo được khung; console chỉ có
        # «captcha required». Trước đây báo «KHÔNG được nhận» sau accept_sec.
        msgs, box = [], {}

        def tl(t):
            d = box["d"]
            if t == 50 and "captcha_verified" not in d._gen_events:   # người giải
                d._gen_events += ["captcha_verified", "generate_sent", "generate_http_200"]
            return (2 if t >= 55 else 0, NONE)
        d = box["d"] = drv(tl)
        d._gen_events.append("captcha_required")
        songs = self._wait(d, on_human=msgs.append, captcha_grace_sec=0.005)
        self.assertEqual(len(songs), 2)
        self.assertTrue(msgs and "CAPTCHA" in msgs[0] and "Verify you are human" in msgs[0])
        self.assertGreaterEqual(d.page.front, 1)
        self.assertEqual(d.page.clicks, 0)

    def test_turnstile_pending_never_solved_raises_captcha_required(self):
        d = drv(lambda t: (0, NONE))
        d._gen_events.append("captcha_required")
        with self.assertRaises(SunoCaptchaRequired):
            self._wait(d, timeout_sec=0.01, captcha_wait_sec=0.08, captcha_grace_sec=0.005)

    def test_captcha_verified_quickly_is_not_pending(self):
        # Bình thường: «captcha required» → «captcha verified» → /generate → bài mới.
        d = drv(lambda t: (2 if t >= 40 else 0, NONE))
        d._gen_events += ["captcha_required", "captcha_verified", "generate_sent"]
        msgs = []
        self.assertEqual(len(self._wait(d, on_human=msgs.append, captcha_grace_sec=0)), 2)
        self.assertEqual(msgs, [])

    def test_pending_with_failed_toast_fails_now(self):
        d = drv(lambda t: (0, {**NONE, "toast": "Generation failed"}))
        d._gen_events.append("captcha_required")
        with self.assertRaises(SunoSubmissionUncertain):
            self._wait(d, accept_sec=60, captcha_grace_sec=0)
        self.assertLessEqual(d.page.tick, 1)

    def test_captcha_gone_but_no_generate_fails_after_accept(self):
        # Khung captcha tắt (nhịp 5) mà Suno không gửi /generate → báo sau accept_sec,
        # không chờ đủ captcha_wait_sec.
        d = drv(lambda t: (0, CAP if t < 5 else NONE))
        t0 = time.time()
        with self.assertRaises(SunoCaptchaRequired) as cm:
            self._wait(d, captcha_wait_sec=600, accept_sec=0.05)
        self.assertIn("CAPTCHA đã tắt", str(cm.exception))
        self.assertLess(time.time() - t0, 30)

    def test_gen_flags_pending_logic(self):
        d = drv(lambda t: (0, NONE))
        d._gen_events[:] = ["captcha_required"]
        self.assertTrue(d._gen_flags()["captcha_pending"])
        d._gen_events[:] = ["captcha_required", "captcha_verified_null"]
        self.assertFalse(d._gen_flags()["captcha_pending"])
        d._gen_events[:] = ["captcha_required", "captcha_verified", "captcha_required"]
        self.assertTrue(d._gen_flags()["captcha_pending"])
        d._gen_events[:] = ["captcha_required", "generate_sent"]
        self.assertFalse(d._gen_flags()["captcha_pending"])

    def test_console_never_stores_token(self):
        d = drv(lambda t: (0, NONE))

        class M:
            def __init__(self, text): self.text = text
        d._on_console(M("captcha required, awaiting verification"))
        d._on_console(M("captcha verified P1_eyJ0eXAiOiJKV1QiLCJhbGciOi.secret"))
        d._on_console(M("captcha verified null"))
        self.assertEqual(d._gen_events,
                         ["captcha_required", "captcha_verified", "captcha_verified_null"])
        self.assertNotIn("secret", d._gen_diag())


class RecheckDriver:
    """Driver giả cho _create_and_wait: lượt Create lỗi + tải lại trang."""

    def __init__(self, exc, credits_after, fresh_after=()):
        self.exc, self.credits_after, self.fresh_after = exc, credits_after, set(fresh_after)
        self.clicks, self.reloaded = 0, False
        self.page = type("P", (), {"wait_for_timeout": lambda s, ms: None})()

    def click_create(self): self.clicks += 1

    def wait_for_new_songs(self, before, **kw):
        self.kw = kw
        raise self.exc

    def open_create_page(self): self.reloaded = True
    def read_credits(self): return self.credits_after
    def snapshot_song_ids(self): return set(self.fresh_after)
    def _song_meta(self, sid): return ("t", None)


class CreateAndWaitTests(unittest.TestCase):
    def setUp(self):
        eng = create_engine("sqlite://")
        SQLModel.metadata.create_all(eng)
        self.s = Session(eng)
        self.addCleanup(self.s.close)
        p = Project(name="P")
        self.s.add(p); self.s.commit(); self.s.refresh(p)
        self.batch = SunoBatch(project_id=p.id, idempotency_key="k", create_actions_used=3)
        self.s.add(self.batch); self.s.commit(); self.s.refresh(self.batch)
        self.logs, self.reports = [], []

    def _run(self, d, credits_before=4420):
        return ss._create_and_wait(self.s, self.batch, d, set(), credits_before, 2,
                                   lambda m, *a: self.reports.append(m), self.logs.append)

    def test_unaccepted_create_is_returned_to_budget(self):
        d = RecheckDriver(SunoSubmissionUncertain("x"), credits_after=4420)
        with self.assertRaises(SunoSubmissionUncertain):
            self._run(d)
        self.assertTrue(d.reloaded)
        self.assertEqual(d.clicks, 1)               # KHÔNG bấm Create lại
        self.assertEqual(self.batch.create_actions_used, 2)
        self.assertIn("accept_sec", d.kw)
        self.assertIn("on_human", d.kw)

    def test_captcha_timeout_also_returns_budget_and_reraises(self):
        d = RecheckDriver(SunoCaptchaRequired("cap"), credits_after=4420)
        with self.assertRaises(SunoCaptchaRequired):
            self._run(d)
        self.assertEqual(self.batch.create_actions_used, 2)

    def test_credits_spent_keeps_count(self):
        d = RecheckDriver(SunoSubmissionUncertain("x"), credits_after=4400)
        with self.assertRaises(SunoSubmissionUncertain):
            self._run(d)
        self.assertEqual(self.batch.create_actions_used, 3)

    def test_songs_found_after_reload_are_used(self):
        d = RecheckDriver(SunoSubmissionUncertain("x"), credits_after=4400,
                          fresh_after=[U.format(1), U.format(2)])
        songs = self._run(d)
        self.assertEqual(len(songs), 2)
        self.assertIsInstance(songs[0], NewSong)
        self.assertEqual(self.batch.create_actions_used, 3)

    def test_on_human_updates_batch_message(self):
        d = RecheckDriver(None, 0)

        def wait(before, **kw):
            kw["on_human"]("⚠️ CẦN BẠN: giải CAPTCHA")
            return [NewSong(song_id="a"), NewSong(song_id="b")]
        d.wait_for_new_songs = wait
        self.assertEqual(len(self._run(d)), 2)
        self.assertIn("CAPTCHA", self.batch.message)
        self.assertIn("⚠️ CẦN BẠN: giải CAPTCHA", self.reports)


class PacingTests(unittest.TestCase):
    """Giãn nhịp Create + lô TẠM DỪNG chờ người tích captcha (không tự bấm)."""

    def setUp(self):
        p = patch.object(ss, "_LAST_CREATE_MONO", 0.0)
        p.start()
        self.addCleanup(p.stop)

    def test_defaults_wait_long_and_pace_creates(self):
        self.assertGreaterEqual(ss.vconfig.SUNO.captcha_wait_sec, 3600)
        self.assertGreaterEqual(ss.vconfig.SUNO.create_min_gap_sec, 30)

    def test_pace_waits_remaining_gap_after_real_create(self):
        waited = []
        with patch.object(ss, "_countdown", lambda sec, *a, **k: waited.append(sec)):
            ss._pace_create()                       # chưa từng Create → không chờ
            self.assertEqual(waited, [])
            ss._LAST_CREATE_MONO = time.monotonic() - 10
            ss._pace_create()
        self.assertEqual(len(waited), 1)
        self.assertAlmostEqual(waited[0], ss.vconfig.SUNO.create_min_gap_sec - 10, delta=1)

    def test_no_wait_once_gap_elapsed(self):
        waited = []
        ss._LAST_CREATE_MONO = time.monotonic() - ss.vconfig.SUNO.create_min_gap_sec - 1
        with patch.object(ss, "_countdown", lambda sec, *a, **k: waited.append(sec)):
            ss._pace_create()
        self.assertEqual(waited, [])

    def test_fake_driver_does_not_set_pace_clock(self):
        d = RecheckDriver(None, 0)
        d.wait_for_new_songs = lambda before, **kw: [NewSong(song_id="a")]
        eng = create_engine("sqlite://")
        SQLModel.metadata.create_all(eng)
        with Session(eng) as s:
            b = SunoBatch(project_id=1, idempotency_key="k")
            s.add(b); s.commit(); s.refresh(b)
            ss._create_and_wait(s, b, d, set(), 100, 1, lambda *a: None, lambda m: None)
        self.assertEqual(ss._LAST_CREATE_MONO, 0.0)

    def test_real_driver_click_sets_pace_clock(self):
        d = drv(lambda t: (2, NONE))
        d.click_create = lambda: None
        eng = create_engine("sqlite://")
        SQLModel.metadata.create_all(eng)
        with Session(eng) as s:
            b = SunoBatch(project_id=1, idempotency_key="k")
            s.add(b); s.commit(); s.refresh(b)
            ss._create_and_wait(s, b, d, set(), 100, 2, lambda *a: None, lambda m: None)
        self.assertGreater(ss._LAST_CREATE_MONO, 0)

    def test_reminder_beeps_while_waiting(self):
        beeps = []
        d = drv(lambda t: (2 if t >= 40 else 0, CAP))
        d._wait = lambda ms: d.page.wait_for_timeout(0)     # wait_ms thật dùng đồng hồ
        orig = d._alert_human
        d._alert_human = lambda msg, cb, beep=False: (beeps.append(beep), orig(msg, cb, False))
        with patch.dict(sys.modules, {"winsound": None}), \
             patch("backend.video.suno_driver.time.time",
                   side_effect=(lambda c=iter(range(0, 10**6, 5)): float(next(c)))):
            songs = d.wait_for_new_songs(set(), expected=2, timeout_sec=600, poll_sec=0,
                                         accept_sec=90, captcha_wait_sec=3600,
                                         captcha_beep_sec=180)
        self.assertEqual(len(songs), 2)
        self.assertTrue(beeps[0])                   # lần đầu luôn bíp
        self.assertIn(True, beeps[1:])              # nhắc lại có bíp định kỳ
        self.assertIn(False, beeps[1:])             # nhưng không bíp mỗi phút
        self.assertEqual(d.page.clicks, 0)          # KHÔNG bấm vào captcha


class BatchStopTests(unittest.TestCase):
    def test_submission_uncertain_and_captcha_stop_whole_batch(self):
        self.assertTrue(bsvc._needs_human(ss.SUBMISSION_UNCERTAIN, "x"))
        self.assertTrue(bsvc._needs_human(
            ss.WAITING_FOR_HUMAN, "Suno ở phase WAITING_FOR_HUMAN — CAPTCHA: chưa giải"))
        self.assertTrue(bsvc._needs_human(ss.WAITING_FOR_LOGIN, ""))

    def test_other_human_waits_still_auto_retry(self):
        # «Mới có x/15 WAV hợp lệ» — tải lại miễn phí, cho tự thử lại.
        self.assertFalse(bsvc._needs_human(
            ss.WAITING_FOR_HUMAN, "Mới có 13/15 WAV hợp lệ — bấm Resume"))
        self.assertFalse(bsvc._needs_human(ss.UI_CHANGED, "x"))


if __name__ == "__main__":
    unittest.main()
