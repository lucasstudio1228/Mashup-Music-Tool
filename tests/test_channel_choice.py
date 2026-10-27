"""Kênh YouTube do người dùng CHỌN (project.channel_id) — kênh nào cũng nhận nhạc
cụ / thể loại nào, không cần khớp dòng ánh xạ (2026-10-25). Project cũ (chưa
chọn kênh) vẫn tự khớp theo (nhạc cụ, thể loại). Chỉ dùng DB tạm + mock."""
from pathlib import Path
import sqlite3
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi import HTTPException
from sqlmodel import Session, SQLModel, create_engine

from backend.models import ChannelMapping, Project
from backend.video import service

ROWS = [
    {"id": 1, "instrument": "Lofi Piano", "music_style": "Lofi", "channel_name": "Piano Ch"},
    {"id": 2, "instrument": "Guitar", "music_style": "Lofi", "channel_name": "Guitar Ch"},
    {"id": 3, "instrument": "guitar", "music_style": "lofi", "channel_name": "Guitar New"},
]


class MatchChannelTests(unittest.TestCase):
    def test_exact_case_insensitive_newest_wins(self):
        self.assertEqual(service.match_channel(ROWS, " GUITAR ", "LoFi")["id"], 3)

    def test_same_instrument_same_style(self):
        self.assertEqual(service.match_channel(ROWS, "Piano", "Lofi")["id"], 1)

    def test_no_match_or_missing_fields(self):
        self.assertIsNone(service.match_channel(ROWS, "Shakuhachi", "Chinese Zen Music"))
        self.assertIsNone(service.match_channel(ROWS, "", "Lofi"))
        self.assertIsNone(service.match_channel(ROWS, "Guitar", ""))


class ChannelForTests(unittest.TestCase):
    def test_chosen_channel_any_instrument(self):
        chosen = {"id": 2, "channel_name": "Guitar Ch", "instrument": "Guitar"}
        with patch.object(service, "_lookup_channel_by_id", return_value=chosen) as by_id, \
                patch.object(service, "_lookup_channel_mapping") as by_pair:
            m = service.channel_for(2, "Shakuhachi", "Chinese Zen Music")
        self.assertEqual(m, chosen)
        by_id.assert_called_once_with(2)
        by_pair.assert_not_called()

    def test_chosen_channel_without_instrument(self):
        with patch.object(service, "_lookup_channel_by_id", return_value={"id": 5}):
            self.assertEqual(service.channel_for(5, "", "")["id"], 5)

    def test_not_chosen_falls_back_to_pair(self):
        with patch.object(service, "_lookup_channel_by_id", return_value=None), \
                patch.object(service, "_lookup_channel_mapping",
                             return_value={"id": 1}) as by_pair:
            self.assertEqual(service.channel_for(None, "Piano", "Lofi")["id"], 1)
            by_pair.assert_called_once_with("Piano", "Lofi")
            by_pair.reset_mock()
            self.assertIsNone(service.channel_for(None, "Piano", ""))
            by_pair.assert_not_called()

    def test_no_channel_error_points_to_picker(self):
        msg = service._no_channel_error("", "", "Short")
        self.assertIn("chưa chọn kênh", msg)
        self.assertIn("Short", msg)
        self.assertNotIn("ánh xạ", msg)
        self.assertIn("ánh xạ", service._no_channel_error("Piano", "Lofi"))


class LookByChosenChannelTests(unittest.TestCase):
    def test_mapping_for_prefers_chosen_channel(self):
        from backend.video import channel_looks
        chosen = {"id": 2, "channel_name": "Guitar Ch", "instrument": "Guitar",
                  "music_style": "Lofi", "visual_look": "warm_ghibli"}
        with patch.object(service, "_lookup_channel_by_id", return_value=chosen), \
                patch.object(service, "_lookup_channel_mapping") as by_pair, \
                patch.object(channel_looks, "valid_look", return_value=True):
            m = channel_looks.mapping_for("Shakuhachi", "Chinese Zen Music", channel_id=2)
        self.assertEqual(m["id"], 2)
        by_pair.assert_not_called()


