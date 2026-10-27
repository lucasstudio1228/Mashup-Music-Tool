"""Intro thumbnail động (intro_mode) + chế độ chỉ cảnh vật (cast_mode) — 2026-10-26.

Người dùng: "Mặc định sẽ là tạo ảnh thumbnail và dùng ảnh thumbnail để tạo video
động giới thiệu từ 00:00. Font chữ có sẵn sẽ chỉ là option" + "thêm lựa chọn
không cần nhân vật, chỉ có cảnh vật … mặc định vẫn là nhân vật + linh thú".
Chỉ dùng thư mục/DB tạm + mock — không gọi AI/trình duyệt, không đụng :8000."""
import json
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pydantic import ValidationError
from sqlmodel import Session, SQLModel, create_engine

from backend.video import config
from backend.video import scenery_prompts as SP
from backend.video import thumbnail_recipe as _thumb_recipe

_LEDGER_DIR = None
_PATCHES = []


def setUpModule():
    # Sổ công thức thumbnail → file tạm; không bao giờ gọi API ảnh thật.
    global _LEDGER_DIR
    from backend.video import thumbnail_ai as _tai
    _LEDGER_DIR = TemporaryDirectory()
    _PATCHES.append(patch.object(_thumb_recipe, "LEDGER",
                                 Path(_LEDGER_DIR.name) / "thumbnail_recipes.json"))
    _PATCHES.append(patch.object(_tai, "load_settings",
                                 lambda *a, **k: _tai.ThumbAISettings(engine="pil")))
    for p in _PATCHES:
        p.start()


def tearDownModule():
    for p in reversed(_PATCHES):
        p.stop()
    if _LEDGER_DIR:
        _LEDGER_DIR.cleanup()


WORLD = ("A quiet bamboo grove after rain: tall jade stalks, a mossy stone path, a "
         "small wooden footbridge over a clear stream, paper lanterns on low posts.")
MOOD = ("Soft misty morning light, pale green and silver palette, gentle haze, dew "
        "drops glistening, slow drifting fog between the stalks.")


def scenery_payload(n: int, living: bool = False) -> dict:
    scene = "Slow push along the mossy stone path as mist drifts between bamboo stalks"
    scenes = [f"{scene}, angle {i}, dew drops falling softly from the leaves." for i in range(n)]
    if living:
        scenes[1] = scenes[1] + " A monk walks across the bridge."
    return {
        "world_title": "Bamboo Grove",
        "world_sheet": WORLD + (" A heron stands in the stream." if living else ""),
        "world_bible": "Bamboo grove bible.",
        "mood_sheet": MOOD,
        "thumbnail": "Wide view of the footbridge over the stream framed by bamboo, "
                     "calm empty sky on the left.",
        "world_views": ["Wide view of the grove", "Close view of the footbridge"],
        "world_insets": ["Moss texture", "Lantern detail", "Stream pebbles"],
        "mood_frames": ["Dawn mist", "Noon haze"],
        "mood_insets": ["Dew drop", "Light shafts"],
        "palette": ["#9FBF8F jade", "#DDE5D8 mist", "#6B7F5A moss"],
        "scenes": scenes,
    }


# ── Cấu hình + nhận diện ────────────────────────────────────────────
class ConfigModeTests(unittest.TestCase):
    def test_normalize_defaults(self):
        self.assertEqual(config.normalize_cast_mode(None), "character_pet")
        self.assertEqual(config.normalize_cast_mode(" SCENERY "), "scenery")
        self.assertEqual(config.normalize_cast_mode("weird"), "character_pet")
        self.assertEqual(config.normalize_intro_mode(None), "thumbnail")
        self.assertEqual(config.normalize_intro_mode("font"), "font")
        self.assertEqual(config.normalize_intro_mode("x"), "thumbnail")

    def test_mode_aware_texts(self):
        self.assertEqual(config.intro_motion_prompt("scenery"),
                         config.INTRO_MOTION_PROMPT_SCENERY)
        self.assertNotEqual(config.intro_motion_prompt("character_pet"),
                            config.INTRO_MOTION_PROMPT_SCENERY)
        self.assertEqual(config.image_roles("scenery"), config.IMAGE_ROLES_SCENERY)
        scen = config.SCENERY_MARK + " — scene"
        self.assertEqual(config.motion_negative(scen), config.MOTION_NEGATIVE_SCENERY)
        self.assertNotEqual(config.motion_negative("A woman plays piano"),
                            config.MOTION_NEGATIVE_SCENERY)
        self.assertEqual(config.thumbnail_ref_note(scen), config.THUMBNAIL_REF_NOTE_SCENERY)

    def test_images_cast_mode_reads_manifest(self):
        with TemporaryDirectory() as d:
            self.assertEqual(config.images_cast_mode(d), "character_pet")   # chưa có hồ sơ
            (Path(d) / "prompts.json").write_text(json.dumps({"cast_mode": "scenery"}),
                                                  encoding="utf-8")
            self.assertEqual(config.images_cast_mode(d), "scenery")
            (Path(d) / "prompts.json").write_text("{bad", encoding="utf-8")
            self.assertEqual(config.images_cast_mode(d), "character_pet")


