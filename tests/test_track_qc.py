"""Rà soát track lẻ + tạo lại Suno cho bài lỗi + vá mix theo thứ tự cũ.
Audio tổng hợp, SQLite trong RAM, driver Suno giả → không mở trình duyệt,
không tốn credit."""
from pathlib import Path
import json
import sys
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import soundfile as sf

sys.path.insert(0, str(Path(__file__).parent.parent))
from sqlmodel import Session, SQLModel, create_engine, select

import backend.database as bdb
from backend.models import Project, SunoBatch, SunoCandidate, Track
from backend.video import config as vconfig, track_qc, suno_fix
from backend.video import suno_service as ss
from backend.video.suno_driver import NewSong, SunoUIChanged
from backend import mix_patch

SR = 16000


def tone(sec=125.0, seed=0, amp=0.25, fade_out=5.0):
    """Bài 'sạch': vài nốt trầm, fade-out cuối (như bài Suno bình thường)."""
    rng = np.random.default_rng(seed)
    t = np.arange(int(sec * SR)) / SR
    f = 110 * (1 + rng.integers(1, 4))
    x = amp * (np.sin(2 * np.pi * f * t) + 0.5 * np.sin(2 * np.pi * 2 * f * t)) / 1.5
    x *= 0.8 + 0.2 * np.sin(2 * np.pi * 0.05 * t)
    n = int(fade_out * SR)
    x[-n:] *= np.linspace(1, 0, n)
    return x.astype(np.float32)


def write(path, x):
    sf.write(str(path), np.stack([x, x], axis=1), SR, subtype="PCM_16")
    return str(path)


class AnalyzeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        d = Path(cls.tmp.name)
        files = [write(d / f"clean{i}.wav", tone(seed=i)) for i in range(5)]
        noisy = tone(seed=9)
        noisy += 0.08 * np.random.default_rng(1).standard_normal(len(noisy)).astype(np.float32)
        files.append(write(d / "noisy.wav", noisy))
        clip = tone(seed=10)
        clip[30 * SR:40 * SR] *= 6
        files.append(write(d / "clip.wav", clip))
        drop = tone(seed=11)
        drop[60 * SR:65 * SR] = 0
        files.append(write(d / "drop.wav", drop))
        files.append(write(d / "abrupt.wav", tone(seed=12, fade_out=0.001)))
        files.append(write(d / "short.wav", tone(sec=90, seed=13)))
        cls.files = files
        with patch.object(vconfig, "MEDIA_ROOT", d / "media"):
            cls.rep = track_qc.review_tracks(
                1, "P", [(i, Path(f).name, f) for i, f in enumerate(files)])

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def status(self, name):
        for e in self.rep["tracks"].values():
            if e["filename"] == name:
                return e["status"], {i["code"] for i in e["issues"]}
        raise KeyError(name)

    def test_clean_tracks_pass(self):
        for i in range(5):
            self.assertEqual(self.status(f"clean{i}.wav"), ("ok", set()))

    def test_detects_each_fault(self):
        self.assertEqual(self.status("noisy.wav")[0], "error")
        self.assertIn("noise", self.status("noisy.wav")[1])
        self.assertIn("clipping", self.status("clip.wav")[1])
        self.assertIn("dropout", self.status("drop.wav")[1])
        self.assertEqual(self.status("abrupt.wav"), ("warning", {"abrupt_end"}))
        self.assertIn("too_short", self.status("short.wav")[1])

    def test_cache_and_manual_flag(self):
        with tempfile.TemporaryDirectory() as t, \
                patch.object(vconfig, "MEDIA_ROOT", Path(t)):
            calls = []

            def m(p):
                calls.append(p)
                return track_qc.measure(p)
            tr = [(0, "clean0.wav", self.files[0]), (1, "clean1.wav", self.files[1])]
            track_qc.review_tracks(2, "Q", tr, _measure=m)
            track_qc.review_tracks(2, "Q", tr, _measure=m)
            self.assertEqual(len(calls), 2)             # lần 2 dùng cache
            rep = track_qc.set_manual_flag(2, "Q", 1, True)
            self.assertEqual(rep["tracks"]["1"]["status"], "error")
            rep = track_qc.review_tracks(2, "Q", tr, _measure=m)
            self.assertEqual(rep["tracks"]["1"]["status"], "error")   # cờ tay giữ nguyên
            rep = track_qc.set_manual_flag(2, "Q", 1, False)
            self.assertEqual(rep["tracks"]["1"]["status"], "ok")


