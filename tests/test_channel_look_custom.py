"""Phong cách kênh chọn TỰ DO + phong cách TỰ NHẬP (2026-10-26).

Người dùng: "phong cách có thể chọn tự do, cũng có thể nhập thêm" — bỏ ràng buộc
mỗi kênh 1 look khác nhau (409) và cho thêm phong cách riêng (bảng customlook).
Mọi test chạy trên DB sqlite TẠM + sổ thumbnail tạm, không gọi API dịch thật."""
import random
import sqlite3
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi import HTTPException  # noqa: E402
from sqlmodel import Session, SQLModel, create_engine  # noqa: E402

import backend.database as database  # noqa: E402
from backend import migrations  # noqa: E402
from backend.models import ChannelMapping  # noqa: E402
from backend.routers import youtube as yt_router  # noqa: E402
from backend.video import channel_looks as L, config  # noqa: E402
from backend.video import thumbnail_recipe as tr  # noqa: E402

_PATCHES = []
_TMP = None


def setUpModule():
    global _TMP
    _TMP = TemporaryDirectory()
    _PATCHES.append(patch.object(tr, "LEDGER", Path(_TMP.name) / "thumbnail_recipes.json"))
    for p in _PATCHES:
        p.start()


def tearDownModule():
    for p in reversed(_PATCHES):
        p.stop()
    L.invalidate_custom_looks()
    _TMP.cleanup()


class TempDb(unittest.TestCase):
    """DB sqlite tạm có đủ bảng SQLModel (channelmapping, project…) + customlook."""

    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "app.db"
        self.engine = create_engine(f"sqlite:///{self.db_path}")
        SQLModel.metadata.create_all(self.engine)
        with Session(self.engine) as db:
            db.add(ChannelMapping(id=1, instrument="Bamboo Flute", music_style="Zen",
                                  gpm_profile_id="p1", channel_name="Bamboo",
                                  visual_look="ink_zen"))
            db.add(ChannelMapping(id=2, instrument="Piano", music_style="Lofi",
                                  gpm_profile_id="p2", channel_name="Lofi",
                                  visual_look="cozy_lofi"))
            db.commit()
        self._p = patch.object(database, "DB_PATH", self.db_path)
        self._p.start()
        L.invalidate_custom_looks()

    def tearDown(self):
        self._p.stop()
        L.invalidate_custom_looks()
        self.engine.dispose()
        self.tmp.cleanup()

    def set_look(self, mapping_id: int, look: str):
        con = sqlite3.connect(str(self.db_path))
        con.execute("UPDATE channelmapping SET visual_look=? WHERE id=?", (look, mapping_id))
        con.commit()
        con.close()

    def look_of(self, mapping_id: int) -> str:
        con = sqlite3.connect(str(self.db_path))
        row = con.execute("SELECT visual_look FROM channelmapping WHERE id=?",
                          (mapping_id,)).fetchone()
        con.close()
        return row[0]


