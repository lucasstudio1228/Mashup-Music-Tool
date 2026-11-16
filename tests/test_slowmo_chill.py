"""2026-10-28 «video trôi quá nhanh»: slow motion nội suy (mci) song song + cache,
chuyển cảnh dài smootherstep, chặn tốc độ/độ dài chuyển cảnh cho mọi look."""
import shutil
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from backend.video import assembler as A
from backend.video import config
from backend.video.channel_looks import LOOKS, finish_for

HAS_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


def _clip(path: Path, color: str, seconds: float = 2.0, fps: int = 12) -> str:
    subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i",
                    f"testsrc2=s=160x90:r={fps}:d={seconds}", "-vf", f"drawbox=c={color}:t=8",
                    "-pix_fmt", "yuv420p", str(path)], capture_output=True, check=True)
    return str(path)


class ChillFinishTests(unittest.TestCase):
    def test_clamps_speed_and_blend(self):
        out = config.chill_finish({"slow_speed": 1.0, "blend_seconds": 2.6,
                                   "grade": "eq=gamma=1.02", "transition": "softfade"})
        self.assertEqual(out["slow_speed"], config.MAX_PLAYBACK_SPEED)
        self.assertEqual(out["blend_seconds"], config.MIN_BLEND_SECONDS)
        self.assertEqual(out["grade"], "eq=gamma=1.02")
        slower = config.chill_finish({"slow_speed": 0.5, "blend_seconds": 6.0})
        self.assertEqual((slower["slow_speed"], slower["blend_seconds"]), (0.5, 6.0))

    def test_every_look_is_slow_and_soft(self):
        for key in LOOKS:
            with self.subTest(key):
                fin = finish_for(key)
                self.assertLessEqual(fin["slow_speed"], config.MAX_PLAYBACK_SPEED)
                self.assertGreaterEqual(fin["blend_seconds"], config.MIN_BLEND_SECONDS)
        self.assertLessEqual(config.PARAMS.slow_speed, config.MAX_PLAYBACK_SPEED)
        self.assertGreaterEqual(config.PARAMS.blend_seconds, config.MIN_BLEND_SECONDS)

    def test_softfade_uses_smootherstep(self):
        self.assertIn("P*P*P*(P*(P*6-15)+10)", A._SOFTFADE_EXPR)
        self.assertIn("custom:expr=A*", A._xfade("softfade", 4.5, 10.0))
        # Đầu (P=1) = nguyên clip A, cuối (P=0) = nguyên clip B, giữa = 50/50.
        ease = lambda P: P * P * P * (P * (P * 6 - 15) + 10)   # noqa: E731
        self.assertEqual((ease(1.0), ease(0.0), ease(0.5)), (1.0, 0.0, 0.5))

    def test_slowmo_command_interpolates(self):
        cmd = A._slowmo_cmd(Path("a.mp4"), Path("b.mp4"), 0.58, 30, 1920, 1080, True)
        vf = cmd[cmd.index("-vf") + 1]
        self.assertIn("setpts=PTS/0.58", vf)
        self.assertIn("minterpolate=fps=30:mi_mode=mci", vf)
        self.assertLess(vf.index("setpts"), vf.index("minterpolate"))
        self.assertIn("-an", cmd)
        plain = A._slowmo_cmd(Path("a.mp4"), Path("b.mp4"), 0.58, 30, 1920, 1080, False)
        self.assertNotIn("minterpolate", plain[plain.index("-vf") + 1])


@unittest.skipUnless(HAS_FFMPEG, "cần ffmpeg")
class SlowmoClipsTests(unittest.TestCase):
    def setUp(self):
        tmp = TemporaryDirectory(prefix="slowmo-")
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.a = _clip(self.root / "clip_01.mp4", "red")
        self.b = _clip(self.root / "clip_02.mp4", "blue")
        self.cache = self.root / "_slowmo"

    def test_slows_down_dedupes_and_caches(self):
        logs = []
        out = A.slowmo_clips([self.a, self.b, self.a], 0.5, self.cache, fps=12,
                             width=160, height=90, workers=2, progress=logs.append)
        self.assertEqual(len(out), 3)
        self.assertEqual(out[0], out[2])                       # trùng → xử lý 1 lần
        self.assertEqual(len(list(self.cache.glob("*.mp4"))), 2)
        self.assertFalse(list(self.cache.glob("*.part.mp4")))
        self.assertAlmostEqual(A.probe_duration(out[0]), 4.0, delta=0.3)   # 2s / 0.5
        self.assertTrue(any("2/2" in m for m in logs))
        # Lần 2: lấy cache, không chạy ffmpeg nào.
        with patch.object(A.subprocess, "Popen") as pop:
            again = A.slowmo_clips([self.a, self.b], 0.5, self.cache, fps=12,
                                   width=160, height=90)
        pop.assert_not_called()
        self.assertEqual(again, out[:2])
        # Tốc độ khác → file cache khác.
        other = A.slowmo_clips([self.a], 0.6, self.cache, fps=12, width=160, height=90)
        self.assertNotEqual(other[0], out[0])

    def test_interpolation_failure_falls_back_to_frame_repeat(self):
        real = A._slowmo_cmd

        def broken(src, dst, speed, fps, w, h, interpolate):
            cmd = real(src, dst, speed, fps, w, h, interpolate)
            if interpolate:
                cmd[cmd.index("-vf") + 1] += ",nosuchfilter"
            return cmd
        logs = []
        with patch.object(A, "_slowmo_cmd", side_effect=broken):
            out = A.slowmo_clips([self.a], 0.5, self.cache, fps=12, width=160,
                                 height=90, progress=logs.append)
        self.assertAlmostEqual(A.probe_duration(out[0]), 4.0, delta=0.3)
        self.assertTrue(any("nhân bản khung" in m for m in logs))

    def test_cancel_kills_ffmpeg_and_raises(self):
        from backend.video.job_manager import JobCancelled
        with self.assertRaises(JobCancelled):
            A.slowmo_clips([self.a, self.b], 0.5, self.cache, fps=12, width=160,
                           height=90, cancel_check=lambda: True)
        self.assertFalse(list(self.cache.glob("*.mp4")))

    def test_assemble_uses_slowmo_clips_at_1x(self):
        clips = [_clip(self.root / f"c{i}.mp4", c, 3.0)
                 for i, c in enumerate(["red", "green", "blue"])]
        audio = self.root / "a.m4a"
        subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "sine=f=440:d=12",
                        "-c:a", "aac", str(audio)], capture_output=True, check=True)
        out = self.root / "final" / "final.mp4"
        res = A.assemble_video_blend(clips, str(audio), str(out), target_seconds=12,
                                     blend_seconds=1.5, slow_speed=0.5, width=160,
                                     height=90, fps=12, smooth_slowmo=True,
                                     slowmo_cache=self.cache, slowmo_workers=3)
        self.assertAlmostEqual(res["duration"], 12.0, delta=0.6)
        self.assertEqual(len(list(self.cache.glob("*.mp4"))), 3)
        self.assertAlmostEqual(A.probe_duration(str(next(self.cache.glob("c0_*.mp4")))),
                               6.0, delta=0.3)


if __name__ == "__main__":
    unittest.main()
