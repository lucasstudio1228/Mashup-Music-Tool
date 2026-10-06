"""Prompt workflow tests use temporary catalogs and mocked AI/browser only."""
import json
from contextlib import closing
from pathlib import Path
import sqlite3
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))
from backend.video import config, prompt_catalog as catalog, prompt_workflow as workflow
from backend.video import suno_prompt, suno_service
REAL_BOOTSTRAP = catalog.bootstrap_existing


class PromptWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory(prefix="mixer-prompts-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for obj, key, value in ((config, "MEDIA_ROOT", self.root / "media"),
                                (catalog, "CATALOG_PATH", self.root / "catalog.sqlite3"),
                                (catalog, "APP_DB_PATH", self.root / "absent.sqlite3")):
            p = patch.object(obj, key, value)
            p.start()
            self.addCleanup(p.stop)
        p = patch.object(catalog, "bootstrap_existing", return_value={})
        p.start()
        self.addCleanup(p.stop)
        self.context = {"title": "Cedar Rain", "visual_idea": "pianist in a wooden room",
                        "music_idea": "felt piano", "instrument": "piano", "purpose": "meditation",
                        "description": ""}
        p = patch.object(workflow, "project_context", return_value=self.context)
        p.start()
        self.addCleanup(p.stop)
        p = patch("backend.core_bridge.get_api_config_from_db", return_value=SimpleNamespace(api_key="test"))
        p.start()
        self.addCleanup(p.stop)

    SHEET = "Same adult woman, brown bob, cream cardigan."
    PET = "Same small grey tabby cat, white paws, green eyes."

    def fake_generate(self, **kwargs):
        sheet, pet = self.SHEET, self.PET
        kwargs["metadata_out"].update(character_sheet=sheet, pet_sheet=pet,
                                      pet_name="Mun", palette=["#AABBCC"],
                                      settings=[], character_bible="bible")
        prompts = {
            "0": f"{sheet} {pet} Clean cover, negative space left.",
            "1": f"{sheet} MAIN CHARACTER SHEET, FRONT VIEW / SIDE VIEW / BACK VIEW.",
            "2": f"{pet} COMPANION CREATURE SHEET, FRONT VIEW / SIDE VIEW / BACK VIEW.",
        }
        n = kwargs["scene_count"]
        motions = {str(i): f"{sheet} {pet} Scene {i}: a distinct setting."
                   for i in range(n)}
        return prompts, motions

    def test_normalization_cross_project_duplicates_and_atomic_claim(self):
        catalog.claim_prompts(1, "suno", {"0": "Piano   AMBIENT"})
        catalog.claim_prompts(1, "suno", {"0": "piano ambient"})
        with self.assertRaises(catalog.DuplicatePromptError):
            catalog.claim_prompts(2, "suno", {"new": "new prompt", "dup": "Ｐｉａｎｏ ambient"})
        with closing(sqlite3.connect(catalog.CATALOG_PATH)) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM prompt_claim").fetchone()[0], 1)

    def test_briefs_persist_and_differ_on_three_axes(self):
        first = catalog.project_brief(1, "Cedar Rain")
        self.assertEqual(first, catalog.project_brief(1, "Renamed"))
        second = catalog.project_brief(2, "Cedar Rain")
        self.assertGreaterEqual(sum(a != b for a, b in zip(first["axes"], second["axes"])), 3)

    def test_historical_duplicates_resume_but_copied_manifest_cannot_bypass(self):
        for pid in (1, 2):
            path = config.images_dir(pid, "Legacy") / "prompts.json"
            workflow.save_manifest(path, {"prompts": {"0": "legacy shared image"},
                                          "motions": {"0": "legacy shared motion"}})
        REAL_BOOTSTRAP(config.MEDIA_ROOT, catalog.APP_DB_PATH)
        catalog.claim_prompts(1, "gemini", {"0": "legacy shared image"})
        catalog.claim_prompts(2, "gemini", {"0": "legacy shared image"})
        workflow.save_manifest(config.images_dir(3, "Copied") / "prompts.json",
                               {"prompts": {"0": "legacy shared image"}})
        self.assertEqual(REAL_BOOTSTRAP(config.MEDIA_ROOT, catalog.APP_DB_PATH)["imported_sources"], 0)
        with self.assertRaises(catalog.DuplicatePromptError):
            catalog.claim_prompts(3, "gemini", {"0": "legacy shared image"})

    def test_concurrent_claims_cannot_allocate_same_prompt_to_two_projects(self):
        from concurrent.futures import ThreadPoolExecutor
        # Initialize schema before concurrent claim transactions.
        catalog.project_brief(9)
        def claim(pid):
            try:
                catalog.claim_prompts(pid, "flow", {"0": "same atomic motion"})
                return True
            except catalog.DuplicatePromptError:
                return False
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(sorted(pool.map(claim, [1, 2])), [False, True])

    def test_reused_project_id_does_not_inherit_deleted_owner_rights(self):
        with closing(sqlite3.connect(catalog.APP_DB_PATH)) as db:
            db.execute("CREATE TABLE project (id INTEGER PRIMARY KEY, created_at TEXT)")
            db.execute("INSERT INTO project VALUES (1, 'first-incarnation')")
            db.commit()
        catalog.claim_prompts(1, "flow", {"0": "old project motion"})
        with closing(sqlite3.connect(catalog.APP_DB_PATH)) as db:
            db.execute("UPDATE project SET created_at='new-incarnation' WHERE id=1")
            db.commit()
        with self.assertRaises(catalog.DuplicatePromptError):
            catalog.claim_prompts(1, "flow", {"0": "old project motion"})

    def test_prepare_freezes_full_manifest_and_resume_does_not_call_ai(self):
        with patch("backend.video.prompt_gen.generate_prompts", side_effect=self.fake_generate) as ai:
            first = workflow.prepare(1, "Cedar Rain")
            second = workflow.prepare(1, "Cedar Rain")
            self.assertEqual(first, second)
            self.assertEqual(ai.call_count, 1)
            self.assertEqual(len(first["prompts"]), config.PARAMS.image_count)
            self.assertEqual(len(first["motions"]), config.PARAMS.total_clips)
            self.assertIn("no cuts", first["motions"]["0"])
            self.assertTrue((config.images_dir(1, "Cedar Rain") / "prompts.json").exists())

    def test_partial_cache_is_rejected_and_full_restart_can_repair(self):
        with patch("backend.video.prompt_gen.generate_prompts", side_effect=self.fake_generate):
            first = workflow.prepare(1, "Cedar Rain")
            first["prompts"].pop("2")
            workflow.save_manifest(config.images_dir(1, "Cedar Rain") / "prompts.json", first)
            with self.assertRaisesRegex(ValueError, "đủ 3 mục"):
                workflow.prepare(1, "Cedar Rain")
            repaired = workflow.prepare(1, "Cedar Rain", allow_rebuild=True)
            self.assertEqual(len(repaired["prompts"]), config.PARAMS.image_count)

    def test_changed_context_does_not_mix_new_prompts_with_existing_images(self):
        with patch("backend.video.prompt_gen.generate_prompts", side_effect=self.fake_generate) as ai:
            workflow.prepare(1, "Cedar Rain")
            image = config.images_dir(1, "Cedar Rain") / "0.png"
            image.write_bytes(b"existing image")
            self.context["visual_idea"] = "different world"
            with self.assertRaisesRegex(ValueError, "không trộn"):
                workflow.prepare(1, "Cedar Rain")
            self.assertEqual(image.read_bytes(), b"existing image")
            self.assertEqual(ai.call_count, 1)

    def test_missing_motion_or_character_lock_is_not_silently_accepted(self):
        with patch("backend.video.prompt_gen.generate_prompts", side_effect=self.fake_generate):
            saved = workflow.prepare(1, "Cedar Rain")
        keep = saved["motions"]["2"]
        saved["motions"].pop("2")
        with self.assertRaises(ValueError):
            workflow.validate_manifest(1, saved)
        # Cảnh mất khối khoá nhân vật → clip sẽ lệch nhân vật, phải bị chặn.
        saved["motions"]["2"] = "restored motion without any identity lock"
        with self.assertRaisesRegex(ValueError, "character_sheet"):
            workflow.validate_manifest(1, saved)
        # Bảng linh thú (ảnh 2) mất khối khoá pet_sheet cũng bị chặn.
        saved["motions"]["2"] = keep
        saved["prompts"]["2"] = "some disconnected sheet"
        with self.assertRaisesRegex(ValueError, "pet_sheet"):
            workflow.validate_manifest(1, saved)

    def test_suno_plan_unique_under_limit_and_vocal_exclusions(self):
        plan = suno_prompt.build_suno_prompt_plan("Soft felt piano, instrumental.", "drums", 15)
        self.assertEqual(len({p["styles"] for p in plan}), 15)
        for item in plan:
            self.assertLessEqual(len(item["styles"]), 1000)
            self.assertIn("vocals", item["exclusions"])
        with self.assertRaisesRegex(ValueError, "1000"):
            suno_prompt.build_suno_prompt_plan("x" * 1001, "vocals", 15)

    def test_suno_preparation_reuses_frozen_plan(self):
        project = SimpleNamespace(id=1, name="Cedar Rain")
        cfg = suno_service.resolve_batch_config({"styles": "Felt piano meditation, no vocals."})
        prepared = suno_service.prepare_batch_prompts(project, cfg)
        self.assertEqual(len(prepared["prompt_plan"]), cfg["max_create_actions"])
        self.assertEqual(prepared, suno_service.prepare_batch_prompts(project, prepared))

    def test_actual_suno_create_loop_uses_corresponding_prompt(self):
        plan = suno_prompt.build_suno_prompt_plan("Felt piano, instrumental.", "vocals", 3)
        batch = SimpleNamespace(create_actions_used=0)
        driver = Mock()
        driver.read_credits.return_value = 50
        driver.wait_for_new_songs.return_value = []
        selected = [0]
        with patch.object(suno_service, "_touch"), \
                patch.object(suno_service, "_throttle_inflight"), \
                patch.object(suno_service, "_count", side_effect=lambda *a, **k: selected[0]), \
                patch.object(suno_service, "_mark_selection", side_effect=lambda *a: selected.__setitem__(0, selected[0]+1)):
            suno_service._generate_until_target(None, batch,
                {"max_create_actions": 3, "prompt_plan": plan}, driver, 3, Mock(), Mock())
        self.assertEqual(driver.click_create.call_count, 3)
        self.assertEqual([c.args[0] for c in driver.fill_styles.call_args_list], [p["styles"] for p in plan])

    def test_actual_ai_parser_builds_three_sheets_and_locks_every_scene(self):
        from backend.video.prompt_gen import generate_prompts
        payload = {
            "character_bible": "A pianist and her cat in a cedar room.",
            "character_sheet": "The same adult woman wearing a cream cardigan.",
            "main_subject": "Slim adult woman, 6.5 heads tall, relaxed stance.",
            "main_expressions": ["NEUTRAL — calm", "CALM — eyes half shut"],
            "main_insets": ["HAIR DETAIL — bob", "HANDS — long fingers",
                            "CARDIGAN — cream wool", "SHOES — felt slippers"],
            "main_scale": "HEIGHT 1.62 m",
            "pet_name": "Mun",
            "pet_sheet": "The same small grey tabby cat with white paws.",
            "pet_subject": "Small tabby cat, rounded body, upright ears.",
            "pet_expressions": ["NEUTRAL — still", "ALERT — ears up"],
            "pet_insets": ["HEAD — round", "EARS — upright", "PAWS — white",
                           "TAIL — striped"],
            "pet_scale": "SIZE MAP: 1.62 m woman vs 25 cm cat",
            "pet_traits": ["CURIOUS — follows the music"],
            "palette": ["#F0E6D2 — cream", "#6E7F6B — sage", "#3A2E28 — brown"],
            "thumbnail": ("Clean cover art with the woman on the right and her cat, "
                          "wide negative space on the left half, no text at all."),
            "scenes": [{"setting": "rain garden", "prompt": "Rain garden at dusk."},
                       {"setting": "night lake", "prompt": "Still lake under stars."}],
        }
        client = Mock()
        client.chat.completions.create.return_value = SimpleNamespace(choices=[SimpleNamespace(
            message=SimpleNamespace(content=json.dumps(payload)), finish_reason="stop")])
        api = SimpleNamespace(api_key="offline", model="test", to_client_kwargs=lambda: {})
        metadata = {}
        with patch("openai.OpenAI", return_value=client):
            result = generate_prompts("pianist", "Cedar", "Zen", 2, "16:9", "2d", api,
                                      creative_brief="shared signature", metadata_out=metadata)
        self.assertIsNotNone(result)
        prompts, scenes = result
        # ĐÚNG 3 ảnh, mỗi ảnh mang khối khoá của chính nó.
        self.assertEqual(set(prompts), {"0", "1", "2"})
        self.assertIn(payload["character_sheet"], prompts["1"])
        self.assertIn(payload["pet_sheet"], prompts["2"])
        for key in ("0", "1", "2"):
            for lock in ("character_sheet", "pet_sheet"):
                if lock == "character_sheet" and key == "2":
                    continue
                if lock == "pet_sheet" and key == "1":
                    continue
                self.assertIn(payload[lock], prompts[key])
        self.assertIn("FRONT VIEW", prompts["1"])
        self.assertIn("SIZE MAP", prompts["2"])
        # MỌI prompt cảnh mang CẢ HAI khối khoá → 2 clip cùng nhân vật, khác cảnh.
        self.assertEqual(set(scenes), {"0", "1"})
        for value in scenes.values():
            self.assertIn(payload["character_sheet"], value)
            self.assertIn(payload["pet_sheet"], value)
        self.assertEqual(metadata["pet_name"], "Mun")
        self.assertEqual(metadata["settings"], ["rain garden", "night lake"])
        request = client.chat.completions.create.call_args.kwargs["messages"][1]["content"]
        self.assertIn("SHUFFLE-SAFE", request)
        self.assertIn("shared signature", request)

    def test_api_prepare_only_queues_text_planning_and_missing_project_404(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from backend.routers import video
        app = FastAPI()
        app.include_router(video.router)
        session = Mock()
        session.get.return_value = SimpleNamespace(name="Cedar Rain", video_idea="pianist", video_style="2d")
        app.dependency_overrides[video.get_session] = lambda: session
        with TestClient(app) as client, patch.object(video.video_job_manager, "is_busy", return_value=False), \
                patch.object(video.video_job_manager, "submit") as submit:
            response = client.post("/api/projects/1/video/prompts/prepare", json={})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(submit.call_args.args[1], "prompts")
            self.assertEqual(client.get("/api/projects/1/video/prompts").json(), {"manifest": None})
            session.get.return_value = None
            self.assertEqual(client.post("/api/projects/99/video/prompts/prepare", json={}).status_code, 404)


if __name__ == "__main__":
    unittest.main()
