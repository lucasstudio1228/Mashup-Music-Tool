"""2026-11-16 tách «FULL AUTO» (Suno → Mix → Video → nháp YouTube) và «AUTO AUDIO»
(Suno → Mix rồi dừng) + nút «📂 Mở thư mục» cho Audio (mix) và Final.

DB tạm + MEDIA_ROOT tạm; Explorer/os.startfile/Suno/mix đều mock — không đụng
backend thật, trình duyệt hay credit."""
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from sqlmodel import Session, SQLModel, create_engine

from backend.models import Mix, Project
from backend.video import config


class _TempDb(unittest.TestCase):
    def setUp(self):
        tmp = TemporaryDirectory(prefix="auto-audio-")
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.engine = create_engine(f"sqlite:///{self.root / 's.sqlite'}")
        SQLModel.metadata.create_all(self.engine)
        self.addCleanup(self.engine.dispose)
        with Session(self.engine) as s:
            proj = Project(name="Moon Lake", auto_video=True, auto_upload=True)
            s.add(proj)
            s.commit()
            self.pid = proj.id

    def _mix(self, status="completed", with_wav=True) -> Mix:
        out = self.root / "outputs" / f"mix_{status}"
        out.mkdir(parents=True, exist_ok=True)
        if with_wav:
            (out / "mix.wav").write_bytes(b"RIFF")
        with Session(self.engine) as s:
            mix = Mix(project_id=self.pid, title="m", status=status,
                      duration_minutes=120, crossfade_seconds=5,
                      sample_rate=48000, bit_depth=32, output_dir=str(out))
            s.add(mix)
            s.commit()
            s.refresh(mix)
            return mix


class SunoConfigTests(unittest.TestCase):
    def test_audio_only_flag_in_batch_config(self):
        from backend.video.suno_service import resolve_batch_config
        self.assertFalse(resolve_batch_config({})["audio_only"])
        self.assertTrue(resolve_batch_config({"audio_only": True})["audio_only"])


class MixSkipVideoTests(_TempDb):
    def _run_on_success(self, **kw):
        from backend.routers import mixes
        mix = self._mix(status="running")
        seen = {}
        with mock.patch.object(mixes, "engine", self.engine), \
                mock.patch("backend.video.qc_autofix.after_mix",
                           side_effect=lambda pid, mid, ctx: seen.update(ctx=ctx)):
            mixes._on_success(mix.id, {"output_dir": mix.output_dir,
                                       "track_count": 15}, **kw)
        return seen["ctx"]

    def test_full_auto_builds_video_context(self):
        ctx = self._run_on_success(force_video=True)
        self.assertEqual(ctx["project_id"], self.pid)
        self.assertTrue(ctx["audio_path"].endswith("mix.wav"))

    def test_audio_only_never_starts_video(self):
        # Project vẫn bật auto_video nhưng AUTO AUDIO → không có context video.
        self.assertIsNone(self._run_on_success(force_video=False, skip_video=True))


