"""Thumbnail AI qua API: prompt tiếng Anh theo công thức, gọi Gemini native /
OpenAI chat / images (HTTP được MOCK — không gọi API thật, không tốn phí), cắt về
1280x720, bộ nhớ đệm không gọi lại API, kiểm tra chữ sai → thử lại → rơi về PIL,
endpoint cài đặt không lộ key. Chỉ dùng thư mục/DB tạm."""
import base64
import io
import json
from pathlib import Path
import sqlite3
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent.parent))

from PIL import Image  # noqa: E402

from backend.video import config, service  # noqa: E402
from backend.video import thumbnail_ai as T  # noqa: E402

# Sổ công thức thumbnail → file tạm (không ghi vào sổ thật của người dùng).
from backend.video import thumbnail_recipe as _thumb_recipe  # noqa: E402
TR = _thumb_recipe
_LEDGER_DIR = None
_LEDGER_PATCH = None


def setUpModule():
    global _LEDGER_DIR, _LEDGER_PATCH
    import tempfile as _tf
    _LEDGER_DIR = _tf.TemporaryDirectory()
    _LEDGER_PATCH = mock.patch.object(
        _thumb_recipe, "LEDGER", Path(_LEDGER_DIR.name) / "thumbnail_recipes.json")
    _LEDGER_PATCH.start()


def tearDownModule():
    if _LEDGER_PATCH:
        _LEDGER_PATCH.stop()
    if _LEDGER_DIR:
        _LEDGER_DIR.cleanup()


KEY = "sk-test-SECRET-123"
RECIPE = {"look": "ink_zen", "layout": "right", "treatment": "ink_seal",
          "palette": "jade", "font": "mistral", "seal": "静", "upper": False}


def _png(w=2752, h=1536, color=(40, 90, 70)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (w, h), color).save(buf, format="PNG")
    return buf.getvalue()


class _Resp:
    def __init__(self, status=200, payload=None, content=b"", text=""):
        self.status_code = status
        self._payload = payload
        self.content = content
        self.text = text or (json.dumps(payload) if payload is not None else "")

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


def _settings(model="gemini-3-pro-image-preview", verify=False):
    return T.ThumbAISettings(api_key=KEY, base_url="https://api.shopaikey.com/v1/",
                             model=model, enabled=True, verify=verify, engine="api")


