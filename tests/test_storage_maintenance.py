"""Quét/dọn dữ liệu thừa: chỉ dùng thư mục + DB tạm, không đụng media thật."""
from datetime import datetime, timedelta
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlmodel import Session, SQLModel, create_engine, select

from backend.models import Mix, Project
from backend.project_cleanup import CleanupRoots
from backend.storage_maintenance import clean, scan, usage_bytes


def _touch(path: Path, data: bytes, age: float) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    stamp = time.time() - age
    os.utime(path, (stamp, stamp))
    return path


class StorageMaintenanceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory(prefix="mixer-storage-test-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.roots = CleanupRoots(self.root)
        self.engine = create_engine(f"sqlite:///{self.root / 'test.sqlite'}")
        SQLModel.metadata.create_all(self.engine)
        self.addCleanup(self.engine.dispose)
        self.session = Session(self.engine)
        self.addCleanup(self.session.close)
        self.audio = SimpleNamespace(_lock=threading.Lock(), _jobs={})
        self.stem = SimpleNamespace(_lock=threading.Lock(), _statuses={}, _futures={})
        self.video = SimpleNamespace(_lock=threading.Lock(), _jobs={})
        for target, value in (
                ("backend.project_cleanup._managers",
                 lambda: (self.audio, self.stem, self.video)),
                ("backend.project_cleanup._sleep", lambda _s: None)):
            patcher = patch(target, value)
            patcher.start()
            self.addCleanup(patcher.stop)

        self.session.add(Project(id=7, name="Calm"))
        self.session.commit()
        now = datetime.now()
        outputs = self.roots.outputs / "7"
        self.base = Mix(id=1, project_id=7, title="Mix 1", status="completed",
                        output_dir=str(outputs / "1"),
                        created_at=now - timedelta(hours=2))
        self.patched = Mix(id=2, project_id=7, title="Vá mix #1 (thay 1 bài)",
                           status="completed", output_dir=str(outputs / "2"),
                           created_at=now - timedelta(hours=1))
        self.session.add_all([self.base, self.patched])
        self.session.commit()
        _touch(outputs / "1" / "mix.wav", b"old mix" * 100, age=7200)
        _touch(outputs / "1" / "mix_info.json", b"{}", age=7200)
        self.new_wav = _touch(outputs / "2" / "mix.wav", b"new mix" * 100, age=3600)
        _touch(outputs / "2" / "mix_info.json", b"{}", age=3600)
        final_dir = self.roots.media / "Calm (#7)" / "final"
        self.final = _touch(final_dir / "final.mp4", b"video", age=60)
        self.patched_mp4 = _touch(final_dir / "_final_patched.mp4", b"tmp video", age=600)
        tracks = self.roots.media / "Calm (#7)" / "suno_tracks"
        staging = self.roots.suno / "project_7" / "abc"
        self.track = _touch(tracks / "Song_1.wav", b"RIFF" + b"a" * 5000, age=900)
        self.stage = _touch(staging / "Song_1.wav", b"RIFF" + b"a" * 5000, age=900)
        self.spare = _touch(staging / "Spare_2.wav", b"RIFF" + b"b" * 5000, age=900)
        # Dữ liệu mồ côi của project #9 đã xoá + vỏ quarantine rỗng.
        self.orphans = [self.roots.media / "Gone (#9)", self.roots.outputs / "9",
                        self.roots.suno / "project_9"]
        for folder in self.orphans:
            _touch(folder / "x.dat", b"orphan", age=10)
        (self.roots.outputs / ".project-trash" / f"project_9_{'a' * 32}").mkdir(
            parents=True)
        self.legacy = _touch(self.root / "media" / "Legacy Name" / "keep.dat",
                             b"unknown owner", age=10)

    def _keys(self, report, category):
        group = next(c for c in report["categories"] if c["key"] == category)
        return [item["key"] for item in group["items"]]

    def test_scan_lists_junk_without_touching_anything(self):
        report = scan(self.session, self.roots)
        self.assertEqual(sorted(self._keys(report, "orphans")),
                         ["orphans:data/suno_batches/project_9",
                          "orphans:media/Gone (#9)", "orphans:outputs/9"])
        self.assertEqual(self._keys(report, "trash"), ["trash:empty"])
        self.assertEqual(self._keys(report, "patched"), ["patched:7"])
        self.assertEqual(self._keys(report, "old_mixes"), ["old_mixes:1"])
        self.assertEqual(self._keys(report, "dup_wavs"), ["dup_wavs:7"])
        for folder in self.orphans:
            self.assertTrue(folder.exists())
        self.assertTrue(self.patched_mp4.exists())
        self.assertIsNotNone(self.session.get(Mix, 1))

    def test_clean_everything_keeps_what_is_in_use(self):
        report = scan(self.session, self.roots)
        keys = [i["key"] for c in report["categories"] for i in c["items"]]
        result = clean(self.session, keys, self.roots)
        self.assertEqual(result["errors"], [])
        self.assertGreater(result["freed_bytes"], 0)
        for folder in self.orphans:
            self.assertFalse(folder.exists())
        self.assertFalse((self.roots.outputs / ".project-trash").exists())
        self.assertFalse(self.patched_mp4.exists())
        self.assertTrue(self.final.exists())
        self.assertIsNone(self.session.get(Mix, 1))
        self.assertFalse((self.roots.outputs / "7" / "1").exists())
        self.assertIsNotNone(self.session.get(Mix, 2))
        self.assertTrue(self.new_wav.exists())
        # WAV trùng → hardlink: còn đủ 2 tên, nội dung giữ nguyên, chung 1 inode.
        self.assertEqual(self.stage.read_bytes(), self.track.read_bytes())
        self.assertEqual(os.stat(self.stage).st_ino, os.stat(self.track).st_ino)
        self.assertTrue(self.spare.exists())
        self.assertTrue(self.legacy.exists())
        self.assertEqual(usage_bytes([self.roots.suno / "project_7",
                                      self.roots.media / "Calm (#7)" / "suno_tracks"]),
                         2 * 5004)
        again = scan(self.session, self.roots)
        self.assertEqual(again["total_bytes"], 0)

    def test_busy_project_is_skipped(self):
        self.video._jobs[7] = SimpleNamespace(status="paused")
        report = scan(self.session, self.roots)
        self.assertEqual(report["busy_project_ids"], [7])
        for category in ("patched", "old_mixes", "dup_wavs"):
            self.assertEqual(self._keys(report, category), [])

    def test_pending_patch_and_its_base_mix_are_kept(self):
        # Thay nhạc lần trước bị khoá: _final_patched mới hơn final.mp4 + mix vá.
        stamp = time.time()
        os.utime(self.patched_mp4, (stamp, stamp))
        old = stamp - 7200
        os.utime(self.final, (old, old))
        report = scan(self.session, self.roots)
        self.assertEqual(self._keys(report, "patched"), [])
        self.assertEqual(self._keys(report, "old_mixes"), [])

    def test_unknown_keys_are_ignored(self):
        result = clean(self.session, ["orphans:../../Windows", "old_mixes:2"], self.roots)
        self.assertEqual(result["done"], [])
        self.assertTrue(result["errors"])
        self.assertIsNotNone(self.session.get(Mix, 2))
        self.assertTrue(self.new_wav.exists())


if __name__ == "__main__":
    unittest.main()