class ResolveTests(unittest.TestCase):
    def test_follows_replacement_chain(self):
        cur = {"c.wav": "T"}
        chain = mix_patch._replacement_chain(
            {"replaced": [{"old_filename": "a.wav", "new_filename": "b.wav"},
                          {"old_filename": "b.wav", "new_filename": "c.wav"}]})
        self.assertEqual(mix_patch._resolve("a.wav", cur, chain), ("T", True))
        self.assertEqual(mix_patch._resolve("c.wav", cur, chain), ("T", False))
        self.assertEqual(mix_patch._resolve("x.wav", cur, chain), (None, False))


class PatchMixTests(unittest.TestCase):
    """Vá mix: đoạn TRƯỚC bài được thay phải giống HỆT bản cũ."""

    def test_prefix_identical_and_hard_cut(self):
        from backend import core_bridge
        with tempfile.TemporaryDirectory() as t:
            d = Path(t)
            db = d / "app.db"
            SQLModel.metadata.create_all(create_engine(f"sqlite:///{db}"))
            sr = 44100

            def song(seed, sec=30):
                rng = np.random.default_rng(seed)
                tt = np.arange(sec * sr) / sr
                x = 0.2 * np.sin(2 * np.pi * (200 + 50 * seed) * tt) \
                    + 0.01 * rng.standard_normal(len(tt))
                p = d / f"s{seed}.wav"
                sf.write(str(p), np.stack([x, x], 1).astype(np.float32), sr,
                         subtype="PCM_24")
                return str(p)
            a, b, c, n = song(1), song(2), song(3), song(4, sec=26)
            cb = lambda *a, **k: None               # noqa: E731
            old = core_bridge.run_patch_mix_job(
                1, cb, [a, b, c, a], ["A", "B", "C", "A2"], str(d / "old"),
                3.0, sr, 32, str(db), 10_000)
            new = core_bridge.run_patch_mix_job(
                2, cb, [a, n, c, a], ["A", "B", "C", "A2"], str(d / "new"),
                3.0, sr, 32, str(db), old["total_duration_seconds"])
            x_old, _ = sf.read(str(d / "old" / "mix.wav"))
            x_new, _ = sf.read(str(d / "new" / "mix.wav"))
            info = json.loads((d / "old" / "mix_info.json").read_text())
            b_start = int(info["tracks"][1]["start_seconds"] * sr)
            self.assertGreater(b_start, sr * 20)
            np.testing.assert_array_equal(x_old[:b_start], x_new[:b_start])
            self.assertFalse(np.array_equal(x_old[b_start:b_start + sr],
                                            x_new[b_start:b_start + sr]))
            self.assertLessEqual(len(x_new), len(x_old))       # cắt theo bản cũ
            ninfo = json.loads((d / "new" / "mix_info.json").read_text())
            self.assertEqual([e["name"] for e in ninfo["tracks"]],
                             ["A", "B", "C", "A2"])            # giữ tên đoạn


class FakeDriver:
    def __init__(self, makers):
        self.makers = makers        # song_id → hàm tạo audio
        self.clicks, self.styles, self.n = 0, [], 0
        self.page = None

    def open_create_page(self): pass
    def read_credits(self): return 500
    def snapshot_song_ids(self): return set()
    def fill_styles(self, s): self.styles.append(s)
    def fill_exclusions(self, e): pass
    def close_extra_tabs(self): pass
    def click_create(self): self.clicks += 1

    def wait_for_new_songs(self, before, expected=2, timeout_sec=300):
        out = []
        for _ in range(2):
            self.n += 1
            out.append(NewSong(song_id=f"new-{self.n}", duration_seconds=125))
        return out

    def download_wav(self, song_id, dest, timeout_sec=240):
        return write(dest, self.makers.get(song_id, lambda: tone(seed=50))())