class CustomLookStoreTests(TempDb):
    def test_no_table_means_no_custom_looks(self):
        self.assertEqual(L.custom_looks(), {})
        self.assertFalse(L.valid_look("custom_deadbeef"))
        self.assertEqual(L.look_info("custom_deadbeef")["label"],
                         L.LOOKS[L.DEFAULT_LOOK]["label"])
        self.assertEqual([c["key"] for c in L.catalog()], list(L.LOOK_ORDER))

    def test_create_resolves_on_top_of_base_look(self):
        styles = [s for s in config.STYLES if L.look_of_style(s) != "cinematic"][:2]
        key = L.create_custom_look("  Đêm   trăng cổ trấn ", "màu chàm, đèn lồng vàng",
                                   base="cinematic", styles=styles, icon="🌙",
                                   signature="indigo night, warm lantern glow")
        self.assertTrue(key.startswith(L.CUSTOM_PREFIX))
        self.assertTrue(L.valid_look(key))
        lk = L.look_info(key)
        base = L.LOOKS["cinematic"]
        self.assertEqual(lk["label"], "Đêm trăng cổ trấn")       # gom khoảng trắng
        self.assertEqual(lk["icon"], "🌙")
        self.assertEqual(lk["styles"], styles)                    # kiểu ảnh của người dùng
        self.assertEqual(lk["signature"], "indigo night, warm lantern glow")
        self.assertEqual(lk["user_desc"], "màu chàm, đèn lồng vàng")
        self.assertEqual(lk["desc"], "màu chàm, đèn lồng vàng")
        self.assertTrue(lk["custom"])
        self.assertEqual(lk["match"], [])                         # không tự gán cho kênh mới
        # Nguyên liệu thumbnail + hoàn thiện video kế thừa look gốc.
        for f in ("fonts", "palettes", "treatments", "layouts", "keywords", "grade",
                  "transitions", "blend_seconds", "slow_speed"):
            self.assertEqual(lk[f], base[f], f)
        self.assertEqual(L.base_look(key), "cinematic")
        self.assertEqual(L.base_look("ink_zen"), "ink_zen")
        self.assertEqual(L.base_look("khong-co"), L.DEFAULT_LOOK)
        self.assertEqual(L.finish_for(key)["grade"], base["grade"])
        cat = L.catalog()
        self.assertEqual([c["key"] for c in cat], [*L.LOOK_ORDER, key])
        ent = cat[-1]
        self.assertTrue(ent["custom"])
        self.assertEqual(ent["base"], "cinematic")
        self.assertEqual([s["key"] for s in ent["styles"]], styles)
        self.assertFalse(any(c["custom"] for c in cat[:-1]))

    def test_empty_styles_and_description_fall_back_to_base(self):
        key = L.create_custom_look("Rừng sương", base="ink_zen")
        lk = L.look_info(key)
        self.assertEqual(lk["styles"], L.LOOKS["ink_zen"]["styles"])
        self.assertEqual(lk["signature"], L.LOOKS["ink_zen"]["signature"])
        self.assertEqual(lk["desc"], L.LOOKS["ink_zen"]["desc"])
        self.assertEqual(lk["user_desc"], "")
        self.assertEqual(lk["icon"], "🎨")

    def test_validation(self):
        bad = [
            dict(label="  "),
            dict(label="x" * (L.MAX_LABEL + 1)),
            dict(label="ok", description="d" * (L.MAX_DESC + 1)),
            dict(label="ok", base="khong-co"),
            dict(label="ok", base="custom_abc"),          # gốc phải là look có sẵn
            dict(label="ok", styles=["khong-co"]),
            dict(label=L.LOOKS["ink_zen"]["label"].upper()),   # trùng tên look có sẵn
        ]
        for kw in bad:
            with self.subTest(kw=kw), self.assertRaises(ValueError):
                L.create_custom_look(**kw)
        L.create_custom_look("Mùa thu")
        with self.assertRaises(ValueError):
            L.create_custom_look("mùa THU")                    # trùng tên tự nhập
        self.assertEqual(len(L.custom_looks()), 1)

    def test_update_keeps_signature_unless_given(self):
        key = L.create_custom_look("Biển đêm", "sóng bạc", base="cinematic",
                                   signature="silver waves at night")
        L.update_custom_look(key, "Biển đêm xanh", "sóng bạc", base="bright", icon="🌊")
        lk = L.look_info(key)
        self.assertEqual(lk["label"], "Biển đêm xanh")
        self.assertEqual(lk["icon"], "🌊")
        self.assertEqual(lk["base"], "bright")
        self.assertEqual(lk["styles"], L.LOOKS["bright"]["styles"])
        self.assertEqual(lk["signature"], "silver waves at night")   # signature=None → giữ
        L.update_custom_look(key, "Biển đêm xanh", "sóng ngọc", base="bright",
                             signature="jade waves")
        self.assertEqual(L.look_info(key)["signature"], "jade waves")
        other = L.create_custom_look("Núi tuyết")
        with self.assertRaises(ValueError):
            L.update_custom_look(other, "biển đêm xanh")       # trùng tên look khác
        L.update_custom_look(other, "Núi tuyết")               # giữ tên chính mình → OK
        with self.assertRaises(KeyError):
            L.update_custom_look("ink_zen", "x")               # look có sẵn không sửa
        with self.assertRaises(KeyError):
            L.update_custom_look("custom_00000000", "x")

    def test_delete_moves_channels_to_base(self):
        key = L.create_custom_look("Hoàng hôn", base="bright")
        self.set_look(1, key)
        self.set_look(2, key)
        self.assertEqual(L.delete_custom_look(key), 2)
        self.assertEqual(self.look_of(1), "bright")
        self.assertEqual(self.look_of(2), "bright")
        self.assertFalse(L.valid_look(key))
        with self.assertRaises(KeyError):
            L.delete_custom_look(key)
        with self.assertRaises(KeyError):
            L.delete_custom_look("ink_zen")

    def test_channel_with_custom_look_is_not_reassigned(self):
        key = L.create_custom_look("Thảo nguyên")
        self.set_look(1, key)
        got = L.ensure_channel_looks()
        self.assertEqual(got[1], key)
        self.assertEqual(self.look_of(1), key)

    def test_style_rotation_uses_custom_styles(self):
        styles = ["3d", "watercolor"] if {"3d", "watercolor"} <= set(config.STYLES) \
            else list(config.STYLES)[:2]
        key = L.create_custom_look("Hai kiểu", styles=styles)
        m = {"id": 1, "visual_look": key}
        self.assertEqual(L.style_for_channel(m, recent=[]), styles[0])
        self.assertEqual(L.style_for_channel(m, recent=[styles[0]]), styles[1])
        self.assertEqual(L.style_for_channel(m, recent=[], offset=1), styles[1])

    def test_realign_keeps_style_of_shared_or_custom_look(self):
        # Kênh 1 dùng look tự nhập có kiểu ảnh thuộc look có sẵn của kênh 2 → giữ.
        lofi_style = L.LOOKS["cozy_lofi"]["styles"][0]
        key = L.create_custom_look("Lofi riêng", styles=[lofi_style])
        m = {"id": 1, "instrument": "Bamboo Flute", "music_style": "Zen", "visual_look": key}
        with patch.object(config, "MEDIA_ROOT", Path(self.tmp.name) / "media"), \
                patch.object(L, "mapping_for", return_value=m):
            self.assertIsNone(L.realign_style(5, "P", lofi_style, "Bamboo Flute", "Zen"))
            # Hai kênh chung 1 look có sẵn → kiểu ảnh của look đó vẫn là của mình.
            m["visual_look"] = "cozy_lofi"
            self.assertIsNone(L.realign_style(5, "P", lofi_style, "Bamboo Flute", "Zen"))

    def test_migration_m017_creates_table_idempotent(self):
        with patch.object(migrations, "DB_PATH", self.db_path):
            migrations.migrate_create_custom_look_table()
            migrations.migrate_create_custom_look_table()       # chạy lại an toàn
        con = sqlite3.connect(str(self.db_path))
        cols = [r[1] for r in con.execute("PRAGMA table_info(customlook)")]
        con.close()
        for c in ("key", "icon", "label", "description", "signature", "base", "styles"):
            self.assertIn(c, cols)
        missing = Path(self.tmp.name) / "khong-co.db"
        with patch.object(migrations, "DB_PATH", missing):
            migrations.migrate_create_custom_look_table()       # DB chưa có → bỏ qua
        self.assertFalse(missing.exists())

    def test_run_all_registers_m017(self):
        import inspect
        self.assertIn("migrate_create_custom_look_table()", inspect.getsource(migrations.run_all))


