"""Master + cân bằng âm lượng khi Mix (port từ tool «Cover nhạc», 2026-11-17).

Âm thanh tổng hợp trong thư mục tạm; ffmpeg thật (bỏ qua nếu máy không có);
cấu hình master trỏ sang file tạm — không đụng data/ thật, không backend/mạng.
"""
from pathlib import Path
import hashlib
import json
import shutil
import sys
import tempfile
import unittest
from unittest import mock

import numpy as np
import soundfile as sf

sys.path.insert(0, str(Path(__file__).parent.parent))
from sqlmodel import SQLModel, create_engine  # noqa: E402

from backend import core_bridge  # noqa: E402
from backend import loudness_balance as lb  # noqa: E402
from backend import mastering_service as ms  # noqa: E402
import backend.models  # noqa: E402,F401 — đăng ký bảng

SR = 44100
HAS_FFMPEG = shutil.which("ffmpeg") is not None


def _song(folder: Path, name: str, amp: float, freq: float, sec: float = 20,
          seed: int = 0, swell: bool = False) -> str:
    """Sóng sin + nhiễu nhẹ; swell=True → nửa sau to gấp 3 (bài nhiều dynamic)."""
    rng = np.random.default_rng(seed)
    t = np.arange(int(sec * SR)) / SR
    x = amp * np.sin(2 * np.pi * freq * t) + amp * 0.05 * rng.standard_normal(len(t))
    if swell:
        x[len(x) // 2:] *= 3.0
    p = folder / f"{name}.wav"
    sf.write(str(p), np.stack([x, x * 0.9], 1).astype(np.float32), SR, subtype="PCM_24")
    return str(p)


def _sha(p) -> str:
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


class _TempConfig(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.d = Path(self._tmp.name)
        self.cfg_file = self.d / "master_config.json"
        p = mock.patch.object(ms, "CONFIG_FILE", self.cfg_file)
        p.start()
        self.addCleanup(p.stop)
        self.addCleanup(self._tmp.cleanup)
        self.db = self.d / "app.db"
        eng = create_engine(f"sqlite:///{self.db}")
        SQLModel.metadata.create_all(eng)
        eng.dispose()                       # Windows: nhả file DB để xoá thư mục tạm

    def write_cfg(self, **over):
        self.cfg_file.write_text(json.dumps(over), encoding="utf-8")


class FilterTests(unittest.TestCase):
    def test_chain_matches_cover_tool(self):
        cfg = ms.load_config(Path("khong-ton-tai.json"))
        self.assertEqual(cfg["preset"], "dynamic")          # healing: -16 LUFS, nén 1.5
        name, preset = ms.preset_values(cfg)
        g = ms.build_filter(cfg, preset, ms._loudnorm_args(cfg, preset))
        for part in ("highpass=f=30", "equalizer=f=300", "equalizer=f=3500",
                     "highshelf=f=10000", "acrossover=split=120",
                     "amix=inputs=2:normalize=0", "acompressor=threshold=-18dB:ratio=1.5",
                     "loudnorm=I=-16:TP=-1:LRA=11"):
            self.assertIn(part, g)
        self.assertEqual(ms.target_lufs(cfg), -16.0)

    def test_signature_changes_with_preset(self):
        cfg = ms.load_config(Path("khong-ton-tai.json"))
        a = ms.signature(cfg, *ms.preset_values(cfg, "dynamic"))
        b = ms.signature(cfg, *ms.preset_values(cfg, "natural"))
        self.assertNotEqual(a, b)


@unittest.skipUnless(HAS_FFMPEG, "máy không có ffmpeg")
class MasterFileTests(_TempConfig):
    def test_master_cached_and_original_untouched(self):
        src = _song(self.d, "quiet", 0.03, 330, sec=12)
        before = _sha(src)
        res = ms.master_file(Path(src))
        self.assertEqual(res["status"], "done", res.get("error"))
        out = Path(res["path"])
        self.assertEqual(out, self.d / "mastered" / "quiet_master.wav")
        self.assertEqual(_sha(src), before)                      # file gốc giữ nguyên
        info = sf.info(str(out))
        self.assertEqual(info.samplerate, SR)                    # không đổi sample rate
        self.assertEqual(info.subtype, "PCM_24")
        self.assertAlmostEqual(res["after"]["lufs"], -16.0, delta=1.0)
        self.assertLessEqual(res["after"]["tp"], -0.9)
        self.assertEqual(ms.master_file(Path(src))["status"], "cached")
        self.write_cfg(preset="natural")                         # đổi preset → làm lại
        res2 = ms.master_file(Path(src))
        self.assertEqual(res2["status"], "done")
        self.assertAlmostEqual(res2["after"]["lufs"], -14.0, delta=1.0)

    def test_missing_ffmpeg_never_raises(self):
        src = _song(self.d, "x", 0.1, 440, sec=5)
        self.write_cfg(ffmpeg_path="Z:/khong/co/ffmpeg.exe")
        res = ms.master_file(Path(src))
        self.assertEqual(res["status"], "failed")
        self.assertIn("ffmpeg", res["error"])


@unittest.skipUnless(HAS_FFMPEG, "máy không có ffmpeg")
class BalancedMixTests(_TempConfig):
    def _cb(self, *a, **k):
        pass

    def test_songs_end_up_equally_loud(self):
        a = _song(self.d, "a_quiet", 0.02, 220, seed=1)
        b = _song(self.d, "b_loud", 0.5, 330, seed=2)
        c = _song(self.d, "c_swell", 0.05, 440, seed=3, swell=True)
        raw = [lb.analyze(sf.read(p, dtype="float32")[0], SR)["loud"] for p in (a, b, c)]
        self.assertGreater(max(raw) - min(raw), 15)
        audio, info = core_bridge._load_mastered_balanced(
            [a, b, c, a], SR, str(self.db), self._cb)
        self.assertEqual(list(audio), [a, b, c])
        self.assertEqual(info["mastered"], 3)
        self.assertTrue(info["balanced"])
        self.assertLess(info["spread_after"], 0.5)
        for p in (a, b, c):
            peak = float(np.max(np.abs(audio[p])))
            self.assertLessEqual(peak, 10 ** (lb.CEILING_DB / 20) + 1e-6)
        mid = float(np.median([r["after"]["integrated"] for r in info["songs"]]))
        self.assertAlmostEqual(mid, -16.0, delta=0.6)
        json.dumps(info)                                         # ghi được vào mix_info

    def test_master_disabled_still_balances(self):
        self.write_cfg(enabled=False)
        a = _song(self.d, "a", 0.08, 220, seed=1)   # lệch ~16 dB: trong biên ±12 dB
        b = _song(self.d, "b", 0.5, 330, seed=2)
        audio, info = core_bridge._load_mastered_balanced([a, b], SR, str(self.db),
                                                          self._cb)
        self.assertEqual(info["mastered"], 0)
        self.assertEqual({r["source"] for r in info["songs"]}, {"original"})
        self.assertLess(info["spread_after"], 0.5)
        self.assertFalse((self.d / "mastered").exists())

    def test_patch_mix_reuses_gains_prefix_identical(self):
        a, b, c = (_song(self.d, n, amp, f, sec=30, seed=s) for n, amp, f, s in
                   (("a", 0.05, 220, 1), ("b", 0.3, 330, 2), ("c", 0.1, 440, 3)))
        n = _song(self.d, "n", 0.4, 550, sec=26, seed=4)
        old = core_bridge.run_patch_mix_job(
            1, self._cb, [a, b, c, a], ["A", "B", "C", "A2"], str(self.d / "old"),
            3.0, SR, 32, str(self.db), 10_000, prev_loudness={"balanced": True})
        old_info = json.loads((self.d / "old" / "mix_info.json").read_text("utf-8"))
        self.assertTrue(old_info["loudness"]["balanced"])
        new = core_bridge.run_patch_mix_job(
            2, self._cb, [a, n, c, a], ["A", "B", "C", "A2"], str(self.d / "new"),
            3.0, SR, 32, str(self.db), old["total_duration_seconds"],
            prev_loudness=old_info["loudness"])
        self.assertGreater(new["total_duration_seconds"], 0)
        x_old, _ = sf.read(str(self.d / "old" / "mix.wav"))
        x_new, _ = sf.read(str(self.d / "new" / "mix.wav"))
        b_start = int(old_info["tracks"][1]["start_seconds"] * SR)
        np.testing.assert_array_equal(x_old[:b_start], x_new[:b_start])
        new_info = json.loads((self.d / "new" / "mix_info.json").read_text("utf-8"))
        self.assertTrue(new_info["loudness"].get("reused_gains"))
        loud = {r["audio_file"]: r["after"]["loud"] for r in new_info["loudness"]["songs"]}
        self.assertAlmostEqual(loud["n.wav"], loud["a.wav"], delta=0.6)


if __name__ == "__main__":
    unittest.main()