class FakeBrowser:
    driver = None

    def __init__(self, dry_run, log): pass
    def open(self): return FakeBrowser.driver
    def __enter__(self): return self
    def __exit__(self, *a): pass


class ReplaceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        d = Path(self.tmp.name)
        eng = create_engine(f"sqlite:///{d / 'app.db'}")
        SQLModel.metadata.create_all(eng)
        self.addCleanup(eng.dispose)          # Windows: nhả file app.db trước khi xoá
        for obj, attr, val in ((suno_fix, "engine", eng), (bdb, "engine", eng),
                               (vconfig, "MEDIA_ROOT", d / "media"),
                               (ss, "_SunoBrowser", FakeBrowser),
                               (ss, "_configure_create_form", lambda *a, **k: None)):
            p = patch.object(obj, attr, val)
            p.start()
            self.addCleanup(p.stop)
        self.eng = eng
        with Session(eng) as s:
            p = Project(name="P")
            s.add(p); s.commit(); s.refresh(p)
            self.pid = p.id
            staging = d / "staging"
            staging.mkdir()
            cfg = {"prompt_plan": [{"styles": f"style {i}", "exclusions": "x"}
                                   for i in range(5)],
                   "minimum_duration_seconds": 120,
                   "preferred_duration_seconds_min": 100,
                   "preferred_duration_seconds_max": 300,
                   "download_min_song_age_sec": 0}
            b = SunoBatch(project_id=p.id, idempotency_key="k", phase="COMPLETED",
                          config_json=json.dumps(cfg), staging_dir=str(staging),
                          create_actions_used=5)
            s.add(b); s.commit(); s.refresh(b)
            self.bid = b.id
            tdir = vconfig.project_dir(p.id, "P") / "suno_tracks"
            tdir.mkdir(parents=True)
            self.tids = []
            for i in range(5):
                x = tone(seed=i)
                if i == 0:      # bài lỗi: rè
                    x = x + 0.08 * np.random.default_rng(3).standard_normal(
                        len(x)).astype(np.float32)
                f = write(tdir / f"song{i}.wav", x)
                t = Track(project_id=p.id, filename=f"song{i}.wav", filepath=f,
                          duration_seconds=125, sample_rate=SR, channels=2,
                          format="WAV", is_lossy=False)
                s.add(t); s.commit(); s.refresh(t)
                self.tids.append(t.id)
                s.add(SunoCandidate(batch_id=b.id, project_id=p.id,
                                    song_id=f"old-{i}", request_id=f"req{i + 1}",
                                    title="Song", selected=True, imported=True,
                                    track_id=t.id, validation_status="valid",
                                    download_status="downloaded", wav_path=f))
            s.add(SunoCandidate(batch_id=b.id, project_id=p.id, song_id="spare-0",
                                request_id="req1", title="Song", is_spare=True))
            s.commit()
        track_qc.review_project(self.pid)

    def run_fix(self, makers):
        FakeBrowser.driver = FakeDriver(makers)
        msgs = []
        res = suno_fix.replace_tracks(self.pid, lambda m, p: msgs.append((m, p)),
                                      track_ids=[self.tids[0]])
        self.assertTrue(all(0 <= p <= 100 for _, p in msgs))
        return res, FakeBrowser.driver

    def test_free_spare_used_first(self):
        res, drv = self.run_fix({})
        self.assertEqual(len(res["replaced"]), 1)
        r = res["replaced"][0]
        self.assertEqual((r["source"], r["creates"]), ("spare", 0))
        self.assertEqual(drv.clicks, 0)                  # không tốn credit
        with Session(self.eng) as s:
            self.assertIsNone(s.get(Track, self.tids[0]))
            new = s.get(Track, r["new_track_id"])
            self.assertTrue(Path(new.filepath).exists())
            sp = s.exec(select(SunoCandidate).where(
                SunoCandidate.song_id == "spare-0")).one()
            self.assertEqual((sp.track_id, sp.selected, sp.content_qc_status),
                             (new.id, True, "passed"))
            old = s.exec(select(SunoCandidate).where(
                SunoCandidate.song_id == "old-0")).one()
            self.assertEqual((old.track_id, old.content_qc_status), (None, "rejected"))
            self.assertEqual(s.get(SunoBatch, self.bid).create_actions_used, 5)
        rej = vconfig.project_dir(self.pid, "P") / "suno_tracks" / "_rejected" / "song0.wav"
        self.assertTrue(rej.exists())                    # file cũ dời, không xoá
        rep = track_qc.load_report(self.pid, "P")
        self.assertEqual(rep["replaced"][0]["old_filename"], "song0.wav")
        self.assertEqual(rep["tracks"][str(r["new_track_id"])]["status"], "ok")

    def test_noisy_spare_then_create_with_same_prompt(self):
        noisy = lambda: tone(seed=7) + 0.08 * np.random.default_rng(5).standard_normal(  # noqa: E731
            125 * SR).astype(np.float32)
        res, drv = self.run_fix({"spare-0": noisy})
        r = res["replaced"][0]
        self.assertEqual((r["source"], r["creates"]), ("create", 1))
        self.assertEqual(drv.clicks, 1)
        self.assertEqual(drv.styles, ["style 0"])        # đúng prompt của bài 1
        with Session(self.eng) as s:
            self.assertEqual(s.get(SunoBatch, self.bid).create_actions_used, 6)
            new_c = s.exec(select(SunoCandidate).where(
                SunoCandidate.track_id == r["new_track_id"])).one()
            self.assertTrue(new_c.request_id.startswith("fix6:p1"))

    def test_gives_up_after_cap(self):
        noisy = lambda: tone(seed=7) + 0.08 * np.random.default_rng(5).standard_normal(  # noqa: E731
            125 * SR).astype(np.float32)
        makers = {k: noisy for k in ["spare-0"] + [f"new-{i}" for i in range(1, 9)]}
        res, drv = self.run_fix(makers)
        self.assertEqual(res["replaced"], [])
        self.assertEqual(drv.clicks, suno_fix.DEFAULT_MAX_CREATES_PER_TRACK)
        with Session(self.eng) as s:
            self.assertIsNotNone(s.get(Track, self.tids[0]))   # giữ bài cũ

    def test_ui_broken_download_stops_before_create(self):
        """Suno đổi UI → tải hỏng hết: DỪNG, không Create đốt credit."""
        def broken():
            raise SunoUIChanged("Không thấy 'Open in Studio' trong submenu Edit của bài.")
        with self.assertRaises(suno_fix.FixError):
            self.run_fix({"spare-0": broken})
        self.assertEqual(FakeBrowser.driver.clicks, 0)
        with Session(self.eng) as s:
            self.assertEqual(s.get(SunoBatch, self.bid).create_actions_used, 5)
            self.assertIsNotNone(s.get(Track, self.tids[0]))

    def test_reuses_songs_of_earlier_fix_creates_for_free(self):
        """Bài của lượt tạo lại trước (fixN:p1) tải hỏng → lần sau tải lại, 0 credit."""
        noisy = lambda: tone(seed=7) + 0.08 * np.random.default_rng(5).standard_normal(  # noqa: E731
            125 * SR).astype(np.float32)
        with Session(self.eng) as s:
            for sid, rid in (("f-a", "fix6:p1"), ("f-b", "fix6:p1"), ("o-2", "fix7:p2")):
                s.add(SunoCandidate(batch_id=self.bid, project_id=self.pid, song_id=sid,
                                    request_id=rid, title="Song", is_spare=True,
                                    download_status="failed",
                                    download_error="Không thấy 'Open in Studio'"))
            s.commit()
        res, drv = self.run_fix({"spare-0": noisy})
        r = res["replaced"][0]
        self.assertEqual((r["source"], r["creates"], drv.clicks), ("spare", 0, 0))
        self.assertIn(r["new_song_id"], ("f-a", "f-b"))      # cùng prompt #1, không lấy p2


if __name__ == "__main__":
    unittest.main()