class ProjectsRouterChannelTests(unittest.TestCase):
    def setUp(self):
        tmp = TemporaryDirectory(prefix="mixer-channel-test-")
        self.addCleanup(tmp.cleanup)
        self.engine = create_engine(f"sqlite:///{Path(tmp.name) / 't.sqlite'}")
        SQLModel.metadata.create_all(self.engine)
        self.addCleanup(self.engine.dispose)
        self.s = Session(self.engine)
        self.addCleanup(self.s.close)
        self.s.add_all([
            ChannelMapping(id=1, instrument="Piano", music_style="Lofi",
                           gpm_profile_id="p", channel_name="Piano Ch"),
            ChannelMapping(id=2, instrument="", music_style="",
                           gpm_profile_id="z", channel_name="Zen Ch"),
        ])
        self.s.commit()

    def test_chosen_channel_is_effective(self):
        from backend.routers import projects as r
        p = Project(id=1, name="A", instrument="Shakuhachi",
                    music_style="Chinese Zen Music", channel_id=2)
        self.s.add(p)
        self.s.commit()
        out = r.to_response(self.s, p)
        self.assertEqual((out.channel_id, out.effective_channel_id,
                          out.effective_channel_name), (2, 2, "Zen Ch"))

    def test_unchosen_auto_matches_or_empty(self):
        from backend.routers import projects as r
        a = Project(id=2, name="B", instrument="piano", music_style="LOFI")
        b = Project(id=3, name="C", instrument="Shakuhachi", music_style="Zen")
        self.s.add_all([a, b])
        self.s.commit()
        out = r.to_response(self.s, a)
        self.assertIsNone(out.channel_id)
        self.assertEqual(out.effective_channel_id, 1)
        out = r.to_response(self.s, b)
        self.assertIsNone(out.effective_channel_id)
        self.assertEqual(out.effective_channel_name, "")

    def test_check_channel(self):
        from backend.routers import projects as r
        r._check_channel(self.s, None)
        r._check_channel(self.s, 2)
        with self.assertRaises(HTTPException) as ctx:
            r._check_channel(self.s, 99)
        self.assertEqual(ctx.exception.status_code, 400)

    def test_delete_channel_unselects_projects(self):
        from backend.routers import youtube as yt
        p = Project(id=4, name="D", channel_id=2)
        self.s.add(p)
        self.s.commit()
        yt.delete_mapping(2, db=self.s)
        self.s.refresh(p)
        self.assertIsNone(p.channel_id)
        self.assertIsNone(self.s.get(ChannelMapping, 2))

    def test_create_mapping_without_instrument(self):
        from backend.routers import youtube as yt
        body = yt.MappingBody(gpm_profile_id="q", channel_name="New Ch",
                              default_hashtags="", language="en-US")
        with patch("backend.video.channel_looks.pick_free_look", return_value="look_x"):
            out = yt.create_mapping(body, db=self.s)
        self.assertEqual(out["channel_name"], "New Ch")
        self.assertEqual(out["instrument"], "")
        with self.assertRaises(HTTPException):
            yt.create_mapping(yt.MappingBody(gpm_profile_id="q", channel_name="",
                                             default_hashtags="", language=""), db=self.s)


class BatchChosenChannelTests(unittest.TestCase):
    def test_batch_passes_chosen_channel(self):
        from backend import batch_service as bs
        ideas = [{"project_name": "X", "description": "", "video_idea": "",
                  "suno_idea": ""}]
        chosen = {"id": 2, "channel_name": "Zen Ch"}
        with patch("backend.video.service._lookup_channel_by_id", return_value=chosen), \
                patch("backend.core_bridge.get_api_config_from_db", return_value={}), \
                patch("backend.video.batch_ideas.generate_batch_ideas", return_value=ideas), \
                patch("backend.video.channel_looks.resolve_video_style", return_value="2d"), \
                patch.object(bs, "_create_batch_rows", return_value={"ok": 1}) as rows:
            bs.create_batch(1, "Shakuhachi", "Chinese Zen Music", "2d", channel_id=2)
        self.assertEqual(rows.call_args.kwargs["channel_id"], 2)
        self.assertIn("Zen Ch", rows.call_args.args)

    def test_batch_rejects_missing_channel(self):
        from backend import batch_service as bs
        with patch("backend.video.service._lookup_channel_by_id", return_value=None), \
                patch("backend.video.batch_ideas.generate_batch_ideas") as gen:
            with self.assertRaisesRegex(RuntimeError, "chọn lại kênh"):
                bs.create_batch(1, "Shakuhachi", "Zen", "2d", channel_id=99)
            gen.assert_not_called()


class MigrationM015Tests(unittest.TestCase):
    def test_adds_channel_id_column(self):
        from backend import migrations
        with TemporaryDirectory() as d:
            db = Path(d) / "app.db"
            con = sqlite3.connect(str(db))
            con.execute("CREATE TABLE project (id INTEGER PRIMARY KEY, name TEXT)")
            con.commit()
            con.close()
            with patch.object(migrations, "DB_PATH", db):
                migrations.migrate_add_project_channel_id()
                migrations.migrate_add_project_channel_id()   # chạy lại an toàn
            con = sqlite3.connect(str(db))
            cols = [r[1] for r in con.execute("PRAGMA table_info(project)")]
            con.close()
        self.assertIn("channel_id", cols)


if __name__ == "__main__":
    unittest.main()
