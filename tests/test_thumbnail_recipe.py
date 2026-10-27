"""Công thức thumbnail riêng từng video (chống dập khuôn) + bộ vẽ chữ nhiều kiểu
+ bố cục ảnh bìa đưa vào prompt + màu/chuyển cảnh video theo phong cách kênh."""
import random
import re
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import Image, ImageChops  # noqa: E402

from backend.video import assembler, channel_looks as L, config, thumbnail  # noqa: E402
from backend.video import thumbnail_recipe as R  # noqa: E402
from backend.video.gemini_driver import _prompt_for  # noqa: E402
from backend.video.prompt_gen import _build_user_prompt  # noqa: E402


class _TmpLedger(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        p = patch.object(R, "LEDGER", Path(self._tmp.name) / "thumbnail_recipes.json")
        p.start()
        self.addCleanup(p.stop)
        self.addCleanup(self._tmp.cleanup)


class RecipeChoiceTests(_TmpLedger):
    def test_channel_never_repeats_a_combo_and_rotates_layouts(self):
        rng = random.Random(7)
        combos, layouts = [], []
        for pid in range(100, 124):
            rec = R.save_recipe(R.choose_recipe(pid, look_key="cozy_lofi", channel="map2",
                                                style_key="lofi", rng=rng))
            combos.append(R._combo(rec))
            layouts.append(rec["layout"])
            self.assertEqual(rec["look"], "cozy_lofi")
        self.assertEqual(len(combos), len(set(combos)), "lặp nguyên tổ hợp trong kênh")
        # Hai video liền nhau không cùng bố cục chữ.
        self.assertTrue(all(a != b for a, b in zip(layouts, layouts[1:])), layouts)
        # Dùng đủ cả 4 bố cục, không dồn hết 1 phía.
        self.assertEqual(set(layouts), set(L.look_info("cozy_lofi")["layouts"]))

    def test_consecutive_videos_differ_on_most_fields(self):
        rng = random.Random(3)
        prev = None
        for pid in range(1, 15):
            rec = R.save_recipe(R.choose_recipe(pid, look_key="ink_zen", channel="map1",
                                                rng=rng))
            if prev:
                same = sum(rec[f] == prev[f] for f in
                           ("layout", "treatment", "palette", "font", "shot", "light"))
                self.assertLessEqual(same, 3, (prev, rec))
            prev = rec

    def test_channels_keep_separate_history(self):
        R.save_recipe(R.choose_recipe(1, look_key="ink_zen", channel="map1"))
        R.save_recipe(R.choose_recipe(2, look_key="cinematic", channel="map3"))
        self.assertEqual([r["project_id"] for r in R.channel_history("map1")], [1])
        self.assertEqual([r["project_id"] for r in R.channel_history("map3")], [2])
        # Chuyển project sang kênh khác → rời lịch sử kênh cũ.
        R.save_recipe(R.choose_recipe(1, look_key="cinematic", channel="map3"))
        self.assertEqual(R.channel_history("map1"), [])
        self.assertEqual(sorted(r["project_id"] for r in R.channel_history("map3")), [1, 2])

    def test_legacy_cover_keeps_left_layout_and_no_directive(self):
        rec = R.choose_recipe(5, look_key="cinematic", channel="map3", legacy=True)
        self.assertEqual(rec["layout"], "left")
        self.assertEqual(rec["shot"], "")
        self.assertEqual(R.composition_directive(rec), "")

    def test_composition_directive_is_english_and_names_layout(self):
        for look in L.LOOK_ORDER:
            for layout in L.look_info(look)["layouts"]:
                rec = R.choose_recipe(9, look_key=look, channel="c",
                                      style_key=L.look_info(look)["styles"][0],
                                      rng=random.Random(1), history=[])
                rec["layout"] = layout
                d = R.composition_directive(rec)
                self.assertTrue(d.startswith(R.COMPOSITION_MARK), d)
                self.assertIn(R.LAYOUT_TEXT[layout], d)
                self.assertIn("no text", d.lower())
                self.assertTrue(d.isascii(), d)   # prompt gửi model: 100% tiếng Anh
                self.assertIn(L.look_info(look)["signature"], d)

    def test_font_lock_and_prompt_recipe_reuse(self):
        m = {**{"id": 2, "instrument": "Piano", "music_style": "Lofi"}, "visual_look": "cozy_lofi"}
        with patch.object(L, "look_for_project", return_value=(m, "cozy_lofi")), \
                patch.object(R, "is_legacy_cover", return_value=False):
            first = R.new_recipe_for_prompts(77, "lofi")
            again = R.new_recipe_for_prompts(77, "3d")
            self.assertEqual(R._combo(first), R._combo(again))   # ổn định qua các lần thử lại
            self.assertEqual(again["style"], "3d")
            locked = R.set_font_lock(77, "bodoni_bold", "P")
            self.assertEqual(locked["font_lock"], "bodoni_bold")
            st = R.render_style(locked)
            self.assertEqual(st["title_fonts"][0], thumbnail.font_entry("bodoni_bold")["file"])
            auto = R.set_font_lock(77, "", "P")
            self.assertEqual(auto["font_lock"], "")
            self.assertEqual(R.render_style(auto)["title_fonts"][0],
                             thumbnail.font_entry(auto["font"])["file"])
            self.assertTrue(R.describe(auto)["font_auto"])


class AiStyleTests(_TmpLedger):
    """2026-10-25: model ảnh TỰ thiết kế chữ — mỗi video 1 kiểu chữ + 1 style riêng."""

    def test_pools_are_english_and_keys_unique(self):
        keys = [e["key"] for e in (*R.LETTERINGS, *R.THUMB_DESIGNS)]
        self.assertEqual(len(keys), len(set(keys)))
        self.assertGreaterEqual(len(R.LETTERINGS), 30)
        self.assertGreaterEqual(len(R.THUMB_DESIGNS), 20)
        for e in (*R.LETTERINGS, *R.THUMB_DESIGNS):
            self.assertTrue(e["text"].isascii(), e["key"])     # prompt gửi model: tiếng Anh
            self.assertTrue(e["vi"], e["key"])
            self.assertTrue(set(e["looks"] or ()) <= set(L.LOOKS), e["key"])
        # Look nào cũng đủ kiểu để không lặp trong nhiều video liền.
        for look in L.LOOKS:
            fit_l = [e for e in R.LETTERINGS if not e["looks"] or look in e["looks"]]
            fit_d = [e for e in R.THUMB_DESIGNS if not e["looks"] or look in e["looks"]]
            self.assertGreaterEqual(len(fit_l), 10, look)
            self.assertGreaterEqual(len(fit_d), 8, look)

    def test_each_video_of_a_channel_gets_its_own_lettering_and_style(self):
        rng = random.Random(11)
        recs = [R.save_recipe(R.choose_recipe(pid, look_key="cinematic", channel="map3",
                                              rng=rng)) for pid in range(200, 208)]
        lets = [r["ai_lettering"] for r in recs]
        dsgs = [r["ai_design"] for r in recs]
        self.assertEqual(len(lets), len(set(lets)), lets)
        self.assertEqual(len(dsgs), len(set(dsgs)), dsgs)
        for r in recs:
            self.assertTrue(R.ai_text(r["ai_lettering"]))
            self.assertTrue(R.ai_text(r["ai_design"]))
        # Kênh khác cũng tránh ~10 kiểu vừa dùng gần nhất của mọi kênh.
        other = R.save_recipe(R.choose_recipe(300, look_key="cinematic", channel="map9",
                                              rng=rng))
        self.assertNotIn(other["ai_lettering"], lets)

    def test_old_recipe_is_back_filled_and_saved(self):
        old = {"project_id": 41, "channel": "map1", "look": "ink_zen", "layout": "left",
               "treatment": "ink_seal", "palette": "jade", "font": "mistral"}
        R.save_recipe(dict(old))
        rec = R.ensure_ai_style(dict(old))
        self.assertIn(rec["ai_lettering"], {e["key"] for e in R.LETTERINGS})
        self.assertIn(rec["ai_design"], {e["key"] for e in R.THUMB_DESIGNS})
        self.assertEqual(R.channel_history("map1")[0]["ai_lettering"], rec["ai_lettering"])
        # Đã có → giữ nguyên (ổn định qua các lần dựng lại).
        self.assertEqual(R.ensure_ai_style(dict(rec))["ai_design"], rec["ai_design"])
        self.assertIsNone(R.ensure_ai_style(None))

    def test_describe_shows_ai_labels_unless_font_locked(self):
        rec = R.choose_recipe(5, look_key="cozy_lofi", channel="map2",
                              rng=random.Random(2), history=[])
        d = R.describe(rec)
        self.assertTrue(d["ai_text"])
        self.assertEqual(d["ai_lettering"], rec["ai_lettering"])
        self.assertEqual(d["ai_lettering_label"], R.ai_label(rec["ai_lettering"]))
        self.assertEqual(d["ai_design_label"], R.ai_label(rec["ai_design"]))
        self.assertFalse(R.describe({**rec, "font_lock": "bodoni_bold"})["ai_text"])


class SubtitleTests(unittest.TestCase):
    def test_hours_label(self):
        self.assertEqual(R.hours_label(None), "")
        self.assertEqual(R.hours_label(0), "")
        self.assertEqual(R.hours_label(45), "45 Minutes")
        self.assertEqual(R.hours_label(60), "1 Hour")
        self.assertEqual(R.hours_label(120), "2 Hours")
        self.assertEqual(R.hours_label(150), "2+ Hours")

    def test_resolve_subtitle_templates(self):
        rec = {"look": "cozy_lofi", "kws": ["Rain", "Focus", "Calm"],
               "subtitle_tpl": "{hours} · {kw1} {instrument}"}
        self.assertEqual(R.resolve_subtitle(rec, "lofi piano", 120), "2 Hours · Rain Lofi Piano")
        # Không biết thời lượng → bỏ mẫu có {hours}.
        self.assertEqual(R.resolve_subtitle(rec, "piano", None), "Rain · Focus · Calm")
        short = {"look": "ink_zen", "kws": ["Zen"], "subtitle_tpl": "{kw1} · {kw2} · {kw3}"}
        self.assertEqual(len(R.resolve_subtitle(short).split(" · ")), 3)

    def test_every_look_template_resolves(self):
        for key in L.LOOK_ORDER:
            lk = L.look_info(key)
            for tpl in lk["subtitles"]:
                rec = {"look": key, "kws": lk["keywords"][:3], "subtitle_tpl": tpl}
                for minutes in (None, 90):
                    out = R.resolve_subtitle(rec, "bamboo flute", minutes)
                    self.assertTrue(out and "{" not in out, (key, tpl, out))


class RendererTests(unittest.TestCase):
    """Mọi bố cục × kiểu chữ vẽ được trên ảnh thường và lớp phủ trong suốt."""

    def _style(self, look, layout, treatment):
        lk = L.look_info(look)
        rec = {"look": look, "layout": layout, "treatment": treatment,
               "palette": sorted(lk["palettes"])[0], "font": lk["fonts"][0],
               "sub_font": lk["sub_fonts"][0], "upper": True,
               "seal": (lk.get("seal_glyphs") or [""])[0]}
        return R.render_style(rec)

    def test_all_layouts_and_treatments_render(self):
        base = Image.new("RGBA", (640, 360), (200, 190, 170, 255))
        looks = list(L.LOOK_ORDER)
        for i, treatment in enumerate(thumbnail.TREATMENTS):
            for layout in thumbnail.LAYOUTS:
                st = self._style(looks[i % len(looks)], layout, treatment)
                out = thumbnail._compose_title(
                    base.copy(), "Autumn Vigil in the Golden Valley", "Calm · Sleep · Focus",
                    st["title_fonts"], st["sub_fonts"], style=st)
                self.assertIsNotNone(out, (layout, treatment))
                self.assertEqual(out.size, base.size)
                self.assertNotEqual(out.tobytes(), base.tobytes(), (layout, treatment))

    def test_text_lands_on_the_chosen_side(self):
        base = Image.new("RGBA", (800, 450), (40, 40, 40, 255))
        for layout in thumbnail.LAYOUTS:
            st = self._style("cinematic", layout, "clean_shadow")
            out = thumbnail._compose_title(base.copy(), "Moonlit Garden Repose", "",
                                           st["title_fonts"], st["sub_fonts"], style=st)
            diff = ImageChops.difference(out.convert("L"), base.convert("L"))
            box = diff.point(lambda v: 255 if v > 60 else 0).getbbox()
            self.assertIsNotNone(box, layout)
            cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
            if layout == "left":
                self.assertLess(cx, 400)
            elif layout == "right":
                self.assertGreater(cx, 400)
            elif layout == "top":
                self.assertLess(cy, 225)
            else:
                self.assertGreater(cy, 225)

    def test_render_title_file_and_transparent_overlay(self):
        with TemporaryDirectory() as tmp:
            img = Path(tmp) / "thumb.png"
            Image.new("RGB", (960, 540), (90, 120, 140)).save(img)
            st = self._style("ink_zen", "right", "ink_seal")
            thumbnail.render_title(img, "Bamboo Rain", "Zen · Calm", style=st)
            with Image.open(img) as im:
                self.assertEqual(im.size, (960, 540))
            for layout in thumbnail.LAYOUTS:
                png = Path(tmp) / f"ov_{layout}.png"
                ok = thumbnail.render_title_overlay_png(
                    640, 360, "Bamboo Rain", "Zen · Calm", png,
                    style=self._style("painterly", layout, "framed"))
                self.assertTrue(ok, layout)
                with Image.open(png) as ov:
                    self.assertEqual(ov.mode, "RGBA")
                    self.assertGreater(ov.getchannel("A").getextrema()[1], 0)
            # Kiểu cũ (không style) vẫn chạy.
            self.assertTrue(thumbnail.render_title_overlay_png(
                640, 360, "Legacy", "a · b", Path(tmp) / "legacy.png"))

    def test_font_preview_uses_recipe_style(self):
        with TemporaryDirectory() as tmp:
            out = Path(tmp) / "p.jpg"
            st = self._style("cozy_lofi", "top", "banner")
            self.assertTrue(thumbnail.render_font_preview(
                "cooper_black", out, "Rainy Cafe", "Lofi · Study", style=st))
            with Image.open(out) as im:
                self.assertLessEqual(max(im.size), 720)
            self.assertFalse(thumbnail.render_font_preview("khong-co", out, "x"))


class PromptDirectiveTests(unittest.TestCase):
    def test_user_prompt_uses_directive_or_legacy_layout(self):
        args = ("idea", "Title", "kw", 4, "16:9", "anime")
        legacy = _build_user_prompt(*args)
        self.assertIn("LEFT HALF", legacy)
        d = "THUMBNAIL COMPOSITION (mandatory): close-up; title area at the top."
        new = _build_user_prompt(*args, thumbnail_directive=d)
        self.assertIn(d, new)
        self.assertNotIn("LEFT HALF", new)
        self.assertIn("following the THUMBNAIL COMPOSITION", new)

    def test_gemini_cover_suffix_respects_composition(self):
        body = "A flute player on a cliff. THUMBNAIL COMPOSITION (mandatory): title area at the top."
        p = _prompt_for(0, "Zen", config.PARAMS, {"0": body, "1": "a", "2": "b"})
        self.assertIn("NO TEXT AT ALL", p)
        self.assertIn("Keep the THUMBNAIL COMPOSITION", p)
        self.assertNotIn("LEFT", p.split("||")[-1])
        old = _prompt_for(0, "Zen", config.PARAMS, {"0": "A flute player.", "1": "a", "2": "b"})
        self.assertIn("NO TEXT AT ALL", old)
        self.assertNotIn("Keep the THUMBNAIL COMPOSITION", old)


class FontPickerApiTests(_TmpLedger):
    """GET/PUT /thumbnail-fonts: font RIÊNG từng project, "auto" = theo kênh."""

    def setUp(self):
        super().setUp()
        from fastapi.testclient import TestClient
        from sqlalchemy.pool import StaticPool
        from sqlmodel import Session, SQLModel, create_engine
        from backend.database import get_session
        from backend.main import app
        from backend.models import Project
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                                    poolclass=StaticPool)
        SQLModel.metadata.create_all(self.engine)
        with Session(self.engine) as s:
            s.add(Project(id=5, name="Rainy Cafe", instrument="Piano", music_style="Lofi",
                          video_style="lofi"))
            s.commit()

        def _session():
            with Session(self.engine) as s:
                yield s
        app.dependency_overrides[get_session] = _session
        self.addCleanup(app.dependency_overrides.clear)
        m = {"id": 2, "instrument": "Piano", "music_style": "Lofi", "visual_look": "cozy_lofi"}
        for target, value in (("backend.video.channel_looks.look_for_project",
                               lambda pid: (m, "cozy_lofi")),
                              ("backend.video.thumbnail_recipe.is_legacy_cover",
                               lambda *a: False),
                              ("backend.video.thumbnail_recipe._project_row",
                               lambda pid: {"instrument": "Piano", "mix_duration_minutes": 120})):
            p = patch(target, value)
            p.start()
            self.addCleanup(p.stop)
        self.client = TestClient(app)       # không `with` → không chạy startup

    def test_auto_then_lock_then_auto(self):
        base = "/api/projects/5/video/thumbnail-fonts"
        r = self.client.get(base)
        self.assertEqual(r.status_code, 200, r.text)
        d = r.json()
        self.assertTrue(d["auto"])
        self.assertEqual(d["recipe"]["look"], "cozy_lofi")
        self.assertIn(d["auto_font"], L.look_info("cozy_lofi")["fonts"])
        self.assertTrue(set(d["suggested"]) <= {f["key"] for f in d["fonts"]})
        self.assertTrue(d["recipe"]["subtitle"])
        r = self.client.put(base, json={"key": "bodoni_bold"})
        self.assertEqual(r.status_code, 200, r.text)
        d = self.client.get(base).json()
        self.assertFalse(d["auto"])
        self.assertEqual(d["selected"], "bodoni_bold")
        self.assertEqual(R.get_recipe(5)["font_lock"], "bodoni_bold")
        self.client.put(base, json={"key": "auto"})
        self.assertTrue(self.client.get(base).json()["auto"])
        self.assertEqual(self.client.put(base, json={"key": "khong-co"}).status_code, 400)
        self.assertEqual(self.client.get("/api/projects/404/video/thumbnail-fonts").status_code,
                         404)


