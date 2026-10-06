"""Khoá nhạc cụ project (piano → nhân vật đánh piano) + form chuẩn Styles Suno.
Không gọi mạng: OpenAI được giả lập."""
from pathlib import Path
import json
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))

from backend.video import music_spec, prompt_gen, suno_prompt, batch_ideas


def _fake_client(*payloads):
    """Client OpenAI giả trả lần lượt các payload JSON."""
    client = MagicMock()
    client.chat.completions.create.side_effect = [
        SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(
            content=json.dumps(p)))]) for p in payloads]
    return client


API = SimpleNamespace(api_key="test", model="gpt-4o-mini",
                      to_client_kwargs=lambda: {})


class MusicSpecTests(unittest.TestCase):
    def test_find_instrument_aliases_and_custom(self):
        self.assertEqual(music_spec.find_instrument("piano")["key"], "piano")
        self.assertEqual(music_spec.find_instrument("Lofi Piano")["key"], "piano")
        self.assertEqual(music_spec.find_instrument("Chinese Bamboo Flute")["key"], "bamboo_flute")
        custom = music_spec.find_instrument("Ocarina")
        self.assertEqual((custom["key"], custom["label"]), ("custom", "Ocarina"))
        self.assertIsNone(music_spec.find_instrument(""))

    def test_same_instrument(self):
        self.assertTrue(music_spec.same_instrument("Piano", "lofi piano"))
        self.assertFalse(music_spec.same_instrument("Piano", "Acoustic Guitar"))
        self.assertTrue(music_spec.same_instrument("Ocarina", "ocarina"))

    def test_genre_profiles(self):
        self.assertEqual(music_spec.find_genre("Lofi Chill")["key"], "lofi")
        self.assertFalse(music_spec.find_genre("Lofi")["beatless"])
        self.assertEqual(music_spec.find_genre("Chinese Zen Music")["key"], "zen")
        self.assertEqual(music_spec.find_genre("")["key"], "meditation")

    def test_mentions_instrument(self):
        spec = music_spec.find_instrument("Piano")
        self.assertTrue(music_spec.mentions_instrument("She plays the grand piano.", spec))
        self.assertFalse(music_spec.mentions_instrument("He strums a guitar.", spec))


