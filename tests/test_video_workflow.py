"""Offline regression tests against actual workflow/assembler entry points."""
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent))
from backend.video import config, assembler, service
from backend.video.gemini_driver import _prompt_for
from backend.video.flow_driver import generate_clips


class VideoWorkflowTests(unittest.TestCase):
    def test_complete_prompts_and_one_to_one_plan(self):
        n = config.PARAMS.image_count
        self.assertEqual(config.PARAMS.total_clips, n)
        self.assertEqual(set(config.DEFAULT_PROMPTS), {str(i) for i in range(n)})
        plan = config.generate_ingredient_plan(n, n - 1, seed=42)
        self.assertEqual(plan[0], 0)
        self.assertEqual(len(plan), n)
        self.assertEqual(set(plan), set(range(n)))

    def test_clean_thumbnail_and_locked_continuity_in_every_prompt(self):
        for i in range(config.PARAMS.image_count):
            prompt = _prompt_for(i, "Zen Lake", config.PARAMS, config.DEFAULT_PROMPTS)
            for value in ("Zen Lake", "CÙNG MỘT", "hoodie xanh sage", "cabin gỗ", "BỐ CỤC KHÓA"):
                self.assertIn(value, prompt)
        self.assertIn("HOÀN TOÀN KHÔNG", _prompt_for(0, "Zen", config.PARAMS, config.DEFAULT_PROMPTS))
        with self.assertRaisesRegex(ValueError, "Thiếu prompt"):
            _prompt_for(40, "Zen", config.PARAMS, {"0": "one shot"})
        with self.assertRaises(ValueError):
            _prompt_for(0, "Zen", config.PARAMS, {})

    def test_media_folder_uses_name_and_id(self):
        self.assertEqual(config.project_dir(2, "Bamboo Flute").name, "Bamboo Flute (#2)")
        self.assertEqual(config.project_dir(2, 'Zen: Focus / Study?').name, "Zen_ Focus _ Study_ (#2)")

    def test_flow_resume_extends_plan_without_remapping_or_opening_browser(self):
        with TemporaryDirectory() as tmp, patch.object(config, "MEDIA_ROOT", Path(tmp)), \
                patch("backend.video.flow_driver.BrowserSession") as browser:
            images = config.images_dir(12, "Flow Test")
            clips = config.clips_dir(12, "Flow Test")
            images.mkdir(parents=True)
            clips.mkdir(parents=True)
            n = config.PARAMS.image_count
            original = [0, *reversed(range(1, 20))]
            for i in range(n):
                (images / f"{i}.png").write_bytes(b"test image")
                (clips / f"clip_{i:02d}.mp4").write_bytes(b"x" * 10001)
            (clips / "_plan.json").write_text(json.dumps({"plan": original}), encoding="utf-8")
            result = generate_clips(12, resume=True, project_name="Flow Test")
            saved = json.loads((clips / "_plan.json").read_text(encoding="utf-8"))
            self.assertEqual(saved["plan"][:20], original)
            self.assertEqual(set(saved["plan"]), set(range(n)))
            self.assertEqual(len(result), n)
            browser.assert_not_called()

    def test_production_sequence_intro_once_and_window_across_cycles(self):
        with TemporaryDirectory() as tmp, patch.object(config, "MEDIA_ROOT", Path(tmp)), \
                patch.object(service, "probe_duration", return_value=7200), \
                patch.object(service, "overlay_title_on_clip", return_value=False), \
                patch.object(service, "assemble_video_blend", return_value={}) as assemble:
            clips = config.clips_dir(13, "Sequence Test")
            clips.mkdir(parents=True)
            for i in range(config.PARAMS.total_clips):
                (clips / f"clip_{i:02d}.mp4").touch()
            service.step_assemble(13, lambda *args: None, audio_path="mock.wav",
                                  seed=7, project_name="Sequence Test")
            sequence = assemble.call_args.args[0]
            self.assertEqual(Path(sequence[0]).name, "clip_00.mp4")
            rest = sequence[1:]
            self.assertEqual(len(rest), len(set(rest)))
            self.assertNotIn(sequence[0], rest)
            expanded = [sequence[0], *rest, *rest, *rest]
            self.assertEqual(expanded.count(sequence[0]), 1)
            for idx, clip in enumerate(expanded):
                self.assertNotIn(clip, expanded[max(0, idx-config.PARAMS.t_window):idx])

    def test_actual_assembler_keeps_intro_out_of_loop_and_bounded_command(self):
        calls, lists = [], []
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            audio = root / "audio.wav"
            audio.touch()
            def run(cmd):
                calls.append(cmd)
                script = Path(cmd[cmd.index("-filter_complex_script") + 1])
                self.assertIn("xfade=transition=fade", script.read_text())
                return SimpleNamespace(returncode=0, stderr="")
            def mux(path, *args):
                lists.extend(path.read_text(encoding="utf-8").splitlines())
            with patch.object(assembler, "_run", side_effect=run), \
                    patch.object(assembler, "probe_duration", return_value=8), \
                    patch.object(assembler, "_mux_visual_list", side_effect=mux):
                assembler.assemble_video_blend([f"clip_{i:02d}.mp4" for i in range(41)],
                    str(audio), str(root / "final.mp4"), target_seconds=86400)
            self.assertIn("clip_00.mp4", calls[0])
            self.assertNotIn("clip_00.mp4", calls[1])
            self.assertEqual(sum("_visual_intro.mp4" in line for line in lists), 1)
            self.assertTrue(all("_visual_cycle.mp4" in line for line in lists[1:]))
            self.assertLess(max(len(" ".join(cmd)) for cmd in calls), 32767)


if __name__ == "__main__":
    unittest.main()