class FreeChoiceApiTests(TempDb):
    def test_two_channels_share_a_look_via_api(self):
        body = yt_router.MappingBody(gpm_profile_id="p2", channel_name="Lofi",
                                     instrument="Piano", music_style="Lofi",
                                     visual_look="ink_zen")
        with Session(self.engine) as db:
            out = yt_router.update_mapping(2, body, db)
        self.assertEqual(out["visual_look"], "ink_zen")
        with Session(self.engine) as db:
            out = yt_router.create_mapping(yt_router.MappingBody(
                gpm_profile_id="p3", channel_name="Kênh 3", visual_look="ink_zen"), db)
        self.assertEqual(out["visual_look"], "ink_zen")
        with Session(self.engine) as db:
            looks = {c["key"]: c for c in yt_router.list_looks(db)}
        self.assertEqual(looks["ink_zen"]["used_by_ids"], [1, 2, out["id"]])
        self.assertEqual(looks["ink_zen"]["used_by"], "Bamboo, Lofi, Kênh 3")
        self.assertEqual(looks["cozy_lofi"]["used_by_ids"], [])
        self.assertNotIn("used_by_id", looks["ink_zen"])
        with Session(self.engine) as db, self.assertRaises(HTTPException) as cm:
            yt_router.update_mapping(2, yt_router.MappingBody(
                gpm_profile_id="p2", channel_name="Lofi", visual_look="khong-co"), db)
        self.assertEqual(cm.exception.status_code, 400)

    def test_new_channel_without_look_still_gets_a_free_one(self):
        with Session(self.engine) as db:
            out = yt_router.create_mapping(yt_router.MappingBody(
                gpm_profile_id="p3", channel_name="Kênh mới"), db)
        self.assertIn(out["visual_look"], L.LOOKS)
        self.assertNotIn(out["visual_look"], {"ink_zen", "cozy_lofi"})

    def test_create_custom_look_translates_description(self):
        calls = []

        def fake_tr(text, api_config=None, log=None):
            calls.append(text)
            return "misty indigo night with warm lanterns"

        body = yt_router.CustomLookBody(label="Đêm sương", description="đêm chàm có sương",
                                        base="ink_zen", styles=[], icon="🌫")
        with patch("backend.video.translate.to_english_or_none", fake_tr), \
                patch("backend.core_bridge.get_api_config_from_db", return_value=None):
            out = yt_router.create_custom_look(body)
        self.assertEqual(calls, ["đêm chàm có sương"])
        self.assertTrue(out["custom"])
        self.assertEqual(out["user_desc"], "đêm chàm có sương")
        self.assertEqual(L.look_info(out["key"])["signature"],
                         "misty indigo night with warm lanterns")
        # Sửa tên, giữ mô tả → KHÔNG dịch lại.
        body2 = body.model_copy(update={"label": "Đêm sương mới"})
        with patch("backend.video.translate.to_english_or_none", fake_tr), \
                patch("backend.core_bridge.get_api_config_from_db", return_value=None):
            out2 = yt_router.update_custom_look(out["key"], body2)
        self.assertEqual(len(calls), 1)
        self.assertEqual(out2["label"], "Đêm sương mới")
        # Đổi mô tả → dịch lại.
        body3 = body2.model_copy(update={"description": "trăng bạc trên hồ"})
        with patch("backend.video.translate.to_english_or_none",
                   lambda *a, **k: "silver moon over the lake"), \
                patch("backend.core_bridge.get_api_config_from_db", return_value=None):
            yt_router.update_custom_look(out["key"], body3)
        self.assertEqual(L.look_info(out["key"])["signature"], "silver moon over the lake")

    def test_english_description_is_not_translated(self):
        with patch("backend.video.translate.to_english_or_none",
                   side_effect=AssertionError("không được gọi")):
            out = yt_router.create_custom_look(yt_router.CustomLookBody(
                label="Golden hour", description="warm golden haze, soft film grain"))
        self.assertEqual(L.look_info(out["key"])["signature"],
                         "warm golden haze, soft film grain")

    def test_translation_failure_is_502_and_nothing_saved(self):
        with patch("backend.video.translate.to_english_or_none", return_value=None), \
                patch("backend.core_bridge.get_api_config_from_db", return_value=None), \
                self.assertRaises(HTTPException) as cm:
            yt_router.create_custom_look(yt_router.CustomLookBody(
                label="Lỗi dịch", description="mô tả tiếng Việt"))
        self.assertEqual(cm.exception.status_code, 502)
        self.assertEqual(L.custom_looks(), {})

    def test_invalid_body_is_400_before_translating(self):
        with patch("backend.video.translate.to_english_or_none",
                   side_effect=AssertionError("không được gọi")), \
                self.assertRaises(HTTPException) as cm:
            yt_router.create_custom_look(yt_router.CustomLookBody(
                label="", description="mô tả tiếng Việt"))
        self.assertEqual(cm.exception.status_code, 400)

    def test_patch_delete_builtin_is_404(self):
        body = yt_router.CustomLookBody(label="x")
        for fn in (lambda: yt_router.update_custom_look("ink_zen", body),
                   lambda: yt_router.delete_custom_look("ink_zen"),
                   lambda: yt_router.delete_custom_look("custom_00000000")):
            with self.assertRaises(HTTPException) as cm:
                fn()
            self.assertEqual(cm.exception.status_code, 404)

    def test_delete_endpoint_reports_moved_channels(self):
        key = L.create_custom_look("Xoá thử", base="cinematic")
        self.set_look(2, key)
        self.assertEqual(yt_router.delete_custom_look(key), {"ok": True, "moved_channels": 1})
        self.assertEqual(self.look_of(2), "cinematic")

    def test_mapping_out_shows_custom_label(self):
        key = L.create_custom_look("Nhãn riêng", icon="🪷")
        self.set_look(1, key)
        with Session(self.engine) as db:
            out = yt_router._mapping_out(db.get(ChannelMapping, 1))
        self.assertEqual(out["visual_look"], key)
        self.assertEqual(out["look_label"], "🪷 Nhãn riêng")