class CharacterSheetTests(unittest.TestCase):
    def test_piano_sheet_has_separate_reference_boxes(self):
        spec = music_spec.find_instrument("Piano")
        insets, inst = prompt_gen._split_main_insets(
            ["INSTRUMENT — guitar: wooden six-string", "PROPS — silver laptop",
             "HAIR — chestnut ponytail", "HANDS — slender, fingers relaxed on keyboard or notebook",
             "TOP — lavender sweater"], spec)
        self.assertEqual(inst, "")                 # ô guitar sai bị bỏ
        self.assertEqual(insets, ["HAIR — chestnut ponytail.", "HANDS — slender.",
                                  "TOP — lavender sweater."])
        text = prompt_gen._model_sheet_prompt(
            title_en="MAIN CHARACTER SHEET", subject="an adult pianist", insets=insets,
            palette=["ivory"], scale_note="1.7 m", expressions=["calm"],
            instrument_spec=spec, outfit_items=["TOP — lavender sweater"])
        for label in ("INSTRUMENT REFERENCE", "OUTFIT REFERENCE", "PLAYING POSE",
                      "INSTRUMENT LOCK", "EMPTY-HANDED", "CHARACTER ONLY",
                      "seated on the piano bench", "never holds, lifts or carries"):
            self.assertIn(label, text)
        self.assertNotIn("wooden six-string", text)
        self.assertNotIn("laptop,", text.split("DRAWING RULES")[0])

    def test_portable_instrument_not_in_turnaround(self):
        spec = music_spec.find_instrument("Bamboo Flute")
        boxes, rule = prompt_gen._instrument_sheet_parts(spec)
        self.assertIn("never in the turnaround", rule)
        self.assertNotIn("holds the bamboo flute", rule)
        self.assertTrue(boxes[0].startswith("INSTRUMENT REFERENCE"))

    def test_identity_block_strips_instrument_and_props(self):
        spec = music_spec.find_instrument("Piano")
        raw = ("East Asian adult woman, age 30; oval face, chestnut hair. Outfit: navy "
               "trousers (#223355), lavender sweater (#B7A9D6), round glasses. Instrument: "
               "slim silver upright piano. Props: silver laptop, scattered papers on desk.")
        out = prompt_gen._strip_objects(raw, spec)
        self.assertIn("lavender sweater (#B7A9D6)", out)
        self.assertIn("round glasses", out)
        for bad in ("piano", "laptop", "papers", "desk", "Instrument:", "Props:"):
            self.assertNotIn(bad, out)
        ident = prompt_gen._identity_with_instrument(out, spec)
        self.assertIn("INSTRUMENT (Piano)", ident)
        self.assertIn("never holds, lifts or carries it", ident)

    def test_piano_misuse_detection(self):
        spec = music_spec.find_instrument("Piano")
        bad = ["Her silver piano and laptop are on the desk.",
               "She is holding a small piano in her arms.",
               "He carries the piano across the room.",
               "A tiny piano rests on her lap."]
        good = ["She sits on the bench, fingers pressing the piano keys softly.",
                "She lifts her hands from the piano keys and smiles.",
                "The upright piano stands by the window; a cup sits on the table."]
        for t in bad:
            self.assertTrue(prompt_gen._instrument_misuse(t, spec), t)
        for t in good:
            self.assertFalse(prompt_gen._instrument_misuse(t, spec), t)
        flute = music_spec.find_instrument("Bamboo Flute")
        self.assertFalse(prompt_gen._instrument_misuse("holding the flute", flute))

    def test_companion_mode_by_genre(self):
        self.assertEqual(prompt_gen._companion_mode(json.dumps({"purpose": "Lofi Chill"})), "pet")
        self.assertEqual(prompt_gen._companion_mode(json.dumps({"purpose": "Chinese Zen"})), "auto")
        self.assertEqual(prompt_gen._companion_mode("free text idea"), "auto")
        text = prompt_gen._build_user_prompt("idea", "T", "k", 4, "16:9", "2d",
                                             companion_mode="pet")
        self.assertIn("ordinary real-world PET", text)
        self.assertNotIn("qilin", text)
        auto = prompt_gen._build_user_prompt("idea", "T", "k", 4, "16:9", "2d")
        self.assertIn("prefer a real animal", auto)

    def test_user_prompt_requires_playing_scenes(self):
        spec = music_spec.find_instrument("Piano")
        text = prompt_gen._build_user_prompt('{"instrument": "Piano"}', "T", "k", 20,
                                             "16:9", "2d", instrument_spec=spec)
        self.assertIn("FIXED LEAD INSTRUMENT", text)
        self.assertIn("AT LEAST 12 of the 20", text)
        self.assertIn("The character is playing the piano", text)
        self.assertNotIn("flute holes", text)

    def test_no_instrument_keeps_legacy_prompt(self):
        text = prompt_gen._build_user_prompt("idea", "T", "k", 20, "16:9", "2d")
        self.assertNotIn("FIXED LEAD INSTRUMENT", text)

    def test_scene_coverage(self):
        spec = music_spec.find_instrument("Piano")
        scenes = {"0": "plays the piano", "1": "walks", "2": "piano by the window"}
        self.assertAlmostEqual(prompt_gen._instrument_coverage(scenes, spec), 2 / 3)
        self.assertEqual(prompt_gen._instrument_coverage(scenes, None), 1.0)


def _profile(**over):
    """JSON hồ sơ AI trả cho generate_prompts (project piano lofi, 4 cảnh)."""
    base = {
        "character_bible": "a pianist in a city apartment",
        "character_sheet": ("East Asian adult woman, age 30, oval face, chestnut hair. "
                            "Outfit: navy trousers (#223355), lavender sweater (#B7A9D6). "
                            "Instrument: slim silver upright piano. Props: silver laptop."),
        "main_subject": "Slender, 7 heads tall, hands near her keyboard, calm demeanour.",
        "main_expressions": ["NEUTRAL — calm", "CALM — soft", "DETERMINED — focus", "JOYFUL — smile"],
        "main_insets": ["HAIR — chestnut ponytail", "FACE — almond eyes",
                        "HANDS — slender", "TOP — lavender sweater",
                        "INSTRUMENT — black upright piano, glossy lacquer",
                        "ACCESSORIES — closed silver laptop"],
        "main_outfit": ["TOP — lavender sweater (#B7A9D6)", "TROUSERS — navy (#223355)"],
        "instrument_detail": "A glossy black upright piano with brass pedals.",
        "main_scale": "HEIGHT 1.66 m",
        "pet_kind": "pet", "pet_name": "Mochi",
        "pet_sheet": "A small grey British shorthair cat (#9AA0A6), round amber eyes, 25 cm tall.",
        "pet_subject": "A chubby relaxed cat.",
        "pet_expressions": ["NEUTRAL — relaxed", "CALM — sleepy", "ALERT — ears up", "PLAYFUL — paw"],
        "pet_insets": ["HEAD — round", "EYES — amber", "PAWS — soft", "TAIL — thick"],
        "pet_scale": "SIZE MAP: human 1.66 m versus cat 25 cm",
        "pet_traits": ["CALM — loves naps"],
        "palette": ["#223355 — navy — character", "#B7A9D6 — lavender — character",
                    "#9AA0A6 — grey — companion"],
        "thumbnail": ("She sits on the piano bench by a rainy window playing the upright "
                      "piano, the grey cat curled on the rug, neon city glow."),
        "scenes": [{"setting": f"room {i}", "prompt": f"She plays the piano keys softly, scene {i}."}
                   for i in range(4)],
    }
    base.update(over)
    return base


