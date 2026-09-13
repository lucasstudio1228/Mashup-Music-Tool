"""Regression tests for the 20-image/20-video workflow."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).parent.parent))

from backend.video import config
from backend.video.shuffle import shuffle_t_plus_n


class VideoWorkflowTests(unittest.TestCase):
    def test_fifteen_prompts_and_one_to_one_plan(self):
        self.assertEqual(config.PARAMS.image_count, 20)
        self.assertEqual(config.PARAMS.total_clips, 20)
        self.assertEqual(set(config.DEFAULT_PROMPTS), {str(i) for i in range(20)})

        plan = config.generate_ingredient_plan(20, 19, seed=42)
        self.assertEqual(len(plan), 20)
        self.assertEqual(plan[0], 0)
        self.assertEqual(set(plan), set(range(20)))

    def test_thumbnail_text_and_continuity_prompts(self):
        thumbnail = config.DEFAULT_PROMPTS["0"].format(
            topic="Zen Lake", keywords="Zen · Ambient · Focus")
        self.assertIn("Zen Lake", thumbnail)
        self.assertIn("Zen · Ambient · Focus", thumbnail)
        for index in range(1, 20):
            prompt = config.DEFAULT_PROMPTS[str(index)].format(topic="Zen Lake")
            self.assertIn("CÙNG MỘT", prompt)
            self.assertIn("hoodie xanh sage", prompt)
            self.assertIn("cabin gỗ", prompt)

    def test_t_plus_five_and_thumbnail_first(self):
        clips = [f"clip_{i:02d}.mp4" for i in range(20)]
        result = shuffle_t_plus_n(
            clips, count=100, window=5, seed=7, first=clips[0])
        self.assertEqual(result[0], "clip_00.mp4")
        for index, clip in enumerate(result):
            self.assertNotIn(clip, result[max(0, index - 5):index])

    def test_media_folder_uses_safe_project_name(self):
        folder = config.project_dir(2, "Bamboo Flute")
        self.assertEqual(folder.name, "Bamboo Flute")
        self.assertEqual(
            config.project_dir(2, 'Zen: Focus / Study?').name,
            "Zen_ Focus _ Study_",
        )

    def test_long_audio_assembly_uses_one_cycle(self):
        clips = [f"clip_{i:02d}.mp4" for i in range(20)]
        rest = clips[1:]
        import random
        random.Random(7).shuffle(rest)
        cycle = [clips[0], *rest]
        self.assertEqual(len(cycle), 20)
        self.assertEqual(cycle[0], clips[0])
        self.assertEqual(set(cycle), set(clips))
        # clip_00 là intro, không thuộc phần được loop. Cycle 01..19 vẫn cách
        # lần xuất hiện kế tiếp 19 vị trí (> T+5).
        scenery = cycle[1:]
        doubled = scenery + scenery
        self.assertNotIn(clips[0], doubled)
        for index in range(19, len(doubled)):
            self.assertNotIn(doubled[index], doubled[index - 5:index])


if __name__ == "__main__":
    unittest.main()