# ── Sinh prompt chỉ cảnh vật ────────────────────────────────────────
class SceneryPromptTests(unittest.TestCase):
    def test_living_detection_ignores_negations_and_fireflies(self):
        self.assertEqual(SP.living_hits("A heron wades while she watches."), ["heron", "she"])
        self.assertEqual(SP.living_hits("No people, no animals; fireflies glow."), [])
        self.assertEqual(SP.drop_living_sentences("Mist drifts. A deer drinks. Leaves sway."),
                         "Mist drifts. Leaves sway.")

    def _run(self, payloads, n=4):
        api = SimpleNamespace(api_key="k", model="gpt-4.1")
        meta: dict = {}
        with patch.object(SP, "_client_and_model", return_value=(object(), "gpt-4.1")), \
                patch.object(SP, "to_english", side_effect=lambda t, *a, **k: t), \
                patch("backend.video.prompt_gen._resolve_instrument", return_value=None), \
                patch.object(SP, "_ask", side_effect=[(p, "stop") for p in payloads]) as ask:
            out = SP.generate_prompts("bamboo grove after rain", "Bamboo Grove", "", n,
                                      "16:9", "2D illustration", api, metadata_out=meta)
        return out, meta, ask

    def test_living_hit_is_rewritten_then_clean_result_used(self):
        (prompts, motions), meta, ask = self._run(
            [scenery_payload(4, living=True), scenery_payload(4)])
        self.assertEqual(ask.call_count, 2)
        self.assertEqual(set(prompts), {"0", "1", "2"})
        self.assertEqual(len(motions), 4)
        for text in [*prompts.values(), *motions.values()]:
            self.assertIn(config.SCENERY_MARK, text)
        self.assertTrue(prompts["1"].startswith(config.SCENERY_MARK))
        self.assertEqual(meta["cast_mode"], "scenery")
        self.assertEqual((meta["character_sheet"], meta["pet_sheet"]), (WORLD, MOOD))
        self.assertEqual(meta["pet_kind"], "none")

    def test_last_attempt_drops_living_sentences(self):
        from backend.video.prompt_gen import _MAX_ATTEMPTS
        (prompts, motions), meta, _ = self._run(
            [scenery_payload(4, living=True)] * _MAX_ATTEMPTS)
        self.assertNotIn("heron", meta["character_sheet"].lower())
        self.assertFalse(any("monk" in m.lower() for m in motions.values()))

    def test_prompt_gen_delegates_only_for_scenery(self):
        from backend.video import prompt_gen
        with patch.object(SP, "generate_prompts", return_value=("P", "M")) as sp:
            out = prompt_gen.generate_prompts(
                idea="x", title="t", keywords="", scene_count=3, aspect_ratio="16:9",
                style="2d", api_config=None, cast_mode="scenery")
        self.assertEqual(out, ("P", "M"))
        sp.assert_called_once()
        with patch.object(SP, "generate_scene_prompts", return_value={"0": "s"}) as sp2:
            out = prompt_gen.generate_scene_prompts(
                character_sheet=WORLD, pet_sheet=MOOD, idea="x", style="2d",
                api_config=None, scene_count=1, cast_mode="scenery")
        self.assertEqual(out, {"0": "s"})
        self.assertEqual(sp2.call_args.args[:2], (WORLD, MOOD))