class PromptTests(unittest.TestCase):
    def test_locked_font_prompt_follows_recipe(self):
        # Người dùng KHOÁ font tay → model vẽ đúng font + kiểu chữ + màu công thức.
        title, sub = "Bamboo Whispers at Dawn", "Zen · Meditation · Inner Peace"
        p = T.build_prompt(title, sub, {**RECIPE, "font_lock": "mistral",
                                        "ai_lettering": "watercolor_brush",
                                        "ai_design": "movie_poster"})
        self.assertIn(f'"{title}"', p)
        self.assertIn(f'"{sub}"', p)
        self.assertIn("RIGHT part of the frame", p)
        self.assertIn("#ECF8F0", p)                      # màu chữ bảng jade
        self.assertIn("brush-pen script", p)             # font mistral
        self.assertIn("seal stamp", p)
        self.assertNotIn("TITLE LETTERING", p)
        self.assertNotIn("THUMBNAIL STYLE", p)
        # Ngoài 2 chuỗi chữ cần vẽ, prompt phải 100% ASCII (tiếng Anh).
        rest = p.replace(title, "").replace(sub, "")
        self.assertTrue(rest.isascii(), [c for c in rest if not c.isascii()])

    def test_default_prompt_lets_model_design_lettering_and_style(self):
        # Mặc định (2026-10-25): model TỰ thiết kế chữ theo hướng riêng của video,
        # không ép font/màu/kiểu chữ PIL.
        title, sub = "Bamboo Whispers at Dawn", "Zen · Meditation · Inner Peace"
        rec = {**RECIPE, "ai_lettering": "watercolor_brush", "ai_design": "movie_poster"}
        p = T.build_prompt(title, sub, rec)
        self.assertIn(f'"{title}"', p)
        self.assertIn("RIGHT part of the frame", p)
        self.assertIn("TITLE LETTERING", p)
        self.assertIn(TR.ai_text("watercolor_brush"), p)
        self.assertIn("THUMBNAIL STYLE", p)
        self.assertIn(TR.ai_text("movie_poster"), p)
        self.assertIn("Subtitle lettering", p)
        for forced in ("#ECF8F0", "brush-pen script", "seal stamp", "Title typography"):
            self.assertNotIn(forced, p)
        rest = p.replace(title, "").replace(sub, "")
        self.assertTrue(rest.isascii(), [c for c in rest if not c.isascii()])
        # Công thức cũ chưa có ai_* → vẫn để model tự thiết kế chữ.
        p_old = T.build_prompt(title, sub, RECIPE)
        self.assertIn("TITLE LETTERING", p_old)
        self.assertNotIn("brush-pen script", p_old)
        self.assertNotIn("THUMBNAIL STYLE", p_old)

    def test_upper_and_crop_note(self):
        p = T.build_prompt("Quiet Rain", "", {**RECIPE, "upper": True,
                                               "treatment": "neon"},
                           native_wide=False)
        self.assertIn('"QUIET RAIN"', p)
        self.assertNotIn("SUBTITLE", p)
        self.assertNotIn("Subtitle", p)
        self.assertNotIn("seal", p)                      # seal chỉ với ink_seal
        self.assertIn("cropped to 16:9", p)

    def test_cache_key_changes_with_ai_style(self):
        def key(rec):
            return T.cache_key("Quiet Rain", "Sleep", rec, "m", Path("khong-co.png"))
        base = {**RECIPE, "ai_lettering": "chalk", "ai_design": "high_key"}
        k1 = key(base)
        self.assertEqual(k1, key(dict(base)))
        self.assertNotEqual(k1, key({**base, "ai_lettering": "papercut"}))
        self.assertNotEqual(k1, key({**base, "ai_design": "ink_mist"}))

    def test_api_root_and_protocol(self):
        self.assertEqual(T.api_root("https://api.shopaikey.com/v1/"),
                         "https://api.shopaikey.com")
        self.assertEqual(T.api_root("https://api.shopaikey.com/v1beta"),
                         "https://api.shopaikey.com")
        self.assertEqual(T.protocol_for("gemini-3-pro-image-preview"), "gemini")
        self.assertEqual(T.protocol_for("gpt-image-1.5"), "images")
        self.assertEqual(T.protocol_for("gpt-image-2-all"), "chat")


