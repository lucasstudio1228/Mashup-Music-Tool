"""Hạn mức TẢI của Suno (tải thường: Premier 60 bài/tháng từ 03/09/2026; tải WAV qua
Studio của Premier không giới hạn → production không bị chặn) + Max Mode luôn bật.
Không mở trình duyệt, không tốn credit/lượt tải (driver + page giả, SQLite tạm)."""
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent))
from sqlmodel import Session, SQLModel, create_engine, select

from backend.models import Project, SunoBatch, SunoCandidate
from backend.video import suno_service as ss
from backend.video.suno_driver import SunoDownloadLimit, SunoDriver, SunoUIChanged


class _DlDriver:
    """Tải 'thành công' (file giả) — hoặc hiện hộp thoại Out of Downloads."""

    def __init__(self, limit_on_call=None):
        self.calls, self.limit_on_call = 0, limit_on_call

    def download_wav(self, song_id, dest, timeout_sec=240):
        self.calls += 1
        if self.limit_on_call == self.calls:
            raise SunoDownloadLimit("Suno: đã hết lượt tải của gói, làm mới 10/24/26.")
        Path(dest).write_bytes(b"RIFF")
        return str(dest)

    def close_extra_tabs(self):
        pass


def _fake_verify(dur):
    def verify(path, minimum_duration_seconds=120, known_hashes=None):
        return SimpleNamespace(valid=True, reason="", sha256=f"h-{Path(path).name}",
                               duration_seconds=dur, sample_rate=48000, channels=2)
    return verify


class DownloadLimitTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory(prefix="suno-dl-limit-")
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.engine = create_engine(f"sqlite:///{self.dir / 'db.sqlite'}")
        SQLModel.metadata.create_all(self.engine)
        self.addCleanup(self.engine.dispose)
        self.s = Session(self.engine)
        self.addCleanup(self.s.close)
        p = Project(name="P")
        self.s.add(p); self.s.commit(); self.s.refresh(p)
        self.batch = SunoBatch(project_id=p.id, idempotency_key="k", phase="GENERATING",
                               target_tracks=2, staging_dir=str(self.dir))
        self.s.add(self.batch); self.s.commit(); self.s.refresh(self.batch)
        for i in (1, 2):
            for spare in (False, True):
                self.s.add(SunoCandidate(
                    batch_id=self.batch.id, project_id=p.id,
                    song_id=f"{'s' if spare else 'm'}{i}000000-0000",
                    request_id=f"req{i}", selected=not spare, is_spare=spare))
        self.s.commit()
        self.cfg = {"download_min_song_age_sec": 0, "download_gap_sec": (0, 0),
                    "download_retry_wait_sec": 0, "download_passes": 1,
                    "minimum_duration_seconds": 120}
        self.logs = []
        for target, value in (
                ("backend.video.suno_service._make_instrument_qc", lambda *a, **k: None),
                ("backend.video.suno_service._wait_newest_song_age", lambda *a, **k: None),
                ("backend.video.suno_service.suno_verify.verify_wav", _fake_verify(200))):
            pt = patch(target, value)
            pt.start()
            self.addCleanup(pt.stop)

    def _run(self, drv, allowance=None):
        ss._download_and_validate(self.s, self.batch, self.cfg, drv, 2, self.dir,
                                  lambda *a, **k: None, self.logs.append,
                                  allowance=allowance)

    def _cands(self, selected=True):
        return self.s.exec(select(SunoCandidate).where(
            SunoCandidate.selected == selected).order_by(SunoCandidate.song_id)).all()

    def test_out_of_downloads_dialog_stops_batch_and_keeps_song_pending(self):
        drv = _DlDriver(limit_on_call=1)
        with self.assertRaises(SunoDownloadLimit):
            self._run(drv)
        self.assertEqual(drv.calls, 1)                 # không thử tiếp bài khác
        statuses = [c.download_status for c in self._cands()]
        self.assertNotIn("failed", statuses)           # lỗi tài khoản ≠ lỗi bài
        self.assertIn("pending", statuses)

    def test_known_allowance_stops_before_opening_studio(self):
        drv = _DlDriver()
        with self.assertRaises(SunoDownloadLimit):
            self._run(drv, allowance=1)
        self.assertEqual(drv.calls, 1)                 # bài 2: dừng TRƯỚC khi tải
        self.assertEqual(sorted(c.validation_status for c in self._cands()),
                         ["not_verified", "valid"])

    def test_probe_never_eats_allowance_nor_stops_finished_batch(self):
        # Bài 200s nằm ngoài 3–5' giả lập (250–300s) → muốn tải bản so sánh.
        self.cfg.update(preferred_duration_seconds_min=250,
                        preferred_duration_seconds_max=300)
        drv = _DlDriver()
        self._run(drv, allowance=2)                    # đủ 2 bài chính, hết lượt
        self.assertEqual(drv.calls, 2)
        self.assertTrue(all(c.validation_status == "valid" for c in self._cands()))
        self.assertTrue(any("Bỏ tải bản so sánh" in m for m in self.logs))

    def test_probe_uses_only_spare_allowance(self):
        self.cfg.update(preferred_duration_seconds_min=250,
                        preferred_duration_seconds_max=300)
        drv = _DlDriver()
        self._run(drv, allowance=3)                    # 2 bài chính + đúng 1 bản so sánh
        self.assertEqual(drv.calls, 3)

    def test_replacement_create_blocked_without_allowance(self):
        for c in self.s.exec(select(SunoCandidate).where(
                SunoCandidate.request_id == "req2")).all():
            c.download_status, c.validation_status = "downloaded", "invalid"
            self.s.add(c)
        self.s.commit()
        state = {"allowance": 2, "used": 2, "driver": None}
        with self.assertRaises(SunoDownloadLimit):
            ss._replace_dead_creates(self.s, self.batch,
                                     {"max_replacement_creates": 2, "max_create_actions": 2,
                                      "prompt_plan": [{"styles": "a", "exclusions": ""}] * 2},
                                     state, None, lambda *a, **k: None, self.logs.append)
        # Lượt hỏng KHÔNG bị loại → resume sau khi có lượt tải vẫn Create thay thế.
        self.assertFalse(any(c.download_status == "discarded"
                             for c in self.s.exec(select(SunoCandidate)).all()))

    def test_studio_production_is_not_gated_by_account_allowance(self):
        # Production tải WAV qua Studio (Premier: không giới hạn) → mặc định KHÔNG
        # đọc «Downloads Remaining», KHÔNG chặn Create dù /account báo 0.
        seen = []

        class _Stop(Exception):
            pass

        class _Drv:
            def open_create_page(self): pass
            def read_credits(self): return 6300
            def read_download_allowance(self):
                seen.append(1)
                return 0

        class _Browser:
            def __init__(self, *a, **k): pass
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def open(self): return _Drv()

        def _form(*a, **k):
            raise _Stop()                              # tới bước điền form = đã qua preflight

        with patch.object(ss, "_SunoBrowser", _Browser),                 patch.object(ss, "_configure_create_form", _form):
            with self.assertRaises(_Stop):
                ss._drive_batch(self.s, self.batch, {"target_tracks": 15}, False,
                                lambda *a, **k: None, self.logs.append)
        self.assertEqual(seen, [])

    def test_opt_in_preflight_stops_before_any_create(self):
        clicks = []

        class _Drv:
            def open_create_page(self): pass
            def read_credits(self): return 6300
            def read_download_allowance(self): return 3
            def click_create(self): clicks.append(1)

        class _Browser:
            def __init__(self, *a, **k): pass
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def open(self): return _Drv()

        with patch.object(ss, "_SunoBrowser", _Browser):
            with self.assertRaises(SunoDownloadLimit) as cm:
                ss._drive_batch(self.s, self.batch,
                                {"target_tracks": 15, "check_download_allowance": True},
                                False, lambda *a, **k: None, self.logs.append)
        self.assertIn("còn 3 lượt tải", str(cm.exception))
        self.assertEqual(clicks, [])

    def test_dry_run_does_not_read_allowance(self):
        seen = []

        class _Drv:
            def open_create_page(self): pass
            def read_credits(self): return 6300
            def read_download_allowance(self):
                seen.append(1)
                return 0

        class _Browser:
            def __init__(self, *a, **k): pass
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def open(self): return _Drv()

        with patch.object(ss, "_SunoBrowser", _Browser), \
                patch.object(ss, "_configure_create_form", lambda *a, **k: None):
            ss._drive_batch(self.s, self.batch, {"target_tracks": 15, "styles": "x"},
                            True, lambda *a, **k: None, self.logs.append)
        self.assertEqual(seen, [])