# ── Workflow: chuẩn bị hồ sơ ────────────────────────────────────────
class SceneryWorkflowTests(unittest.TestCase):
    def setUp(self):
        from backend.video import prompt_catalog as catalog, prompt_workflow as workflow
        self.workflow = workflow
        tmp = TemporaryDirectory(prefix="mixer-scenery-test-")
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        for obj, key, value in ((config, "MEDIA_ROOT", root / "media"),
                                (catalog, "CATALOG_PATH", root / "catalog.sqlite3"),
                                (catalog, "APP_DB_PATH", root / "absent.sqlite3")):
            p = patch.object(obj, key, value)
            p.start()
            self.addCleanup(p.stop)
        for target, kw in ((catalog, {"bootstrap_existing": {}}),):
            for name, rv in kw.items():
                p = patch.object(target, name, return_value=rv)
                p.start()
                self.addCleanup(p.stop)
        self.context = {"title": "Bamboo Grove", "visual_idea": "bamboo grove after rain",
                        "music_idea": "flute", "instrument": "", "purpose": "meditation",
                        "description": "", "cast_mode": "scenery"}
        p = patch.object(workflow, "project_context", return_value=self.context)
        p.start()
        self.addCleanup(p.stop)
        p = patch("backend.core_bridge.get_api_config_from_db",
                  return_value=SimpleNamespace(api_key="test"))
        p.start()
        self.addCleanup(p.stop)
        self.calls = []

    def fake_generate(self, **kw):
        self.calls.append(kw)
        n = kw["scene_count"]
        data = scenery_payload(n)
        prompts = {
            "1": SP.world_sheet_prompt(title_en="BAMBOO GROVE", world=WORLD,
                                       views=data["world_views"], insets=data["world_insets"],
                                       palette=data["palette"], aspect_ratio="16:9"),
            "2": SP.mood_sheet_prompt(world=WORLD, mood=MOOD, frames=data["mood_frames"],
                                      insets=data["mood_insets"], palette=data["palette"],
                                      aspect_ratio="16:9"),
            "0": SP.thumbnail_prompt(WORLD, MOOD, data["thumbnail"]),
        }
        kw["metadata_out"].update(character_sheet=WORLD, pet_sheet=MOOD, pet_name="",
                                  pet_kind="none", palette=data["palette"],
                                  cast_mode="scenery", settings=[], character_bible="b")
        return prompts, SP.scene_dict(data["scenes"], n, WORLD, MOOD)

    def test_prepare_scenery_manifest(self):
        with patch("backend.video.prompt_gen.generate_prompts",
                   side_effect=self.fake_generate):
            saved = self.workflow.prepare(1, "Bamboo Grove")
        self.assertEqual(self.calls[0]["cast_mode"], "scenery")
        self.assertIn("no people, no animals", self.calls[0]["thumbnail_directive"])
        self.assertEqual(saved["cast_mode"], "scenery")
        self.assertEqual(self.workflow.manifest_cast_mode(saved), "scenery")
        self.assertEqual(len(saved["motions"]), config.PARAMS.total_clips)
        for m in saved["motions"].values():
            self.assertTrue(m.endswith(self.workflow.SCENE_SUFFIX_SCENERY))
        img_dir = config.images_dir(1, "Bamboo Grove")
        self.assertEqual(config.images_cast_mode(img_dir), "scenery")

    def test_default_mode_does_not_pass_cast_mode(self):
        self.assertEqual(self.workflow.scene_suffix("character_pet"), self.workflow.SCENE_SUFFIX)
        self.assertEqual(self.workflow.manifest_cast_mode({}), "character_pet")
        self.context.pop("cast_mode")
        seen = {}

        def gen(**kw):
            seen.update(kw)
            raise RuntimeError("stop here")
        with patch("backend.video.prompt_gen.generate_prompts", side_effect=gen):
            with self.assertRaises(Exception):
                self.workflow.prepare(2, "Cedar Rain")
        self.assertNotIn("cast_mode", seen)
        self.assertNotIn("no people, no animals", seen.get("thumbnail_directive", ""))


# ── Bọc prompt clip + bản an toàn ───────────────────────────────────
class SceneryClipPromptTests(unittest.TestCase):
    def test_flow_wrapper_uses_scenery_negative(self):
        from backend.video import flow_driver
        with patch.object(config, "clip_prompt_mode", return_value="full"):
            wrap = flow_driver._style_wrapper("2d")
        scen = SP.with_world_lock("Slow pan over the stream.", WORLD, MOOD)
        out = wrap(scen)
        self.assertTrue(out.endswith(config.MOTION_NEGATIVE_SCENERY.strip()) or
                        config.MOTION_NEGATIVE_SCENERY.strip() in out)
        self.assertEqual(flow_driver.motion_base("scenery"), config.FLOW_MOTION_PROMPT_SCENERY)

    def test_safe_prompt_keeps_place_lock(self):
        from backend.video import veo_safety
        scen = SP.with_world_lock("Slow pan over the stream.", WORLD, MOOD)
        safe = veo_safety.safe_clip_prompt(scen, "")
        self.assertIn(veo_safety.SCENERY_LOCK, safe)
        self.assertIn("Place: ", safe)
        self.assertTrue(safe.endswith(veo_safety.SAFE_TAIL_SCENERY))
        self.assertNotIn("Identity: ", safe)


