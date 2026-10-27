"""Phong cách riêng từng kênh (video/channel_looks.py) + API chọn phong cách kênh."""
import sqlite3
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi import HTTPException  # noqa: E402
from sqlmodel import Session, SQLModel, create_engine  # noqa: E402

from backend.models import ChannelMapping  # noqa: E402
from backend.routers import youtube as yt_router  # noqa: E402
from backend.video import assembler, channel_looks as L, config, thumbnail  # noqa: E402

BAMBOO = {"id": 1, "instrument": "Bamboo Flute", "music_style": "Chinese Zen Music",
          "channel_name": "Bamboo Echoes"}
PIANO = {"id": 2, "instrument": "Piano", "music_style": "Lofi",
         "channel_name": "Lucas Healing Music"}
GUITAR = {"id": 3, "instrument": "Guitar", "music_style": "Zen Meditation",
          "channel_name": "Zen Meditation"}


class LookTableTests(unittest.TestCase):
    def test_every_style_belongs_to_at_most_one_look(self):
        seen = {}
        for key, lk in L.LOOKS.items():
            for s in lk["styles"]:
                self.assertIn(s, config.STYLES, f"{key}: style {s} không tồn tại")
                self.assertNotIn(s, seen, f"style {s} thuộc cả {seen.get(s)} và {key}")
                seen[s] = key
                self.assertEqual(L.look_of_style(s), key)

    def test_look_ingredients_are_known(self):
        fonts = {f["key"] for f in thumbnail.FONT_CATALOG}
        for key, lk in L.LOOKS.items():
            self.assertTrue(set(lk["fonts"]) <= fonts, key)
            self.assertTrue(set(lk["sub_fonts"]) <= set(thumbnail.SUB_FONTS), key)
            self.assertTrue(set(lk["treatments"]) <= set(thumbnail.TREATMENTS), key)
            self.assertTrue(set(lk["layouts"]) <= set(thumbnail.LAYOUTS), key)
            self.assertTrue(set(lk["transitions"]) <= set(assembler.XFADE_TRANSITIONS), key)
            self.assertGreaterEqual(len(lk["keywords"]), 3, key)
            self.assertTrue(lk["palettes"], key)

    def test_looks_differ_from_each_other(self):
        # Hai kênh bất kỳ không chung phong cách ảnh, bộ font hay kiểu chữ chính.
        keys = list(L.LOOKS)
        for i, a in enumerate(keys):
            for b in keys[i + 1:]:
                A, B = L.LOOKS[a], L.LOOKS[b]
                self.assertFalse(set(A["styles"]) & set(B["styles"]), (a, b))
                self.assertNotEqual(A["treatments"][0], B["treatments"][0], (a, b))
                self.assertNotEqual(A["fonts"][0], B["fonts"][0], (a, b))

    def test_catalog_lists_all_looks_in_order(self):
        with patch.object(L, "custom_looks", return_value={}):   # không đọc DB thật
            cat = L.catalog()
        self.assertEqual([c["key"] for c in cat], list(L.LOOK_ORDER))
        for c in cat:
            self.assertTrue(c["styles"] and c["transitions"] and c["label"])


class PickLookTests(unittest.TestCase):
    def test_default_channels_get_distinct_fitting_looks(self):
        taken = set()
        got = []
        for m in (BAMBOO, PIANO, GUITAR):
            lk = L.pick_free_look(m, taken)
            taken.add(lk)
            got.append(lk)
        self.assertEqual(got, ["ink_zen", "cozy_lofi", "cinematic"])

    def test_never_reuses_a_taken_look_while_free_ones_remain(self):
        taken = set()
        for i in range(len(L.LOOKS)):
            lk = L.pick_free_look({"instrument": f"x{i}", "music_style": "y"}, taken)
            self.assertNotIn(lk, taken)
            taken.add(lk)
        self.assertEqual(taken, set(L.LOOKS))
        # Hết look trống → vẫn trả 1 look hợp lệ (dùng chung, có cảnh báo).
        self.assertIn(L.pick_free_look(PIANO, taken), L.LOOKS)

    def test_ensure_channel_looks_assigns_distinct_and_is_idempotent(self):
        with TemporaryDirectory() as tmp:
            con = sqlite3.connect(str(Path(tmp) / "t.db"))
            con.row_factory = sqlite3.Row
            con.execute("CREATE TABLE channelmapping (id INTEGER PRIMARY KEY, instrument TEXT,"
                        " music_style TEXT, channel_name TEXT, visual_look TEXT DEFAULT '')")
            for m in (BAMBOO, PIANO, GUITAR):
                con.execute("INSERT INTO channelmapping VALUES (?,?,?,?,'')",
                            (m["id"], m["instrument"], m["music_style"], m["channel_name"]))
            con.commit()
            first = L.ensure_channel_looks(con)
            self.assertEqual(first, {1: "ink_zen", 2: "cozy_lofi", 3: "cinematic"})
            self.assertEqual(L.ensure_channel_looks(con), first)
            con.close()

    def test_ensure_channel_looks_skips_db_without_column(self):
        con = sqlite3.connect(":memory:")
        con.row_factory = sqlite3.Row
        con.execute("CREATE TABLE channelmapping (id INTEGER PRIMARY KEY, instrument TEXT,"
                    " music_style TEXT, channel_name TEXT)")
        con.execute("INSERT INTO channelmapping VALUES (1,'Piano','Lofi','x')")
        self.assertEqual(L.ensure_channel_looks(con), {})
        con.close()