class AdapterTests(unittest.TestCase):
    def test_gemini_native_request_and_parse(self):
        final = _png(64, 36, (1, 2, 3))
        thought = _png(64, 36, (9, 9, 9))
        payload = {"candidates": [{"finishReason": "STOP", "content": {"parts": [
            {"inlineData": {"mimeType": "image/png",
                            "data": base64.b64encode(thought).decode()}, "thought": True},
            {"text": "here you go"},
            {"inlineData": {"mimeType": "image/png",
                            "data": base64.b64encode(final).decode()}},
        ]}}]}
        with mock.patch("httpx.post", return_value=_Resp(200, payload)) as post:
            out = T.call_model(_settings(), "PROMPT", b"jpegbytes")
        self.assertEqual(out, final)                     # bỏ ảnh "thought"
        url = post.call_args.args[0]
        self.assertEqual(url, "https://api.shopaikey.com/v1beta/models/"
                              "gemini-3-pro-image-preview:generateContent")
        kw = post.call_args.kwargs
        self.assertEqual(kw["headers"]["x-goog-api-key"], KEY)
        cfg = kw["json"]["generationConfig"]["imageConfig"]
        self.assertEqual(cfg, {"aspectRatio": "16:9", "imageSize": "2K"})
        parts = kw["json"]["contents"][0]["parts"]
        self.assertEqual(parts[0]["inlineData"]["data"],
                         base64.b64encode(b"jpegbytes").decode())
        self.assertEqual(parts[1]["text"], "PROMPT")

    def test_gemini_25_has_no_image_size(self):
        payload = {"candidates": [{"content": {"parts": [
            {"inlineData": {"data": base64.b64encode(_png(8, 8)).decode()}}]}}]}
        with mock.patch("httpx.post", return_value=_Resp(200, payload)) as post:
            T.call_model(_settings("gemini-2.5-flash-image"), "P", b"x")
        self.assertNotIn("imageSize",
                         post.call_args.kwargs["json"]["generationConfig"]["imageConfig"])

    def test_gemini_safety_block_is_reported(self):
        payload = {"candidates": [{"finishReason": "IMAGE_SAFETY",
                                   "content": {"parts": []}}]}
        with mock.patch("httpx.post", return_value=_Resp(200, payload)):
            with self.assertRaisesRegex(T.ThumbAIError, "IMAGE_SAFETY") as cm:
                T.call_model(_settings(), "P", b"x")
        self.assertFalse(cm.exception.retryable)

    def test_chat_markdown_url_is_downloaded(self):
        img = _png(32, 18)
        payload = {"choices": [{"message": {"content":
                   "> progress 100%\n![image](https://cdn.example.com/a/b.png)"}}]}
        with mock.patch("httpx.post", return_value=_Resp(200, payload)) as post, \
                mock.patch("httpx.get", return_value=_Resp(200, content=img)) as get:
            out = T.call_model(_settings("gpt-image-2-all"), "P", b"ref")
        self.assertEqual(out, img)
        self.assertTrue(post.call_args.args[0].endswith("/v1/chat/completions"))
        self.assertEqual(get.call_args.args[0], "https://cdn.example.com/a/b.png")
        content = post.call_args.kwargs["json"]["messages"][0]["content"]
        self.assertTrue(content[1]["image_url"]["url"].startswith("data:image/jpeg;base64,"))

    def test_chat_data_url_and_images_api(self):
        img = _png(16, 9)
        b64 = base64.b64encode(img).decode()
        payload = {"choices": [{"message": {"content": f"![x](data:image/png;base64,{b64})"}}]}
        with mock.patch("httpx.post", return_value=_Resp(200, payload)):
            self.assertEqual(T.call_model(_settings("gpt-image-1.5-all"), "P", b"r"), img)
        with mock.patch("httpx.post",
                        return_value=_Resp(200, {"data": [{"b64_json": b64}]})) as post:
            self.assertEqual(T.call_model(_settings("gpt-image-1.5"), "P", b"r"), img)
        self.assertTrue(post.call_args.args[0].endswith("/v1/images/edits"))
        self.assertIn("image", post.call_args.kwargs["files"])

    def test_http_errors_are_friendly_and_never_leak_key(self):
        for code, pat in ((401, "401"), (403, "nhóm của key"), (404, "404"),
                          (429, "429"), (502, "502")):
            resp = _Resp(code, {"error": {"message": "bad thing"}})
            with mock.patch("httpx.post", return_value=resp):
                with self.assertRaisesRegex(T.ThumbAIError, pat) as cm:
                    T.call_model(_settings(), "P", b"x")
            self.assertNotIn(KEY, str(cm.exception))
            self.assertEqual(cm.exception.retryable, code in (429, 502))


