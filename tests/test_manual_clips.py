"""20 clip/video + chế độ "Tạo video thủ công" — không mở trình duyệt, không gọi AI."""
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent))
from backend.video import config, prompt_catalog, prompt_workflow as workflow
from backend.video import flow_driver as fd, flow_manual


def _sig(d: dict) -> str:
    keys = workflow._SIGNATURE_KEYS
    return prompt_catalog.prompt_hash(json.dumps({k: d[k] for k in keys},
                                                 ensure_ascii=False, sort_keys=True))


class TwentyClipTests(unittest.TestCase):
    def test_default_is_twenty_clips(self):
        self.assertEqual(config.PARAMS.total_clips, 20)
        plan = config.generate_ingredient_plan(config.PARAMS.total_clips)
        self.assertEqual(len(plan), 20)
        self.assertIn(config.IMG_THUMBNAIL, plan[0])
        self.assertTrue(all(config.IMG_THUMBNAIL not in ings for ings in plan[1:]))

    def test_fit_scene_count_trims_40_to_20_and_resigns(self):
        saved = {"context": {"title": "t"}, "style": "2d", "count": 3, "scene_count": 40,
                 "style_version": config.STYLE_VERSION, "workflow_version": 2,
                 "motions": {str(i): f"scene {i}" for i in range(40)},
                 "prompt_hashes": {"flow": {str(i): f"h{i}" for i in range(40)}}}
        saved["input_signature"] = _sig(saved)
        out = workflow.fit_scene_count(saved, 20)
        self.assertIsNot(out, saved)
        self.assertEqual(sorted(out["motions"], key=int), [str(i) for i in range(20)])
        self.assertEqual(out["scene_count"], 20)
        self.assertEqual(out["input_signature"], _sig(out))
        self.assertEqual(len(out["prompt_hashes"]["flow"]), 20)
        self.assertEqual(out["trimmed_from_scene_count"], 40)
        self.assertEqual(len(saved["motions"]), 40)          # bản gốc không bị sửa

    def test_fit_scene_count_noop_when_small_or_gappy(self):
        small = {"motions": {str(i): "x" for i in range(20)}}
        self.assertIs(workflow.fit_scene_count(small, 20), small)
        gappy = {"motions": {str(i): "x" for i in range(5, 40)}}
        self.assertIs(workflow.fit_scene_count(gappy, 20), gappy)

    def test_fit_scene_count_keeps_foreign_signature(self):
        saved = {"context": {}, "style": "2d", "count": 3, "scene_count": 40,
                 "style_version": 1, "workflow_version": 2, "input_signature": "other",
                 "motions": {str(i): "x" for i in range(40)}}
        self.assertEqual(workflow.fit_scene_count(saved, 20)["input_signature"], "other")