# ── Thumbnail theo chế độ ───────────────────────────────────────────
class SceneryThumbnailTests(unittest.TestCase):
    REC = {"look": None, "shot": "medium", "layout": "left", "light": "golden"}

    def test_composition_directive_scenery(self):
        normal = _thumb_recipe.composition_directive(self.REC)
        scen = _thumb_recipe.composition_directive(self.REC, scenery=True)
        self.assertIn("characters", normal)
        self.assertNotIn("characters", scen)
        self.assertIn("no people, no animals", scen)
        self.assertIn("focal landmark", scen)
        for key in (s["key"] for s in _thumb_recipe.SHOTS):
            self.assertIn(key, _thumb_recipe.SCENERY_SHOT_TEXT)

    def test_build_prompt_and_cache_key(self):
        from backend.video import thumbnail_ai as T
        normal = T.build_prompt("Bamboo", "3 Hours", dict(self.REC))
        scen = T.build_prompt("Bamboo", "3 Hours", {**self.REC, "scenery": True},
                              native_wide=False)
        self.assertIn("characters' faces", normal)
        self.assertNotIn("character", scen.lower())
        self.assertIn("Do not add any people, animals", scen)
        with TemporaryDirectory() as d:
            cover = Path(d) / "0.png"
            cover.write_bytes(b"x")
            k1 = T.cache_key("B", "S", dict(self.REC), "m", cover)
            k2 = T.cache_key("B", "S", {**self.REC, "scenery": False}, "m", cover)
            k3 = T.cache_key("B", "S", {**self.REC, "scenery": True}, "m", cover)
        self.assertEqual(k1, k2)          # khoá cũ không đổi → ảnh đã vẽ vẫn dùng lại
        self.assertNotEqual(k1, k3)

    def test_render_thumbnail_flags_scenery_recipe(self):
        from backend.video import chatgpt_thumbnail, service
        with TemporaryDirectory() as d, patch.object(config, "MEDIA_ROOT", Path(d)):
            img = config.images_dir(7, "Bamboo")
            img.mkdir(parents=True, exist_ok=True)
            (img / "0.png").write_bytes(b"png")
            (img / "prompts.json").write_text(json.dumps({"cast_mode": "scenery"}),
                                              encoding="utf-8")
            with patch.object(chatgpt_thumbnail, "generate") as gen:
                out = service.render_thumbnail(7, "Bamboo", "3 Hours", style={},
                                               recipe={"layout": "left"}, engine="chatgpt")
            self.assertTrue(out.endswith("thumbnail.png"))
            self.assertTrue(gen.call_args.kwargs["recipe"]["scenery"])
            (img / "prompts.json").unlink()
            with patch.object(chatgpt_thumbnail, "generate") as gen:
                service.render_thumbnail(7, "Bamboo", "3 Hours", style={},
                                         recipe={"layout": "left"}, engine="chatgpt")
            self.assertNotIn("scenery", gen.call_args.kwargs["recipe"])