class GenerateTests(unittest.TestCase):
    def setUp(self):
        tmp = TemporaryDirectory(prefix="thumb-ai-test-")
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.cover = self.root / "images" / "0.png"
        self.cover.parent.mkdir(parents=True)
        Image.new("RGB", (1376, 768), (90, 120, 100)).save(self.cover)
        self.out = self.root / "final" / "thumbnail.png"
        self.out.parent.mkdir(parents=True)
        p = mock.patch.object(T.time, "sleep")
        p.start()
        self.addCleanup(p.stop)

    def _gen(self, **kw):
        args = dict(title="Bamboo Whispers", subtitle="Zen · Sleep", recipe=RECIPE,
                    cover=self.cover, out=self.out, settings=_settings())
        args.update(kw)
        return T.generate(**args)

    def test_generate_crops_to_1280x720_and_caches(self):
        with mock.patch.object(T, "call_model", return_value=_png(2752, 1536)) as call:
            rec = self._gen()
            self.assertEqual(call.call_count, 1)
            with Image.open(self.out) as im:
                self.assertEqual(im.size, (1280, 720))
            self.assertLess(self.out.stat().st_size, T.MAX_BYTES)
            meta = T.read_meta(self.out.parent)
            self.assertEqual(meta["current"], "ai")
            self.assertEqual(meta["ai"]["cache_key"], rec["cache_key"])
            self.assertNotIn(KEY, json.dumps(meta))
            # Ghép lại video, không đổi gì → dùng lại, KHÔNG gọi API (không tốn phí).
            again = self._gen()
            self.assertTrue(again["cached"])
            self.assertEqual(call.call_count, 1)
            # Đổi tiêu đề → phải vẽ lại.
            self._gen(title="New Name")
            self.assertEqual(call.call_count, 2)
            # Nút «Tạo thumbnail AI» = force → vẽ bản mới.
            self._gen(title="New Name", force=True)
            self.assertEqual(call.call_count, 3)

    def test_tall_image_is_cropped_not_squashed(self):
        with mock.patch.object(T, "call_model", return_value=_png(1024, 1024)):
            self._gen()
        with Image.open(self.out) as im:
            self.assertEqual(im.size, (1280, 720))

    def test_wrong_text_retries_once_then_fails(self):
        bad = {"ok": False, "read_title": "Bambo Wispers", "read_subtitle": "",
               "title_ratio": 0.8, "subtitle_ratio": 1.0}
        with mock.patch.object(T, "call_model", return_value=_png()) as call, \
                mock.patch.object(T, "verify_text", return_value=bad):
            with self.assertRaisesRegex(T.ThumbAIError, "không khớp"):
                self._gen(settings=_settings(verify=True))
        self.assertEqual(call.call_count, T.MAX_CALLS)
        # Lượt 2 có câu nhắc viết đúng chính tả.
        self.assertIn("misspelled", call.call_args_list[1].args[1])
        self.assertFalse(self.out.exists())

    def test_wrong_then_right_text_succeeds(self):
        checks = [{"ok": False, "read_title": "Bambo", "read_subtitle": "",
                   "title_ratio": 0.5, "subtitle_ratio": 1.0},
                  {"ok": True, "read_title": "Bamboo Whispers", "read_subtitle": "",
                   "title_ratio": 1.0, "subtitle_ratio": 1.0}]
        with mock.patch.object(T, "call_model", return_value=_png()) as call, \
                mock.patch.object(T, "verify_text", side_effect=checks):
            rec = self._gen(settings=_settings(verify=True))
        self.assertEqual(call.call_count, 2)
        self.assertEqual(rec["attempts"], 2)
        self.assertTrue(rec["verified"])

    def test_non_retryable_error_stops_after_one_call(self):
        with mock.patch.object(T, "call_model",
                               side_effect=T.ThumbAIError("401")) as call:
            with self.assertRaises(T.ThumbAIError):
                self._gen()
        self.assertEqual(call.call_count, 1)

    def test_verify_text_ratio(self):
        with mock.patch.object(T, "_read_text", return_value={
                "title": "BAMBOO WHISPERS!", "subtitle": "Zen · Sleep"}):
            self.assertTrue(T.verify_text(None, "Bamboo Whispers", "Zen · Sleep")["ok"])
        with mock.patch.object(T, "_read_text", return_value={
                "title": "Bambo Wihspres", "subtitle": "Zen · Sleep"}):
            self.assertFalse(T.verify_text(None, "Bamboo Whispers", "Zen · Sleep")["ok"])
        with mock.patch.object(T, "_read_text", return_value=None):
            self.assertIsNone(T.verify_text(None, "A", "B"))

    def test_load_settings_from_db(self):
        db = self.root / "app.db"
        con = sqlite3.connect(db)
        con.execute("CREATE TABLE appsettings (id INTEGER PRIMARY KEY, thumb_api_key "
                    "VARCHAR, thumb_api_base_url VARCHAR, thumb_api_model VARCHAR, "
                    "thumb_ai_enabled BOOLEAN, thumb_ai_verify BOOLEAN)")
        con.execute("INSERT INTO appsettings VALUES (1, ?, 'https://x.test', "
                    "'gpt-image-2-all', 1, 0)", (KEY,))
        con.commit()
        con.close()
        s = T.load_settings(db)
        self.assertTrue(s.active)
        self.assertEqual(s.model, "gpt-image-2-all")
        self.assertFalse(s.verify)
        self.assertNotIn(KEY, repr(s))                    # repr không lộ key
        # Bảng chưa có cột (DB cũ) → mặc định, không hoạt động.
        db2 = self.root / "old.db"
        con = sqlite3.connect(db2)
        con.execute("CREATE TABLE appsettings (id INTEGER PRIMARY KEY, api_key VARCHAR)")
        con.close()
        self.assertFalse(T.load_settings(db2).active)
        self.assertEqual(T.load_settings(db2).engine, "chatgpt")   # mặc định mới
        self.assertEqual(s.engine, "chatgpt")             # DB chưa chạy M014