class GeneratePromptsCompanionTests(unittest.TestCase):
    def _run(self, *payloads, purpose="Lofi"):
        client = _fake_client(*payloads)
        ctx = json.dumps({"title": "Neon Window Study", "instrument": "Piano",
                          "purpose": purpose, "visual_idea": "pianist at night"})
        meta = {}
        with patch("openai.OpenAI", return_value=client), \
                patch.object(prompt_gen, "to_english", side_effect=lambda t, *a, **k: t):
            res = prompt_gen.generate_prompts(ctx, "Neon Window Study", "calm", 4, "16:9",
                                              "2d anime", API, metadata_out=meta,
                                              instrument="Piano")
        return client, res, meta

    def test_lofi_rejects_spirit_companion_then_accepts_pet(self):
        spirit = _profile(pet_kind="spirit creature", pet_name="Lumi",
                          pet_sheet="A cat-sized qilin spirit with pearl horns and a soft glow.")
        client, res, meta = self._run(spirit, _profile())
        self.assertEqual(client.chat.completions.create.call_count, 2)
        prompts, motions = res
        self.assertIn("COMPANION PET SHEET", prompts["2"])
        self.assertEqual(meta["pet_kind"], "pet")
        sheet1 = prompts["1"]
        turnaround = sheet1.split("REQUIRED LAYOUT")[0]
        self.assertNotIn("piano", turnaround.lower())       # SUBJECT không còn đàn
        self.assertNotIn("laptop", sheet1.split("DRAWING RULES")[0])   # chỉ còn trong câu cấm
        self.assertIn("INSTRUMENT REFERENCE", sheet1)
        self.assertIn("glossy black upright piano", sheet1)
        # khoá continuity: khối danh tính có trong ảnh 0, 1 và mọi cảnh
        lock = meta["character_sheet"]
        self.assertIn(lock, prompts["0"])
        self.assertIn(lock, prompts["1"])
        self.assertTrue(all(lock in m and "INSTRUMENT (Piano)" in m for m in motions.values()))

    def test_thumbnail_with_piano_on_desk_is_rewritten(self):
        bad = _profile(thumbnail=("She sits cross-legged by the window. Her silver piano and "
                                  "laptop are on the desk, neon light everywhere."))
        client, res, _ = self._run(bad, _profile())
        self.assertEqual(client.chat.completions.create.call_count, 2)
        self.assertNotIn("piano and laptop are on the desk", res[0]["0"])

    def test_zen_project_may_keep_spirit_creature(self):
        spirit = _profile(pet_kind="spirit creature", pet_name="Lumi",
                          pet_sheet="A small qilin spirit with pearl horns, silver-blue fur.")
        client, res, meta = self._run(spirit, purpose="Chinese Zen")
        self.assertEqual(client.chat.completions.create.call_count, 1)
        self.assertIn("COMPANION CREATURE SHEET", res[0]["2"])
        self.assertEqual(meta["pet_kind"], "spirit creature")


def _form(**over):
    base = {"genre": "Lo-fi chillhop", "mood": "warm, nostalgic, peaceful",
            "lead": "solo felt piano, soft hammers", "support": "soft Rhodes pads, "
            "dusty boom-bap drums, warm sub bass", "bpm": 120, "groove": "laid-back swing",
            "key": "F major", "progression": ["Fmaj7", "Dm9", "Bbmaj7", "C7"],
            "melody": "mid-register phrases with gentle grace notes",
            "structure": "sparse intro, main theme, soft variation, fading outro",
            "mix": "tape saturation, warm low-pass, cozy stereo",
            "exclusions": ["vocals", "piano", "harsh distortion"]}
    base.update(over)
    return base