class StyleRotationTests(unittest.TestCase):
    def test_style_for_channel_rotates_and_avoids_previous(self):
        m = {**PIANO, "visual_look": "cozy_lofi"}
        styles = L.look_info("cozy_lofi")["styles"]
        self.assertEqual(L.style_for_channel(m, recent=[]), styles[0])
        for i, last in enumerate(styles):
            nxt = L.style_for_channel(m, recent=[last, "real"])
            self.assertEqual(nxt, styles[(i + 1) % len(styles)])
            if len(styles) > 1:
                self.assertNotEqual(nxt, last)
        # Lô N project: offset xoay tiếp trong cặp phong cách của kênh.
        batch = [L.style_for_channel(m, recent=[], offset=i) for i in range(4)]
        self.assertTrue(set(batch) <= set(styles))
        self.assertEqual(batch[0], styles[0])
        self.assertEqual(batch[1], styles[1 % len(styles)])

    def test_resolve_video_style(self):
        self.assertEqual(L.resolve_video_style("3d", "Piano", "Lofi"), "3d")
        with patch.object(L, "mapping_for", return_value=None):
            self.assertEqual(L.resolve_video_style("channel", "Oud", "?"),
                             config.DEFAULT_STYLE)
            self.assertEqual(L.resolve_video_style("", "Oud", "?"), config.DEFAULT_STYLE)
        m = {**BAMBOO, "visual_look": "ink_zen"}
        with patch.object(L, "mapping_for", return_value=m), \
                patch.object(L, "recent_channel_styles", return_value=[]):
            self.assertIn(L.resolve_video_style("channel", "Bamboo Flute", "Zen"),
                          L.look_info("ink_zen")["styles"])

    def test_realign_style_only_when_no_images_and_other_channel_style(self):
        m = {**BAMBOO, "visual_look": "ink_zen"}
        with TemporaryDirectory() as tmp, \
                patch.object(config, "MEDIA_ROOT", Path(tmp)), \
                patch.object(L, "mapping_for", return_value=m), \
                patch.object(L, "other_channel_looks", return_value={"cozy_lofi", "cinematic"}), \
                patch.object(L, "recent_channel_styles", return_value=[]):
            cin_style = L.look_info("cinematic")["styles"][0]
            # Phong cách của kênh khác → chuyển sang phong cách kênh này.
            new = L.realign_style(99, "P", cin_style, "Bamboo Flute", "Chinese Zen Music")
            self.assertIn(new, L.look_info("ink_zen")["styles"])
            # Đã đúng phong cách kênh → giữ.
            own = L.look_info("ink_zen")["styles"][0]
            self.assertIsNone(L.realign_style(99, "P", own, "Bamboo Flute", "x"))
            # Phong cách không thuộc kênh nào khác (người dùng chọn tay) → giữ.
            free = next(s for s in config.STYLES
                        if L.look_of_style(s) not in {"ink_zen", "cozy_lofi", "cinematic"})
            self.assertIsNone(L.realign_style(99, "P", free, "Bamboo Flute", "x"))
            # Đã có ảnh → không đổi nữa.
            img = config.images_dir(99, "P")
            img.mkdir(parents=True, exist_ok=True)
            (img / "0.png").write_bytes(b"x")
            self.assertIsNone(L.realign_style(99, "P", cin_style, "Bamboo Flute", "x"))
        with patch.object(L, "mapping_for", return_value=None):
            self.assertIsNone(L.realign_style(99, "P", "3d", "Oud", "x"))

    def test_finish_for(self):
        for key, lk in L.LOOKS.items():
            f = L.finish_for(key, {"transition": "nope"})
            self.assertEqual(f["transition"], lk["transitions"][0])
            self.assertIsInstance(f["blend_seconds"], float)
            self.assertGreater(f["slow_speed"], 0)
            last = lk["transitions"][-1]
            self.assertEqual(L.finish_for(key, {"transition": last})["transition"], last)
        # Chuyển cảnh ÊM, đủ dài cho nhạc chill (người dùng chê hblur/trượt ngang
        # "gấp quá" 2026-10-25).
        for key, lk in L.LOOKS.items():
            self.assertEqual(lk["transitions"], ["softfade"], key)
            self.assertGreaterEqual(lk["blend_seconds"], 2.5, key)
        # Khác kênh → khác cảm giác video (màu hoặc tốc độ/chuyển cảnh).
        fins = {k: L.finish_for(k) for k in L.LOOKS}
        sigs = {(f["grade"], f["transition"], f["slow_speed"]) for f in fins.values()}
        self.assertEqual(len(sigs), len(L.LOOKS))


class MappingLookApiTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://")
        SQLModel.metadata.create_all(self.engine)
        with Session(self.engine) as db:
            db.add(ChannelMapping(id=1, instrument="Bamboo Flute", music_style="Zen",
                                  gpm_profile_id="p1", channel_name="Bamboo",
                                  visual_look="ink_zen"))
            db.add(ChannelMapping(id=2, instrument="Piano", music_style="Lofi",
                                  gpm_profile_id="p2", channel_name="Lofi",
                                  visual_look="cozy_lofi"))
            db.commit()

    def test_same_look_for_two_channels_is_allowed(self):
        # Từ 2026-10-26 chọn phong cách TỰ DO — kênh khác dùng rồi vẫn chọn được.
        self.assertEqual(yt_router._check_look("ink_zen"), "ink_zen")
        self.assertEqual(yt_router._check_look(" cinematic "), "cinematic")
        with self.assertRaises(HTTPException) as cm:
            yt_router._check_look("khong-co")
        self.assertEqual(cm.exception.status_code, 400)

    def test_mapping_out_includes_look(self):
        with Session(self.engine) as db:
            out = yt_router._mapping_out(db.get(ChannelMapping, 1))
        self.assertEqual(out["visual_look"], "ink_zen")
        self.assertIn(L.look_info("ink_zen")["label"], out["look_label"])


if __name__ == "__main__":
    unittest.main()