class _Keyboard:
    def __init__(self):
        self.pressed = []

    def press(self, key):
        self.pressed.append(key)


class _Page:
    def __init__(self, results):
        self.results = list(results)
        self.keyboard = _Keyboard()
        self.url = "https://suno.com/account"

    def goto(self, *a, **k):
        pass

    def evaluate(self, js, *args):
        return self.results.pop(0) if self.results else None

    def wait_for_timeout(self, ms):
        pass


class DriverLimitTests(unittest.TestCase):
    def _drv(self, *results):
        return SunoDriver(_Page(results), dry_run=False, selectors={}, log=lambda m: None)

    def test_out_of_downloads_dialog_is_closed_with_escape_never_buy(self):
        d = self._drv({"refresh": "10/24/26"})
        with self.assertRaises(SunoDownloadLimit) as cm:
            d._raise_if_out_of_downloads()
        self.assertIn("10/24/26", str(cm.exception))
        self.assertEqual(d.page.keyboard.pressed, ["Escape"])

    def test_normal_download_dialog_passes(self):
        d = self._drv(None)
        d._raise_if_out_of_downloads()
        self.assertEqual(d.page.keyboard.pressed, [])

    def test_read_download_allowance(self):
        with patch("backend.video.suno_driver.wait_ms", lambda *a: None):
            self.assertEqual(self._drv({"downloads remaining": "0",
                                        "current plan": "Premier Plan"})
                             .read_download_allowance(), 0)
            self.assertEqual(self._drv({"downloads remaining": "1,234"})
                             .read_download_allowance(), 1234)
            self.assertIsNone(self._drv(*([{}] * 10)).read_download_allowance())

    def test_max_mode_clicks_only_inside_its_row_and_verifies(self):
        clicked = []
        d = self._drv({"on": False, "off": True}, {"on": True, "off": False})
        d._real_click = lambda loc: clicked.append(loc) or True
        d.page.locator = lambda sel: SimpleNamespace(first=f"loc:{sel}")
        d.set_max_mode(True)
        self.assertEqual(clicked, ["loc:[data-tool-toggle='1']"])

    def test_max_mode_already_on_is_not_clicked(self):
        d = self._drv({"on": True, "off": False})
        d._real_click = lambda loc: self.fail("không được bấm khi đã On")
        d.set_max_mode(True)

    def test_max_mode_that_does_not_stick_raises(self):
        d = self._drv(*([{"on": False, "off": True}] * 6))
        d._real_click = lambda loc: True
        d.page.locator = lambda sel: SimpleNamespace(first="loc")
        with patch("backend.video.suno_driver.wait_ms", lambda *a: None):
            with self.assertRaises(SunoUIChanged):
                d.set_max_mode(True)

    def test_no_mp3_or_normal_download_path(self):
        # Tải thường/MP3 bị trừ lượt Suno → chỉ được tải WAV qua Studio.
        self.assertFalse(hasattr(SunoDriver, "download_mp3"))
        root = Path(__file__).parent.parent / "backend"
        offenders = [str(f) for f in root.rglob("*.py")
                     if "download_mp3" in f.read_text(encoding="utf-8")]
        self.assertEqual(offenders, [])

    def test_max_mode_always_on_in_config(self):
        self.assertTrue(ss.vconfig.SUNO.max_mode)
        self.assertTrue(ss.resolve_batch_config({"max_mode": False})["max_mode"])


if __name__ == "__main__":
    unittest.main()