class ExtendSceneCountTests(unittest.TestCase):
    """Hồ sơ 20 cảnh → cấu hình 40 clip: AI chỉ viết THÊM cảnh 20..39."""
    CH = "Han Chinese man in his thirties, indigo headband, linen tunic"
    PET = "Gigantic golden turtle mascot with a domed shell"

    def _saved(self, n=20, version=2):
        motions = {str(i): f"Lock {self.CH} {self.PET}. SCENE: place {i}." for i in range(n)}
        saved = {"context": {"title": "t"}, "style": "2d", "count": 3, "scene_count": n,
                 "style_version": config.STYLE_VERSION, "workflow_version": version,
                 "style_key": "2d", "continuity": {"character_sheet": self.CH, "pet_sheet": self.PET,
                                                   "settings": ["lotus lake"]},
                 "prompts": {"0": "a", "1": "b", "2": "c"}, "motions": motions}
        saved["input_signature"] = _sig(saved)
        return saved

    def _gen(self, calls):
        def fake(**kw):
            calls.append(kw)
            return {str(j): f"Lock {kw['character_sheet']} {kw['pet_sheet']}. SCENE: new {j}."
                    for j in range(kw["scene_count"])}
        return fake

    def test_extends_20_to_40_keeping_old_scenes(self):
        saved, calls = self._saved(), []
        with patch.object(workflow, "validate_manifest", return_value={"flow": {}}) as vm:
            out = workflow.extend_scene_count(1, saved, 40, api_config=object(),
                                              _generate=self._gen(calls))
        self.assertIsNot(out, saved)
        self.assertEqual(len(out["motions"]), 40)
        self.assertEqual(calls[0]["scene_count"], 20)
        self.assertEqual(calls[0]["avoid"], ["lotus lake"])
        for i in range(20):
            self.assertEqual(out["motions"][str(i)], saved["motions"][str(i)])
        for i in range(20, 40):
            self.assertIn(f"new {i - 20}.", out["motions"][str(i)])
            self.assertTrue(out["motions"][str(i)].endswith(workflow.SCENE_SUFFIX))
            self.assertIn(self.CH, out["motions"][str(i)])
        self.assertEqual(out["scene_count"], 40)
        self.assertEqual(out["input_signature"], _sig(out))
        self.assertEqual(out["extended_from_scene_count"], 20)
        self.assertEqual(vm.call_args[0][3], 40)
        self.assertEqual(len(saved["motions"]), 20)          # bản gốc không bị sửa

    def test_noop_when_enough_or_legacy(self):
        calls = []
        full = self._saved(40)
        self.assertIs(workflow.extend_scene_count(1, full, 40, _generate=self._gen(calls)), full)
        legacy = self._saved(20, version=None)
        self.assertIs(workflow.extend_scene_count(1, legacy, 40, _generate=self._gen(calls)), legacy)
        self.assertEqual(calls, [])

    def test_ai_failure_and_missing_locks_raise(self):
        with self.assertRaises(RuntimeError):
            workflow.extend_scene_count(1, self._saved(), 40, api_config=object(),
                                        _generate=lambda **kw: None)
        bad = self._saved()
        bad["continuity"] = {}
        with self.assertRaises(ValueError):
            workflow.extend_scene_count(1, bad, 40, _generate=self._gen([]))

    def test_ensure_english_manifest_saves_extension(self):
        with TemporaryDirectory(prefix="mixer-extend-test-") as tmp,                 patch.object(config, "MEDIA_ROOT", Path(tmp) / "media"),                 patch.object(config, "PARAMS", config.VideoParams(scene_clips=40)),                 patch.object(workflow, "validate_manifest", return_value={"flow": {}}),                 patch("backend.video.prompt_gen.generate_scene_prompts",
                      side_effect=self._gen([])):
            img = config.images_dir(6, "E")
            img.mkdir(parents=True)
            (img / "prompts.json").write_text(json.dumps(self._saved()), encoding="utf-8")
            out = workflow.ensure_english_manifest(6, "E", api_config=object())
            self.assertEqual(len(out["motions"]), 40)
            disk = json.loads((img / "prompts.json").read_text(encoding="utf-8"))
            self.assertEqual(len(disk["motions"]), 40)
            same = workflow.ensure_english_manifest(6, "E", api_config=object(), extend=False)
            self.assertEqual(len(same["motions"]), 40)


class ReadManifestTrimTests(unittest.TestCase):
    def test_read_manifest_trims_and_keeps_backup(self):
        with TemporaryDirectory(prefix="mixer-trim-test-") as tmp:
            with patch.object(config, "MEDIA_ROOT", Path(tmp) / "media"):
                img = config.images_dir(5, "R")
                img.mkdir(parents=True)
                data = {"scene_count": 40, "motions": {str(i): f"s{i}" for i in range(40)}}
                (img / "prompts.json").write_text(json.dumps(data), encoding="utf-8")
                out = workflow.read_manifest(5, "R")
                self.assertEqual(len(out["motions"]), 20)
                disk = json.loads((img / "prompts.json").read_text(encoding="utf-8"))
                self.assertEqual(len(disk["motions"]), 20)
                bak = json.loads((img / "prompts.40scenes.bak.json").read_text(encoding="utf-8"))
                self.assertEqual(len(bak["motions"]), 40)


