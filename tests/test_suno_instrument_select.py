"""Chọn 1 trong 2 bài của mỗi lượt Create theo chấm nhạc cụ chính (0 credit)."""
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlmodel import Session, SQLModel, create_engine, select  # noqa: E402

from backend.models import Project, SunoBatch, SunoCandidate  # noqa: E402
from backend.video import suno_service as ss  # noqa: E402
from backend.video import instrument_qc  # noqa: E402


def qc(clean, leak=0.0, vocal=0.0):
    return {"clean": clean, "leak_score": leak, "vocal_share": vocal,
            "lead_share": 0.8, "leaks": {}}


CFG = {"preferred_duration_seconds_min": 180, "preferred_duration_seconds_max": 300}


class SwapReasonTests(unittest.TestCase):
    def test_clean_beats_duration(self):
        # Bản còn lại sạch nhưng 130s vẫn thắng bản 240s lẫn nhạc cụ.
        self.assertIn("nhạc cụ chính",
                      ss._swap_reason(130, qc(True), 240, qc(False, .6), 180, 300))
        self.assertIsNone(ss._swap_reason(240, qc(False, .1), 130, qc(True), 180, 300))

    def test_both_leaky_clear_gap_wins(self):
        self.assertIn("ít lẫn",
                      ss._swap_reason(150, qc(False, .3), 240, qc(False, .7), 180, 300))
        self.assertIsNone(ss._swap_reason(240, qc(False, .7), 150, qc(False, .3), 180, 300))

    def test_small_gap_falls_back_to_duration(self):
        self.assertIn("gần khoảng",
                      ss._swap_reason(240, qc(False, .5), 150, qc(False, .4), 180, 300))

    def test_no_qc_keeps_old_duration_rule(self):
        self.assertIn("gần khoảng", ss._swap_reason(200, None, 150, None, 180, 300))
        self.assertIsNone(ss._swap_reason(150, None, 200, None, 180, 300))

    def test_vocals_count_as_badness(self):
        self.assertEqual(instrument_qc.badness(qc(False, .2, .3)), .5)


class PreferTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.eng = create_engine("sqlite://")
        SQLModel.metadata.create_all(self.eng)
        self.s = Session(self.eng)
        p = Project(name="P", instrument="bamboo flute", music_style="Chinese Zen Music")
        self.s.add(p); self.s.commit(); self.s.refresh(p)
        b = SunoBatch(project_id=p.id, idempotency_key="k")
        self.s.add(b); self.s.commit(); self.s.refresh(b)
        self.batch = b
        self.logs = []

    def tearDown(self):
        self.s.close()
        self.tmp.cleanup()

    def cand(self, sid, req, selected, dur, downloaded):
        c = SunoCandidate(batch_id=self.batch.id, project_id=self.batch.project_id,
                          song_id=sid, request_id=req, selected=selected,
                          is_spare=not selected)
        if downloaded:
            f = self.dir / f"{sid}.wav"
            f.write_bytes(b"x")
            c.wav_path, c.download_status = str(f), "downloaded"
            c.validation_status, c.verified_duration_seconds = "valid", dur
        self.s.add(c); self.s.commit(); self.s.refresh(c)
        return c

    def run_prefer(self, scores, durs):
        downloads = []

        def attempt(c, probe=False):
            downloads.append((c.song_id, probe))
            f = self.dir / f"{c.song_id}.wav"
            f.write_bytes(b"x")
            c.wav_path, c.download_status = str(f), "downloaded"
            c.validation_status, c.verified_duration_seconds = "valid", durs[c.song_id]
            self.s.add(c); self.s.commit()
            return True

        def fake_qc(c):
            return scores.get(c.song_id)
        ss._prefer_duration(self.s, self.batch, CFG, attempt, self.logs.append, qc=fake_qc)
        return downloads

    def selected(self):
        return sorted(c.song_id for c in self.s.exec(select(SunoCandidate).where(
            SunoCandidate.selected == True)).all())  # noqa: E712

    def test_leaky_first_swaps_to_clean_spare(self):
        self.cand("a1", "req1", True, 240, True)
        self.cand("a2", "req1", False, 0, False)
        dl = self.run_prefer({"a1": qc(False, .6), "a2": qc(True, .05)}, {"a2": 200})
        self.assertEqual(dl, [("a2", True)])            # tải bản còn lại = probe
        self.assertEqual(self.selected(), ["a2"])

    def test_clean_in_range_no_extra_download(self):
        self.cand("b1", "req1", True, 240, True)
        self.cand("b2", "req1", False, 0, False)
        dl = self.run_prefer({"b1": qc(True)}, {})
        self.assertEqual(dl, [])
        self.assertEqual(self.selected(), ["b1"])

    def test_clean_but_long_keeps_clean_over_leaky_spare(self):
        self.cand("c1", "req1", True, 400, True)
        self.cand("c2", "req1", False, 0, False)
        self.run_prefer({"c1": qc(True), "c2": qc(False, .5)}, {"c2": 240})
        self.assertEqual(self.selected(), ["c1"])

    def test_qc_unavailable_falls_back_to_duration(self):
        self.cand("d1", "req1", True, 130, True)
        self.cand("d2", "req1", False, 0, False)
        self.run_prefer({}, {"d2": 220})                # qc trả None
        self.assertEqual(self.selected(), ["d2"])

    def test_make_qc_skips_unknown_instrument_and_disabled(self):
        self.assertIsNone(ss._make_instrument_qc(self.s, self.batch,
                                                 {"instrument_qc": False}, print))
        p = self.s.get(Project, self.batch.project_id)
        p.instrument = "theremin"
        self.s.add(p); self.s.commit()
        self.assertIsNone(ss._make_instrument_qc(self.s, self.batch, {}, print))

    def test_make_qc_marks_status_and_survives_analyzer_error(self):
        c = self.cand("e1", "req1", True, 240, True)
        with patch.object(instrument_qc, "available", return_value=True), \
                patch.object(instrument_qc, "analyze", return_value=qc(True)):
            run = ss._make_instrument_qc(self.s, self.batch, {}, self.logs.append)
            self.assertTrue(run(c)["clean"])
        self.assertEqual(c.content_qc_status, "clean")
        c2 = self.cand("e2", "req2", True, 240, True)
        with patch.object(instrument_qc, "available", return_value=True), \
                patch.object(instrument_qc, "analyze", side_effect=RuntimeError("gpu")):
            run = ss._make_instrument_qc(self.s, self.batch, {}, self.logs.append)
            self.assertIsNone(run(c2))
        self.assertEqual(c2.content_qc_status, "not_verified")


if __name__ == "__main__":
    unittest.main()


class UpgradePlanTests(unittest.TestCase):
    V1 = ("Zen meditation, serene, gentle. Lead: solo fingerpicked acoustic guitar, "
          "warm nylon-string tone. Support: airy wind textures, soft drone. Tempo: 54 BPM, "
          "free-flowing rubato. Key: A major pentatonic. Chord progression: A - D - E - A. "
          "Melody: Mid-high register, smooth fingerpicking. Structure: spacious intro, "
          "meditative theme, fading outro. Mix: long hall reverb, wide stereo. "
          "Purely instrumental, no vocals. Signature: a three-note motif answered low.")

    def plan(self):
        from backend.video import suno_prompt as sp
        return sp.build_suno_prompt_plan(self.V1, "vocals, piano", 4,
                                         music_style="Zen Meditation", instrument="Guitar")

    def test_v1_upgraded_to_lead_first_form(self):
        cfg = {"prompt_plan": self.plan(), "prompt_workflow_version": 1}
        self.assertTrue(ss.upgrade_plan_v2(cfg, "Guitar", "Zen Meditation"))
        self.assertEqual(len(cfg["prompt_plan"]), 4)
        self.assertEqual(len(cfg["prompt_plan_v1"]), 4)
        st = cfg["prompt_plan"][0]["styles"]
        self.assertTrue(st.startswith("Lead:"))
        self.assertIn("The guitar alone carries the melody", st)
        self.assertIn("Signature: a three-note motif answered low", st)
        self.assertEqual(cfg["prompt_workflow_version"], 2)
        # chạy lại → không nâng lần 2
        self.assertFalse(ss.upgrade_plan_v2(cfg, "Guitar", "Zen Meditation"))

    def test_unknown_instrument_or_v2_untouched(self):
        cfg = {"prompt_plan": self.plan()}
        self.assertFalse(ss.upgrade_plan_v2(cfg, "", "Zen Meditation"))
        self.assertNotIn("prompt_plan_v1", cfg)