class RenderThumbnailIntegrationTests(unittest.TestCase):
    """service.render_thumbnail: AI khi bật + có key; AI lỗi → chữ PIL."""

    def setUp(self):
        tmp = TemporaryDirectory(prefix="thumb-ai-svc-")
        self.addCleanup(tmp.cleanup)
        p = mock.patch.object(config, "MEDIA_ROOT", Path(tmp.name))
        p.start()
        self.addCleanup(p.stop)
        img = config.images_dir(77, "Moon Lake")
        img.mkdir(parents=True)
        Image.new("RGB", (1376, 768), (30, 40, 60)).save(img / "0.png")
        self.thumb = config.final_dir(77, "Moon Lake") / "thumbnail.png"

    def _render(self, settings, **kw):
        with mock.patch.object(T, "load_settings", return_value=settings), \
                mock.patch.object(service, "_title_look",
                                  return_value=(None, "Calm · Sleep", {}, RECIPE)), \
                mock.patch("backend.video.thumbnail.render_title",
                           return_value=True) as pil:
            path = service.render_thumbnail(77, "Moon Lake", **kw)
        return path, pil

    def test_ai_used_when_active(self):
        with mock.patch.object(T, "call_model", return_value=_png()) as call:
            path, pil = self._render(_settings())
        self.assertEqual(path, str(self.thumb))
        call.assert_called_once()
        pil.assert_not_called()
        self.assertEqual(T.read_meta(self.thumb.parent)["current"], "ai")

    def test_ai_error_falls_back_to_pil(self):
        with mock.patch.object(T, "call_model", side_effect=T.ThumbAIError("HTTP 403")):
            path, pil = self._render(_settings())
        self.assertEqual(path, str(self.thumb))
        pil.assert_called_once()
        meta = T.read_meta(self.thumb.parent)
        self.assertEqual(meta["current"], "pil")
        self.assertIn("403", meta["current_note"])

    def test_no_key_or_pil_engine_never_calls_api(self):
        with mock.patch.object(T, "call_model") as call:
            self._render(T.ThumbAISettings(engine="api"))     # chưa có key
            self._render(_settings(), engine="pil")           # ép PIL
            off = _settings()
            off.enabled = False
            self._render(off)                                 # tắt tự động
        call.assert_not_called()

    def test_make_thumbnail_job_forces_new_ai_image(self):
        msgs = []
        with mock.patch.object(T, "call_model", return_value=_png()) as call:
            self._render(_settings())
            with mock.patch.object(T, "load_settings", return_value=_settings()), \
                    mock.patch.object(service, "_title_look",
                                      return_value=(None, "Calm · Sleep", {}, RECIPE)):
                out = service.make_thumbnail(77, lambda m, p: msgs.append(m), "Moon Lake",
                                             engine="ai")
        self.assertEqual(call.call_count, 2)                  # force → không dùng cache
        self.assertEqual(out["engine"], "ai")
        self.assertIn("AI", msgs[-1])