class VideoFinishTests(unittest.TestCase):
    def test_blend_uses_grade_and_transition(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            audio = root / "a.wav"
            audio.touch()
            scripts = []

            def run(cmd):
                if "-filter_complex_script" in cmd:
                    scripts.append(Path(cmd[cmd.index("-filter_complex_script") + 1]).read_text())
                return SimpleNamespace(returncode=0, stderr="")

            # Recipe cũ ghi "fadegrays" → từ 2026-10-25 vẫn ra softfade (êm).
            fin = L.finish_for("ink_zen", {"transition": "fadegrays"})
            self.assertEqual(fin["transition"], "softfade")
            with patch.object(assembler, "_run", side_effect=run), \
                    patch.object(assembler, "probe_duration", return_value=8), \
                    patch.object(assembler, "_mux_visual_list"):
                assembler.assemble_video_blend(
                    [f"c{i}.mp4" for i in range(5)], str(audio), str(root / "f.mp4"),
                    target_seconds=60, blend_seconds=fin["blend_seconds"],
                    slow_speed=fin["slow_speed"], grade=fin["grade"],
                    transition=fin["transition"])
                text = "\n".join(scripts)
                self.assertIn("xfade=transition=custom:expr=A*(P*P*(3-2*P))", text)
                self.assertIn(f"duration={fin['blend_seconds']}:", text)
                self.assertNotIn("xfade=transition=fade:", text)
                self.assertNotIn("hblur", text)
                if fin["grade"]:
                    self.assertIn(fin["grade"].split(",")[0], text)
                    self.assertLess(text.index(fin["grade"].split(",")[0]),
                                    text.index("format=yuv420p"))
                # Kiểu chuyển cảnh lạ → về softfade; không grade → không chèn gì.
                scripts.clear()
                assembler.assemble_video_blend(
                    [f"c{i}.mp4" for i in range(5)], str(audio), str(root / "g.mp4"),
                    target_seconds=60, transition="bogus")
                text = "\n".join(scripts)
                self.assertIn("xfade=transition=custom:expr=", text)
                # Kiểu cũ vẫn chạy được nếu chỉ định rõ; blend quá dài bị rút
                # còn 45% clip ngắn nhất (8s / 0.7 → 5.143s).
                scripts.clear()
                assembler.assemble_video_blend(
                    [f"c{i}.mp4" for i in range(5)], str(audio), str(root / "h.mp4"),
                    target_seconds=60, transition="fade", blend_seconds=9)
                text = "\n".join(scripts)
                self.assertIn("xfade=transition=fade:duration=5.142857", text)
                self.assertIn(f"fps=30,format=yuv420p", text)

    def test_title_overlay_slides_from_layout_side(self):
        captured = []

        def run(cmd):
            captured.append(" ".join(map(str, cmd)))
            return SimpleNamespace(returncode=0, stderr="")

        with TemporaryDirectory() as tmp, \
                patch.object(assembler, "_run", side_effect=run), \
                patch.object(assembler, "probe_duration", return_value=8), \
                patch.object(assembler, "_probe_resolution", return_value=(640, 360)), \
                patch.object(thumbnail, "render_title_overlay_png", return_value=True):
            for layout, expect in (("left", "overlay=x=if("), ("right", "overlay=x=if("),
                                   ("top", "overlay=x=0:y=if("), (None, "overlay=x=0:y=if(")):
                captured.clear()
                st = {"layout": layout} if layout else None
                assembler.overlay_title_on_clip("in.mp4", "T", "S", str(Path(tmp) / "o.mp4"),
                                                style=st)
                joined = "\n".join(captured)
                self.assertIn(expect, joined, layout)
                from_neg = re.search(r"overlay=x=if\(lt\(t\\,[\d.]+\)\\,-", joined)
                self.assertEqual(bool(from_neg), layout == "left", layout)


if __name__ == "__main__":
    unittest.main()