class SunoFormTests(unittest.TestCase):
    def test_compose_follows_form_and_clamps_bpm(self):
        spec = music_spec.find_instrument("Piano")
        genre = music_spec.find_genre("Lofi")
        styles, excl, problems = suno_prompt.compose_form_styles(_form(), spec, genre)
        self.assertEqual(problems, [])
        self.assertIn("Tempo: 88 BPM", styles)            # 120 kẹp vào 68–88
        self.assertIn("Chord progression: Fmaj7 – Dm9 – Bbmaj7 – C7", styles)
        self.assertTrue(styles.endswith("Purely instrumental, no vocals."))
        self.assertLessEqual(len(styles), suno_prompt.BASE_STYLES_MAX_CHARS)
        terms = [t.strip().lower() for t in excl.split(",")]
        self.assertNotIn("piano", terms)                  # không bao giờ loại lead
        self.assertIn("singing", terms)
        self.assertNotIn("drums", terms)                  # lofi có trống

    def test_lead_without_instrument_is_fixed(self):
        spec = music_spec.find_instrument("Piano")
        genre = music_spec.find_genre("Lofi")
        styles, _, _ = suno_prompt.compose_form_styles(_form(lead="soft lead melody"), spec, genre)
        self.assertIn("Lead: solo acoustic piano", styles)

    def test_beatless_genre_rejects_drums_and_bad_fields(self):
        spec = music_spec.find_instrument("Piano")
        genre = music_spec.find_genre("Deep Sleep")
        _, excl, problems = suno_prompt.compose_form_styles(
            _form(key="happy", progression=["X"]), spec, genre)
        joined = " ".join(problems)
        self.assertIn("BEATLESS", joined)
        self.assertIn("'key'", joined)
        self.assertIn("'progression'", joined)
        self.assertIn("drums", [t.strip() for t in excl.split(",")])

    def test_generate_retries_until_form_valid(self):
        good = _form(support="soft Rhodes pads, dusty boom-bap drums")
        client = _fake_client(_form(key="nice"), good)
        ctx = json.dumps({"title": "Rainy Cafe", "instrument": "Piano", "purpose": "Lofi",
                          "description": "late night study"})
        with patch("openai.OpenAI", return_value=client), \
                patch.object(suno_prompt, "to_english", side_effect=lambda t, *a, **k: t):
            out = suno_prompt.generate_suno_styles(ctx, API, project_title="Rainy Cafe")
        self.assertEqual(client.chat.completions.create.call_count, 2)
        self.assertEqual((out["genre"], out["instrument"]), ("lofi", "Piano"))
        self.assertIn("Lead: solo felt piano", out["styles"])

    def test_plan_uses_genre_bpm_range(self):
        styles = ("Lo-fi, warm. Lead: piano. Tempo: 80 BPM, swing. Key: F major. "
                  "Chord progression: Fmaj7 – Dm9 – Bbmaj7 – C7. Purely instrumental, no vocals.")
        plan = suno_prompt.build_suno_prompt_plan(styles, "vocals", 15, music_style="Lofi")
        bpms = [int(suno_prompt._BPM_RE.search(p["styles"]).group(1)) for p in plan]
        self.assertTrue(all(68 <= b <= 88 for b in bpms))
        self.assertGreater(max(bpms), 72)                 # không bị kẹp dải ngủ
        self.assertNotIn("temple bell", " ".join(p["variation"] for p in plan))


class BatchIdeaTests(unittest.TestCase):
    def test_idea_missing_instrument_is_completed(self):
        spec = music_spec.find_instrument("Piano")
        item = batch_ideas._lock_instrument(
            {"project_name": "A", "video_idea": "A monk by a lake at dawn.",
             "suno_idea": "Calm lofi", "description": "d"}, spec)
        self.assertIn("plays the piano", item["video_idea"])
        self.assertTrue(item["suno_idea"].startswith("Lead: solo acoustic piano"))