# ── Ghép video: intro thumbnail mặc định, font là tuỳ chọn ──────────
class StepAssembleIntroTests(unittest.TestCase):
    def _run(self, intro_mode, thumb_ok=True, cast=None):
        from backend.video import service
        d = TemporaryDirectory()
        self.addCleanup(d.cleanup)
        with patch.object(config, "MEDIA_ROOT", Path(d.name)):
            if cast:
                img = config.images_dir(3, "Bamboo")
                img.mkdir(parents=True, exist_ok=True)
                (img / "prompts.json").write_text(json.dumps({"cast_mode": cast}),
                                                  encoding="utf-8")
            clip_dir = config.clips_dir(3, "Bamboo")
            clip_dir.mkdir(parents=True, exist_ok=True)
            for i in range(config.PARAMS.total_clips):
                (clip_dir / f"clip_{i:02d}.mp4").write_bytes(b"v")
            thumb = config.final_dir(3, "Bamboo") / "thumbnail.png"
            thumb.parent.mkdir(parents=True, exist_ok=True)
            if thumb_ok:
                thumb.write_bytes(b"png")
            finish = {"transition": "softfade", "blend_seconds": 2.8,
                      "slow_speed": 0.7, "grade": ""}
            with patch.object(service, "_project_intro_mode", return_value=intro_mode), \
                    patch.object(service, "_title_look", return_value=({}, "3 Hours", finish, None)), \
                    patch.object(service, "probe_duration", return_value=600.0), \
                    patch.object(service, "render_thumbnail",
                                 return_value=str(thumb) if thumb_ok else None) as rt, \
                    patch.object(service, "overlay_title_on_clip", return_value=True) as ov, \
                    patch.object(service, "assemble_video_blend", return_value={"ok": 1}) as asm:
                service.step_assemble(3, lambda m, p: None, audio_path="a.m4a",
                                      project_name="Bamboo")
        return rt, ov, asm, thumb

    def test_thumbnail_intro_is_default(self):
        rt, ov, asm, thumb = self._run("thumbnail")
        rt.assert_called_once()
        ov.assert_not_called()
        kw = asm.call_args.kwargs
        self.assertEqual(kw["lead_image"], str(thumb))
        self.assertEqual(kw["lead_seconds"], config.INTRO_THUMBNAIL_SECONDS)
        self.assertTrue(asm.call_args.args[0][0].endswith("clip_00.mp4"))

    def test_font_option_overlays_title(self):
        rt, ov, asm, _ = self._run("font")
        rt.assert_called_once()           # thumbnail vẫn được tạo (để đăng YouTube)
        ov.assert_called_once()
        self.assertIsNone(asm.call_args.kwargs["lead_image"])
        self.assertTrue(asm.call_args.args[0][0].endswith("_intro_titled.mp4"))

    def test_missing_thumbnail_falls_back_to_font(self):
        _, ov, asm, _ = self._run("thumbnail", thumb_ok=False)
        ov.assert_called_once()
        self.assertIsNone(asm.call_args.kwargs["lead_image"])

    def test_scenery_plays_at_real_speed(self):
        # Chỉ cảnh vật: không làm chậm 0.7x (cảnh trông như đứng hình).
        _, _, asm, _ = self._run("thumbnail", cast="scenery")
        self.assertEqual(asm.call_args.kwargs["slow_speed"], config.SCENERY_PLAYBACK_SPEED)
        self.assertEqual(config.SCENERY_PLAYBACK_SPEED, 1.0)
        _, _, asm, _ = self._run("thumbnail", cast="character_pet")
        self.assertEqual(asm.call_args.kwargs["slow_speed"], 0.7)


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "cần ffmpeg")
class AssemblerLeadImageTests(unittest.TestCase):
    def test_video_opens_on_thumbnail_then_clips(self):
        from backend.video import assembler as A
        from PIL import Image
        with TemporaryDirectory() as d:
            d = Path(d)
            clips = []
            for i, c in enumerate(["red", "green", "blue", "yellow"]):
                p = d / f"clip_{i:02d}.mp4"
                subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i",
                                f"color=c={c}:s=320x180:r=12:d=4", "-pix_fmt", "yuv420p",
                                str(p)], capture_output=True, check=True)
                clips.append(str(p))
            thumb = d / "thumb.png"
            Image.new("RGB", (1280, 720), (250, 0, 250)).save(thumb)   # tím → dễ nhận
            audio = d / "a.m4a"
            subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "sine=f=440:d=30",
                            "-c:a", "aac", str(audio)], capture_output=True, check=True)
            out = d / "out.mp4"
            res = A.assemble_video_blend(clips, str(audio), str(out), target_seconds=30,
                                         blend_seconds=1.0, slow_speed=0.7, width=320,
                                         height=180, fps=12, lead_image=str(thumb),
                                         lead_seconds=4.0)
            self.assertEqual(res["lead_image"], str(thumb))
            self.assertAlmostEqual(A.probe_duration(str(out)), 30.0, delta=0.6)
            f0, f9 = d / "f0.png", d / "f9.png"
            for ts, f in ((0.0, f0), (9.0, f9)):
                subprocess.run(["ffmpeg", "-y", "-ss", str(ts), "-i", str(out),
                                "-frames:v", "1", str(f)], capture_output=True, check=True)
            r, g, b = Image.open(f0).convert("RGB").resize((1, 1)).getpixel((0, 0))
            self.assertGreater(r, 180)
            self.assertGreater(b, 180)
            self.assertLess(g, 80)                       # 00:00 = thumbnail tím
            r, g, b = Image.open(f9).convert("RGB").resize((1, 1)).getpixel((0, 0))
            self.assertFalse(r > 180 and b > 180 and g < 80)   # đã sang clip