class SettingsEndpointTests(unittest.TestCase):
    def setUp(self):
        from fastapi.testclient import TestClient
        from sqlmodel import Session, SQLModel, create_engine
        from backend.database import get_session
        from backend.main import app
        tmp = TemporaryDirectory(prefix="thumb-ai-api-")
        self.addCleanup(tmp.cleanup)
        engine = create_engine(f"sqlite:///{Path(tmp.name) / 's.sqlite'}")
        SQLModel.metadata.create_all(engine)
        self.addCleanup(engine.dispose)

        def _session():
            with Session(engine) as s:
                yield s
        app.dependency_overrides[get_session] = _session
        self.addCleanup(app.dependency_overrides.clear)
        self.client = TestClient(app)

    def test_get_patch_never_returns_key(self):
        r = self.client.get("/api/settings/thumbnail-ai")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertFalse(body["has_api_key"])
        self.assertEqual(body["model"], "gemini-3-pro-image-preview")
        self.assertEqual(body["protocol"], "gemini")
        self.assertTrue(any(m["key"] == "gpt-image-2-all" for m in body["models"]))
        r = self.client.patch("/api/settings/thumbnail-ai",
                              json={"api_key": KEY, "model": "gpt-image-2-all",
                                    "verify": False})
        self.assertEqual(r.status_code, 200)
        self.assertNotIn(KEY, r.text)
        self.assertTrue(r.json()["has_api_key"])
        self.assertEqual(r.json()["protocol"], "chat")
        self.assertFalse(r.json()["verify"])
        # Chuỗi rỗng không xoá key; clear_api_key mới xoá.
        self.assertTrue(self.client.patch("/api/settings/thumbnail-ai",
                                          json={"api_key": ""}).json()["has_api_key"])
        self.assertFalse(self.client.patch("/api/settings/thumbnail-ai",
                                           json={"clear_api_key": True}).json()["has_api_key"])

    def test_connection_check_is_free_models_list(self):
        self.client.patch("/api/settings/thumbnail-ai", json={"api_key": KEY})
        payload = {"data": [{"id": "gpt-image-2-all"}, {"id": "gpt-4o-mini"},
                            {"id": "gpt-image-1.5-all"}]}
        with mock.patch("httpx.get", return_value=_Resp(200, payload)) as get, \
                mock.patch("httpx.post") as post:
            r = self.client.post("/api/settings/thumbnail-ai/test", json={})
        post.assert_not_called()                              # không tạo ảnh
        self.assertEqual(get.call_args.args[0], "https://api.shopaikey.com/v1/models")
        body = r.json()
        self.assertTrue(body["ok"])
        self.assertFalse(body["model_available"])             # gemini không có trong nhóm
        self.assertEqual(body["image_models"], ["gpt-image-1.5-all", "gpt-image-2-all"])
        self.assertNotIn(KEY, r.text)
        with mock.patch("httpx.get", return_value=_Resp(401, {"error": {"message": "x"}})):
            r = self.client.post("/api/settings/thumbnail-ai/test", json={})
        self.assertFalse(r.json()["ok"])
        self.assertIn("401", r.json()["error"])


class ThumbnailEndpointTests(unittest.TestCase):
    """POST /video/thumbnail: chặn khi chưa có key/ảnh bìa; chỉ GỬI job (mock)."""

    def setUp(self):
        from fastapi.testclient import TestClient
        from sqlmodel import Session, SQLModel, create_engine
        from backend.database import get_session
        from backend.main import app
        from backend.models import Project
        tmp = TemporaryDirectory(prefix="thumb-ai-ep-")
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        engine = create_engine(f"sqlite:///{root / 's.sqlite'}")
        SQLModel.metadata.create_all(engine)
        self.addCleanup(engine.dispose)
        with Session(engine) as s:
            proj = Project(name="Moon Lake")
            s.add(proj)
            s.commit()
            self.pid = proj.id

        def _session():
            with Session(engine) as s:
                yield s
        app.dependency_overrides[get_session] = _session
        self.addCleanup(app.dependency_overrides.clear)
        for p in (mock.patch.object(config, "MEDIA_ROOT", root / "media"),
                  mock.patch("backend.routers.video.video_job_manager")):
            self.jm = p.start()
            self.addCleanup(p.stop)
        self.jm.is_busy.return_value = False
        self.client = TestClient(app)
        self.url = f"/api/projects/{self.pid}/video/thumbnail"

    def _cover(self):
        img = config.images_dir(self.pid, "Moon Lake")
        img.mkdir(parents=True)
        Image.new("RGB", (64, 36)).save(img / "0.png")

    def test_requires_cover_and_key(self):
        with mock.patch.object(T, "load_settings", return_value=T.ThumbAISettings()):
            r = self.client.post(self.url, json={"engine": "ai"})
            self.assertEqual(r.status_code, 400)
            self.assertIn("ảnh bìa", r.json()["detail"])
            self._cover()
            r = self.client.post(self.url, json={"engine": "ai"})
            self.assertEqual(r.status_code, 400)
            self.assertIn("API key", r.json()["detail"])
            # Chữ font (miễn phí) không cần key.
            r = self.client.post(self.url, json={"engine": "pil"})
        self.assertEqual(r.status_code, 200)
        args = self.jm.submit.call_args.args
        self.assertEqual(args[1], "thumbnail")
        self.assertEqual(args[-1], "pil")

    def test_ai_submits_job_when_key_saved(self):
        self._cover()
        with mock.patch.object(T, "load_settings", return_value=_settings()):
            r = self.client.post(self.url, json={"engine": "ai"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["engine"], "ai")
        self.assertEqual(self.jm.submit.call_args.args[-1], "ai")


if __name__ == "__main__":
    unittest.main()