class BatchAudioOnlyTests(_TempDb):
    def setUp(self):
        super().setUp()
        from backend import batch_service
        self.bs = batch_service
        p = mock.patch.object(batch_service, "engine", self.engine)
        p.start()
        self.addCleanup(p.stop)

    def test_verify_product_audio_needs_completed_mix_wav(self):
        self.assertFalse(self.bs._verify_product(self.pid, audio_only=True)[0])
        self._mix(status="failed")
        self.assertFalse(self.bs._verify_product(self.pid, audio_only=True)[0])
        self._mix(status="completed", with_wav=False)
        self.assertFalse(self.bs._verify_product(self.pid, audio_only=True)[0])
        self._mix(status="completed")
        ok, why = self.bs._verify_product(self.pid, audio_only=True)
        self.assertTrue(ok, why)

    def _stage(self, **kw):
        done = {"phase": "COMPLETED", "imported_tracks": 15}
        with mock.patch.object(self.bs.suno_service, "batch_status", return_value=done), \
                mock.patch.object(self.bs.suno_service, "COMPLETED", "COMPLETED"), \
                mock.patch.object(self.bs, "_suno_done", return_value=True), \
                mock.patch("backend.routers.mixes.start_mix_for_project",
                           return_value=1) as start_mix, \
                mock.patch.object(self.bs.video_job_manager, "submit") as submit:
            stage = self.bs._submit_stage(self.pid, {}, resume=True, **kw)
        return stage, start_mix, submit

    def test_submit_stage_audio_mixes_without_video(self):
        stage, start_mix, submit = self._stage(audio_only=True)
        self.assertEqual(stage, "mix")
        start_mix.assert_called_once_with(self.pid, skip_video=True)
        submit.assert_not_called()

    def test_submit_stage_audio_done_when_mix_exists(self):
        self._mix(status="completed")
        stage, start_mix, submit = self._stage(audio_only=True)
        self.assertEqual(stage, "done")
        start_mix.assert_not_called()
        submit.assert_not_called()


class GenerateStylesFlagsTests(_TempDb):
    def test_classify_sets_auto_flags_per_mode(self):
        from backend.routers.suno import _classify_and_prepare
        for audio_only, expect in ((True, False), (False, True)):
            with self.subTest(audio_only=audio_only), Session(self.engine) as s:
                proj = s.get(Project, self.pid)
                with mock.patch("backend.video.channel_match.classify_channel",
                                return_value=None):
                    _classify_and_prepare(s, proj, "t", "idea", "styles", None,
                                          audio_only=audio_only)
                self.assertEqual(proj.auto_video, expect)
                self.assertEqual(proj.auto_upload, expect)


@unittest.skipUnless(os.name == "nt", "Explorer chỉ có trên Windows")
class OpenFolderTests(_TempDb):
    def setUp(self):
        super().setUp()
        from fastapi.testclient import TestClient
        from backend.database import get_session
        from backend.main import app

        def _session():
            with Session(self.engine) as s:
                yield s
        app.dependency_overrides[get_session] = _session
        self.addCleanup(app.dependency_overrides.clear)
        p = mock.patch.object(config, "MEDIA_ROOT", self.root / "media")
        p.start()
        self.addCleanup(p.stop)
        self.client = TestClient(app)

    def _post(self, url):
        with mock.patch("backend.open_folder.subprocess.Popen") as pop, \
                mock.patch("os.startfile", create=True) as sf:
            r = self.client.post(url)
        return r, pop, sf

    def test_final_folder_selects_final_mp4(self):
        url = f"/api/projects/{self.pid}/video/open-final-folder"
        r, pop, sf = self._post(url)
        self.assertEqual(r.status_code, 404)              # chưa có thư mục final
        pop.assert_not_called()
        sf.assert_not_called()
        d = config.final_dir(self.pid, "Moon Lake")
        d.mkdir(parents=True)
        r, pop, sf = self._post(url)                      # có thư mục, chưa render
        self.assertEqual(r.status_code, 200)
        sf.assert_called_once_with(str(d))
        pop.assert_not_called()
        (d / "final.mp4").write_bytes(b"\x00")
        r, pop, sf = self._post(url)
        self.assertEqual(r.status_code, 200)
        args = pop.call_args.args[0]
        self.assertEqual(args[0], "explorer")
        self.assertTrue(args[1].startswith("/select,") and args[1].endswith("final.mp4"))
        sf.assert_not_called()

    def test_mix_folder_selects_mix_wav(self):
        mix = self._mix(status="completed")
        r, pop, _ = self._post(f"/api/mixes/{mix.id}/open-folder")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(pop.call_args.args[0][1].endswith("mix.wav"))
        self.assertEqual(r.json()["mix_id"], mix.id)
        self.assertEqual(self._post("/api/mixes/99999/open-folder")[0].status_code, 404)


if __name__ == "__main__":
    unittest.main()