# ── DB / API / lô ───────────────────────────────────────────────────
class ProjectFieldsTests(unittest.TestCase):
    def test_migration_m016(self):
        from backend import migrations
        with TemporaryDirectory() as d:
            db = Path(d) / "app.db"
            con = sqlite3.connect(str(db))
            con.execute("CREATE TABLE project (id INTEGER PRIMARY KEY, name TEXT)")
            con.execute("INSERT INTO project (id, name) VALUES (1, 'old')")
            con.commit()
            con.close()
            with patch.object(migrations, "DB_PATH", db):
                migrations.migrate_add_project_intro_cast()
                migrations.migrate_add_project_intro_cast()      # chạy lại an toàn
            con = sqlite3.connect(str(db))
            row = con.execute("SELECT intro_mode, cast_mode FROM project").fetchone()
            con.close()
        self.assertEqual(row, ("thumbnail", "character_pet"))   # project cũ = mặc định

    def test_schemas(self):
        from backend import schemas
        c = schemas.ProjectCreate(name="A")
        self.assertEqual((c.intro_mode, c.cast_mode), ("thumbnail", "character_pet"))
        with self.assertRaises(ValidationError):
            schemas.ProjectCreate(name="A", cast_mode="robots")
        b = schemas.BatchCreate(count=1, instrument="Piano", music_style="Lofi",
                                cast_mode="scenery", intro_mode="font")
        self.assertEqual((b.intro_mode, b.cast_mode), ("font", "scenery"))
        self.assertIsNone(schemas.ProjectUpdate().cast_mode)

    def test_router_update_and_response(self):
        from backend import schemas
        from backend.models import Project
        from backend.routers import projects as r
        with TemporaryDirectory() as d:
            engine = create_engine(f"sqlite:///{Path(d) / 't.sqlite'}")
            SQLModel.metadata.create_all(engine)
            try:
                with Session(engine) as s:
                    p = Project(id=1, name="A")
                    s.add(p)
                    s.commit()
                    out = r.to_response(s, p)
                    self.assertEqual((out.intro_mode, out.cast_mode),
                                     ("thumbnail", "character_pet"))
                    r.update_project(1, schemas.ProjectUpdate(cast_mode="scenery",
                                                              intro_mode="font"), session=s)
                    r.update_project(1, schemas.ProjectUpdate(cast_mode=None), session=s)
                    s.refresh(p)
                    self.assertEqual((p.intro_mode, p.cast_mode), ("font", "scenery"))
            finally:
                engine.dispose()

    def test_batch_passes_modes(self):
        from backend import batch_service as bs
        ideas = [{"project_name": "X", "description": "", "video_idea": "",
                  "suno_idea": ""}]
        with patch("backend.core_bridge.get_api_config_from_db", return_value={}), \
                patch("backend.video.batch_ideas.generate_batch_ideas",
                      return_value=ideas) as gen, \
                patch("backend.video.channel_looks.resolve_video_style", return_value="2d"), \
                patch.object(bs, "_create_batch_rows", return_value={"ok": 1}) as rows:
            bs.create_batch(1, "Piano", "Lofi", "2d", intro_mode="font", cast_mode="scenery")
        self.assertEqual(gen.call_args.kwargs["cast_mode"], "scenery")
        self.assertEqual(rows.call_args.kwargs["cast_mode"], "scenery")
        self.assertEqual(rows.call_args.kwargs["intro_mode"], "font")


class BatchIdeasSceneryTests(unittest.TestCase):
    def _gen(self, items, cast_mode):
        from backend.video import batch_ideas as B
        resp = SimpleNamespace(choices=[SimpleNamespace(
            message=SimpleNamespace(content=json.dumps({"items": items})))])
        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(
            create=lambda **kw: (self.sent.append(kw), resp)[1])))
        api = SimpleNamespace(api_key="k", model="gpt-4.1", to_client_kwargs=lambda: {})
        self.sent = []
        with patch("openai.OpenAI", return_value=client), \
                patch.object(B, "to_english", side_effect=lambda t, *a, **k: t):
            return B.generate_batch_ideas(len(items), "Piano", "2d", "", api,
                                          music_style="Lofi", cast_mode=cast_mode)

    def test_scenery_ideas_have_no_player_or_animals(self):
        items = [
            {"project_name": "Bamboo Rain", "video_idea": "A bamboo grove after rain with a "
             "mossy path and a footbridge. A fox sleeps by the stream.",
             "suno_idea": "Soft piano lead, lofi", "description": "Relax."},
            {"project_name": "Snow Temple", "video_idea": "A quiet mountain temple in falling "
             "snow, lanterns glowing on the steps at dusk.",
             "suno_idea": "Gentle lofi beat", "description": "Sleep."},
        ]
        out = self._gen(items, "scenery")
        self.assertIn("SCENERY-ONLY SERIES", self.sent[0]["messages"][0]["content"])
        self.assertIn("EMPTY, peaceful place", self.sent[0]["messages"][1]["content"])
        self.assertNotIn("fox", out[0]["video_idea"].lower())
        for it in out:
            self.assertNotIn("main character", it["video_idea"].lower())
            self.assertIn("piano", it["suno_idea"].lower())      # nhạc vẫn khoá nhạc cụ

    def test_default_ideas_lock_player(self):
        items = [{"project_name": "Cedar Room", "video_idea": "A cosy cedar room at dusk "
                  "with rain on the window.", "suno_idea": "Lofi", "description": "Relax."}]
        out = self._gen(items, "character_pet")
        self.assertNotIn("SCENERY-ONLY", self.sent[0]["messages"][0]["content"])
        self.assertIn("main character plays the piano", out[0]["video_idea"].lower())


