"""Tự tạo lại Suno khi rà soát sau mix phát hiện bài lỗi (qc_autofix).
Dùng lại fixture của test_track_qc (SQLite tạm, audio tổng hợp); job sửa /
vá mix / dựng video đều được thay bằng hàm giả → không mở trình duyệt, không
tốn credit, không gọi /qc/regenerate."""
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent))
from sqlmodel import Session

import test_track_qc as base
from backend.models import Mix
from backend.video import qc_autofix, track_qc


class AutoFixTests(unittest.TestCase):
    setUp = base.ReplaceTests.setUp

    def ctx(self):
        return {"project_id": self.pid, "project_name": "P", "idea": "i",
                "audio_path": "orig/mix.wav", "style_key": None}

    def decide(self, mix_id=7):
        calls = []
        handed = qc_autofix._decide(self.pid, mix_id, self.ctx(),
                                    _submit=lambda *a, **k: calls.append((a, k)))
        return handed, calls

    def auto_fix(self):
        return track_qc.load_report(self.pid, "P").get("auto_fix") or {}

    def test_error_track_submits_auto_fix_job(self):
        handed, calls = self.decide()
        self.assertTrue(handed)
        (args, kw), = calls
        self.assertEqual(args[:2], (self.pid, "suno-fix"))
        self.assertIs(args[2], qc_autofix.auto_fix_job)
        self.assertEqual(kw["track_ids"], [self.tids[0]])
        self.assertEqual(kw["mix_id"], 7)
        self.assertEqual(self.auto_fix()["status"], "running")

    def test_toggle_off_only_records(self):
        qc_autofix.set_auto_enabled(self.pid, "P", False)
        handed, calls = self.decide()
        self.assertFalse(handed)
        self.assertEqual(calls, [])
        self.assertEqual(self.auto_fix()["status"], "off")
        self.assertFalse(track_qc.load_report(self.pid, "P")["auto_regen"])

    def test_once_per_mix_no_loop(self):
        self.assertTrue(self.decide(7)[0])
        handed, calls = self.decide(7)
        self.assertFalse(handed)
        self.assertEqual(calls, [])
        self.assertTrue(self.decide(8)[0])        # bản mix mới → được sửa lượt mới

    def test_no_error_tracks_nothing_to_do(self):
        track_qc.set_manual_flag(self.pid, "P", self.tids[0], False)
        with patch.object(qc_autofix, "pick_tracks", return_value=[]):
            handed, calls = self.decide()
        self.assertFalse(handed)
        self.assertEqual(calls, [])

    def test_caps_tracks_per_round(self):
        many = list(range(100, 100 + qc_autofix.AUTO_MAX_TRACKS + 3))
        with patch.object(qc_autofix, "pick_tracks", return_value=many):
            _, calls = self.decide()
        self.assertEqual(len(calls[0][1]["track_ids"]), qc_autofix.AUTO_MAX_TRACKS)
        self.assertIn("còn 3 bài", self.auto_fix()["message"])

    def test_job_replaced_then_patches_and_hands_off(self):
        cont = []
        with patch.object(qc_autofix, "_continue_in_background",
                          lambda *a: cont.append(a)):
            res = qc_autofix.auto_fix_job(
                self.pid, lambda m, p: None, mix_id=7, track_ids=[self.tids[0]],
                auto_ctx=self.ctx(), project_name="P",
                _replace=lambda *a, **k: {"replaced": [{"old_track_id": 1}], "failed": []},
                _patch=lambda pid: 99)
        self.assertEqual(len(res["replaced"]), 1)
        self.assertEqual(cont[0][2], 99)                # chờ bản «Vá mix» #99
        self.assertEqual(self.auto_fix()["status"], "patching")

    def test_job_uses_auto_create_cap(self):
        seen = {}

        def fake_replace(pid, cb, **k):
            seen.update(k)
            return {"replaced": [], "failed": [{"track_id": 1}]}
        cont = []
        with patch.object(qc_autofix, "_continue_in_background",
                          lambda *a: cont.append(a)):
            qc_autofix.auto_fix_job(self.pid, lambda m, p: None, mix_id=7,
                                    track_ids=[1], auto_ctx=self.ctx(),
                                    project_name="P", _replace=fake_replace)
        self.assertEqual(seen["max_creates_per_track"],
                         qc_autofix.AUTO_MAX_CREATES_PER_TRACK)
        self.assertIsNone(cont[0][2])                   # không thay được → mix gốc
        self.assertEqual(self.auto_fix()["status"], "failed")

    def test_job_failure_still_continues_chain(self):
        cont = []

        def boom(*a, **k):
            raise RuntimeError("hết credit")
        with patch.object(qc_autofix, "_continue_in_background",
                          lambda *a: cont.append(a)):
            with self.assertRaises(RuntimeError):
                qc_autofix.auto_fix_job(self.pid, lambda m, p: None, mix_id=7,
                                        track_ids=[1], auto_ctx=self.ctx(),
                                        project_name="P", _replace=boom)
        self.assertEqual(len(cont), 1)
        self.assertIn("hết credit", self.auto_fix()["message"])

    def test_continue_starts_video_with_patched_mix(self):
        with Session(self.eng) as s:
            m = Mix(project_id=self.pid, title="Vá mix #7", status="completed",
                    output_dir="out/99")
            s.add(m); s.commit(); s.refresh(m)
            mid = m.id
        started = []
        qc_autofix._set_active(self.pid, True)
        with patch.object(qc_autofix, "_start_video",
                          lambda ctx, audio=None: started.append(audio)):
            qc_autofix._continue(self.pid, self.ctx(), mid, "P", 7)
        self.assertEqual(Path(started[0]), Path("out/99/mix.wav"))
        self.assertFalse(qc_autofix.is_active(self.pid))
        self.assertEqual(self.auto_fix()["status"], "done")

    def test_after_mix_without_errors_starts_video_with_original(self):
        started = []
        qc_autofix._set_active(self.pid, True)
        with patch.object(qc_autofix, "_decide", return_value=False), \
             patch.object(qc_autofix, "_start_video",
                          lambda ctx, audio=None: started.append((ctx, audio))):
            qc_autofix._after_mix(self.pid, 7, self.ctx())
        self.assertEqual(started, [(self.ctx(), None)])
        self.assertFalse(qc_autofix.is_active(self.pid))

    def test_after_mix_hand_off_keeps_active_and_no_video_yet(self):
        started = []
        qc_autofix._set_active(self.pid, True)
        self.addCleanup(qc_autofix._set_active, self.pid, False)
        with patch.object(qc_autofix, "_decide", return_value=True), \
             patch.object(qc_autofix, "_start_video",
                          lambda *a, **k: started.append(a)):
            qc_autofix._after_mix(self.pid, 7, self.ctx())
        self.assertEqual(started, [])
        self.assertTrue(qc_autofix.is_active(self.pid))