class CustomLookThumbnailTests(TempDb):
    def test_recipe_uses_custom_signature_and_base_family(self):
        style = L.LOOKS["bright"]["styles"][0]
        key = L.create_custom_look("Thumb riêng", base="cinematic", styles=[style],
                                   signature="pastel dawn haze")
        rec = tr.choose_recipe(7, look_key=key, channel="map1", style_key=style,
                               rng=random.Random(3), history=[])
        self.assertEqual(rec["look"], key)
        base = L.LOOKS["cinematic"]
        self.assertIn(rec["font"], base["fonts"])
        self.assertIn(rec["palette"], base["palettes"])
        d = tr.composition_directive(rec)
        self.assertIn("overall look: pastel dawn haze", d)
        # Hướng chữ AI hợp họ look gốc.
        for field, pool in (("ai_lettering", tr.LETTERINGS), ("ai_design", tr.THUMB_DESIGNS)):
            ent = next(e for e in pool if e["key"] == rec[field])
            self.assertTrue(not ent["looks"] or "cinematic" in ent["looks"], (field, ent))
        # Kiểu ảnh không thuộc phong cách → không chèn câu thẩm mỹ.
        other = next(s for s in config.STYLES if s != style)
        self.assertNotIn("overall look", tr.composition_directive({**rec, "style": other}))

    def test_recipe_for_deleted_custom_look_falls_back(self):
        key = L.create_custom_look("Sẽ xoá")
        L.delete_custom_look(key)
        rec = tr.choose_recipe(8, look_key=key, channel="map1", rng=random.Random(1),
                               history=[])
        self.assertEqual(rec["look"], L.DEFAULT_LOOK)


if __name__ == "__main__":
    unittest.main()