# ── 2026-10-27: cảnh vật ĐỘNG (không khoá máy / siêu chậm như ảnh tĩnh) ──
_STATIC = ("locked camera", "camera is locked", "extremely slow", "ambient motion only",
           "no zoom, pan or orbit", "very slow tempo")


class SceneryDynamicMotionTests(unittest.TestCase):
    OLD = ("A misty bamboo grove at dawn. AMBIENT MOTION ONLY: ripples and swaying "
           "leaves. Camera: locked camera, or an EXTREMELY slow push-in. Very slow "
           "tempo, slow motion, seamless loop.")

    def assertDynamic(self, text):
        low = text.lower()
        for bad in _STATIC:
            self.assertNotIn(bad, low)

    def test_generation_prompts_ask_for_living_motion_and_camera_moves(self):
        texts = {
            "user": SP.build_user_prompt("bamboo grove", "Bamboo", "Calm", 6, "16:9", "2d"),
            "scenes": SP.build_scenes_user_prompt("bamboo grove", "2d", WORLD, MOOD, 6),
            "scenes_system": SP._scenes_system("2D hand-drawn"),
            "system": SP._system("2D hand-drawn"),
        }
        for name, t in texts.items():
            with self.subTest(name):
                self.assertDynamic(t)
                self.assertIn("never a still image", t.lower())
        for name in ("user", "scenes", "scenes_system"):
            low = texts[name].lower()
            for move in ("dolly", "tracking", "crane", "aerial drift"):
                self.assertIn(move, low)
        self.assertIn("LIVING MOTION", texts["user"])
        self.assertIn("CAMERA MOVE", texts["user"])

    def test_config_scenery_motion_texts_are_dynamic(self):
        for t in (config.INTRO_MOTION_PROMPT_SCENERY, config.FLOW_MOTION_PROMPT_SCENERY,
                  config.SCENERY_LIVING_MOTION):
            self.assertDynamic(t)
            self.assertIn("never a still image", t.lower())
        self.assertIn("no frozen frame", config.MOTION_NEGATIVE_SCENERY)
        # Chế độ nhân vật giữ nguyên như cũ.
        self.assertIn("locked camera", config.FLOW_MOTION_PROMPT)

    def test_liven_rewrites_old_static_wording(self):
        out = config.liven_scenery_motion(self.OLD)
        self.assertDynamic(out)
        self.assertNotIn("slow motion", out.lower())
        self.assertIn("a smooth push-in", out)
        self.assertEqual(config.liven_scenery_motion(
            "the camera is locked or moves extremely slowly"), "the camera moves smoothly")
        L = config.liven_scenery_motion
        self.assertEqual(L("Still view of the altar."), "A gliding shot of the altar.")
        self.assertEqual(L("Meditative tempo, locked shot."), "Meditative tempo, a gliding shot.")
        self.assertEqual(L("Camera: static shot."), "Camera: one smooth cinematic move.")
        # Không đụng: khoá nơi chốn, vật đứng yên, câu chuyển động/phủ định của tool.
        for keep in ("World locked: still water mirrors the lanterns, the handpan "
                     "remains still.", config.SCENERY_LIVING_MOTION,
                     config.MOTION_NEGATIVE_SCENERY, config.FLOW_MOTION_PROMPT_SCENERY,
                     config.INTRO_MOTION_PROMPT_SCENERY):
            self.assertEqual(L(keep), keep)

    def test_flow_wrapper_livens_old_scenery_manifest(self):
        from backend.video import flow_driver
        with patch.object(config, "clip_prompt_mode", return_value="full"):
            wrap = flow_driver._style_wrapper("2d")
        out = wrap(SP.with_world_lock(self.OLD, WORLD, MOOD))
        self.assertDynamic(out.split("|| STRICTLY FORBIDDEN")[0])
        self.assertEqual(out.count(config.SCENERY_LIVING_MOTION), 1)
        self.assertLess(out.index(config.SCENERY_LIVING_MOTION),
                        out.index("|| STRICTLY FORBIDDEN"))
        self.assertTrue(out.endswith(config.MOTION_NEGATIVE_SCENERY.strip()))
        self.assertEqual(wrap(out), out)                      # bọc lại không nhân đôi
        # Prompt nhân vật: không đụng tới.
        char = wrap("A girl plays the flute, locked camera.")
        self.assertIn("locked camera", char)
        self.assertNotIn(config.SCENERY_LIVING_MOTION, char)

    def test_safe_mode_livens_scenery(self):
        from backend.video import veo_safety
        safe = veo_safety.safe_clip_prompt(SP.with_world_lock(self.OLD, WORLD, MOOD), "")
        self.assertDynamic(safe)
        self.assertTrue(safe.endswith(veo_safety.SAFE_TAIL_SCENERY))
        self.assertIn("never a still image", veo_safety.SAFE_TAIL_SCENERY)

    def test_fallback_variants_move_the_camera(self):
        from backend.video import flow_driver
        scen = flow_driver._fallback_variants(flow_driver.motion_base("scenery"))
        self.assertEqual(len(set(scen)), len(scen))
        self.assertTrue(all(v.startswith(config.FLOW_MOTION_PROMPT_SCENERY) for v in scen))
        self.assertTrue(all(len(v) > len(config.FLOW_MOTION_PROMPT_SCENERY) for v in scen))
        char = flow_driver._fallback_variants("base")
        self.assertEqual(char[0], "base")
        self.assertIn("very slow pan to the right", char[1])

    def test_clip_list_for_scenery_project_is_dynamic(self):
        from backend.video import flow_driver
        n = config.PARAMS.total_clips
        with TemporaryDirectory() as d, patch.object(config, "MEDIA_ROOT", Path(d)), \
                patch.object(config, "clip_prompt_mode", return_value="full"):
            img = config.images_dir(9, "Bamboo")
            img.mkdir(parents=True, exist_ok=True)
            motions = {str(i): SP.with_world_lock(self.OLD, WORLD, MOOD) for i in range(1, n)}
            (img / "prompts.json").write_text(json.dumps(
                {"cast_mode": "scenery", "motions": motions}), encoding="utf-8")
            items = flow_driver.clip_prompt_list(9, "Bamboo", "2d")
        self.assertEqual(len(items), n)
        for it in items:
            body = it["prompt"].split("|| STRICTLY FORBIDDEN")[0]
            self.assertDynamic(body)
            self.assertIn(config.SCENERY_LIVING_MOTION, it["prompt"])

    def test_batch_scenery_ideas_ask_for_motion(self):
        from backend.video import batch_ideas
        rules = batch_ideas._system("scenery")
        self.assertIn("never a still picture", rules)
        self.assertNotIn("gentle ambient motion", rules)


