import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from backend.video import footwear_safety as fs

SHEET = ("Adult man, 30s, indigo hanfu (#2b3a67), gray trousers (#eaeaea), "
         "no shoes (barefoot). Accessory: bamboo bracelet (#bda263).")


class SanitizeTests(unittest.TestCase):
    def test_identity_phrase(self):
        out = fs.sanitize(SHEET)
        self.assertNotIn("barefoot", out.lower())
        self.assertIn("simple woven straw sandals.", out)

    def test_footwear_inset_line_replaced(self):
        text = ("INSETS:\n   – HANDS — empty, relaxed\n"
                "   – FOOTWEAR — Barefoot, elegant clean feet, slightly calloused soles.\n"
                "• Never crop the character's feet or head in any frame.")
        out = fs.sanitize(text)
        self.assertIn("   – " + fs.FOOTWEAR_INSET + "\n", out)
        self.assertNotIn("soles", out)
        # Câu bố cục chung giữ nguyên.
        self.assertIn("Never crop the character's feet or head", out)
        self.assertIn("HANDS — empty, relaxed", out)

    def test_footwear_inset_with_shoes_untouched(self):
        text = "   – FOOTWEAR — soft black cloth shoes (#111111)"
        self.assertEqual(fs.sanitize(text), text)

    def test_scene_phrases(self):
        cases = {
            "He is barefoot amid tall bamboo": "He is in simple woven straw sandals amid tall bamboo",
            "the character stands barefoot, facing downstream":
                "the character stands in simple woven straw sandals, facing downstream",
            "walking in bare feet on moss": "walking in simple woven straw sandals on moss",
            "bare feet resting on stone": "feet in simple woven straw sandals resting on stone",
        }
        for src, want in cases.items():
            self.assertEqual(fs.sanitize(src), want)

    def test_idempotent(self):
        once = fs.sanitize(SHEET + "\n   – FOOTWEAR — barefoot")
        self.assertEqual(fs.sanitize(once), once)
        self.assertFalse(fs.mentions_bare_feet(once))
        self.assertTrue(fs.mentions_bare_feet(SHEET))

    def test_manifest_keeps_lock_consistent_and_signature_inputs(self):
        saved = {"context": {"visual_idea": "stands barefoot"}, "idea": "stands barefoot",
                 "continuity": {"character_sheet": SHEET, "pet_sheet": "x" * 30},
                 "prompts": {"1": "SHEET\n" + SHEET, "0": "THUMB " + SHEET},
                 "motions": {"0": SHEET + " He is barefoot amid bamboo."}}
        out = fs.sanitize_manifest(saved)
        self.assertIsNot(out, saved)
        lock = out["continuity"]["character_sheet"]
        self.assertIn(lock, out["prompts"]["1"])
        self.assertIn(lock, out["motions"]["0"])
        self.assertEqual(out["context"], saved["context"])
        self.assertEqual(out["idea"], saved["idea"])
        self.assertIs(fs.sanitize_manifest(out), out)


class EnsureManifestTests(unittest.TestCase):
    def _run(self, with_sheet_png: bool):
        from backend.video import prompt_workflow as pw
        with tempfile.TemporaryDirectory() as tmp:
            img = Path(tmp)
            saved = {"prompts": {"1": SHEET}, "motions": {"0": "stands barefoot"},
                     "continuity": {"character_sheet": SHEET}}
            (img / "prompts.json").write_text(json.dumps(saved), encoding="utf-8")
            if with_sheet_png:
                (img / "1.png").write_bytes(b"png")
            with mock.patch.object(pw.config, "images_dir", return_value=img), \
                    mock.patch.object(pw, "validate_manifest", return_value={"h": 1}):
                out = pw._footwear_safe(1, "p", saved)
            on_disk = json.loads((img / "prompts.json").read_text(encoding="utf-8"))
            return out, on_disk, (img / "prompts.barefoot.bak.json").exists()

    def test_rewrites_when_sheet_not_drawn(self):
        out, disk, backup = self._run(False)
        self.assertNotIn("barefoot", json.dumps(out))
        self.assertEqual(disk["motions"]["0"], "stands in simple woven straw sandals")
        self.assertTrue(backup)

    def test_keeps_manifest_when_sheet_already_drawn(self):
        out, disk, backup = self._run(True)
        self.assertIn("barefoot", out["motions"]["0"])
        self.assertFalse(backup)


if __name__ == "__main__":
    unittest.main()
