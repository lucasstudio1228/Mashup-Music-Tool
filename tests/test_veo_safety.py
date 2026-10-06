"""Prompt clip gửi Veo phải gọn + không có từ khoá khiến Gemini/Flow từ chối."""
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent))
from backend.video import config, veo_safety as vs
from backend.video import flow_driver as fd

OLD = (
    "The two ingredient images are CHARACTER MODEL SHEETS (design documents), "
    "NOT film frames: use them only to get the EXACT identity. Build ONE "
    "continuous film scene with the main character from ingredient 1 and the "
    "companion creature from ingredient 2, keeping face, hair, outfit, colours, "
    "instrument and props unchanged. ABSOLUTELY NO side-by-side views, no text "
    "labels, no colour-swatch strip, no flat studio backdrop, no duplicated "
    "character.\nIDENTITY TO KEEP: Muscular Han Chinese male warrior, early 30s, "
    "tan skin (#EEC8A3), full lips, topknot. Bamboo flute. Accessories: carved "
    "wooden sword (#7B5335), gourd flask (#D7B065). Golden turtle, scales "
    "(#FFD85A, #E9BC32).\n\nSCENE: Next to a rice terrace. Light: soft gold. "
    "Atmosphere: floating steam from warm water, pollen. Motion: warrior sits, "
    "flute across lap. Locked camera. One single continuous shot, no cuts. Do "
    "not reproduce the reference sheets: no side-by-side views, no labels, no "
    "text, no colour-swatch strip, no flat studio backdrop, no duplicated "
    "character. Keep the character and creature identity unchanged."
    " || STRICTLY FORBIDDEN (avoid absolutely): NO smoke, fire; NO distortion of "
    "the face. negative prompt: no head spinning, no melting hands.")


class VeoSafetyTests(unittest.TestCase):
    def test_old_manifest_prompt_is_rebuilt_safe_and_short(self):
        out = vs.safe_clip_prompt(OLD, "cinematic photorealistic live-action look")
        self.assertEqual(vs.risky_terms(out), [])
        self.assertNotIn("negative prompt", out.lower())
        self.assertNotIn("STRICTLY", out)
        self.assertIn(vs.INGREDIENT_LOCK, out)
        self.assertIn("gourd flask", out)
        self.assertIn("warrior", out)          # chủ đề người dùng chọn — giữ
        self.assertIn("sword", out)            # vũ khí giữ theo mô tả (2026-10-05)
        self.assertIn("Atmosphere: pollen", out)
        self.assertIn("rice terrace", out)
        self.assertTrue(out.endswith(vs.SAFE_TAIL))
        self.assertLess(len(out), len(OLD))

    def test_plain_prompt_keeps_meaning(self):
        out = vs.safe_clip_prompt("A calm lake at dawn, gentle ripples.", "")
        self.assertTrue(out.startswith("A calm lake at dawn, gentle ripples."))
        self.assertNotIn("Ingredients 1 and 2", out)

    def test_long_scene_is_capped(self):
        out = vs.safe_clip_prompt("SCENE: " + "Soft light on the water. " * 200, "")
        self.assertLess(len(out), vs.MAX_SCENE_CHARS + len(vs.SAFE_TAIL) + 50)

    def test_style_wrapper_output_is_safe_for_every_style(self):
        with patch.object(config, "load_overrides", return_value={"clip_prompt_mode": "safe"}):
            self._check_safe_wrappers()

    def _check_safe_wrappers(self):
        for key in config.STYLES:
            wrap = fd._style_wrapper(key)
            for raw in (OLD, config.INTRO_MOTION_PROMPT, config.FLOW_MOTION_PROMPT):
                out = wrap(raw)
                self.assertEqual(vs.risky_terms(out), [], (key, out[:120]))
                self.assertLess(len(out), 2000)



class FullPromptModeTests(unittest.TestCase):
    """Mặc định (2026-09-29, người dùng yêu cầu): prompt ĐẦY ĐỦ + STRICTLY FORBIDDEN."""

    def test_default_mode_is_full(self):
        with patch.object(config, "load_overrides", return_value={}):
            self.assertEqual(config.clip_prompt_mode(), "full")
        with patch.object(config, "load_overrides", return_value={"clip_prompt_mode": "x"}):
            self.assertEqual(config.clip_prompt_mode(), "full")

    def test_full_wrapper_keeps_complex_prompt(self):
        style = config.style_info("real").get("motion")
        with patch.object(config, "load_overrides", return_value={}):
            out = fd._style_wrapper("real")(OLD)
        self.assertTrue(out.startswith(style))
        self.assertIn("CHARACTER MODEL SHEETS", out)
        self.assertIn("IDENTITY TO KEEP", out)
        self.assertIn("SCENE: Next to a rice terrace", out)
        self.assertTrue(out.endswith(config.MOTION_NEGATIVE))
        self.assertEqual(out.count("STRICTLY FORBIDDEN"), 1)   # không nhân đôi

    def test_full_wrapper_upgrades_short_lock(self):
        raw = vs.INGREDIENT_LOCK + "\nIDENTITY TO KEEP: turtle\n\nSCENE: lake."
        with patch.object(config, "load_overrides", return_value={}):
            out = fd._style_wrapper("2d")(raw)
        self.assertIn(config.INGREDIENT_LOCK_FULL, out)
        self.assertNotIn(vs.INGREDIENT_LOCK, out)
        self.assertTrue(out.isascii())

    def test_new_manifest_uses_full_lock(self):
        from backend.video import prompt_gen
        out = prompt_gen._with_ingredient_lock("A lake.", "Warrior.", "Turtle.")
        self.assertTrue(out.startswith(config.INGREDIENT_LOCK_FULL))
        self.assertIn("IDENTITY TO KEEP: Warrior. Turtle.", out)


if __name__ == "__main__":
    unittest.main()


class WeaponsFollowDescriptionTests(unittest.TestCase):
    """2026-10-05: người dùng bỏ luật «không vũ khí» — vũ khí tuỳ mô tả."""

    def test_prompts_no_longer_ban_weapons(self):
        from backend.video import batch_ideas, prompt_gen
        texts = [
            prompt_gen._build_user_prompt("warrior with a sword", "T", "k", 4, "16:9", "2d"),
            prompt_gen._motion_system("2d anime"),
            prompt_gen._build_scenes_user_prompt("idea", "2d", "sheet", "pet", 4),
            batch_ideas._SYSTEM,
        ]
        for t in texts:
            self.assertNotIn("UNARMED", t)
            self.assertNotIn("NO weapons", t)
            self.assertNotIn("NEVER a weapon", t)
            self.assertNotRegex(t, r"weapons or injuries|head spinning, weapons")
        self.assertIn("WEAPONS follow the idea/description", texts[0])

    def test_safe_mode_keeps_described_weapon(self):
        out = vs.safe_clip_prompt("A samurai with a katana at his belt sits by a lake.", "")
        self.assertIn("katana", out)