class RegenMotionsSceneryTests(unittest.TestCase):
    def test_regen_keeps_scenery_mode_and_suffix(self):
        from backend.video import prompt_workflow as W, service
        saved = {"prompts": {"0": "a", "1": "b", "2": "c"}, "motions": {},
                 "continuity": {"character_sheet": WORLD, "pet_sheet": MOOD},
                 "cast_mode": "scenery", "idea": "bamboo grove", "context": {}}
        n = config.PARAMS.total_clips
        scenes = {str(i): f"scene {i}" for i in range(n)}
        with TemporaryDirectory() as d, patch.object(config, "MEDIA_ROOT", Path(d)):
            img = config.images_dir(5, "Bamboo")
            img.mkdir(parents=True, exist_ok=True)
            (img / "prompts.json").write_text("{}", encoding="utf-8")
            with patch.object(W, "ensure_english_manifest", return_value=saved), \
                    patch("backend.core_bridge.get_api_config_from_db", return_value=None), \
                    patch("backend.video.prompt_gen.generate_scene_prompts",
                          return_value=scenes) as gen, \
                    patch.object(W, "validate_manifest", return_value={}), \
                    patch.object(W, "save_manifest") as save, \
                    patch.object(service, "_project_instrument", return_value=""):
                out = service.regen_motions(5, lambda m, p: None, "Bamboo")
        self.assertEqual(out, {"count": n})
        self.assertEqual(gen.call_args.kwargs["cast_mode"], "scenery")
        written = save.call_args.args[1]["motions"]
        self.assertTrue(all(v.endswith(W.SCENE_SUFFIX_SCENERY) for v in written.values()))


if __name__ == "__main__":
    unittest.main()