class EnglishManifestTests(unittest.TestCase):
    VI = {"Nam trung niên, áo lam": "Middle-aged man, blue robe",
          "Rồng Xanh vảy ngọc": "Azure dragon with jade scales",
          "Hai ảnh nguyên liệu là bảng thiết kế.": "The two ingredients are design sheets.",
          "CẢNH: bờ suối bình minh.": "SCENE: stream bank at dawn.",
          "CẢNH: rừng tre sương.": "SCENE: misty bamboo forest.",
          "ý tưởng": "idea"}

    def fake(self, text, api_config=None, log=None):
        if isinstance(text, dict):
            return {k: self.fake(v) for k, v in text.items()}
        if isinstance(text, list):
            return [self.fake(v) for v in text]
        if not isinstance(text, str):
            return text
        for vi, en in sorted(self.VI.items(), key=lambda kv: -len(kv[0])):
            text = text.replace(vi, en)
        return text

    def test_translates_and_keeps_locks_verbatim(self):
        ch, pet = "Nam trung niên, áo lam", "Rồng Xanh vảy ngọc"
        pre = "Hai ảnh nguyên liệu là bảng thiết kế."
        saved = {"idea": "ý tưởng",
                 "continuity": {"character_sheet": ch, "pet_sheet": pet},
                 "prompts": {"0": f"Cover. {ch} {pet}", "1": f"Sheet {ch}", "2": f"Sheet {pet}"},
                 "motions": {"1": f"{pre}\nID: {ch} {pet}\n\nCẢNH: bờ suối bình minh.",
                             "2": f"{pre}\nID: {ch} {pet}\n\nCẢNH: rừng tre sương."}}
        with patch("backend.video.translate.to_english", side_effect=self.fake), \
             patch.object(workflow, "validate_manifest", return_value={}):
            out = workflow.english_manifest(1, saved)
        en_ch, en_pet = "Middle-aged man, blue robe", "Azure dragon with jade scales"
        self.assertEqual(out["continuity"]["character_sheet"], en_ch)
        self.assertEqual(out["motions"]["1"],
                         f"The two ingredients are design sheets.\nID: {en_ch} {en_pet}"
                         "\n\nSCENE: stream bank at dawn.")
        for v in list(out["motions"].values()) + [out["prompts"]["0"]]:
            self.assertIn(en_ch, v)
        self.assertIn(en_pet, out["prompts"]["2"])
        self.assertEqual(out["idea"], "idea")

    def test_english_manifest_is_untouched(self):
        saved = {"motions": {"1": "A calm river."}, "continuity": {"character_sheet": "Man"}}
        self.assertIs(workflow.english_manifest(1, saved), saved)

    def test_gate_blocks_vietnamese_prompt(self):
        with self.assertRaises(RuntimeError):
            fd.assert_english_prompts([(18, "Nhân vật ngồi bệt trong góc phòng gác mái")])
        fd.assert_english_prompts([(18, "The man sits in a café corner.")])


class ManualModeTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory(prefix="mixer-manual-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        p = patch.object(config, "MEDIA_ROOT", self.root / "media")
        p.start()
        self.addCleanup(p.stop)

    def test_clip_prompt_list_has_all_clips_and_intro_first(self):
        img = config.images_dir(1, "P")
        img.mkdir(parents=True)
        (img / "prompts.json").write_text(json.dumps(
            {"motions": {str(i): f"Scene number {i} by the river." for i in range(20)}}),
            encoding="utf-8")
        clips = config.clips_dir(1, "P")
        clips.mkdir(parents=True)
        (clips / "clip_03.mp4").write_bytes(b"0" * 20_000)
        items = fd.clip_prompt_list(1, "P", "2d")
        self.assertEqual(len(items), config.PARAMS.total_clips)
        self.assertEqual([it["index"] for it in items], list(range(20)))
        self.assertIn(config.IMG_THUMBNAIL, items[0]["ingredients"])
        self.assertIn(config.INTRO_MOTION_PROMPT.strip()[:40], items[0]["prompt"])
        self.assertIn("Scene number 5 by the river.", items[5]["prompt"])
        self.assertTrue(items[3]["done"])
        self.assertFalse(items[4]["done"])

    def test_import_clip_file_validates_and_names(self):
        src = self.root / "downloaded from flow.mp4"
        src.write_bytes(b"0" * 50_000)
        clips = config.clips_dir(2, "Q")
        clips.mkdir(parents=True)
        (clips / "_plan.json").write_text(json.dumps({"fallback": [7, 9], "clips": {}}),
                                          encoding="utf-8")
        with patch("backend.video.assembler.probe_duration", return_value=8.0):
            dest = flow_manual.import_clip_file(2, "Q", 7, src)
        self.assertEqual(dest.name, "clip_07.mp4")
        self.assertEqual(dest.stat().st_size, 50_000)
        plan = json.loads((clips / "_plan.json").read_text(encoding="utf-8"))
        self.assertEqual(plan["fallback"], [9])
        self.assertIn("clip_07.mp4", plan["clips"])
        self.assertFalse(list(clips.glob(".import-*")))

        with self.assertRaises(ValueError):
            flow_manual.import_clip_file(2, "Q", 20, src)            # ngoài 0..19
        tiny = self.root / "tiny.mp4"
        tiny.write_bytes(b"0" * 100)
        with self.assertRaises(ValueError):
            flow_manual.import_clip_file(2, "Q", 1, tiny)
        with patch("backend.video.assembler.probe_duration", return_value=0.2):
            with self.assertRaises(ValueError):
                flow_manual.import_clip_file(2, "Q", 1, src)


if __name__ == "__main__":
    unittest.main()
