import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PIL import Image

from backend.video import config, muse_image, service
from backend.video.job_manager import JobCancelled

PROMPTS = {"0": "A calm scene of a flute player beside a moonlit bamboo grove.",
           "1": "CHARACTER MODEL SHEET of an adult man in an indigo hanfu.",
           "2": "CHARACTER MODEL SHEET of a small white fox companion."}


class PostProcessTests(unittest.TestCase):
    def _src(self, tmp, size=(1920, 1280), bg=(236, 236, 236)):
        im = Image.new("RGB", size, bg)
        im.paste((20, 40, 120), (size[0] // 2 - 50, size[1] // 2 - 50,
                                 size[0] // 2 + 50, size[1] // 2 + 50))
        p = Path(tmp) / "raw.webp"
        im.save(p, "WEBP", lossless=True)
        return p

    def test_sheet_is_padded_to_16x9_without_cropping(self):
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "1.png"
            w, h = muse_image.to_png_16x9(self._src(tmp), dest, mode="pad")
            self.assertEqual((w, h), (2276, 1280))
            with Image.open(dest) as im:
                self.assertEqual(im.format, "PNG")
                self.assertEqual(im.getpixel((5, 5)), (236, 236, 236))   # lề = màu nền
                self.assertEqual(im.getpixel((w // 2, h // 2)), (20, 40, 120))

    def test_thumbnail_is_center_cropped(self):
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "0.png"
            self.assertEqual(muse_image.to_png_16x9(self._src(tmp), dest, mode="crop"),
                             (1920, 1080))

    def test_small_image_is_upscaled(self):
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "0.png"
            w, h = muse_image.to_png_16x9(self._src(tmp, (960, 640)), dest, mode="crop")
            self.assertGreaterEqual(w, 1920)
            self.assertGreaterEqual(h, 1080)
            self.assertAlmostEqual(w / h, 16 / 9, places=2)


class DetectTests(unittest.TestCase):
    def test_prompt_spec_once(self):
        p = muse_image.image_prompt("A sheet")
        self.assertTrue(p.startswith(muse_image.MUSE_IMAGE_SPEC))
        self.assertEqual(muse_image.image_prompt(p), p)

    def test_new_image_only_after_new_user_message(self):
        base = {"boxes": 3, "last_user": "u1", "has_msgs": True}
        old = {"has_msgs": True, "last_user": "u1", "after_boxes": 1, "after_ready": 1}
        self.assertEqual(muse_image._new_image(old, base), (0, 0))
        new = {"has_msgs": True, "last_user": "u2", "after_boxes": 1, "after_ready": 1}
        self.assertEqual(muse_image._new_image(new, base), (1, 1))


class GenerateTests(unittest.TestCase):
    def test_restart_wipes_and_draws_sheets_before_thumbnail(self):
        with tempfile.TemporaryDirectory() as tmp:
            img = Path(tmp)
            (img / "1.png").write_bytes(b"x" * 5000)     # ảnh Gemini cũ
            calls = []

            def fake_session(pending, mark_done):
                calls.append(list(pending))
                self.assertFalse((img / "1.png").exists())
                for i in pending:
                    (img / f"{i}.png").write_bytes(b"p" * 5000)
                    mark_done(i)

            with mock.patch.object(config, "images_dir", return_value=img):
                out = muse_image.generate_images_muse(
                    23, "Moonlit", config.PARAMS, None, resume=False,
                    prompts_override=PROMPTS, style="ghibli", _session_impl=fake_session)
            self.assertEqual(calls, [[1, 2, 0]])
            self.assertEqual(len(out), 3)


class FallbackTests(unittest.TestCase):
    def _run(self, gemini_exc=None, engine="gemini", fallback=True, only=None, muse_exc=None):
        cb = mock.Mock()
        gem = mock.Mock(side_effect=gemini_exc, return_value=["g"])
        muse = mock.Mock(side_effect=muse_exc, return_value=["m"])
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(config, "image_engine", return_value=engine), \
                mock.patch.object(config, "image_fallback", return_value=fallback), \
                mock.patch.object(config, "images_dir", return_value=Path(tmp)), \
                mock.patch("backend.video.gemini_driver.generate_images", gem), \
                mock.patch("backend.video.muse_image.generate_images_muse", muse):
            try:
                res = service._generate_images(23, "t", cb, resume=True, project_name="p",
                                               prompts_override=PROMPTS, style="ghibli",
                                               only=only)
            except BaseException as e:   # noqa: BLE001 — test trả lỗi để so
                res = e
        return res, gem, muse, cb

    def test_gemini_ok_no_muse(self):
        res, gem, muse, _ = self._run()
        self.assertEqual(res, ["g"])
        muse.assert_not_called()

    def test_gemini_failure_restarts_all_with_muse(self):
        res, gem, muse, cb = self._run(RuntimeError("Ảnh 2: thất bại sau 4 lần thử"))
        self.assertEqual(res, ["m"])
        kw = muse.call_args.kwargs
        self.assertFalse(kw["resume"])          # vẽ lại TỪ ĐẦU dù job là resume
        self.assertIsNone(kw["only"])
        self.assertIn("Muse.ai", " ".join(str(c.args[0]) for c in cb.call_args_list))

    def test_single_regen_keeps_only(self):
        res, gem, muse, _ = self._run(RuntimeError("x"), only=[0])
        self.assertEqual(muse.call_args.kwargs["only"], [0])

    def test_cancel_and_data_errors_do_not_fall_back(self):
        for exc in (JobCancelled("huỷ"), ValueError("thiếu prompt")):
            res, gem, muse, _ = self._run(exc)
            self.assertIs(res, exc)
            muse.assert_not_called()

    def test_fallback_disabled(self):
        res, gem, muse, _ = self._run(RuntimeError("x"), fallback=False)
        self.assertIsInstance(res, RuntimeError)
        muse.assert_not_called()

    def test_default_engine_is_muse_with_gemini_backup(self):
        with mock.patch.object(config, "load_overrides", return_value={}):
            self.assertEqual(config.image_engine(), "muse")
            self.assertEqual(config.image_fallback_engine(), "gemini")
            self.assertTrue(config.image_fallback())
        with mock.patch.object(config, "load_overrides",
                               return_value={"image_muse_fallback": False}):
            self.assertFalse(config.image_fallback())      # khoá cũ vẫn tôn trọng

    def test_muse_failure_restarts_all_with_gemini(self):
        res, gem, muse, cb = self._run(engine="muse", muse_exc=RuntimeError("Muse lỗi"))
        self.assertEqual(res, ["g"])
        kw = gem.call_args.kwargs
        self.assertFalse(kw["resume"])          # vẽ lại TỪ ĐẦU dù job là resume
        self.assertIsNone(kw["only"])
        msg = " ".join(str(c.args[0]) for c in cb.call_args_list)
        self.assertIn("Muse.ai không tạo được ảnh", msg)
        self.assertIn("Gemini", msg)

    def test_muse_cancel_does_not_fall_back(self):
        res, gem, muse, _ = self._run(engine="muse", muse_exc=JobCancelled("huỷ"))
        self.assertIsInstance(res, JobCancelled)
        gem.assert_not_called()

    def test_engine_muse_skips_gemini(self):
        res, gem, muse, _ = self._run(engine="muse")
        self.assertEqual(res, ["m"])
        gem.assert_not_called()

    def test_muse_failure_mentions_both(self):
        cb = mock.Mock()
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(config, "image_engine", return_value="gemini"), \
                mock.patch.object(config, "image_fallback", return_value=True), \
                mock.patch.object(config, "images_dir", return_value=Path(tmp)), \
                mock.patch("backend.video.gemini_driver.generate_images",
                           side_effect=RuntimeError("Gemini hỏng")), \
                mock.patch("backend.video.muse_image.generate_images_muse",
                           side_effect=RuntimeError("Muse báo hết lượt")):
            with self.assertRaises(RuntimeError) as ctx:
                service._generate_images(23, "t", cb, prompts_override=PROMPTS)
            self.assertIn("Muse báo hết lượt", str(ctx.exception))
            self.assertIn("Gemini hỏng", str(ctx.exception))
            self.assertTrue((Path(tmp) / "_image_fallback.log").exists())


if __name__ == "__main__":
    unittest.main()