class ClassifyRespectsInstrumentTests(unittest.TestCase):
    def test_user_piano_never_switched_to_guitar(self):
        from backend.routers import suno as suno_router
        project = SimpleNamespace(instrument="Piano", music_style="Lofi",
                                  auto_upload=False, auto_video=False)
        rows = [SimpleNamespace(instrument="Guitar", music_style="Lofi",
                                channel_name="Guitar Ch", gpm_profile_id="g"),
                SimpleNamespace(instrument="Lofi Piano", music_style="Lofi",
                                channel_name="Piano Ch", gpm_profile_id="p")]
        db = MagicMock()
        db.exec.return_value.all.return_value = rows
        with patch("backend.video.channel_match.classify_channel",
                   side_effect=lambda t, i, s, pool, api: {**pool[0], "match_confidence": "single"}
                   ) as cc:
            out = suno_router._classify_and_prepare(db, project, "T", "", "", None)
        pool = cc.call_args.args[3]
        self.assertEqual([m["channel_name"] for m in pool], ["Piano Ch"])
        self.assertEqual(out["channel_name"], "Piano Ch")
        self.assertEqual(project.instrument, "Lofi Piano")

    def test_no_channel_for_instrument_keeps_choice(self):
        from backend.routers import suno as suno_router
        project = SimpleNamespace(instrument="Piano", music_style="",
                                  auto_upload=False, auto_video=False)
        db = MagicMock()
        db.exec.return_value.all.return_value = [SimpleNamespace(
            instrument="Guitar", music_style="Lofi", channel_name="G", gpm_profile_id="g")]
        out = suno_router._classify_and_prepare(db, project, "T", "", "", None)
        self.assertFalse(out["matched"])
        self.assertEqual(project.instrument, "Piano")
        self.assertIn("Piano", out["warning"])


class NoPianoLeakTests(unittest.TestCase):
    """2026-10-04: project Guitar + Lofi ra tiếng piano vì màu đệm lofi có
    'soft Rhodes chord pads' (piano điện)."""

    def setUp(self):
        self.guitar = music_spec.find_instrument("Acoustic Guitar")
        self.piano = music_spec.find_instrument("Piano")
        self.lofi = music_spec.find_genre("Lofi")

    def test_plan_for_guitar_has_no_keyboard_accent(self):
        styles = ("Lo-fi, warm. Lead: solo fingerpicked acoustic guitar. Tempo: 76 BPM. "
                  "Key: F major. Purely instrumental, no vocals.")
        for inst in ("Acoustic Guitar", ""):           # "" → đọc từ "Lead:"
            plan = suno_prompt.build_suno_prompt_plan(styles, "vocals", 15,
                                                      music_style="Lofi", instrument=inst)
            text = " ".join(p["styles"] for p in plan).lower()
            self.assertNotIn("rhodes", text)
            self.assertNotIn("piano", text)

    def test_piano_project_keeps_rhodes_accent(self):
        self.assertIn("soft Rhodes chord pads",
                      music_spec.accents_for(self.lofi["accents"], self.piano))

    def test_rules_forbid_other_melodic_instruments(self):
        rules = music_spec.suno_rules(self.guitar, self.lofi)
        self.assertNotIn("Rhodes chord pads", rules)
        self.assertIn("ONLY melodic instrument", rules)

    def test_form_drops_keys_from_support_and_excludes_piano(self):
        data = {"genre": "Lo-fi chillhop", "mood": "warm", "lead": "solo guitar",
                "support": "soft Rhodes chords, vinyl crackle, dusty drums", "bpm": 76,
                "groove": "swing", "key": "F major",
                "progression": ["Fmaj7", "Dm9", "Bbmaj7"], "melody": "stepwise",
                "structure": "intro, theme, outro", "mix": "tape warmth",
                "exclusions": ["vocals"]}
        styles, excl, problems = suno_prompt.compose_form_styles(data, self.guitar, self.lofi)
        self.assertEqual(problems, [])
        self.assertNotIn("Rhodes", styles)
        self.assertIn("piano", excl.lower())
        data["melody"] = "guitar answered by soft piano"
        self.assertTrue(suno_prompt.compose_form_styles(data, self.guitar, self.lofi)[2])

    def test_flute_family_is_own_for_shakuhachi(self):
        spec = music_spec.find_instrument("Shakuhachi")
        self.assertEqual(music_spec.competing_instruments("breathy bamboo flute", spec), [])
        self.assertEqual(music_spec.leak_exclusions(self.piano), [])


if __name__ == "__main__":
    unittest.main()