class HookTests(unittest.TestCase):
    setUp = base.ReplaceTests.setUp

    def test_mix_success_routes_through_autofix(self):
        """Mix xong: không dựng video ngay — giao cho qc_autofix (rà soát trước)."""
        from backend.routers import mixes
        with Session(self.eng) as s:
            m = Mix(project_id=self.pid, title="Mix", status="running",
                    output_dir=str(Path(self.tmp.name)))
            s.add(m); s.commit(); s.refresh(m)
            mid = m.id
        (Path(self.tmp.name) / "mix.wav").write_bytes(b"x")
        calls = []
        with patch.object(mixes, "engine", self.eng),              patch.object(qc_autofix, "after_mix", lambda *a: calls.append(a)),              patch.object(mixes, "_maybe_trigger_auto_video") as trig:
            mixes._on_success(mid, {"output_dir": self.tmp.name,
                                    "total_duration_seconds": 600}, force_video=True)
        (pid, mix_id, ctx), = calls
        self.assertEqual((pid, mix_id), (self.pid, mid))
        self.assertTrue(ctx["audio_path"].endswith("mix.wav"))
        trig.assert_not_called()

    def test_batch_waits_while_autofix_active(self):
        from backend import batch_service
        qc_autofix._set_active(4242, True)
        try:
            self.assertTrue(batch_service._chain_busy())
        finally:
            qc_autofix._set_active(4242, False)


if __name__ == "__main__":
    unittest.main()
