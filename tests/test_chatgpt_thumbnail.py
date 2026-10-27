"""ChatGPT web vẽ thumbnail (mặc định 2026-10-23): prompt 1 dòng tiếng Anh, nhận
diện trang (xác minh / chưa đăng nhập / đã đăng nhập), chờ ảnh, chuỗi dự phòng
ChatGPT → API → PIL, huỷ không bị nuốt, endpoint mở cửa sổ đăng nhập. TOÀN BỘ
trình duyệt được MOCK — không mở Playwright thật, không gọi API thật, chỉ dùng
thư mục/DB tạm (không ghi data/chatgpt_login.json thật)."""
import io
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent.parent))

from PIL import Image  # noqa: E402

from backend.video import browser_base, config, service  # noqa: E402
from backend.video import chatgpt_thumbnail as C  # noqa: E402
from backend.video import thumbnail_ai as T  # noqa: E402
from backend.video.job_manager import JobCancelled  # noqa: E402

from backend.video import thumbnail_recipe as _thumb_recipe  # noqa: E402
_TMP = None
_PATCHES: list = []


def setUpModule():
    global _TMP
    _TMP = TemporaryDirectory(prefix="chatgpt-thumb-mod-")
    root = Path(_TMP.name)
    for p in (mock.patch.object(_thumb_recipe, "LEDGER", root / "thumbnail_recipes.json"),
              # file trạng thái đăng nhập → thư mục tạm (cạnh "DB" giả)
              mock.patch.object(T, "_db_path", lambda: root / "app.db")):
        p.start()
        _PATCHES.append(p)


def tearDownModule():
    for p in reversed(_PATCHES):
        p.stop()
    if _TMP:
        _TMP.cleanup()


RECIPE = {"look": "ink_zen", "layout": "right", "treatment": "ink_seal",
          "palette": "jade", "font": "mistral", "seal": "静", "upper": False}


def _png(w=1536, h=1024, color=(40, 90, 70)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (w, h), color).save(buf, format="PNG")
    return buf.getvalue()


def _settings(**kw):
    base = dict(api_key="sk-test-X", model="gpt-image-2-all", enabled=True,
                verify=False, engine="chatgpt")
    base.update(kw)
    return T.ThumbAISettings(**base)


class PromptAndLoginFileTests(unittest.TestCase):
    def test_prompt_single_line_english_with_title(self):
        base = T.build_prompt("Moon Lake Calm", "Sleep · Healing", RECIPE)
        p = C.chatgpt_prompt(base)
        self.assertNotIn("\n", p)                     # Enter trong ô soạn = gửi
        self.assertTrue(p.startswith("Use your image generation tool"))
        self.assertIn('"Moon Lake Calm"', p)
        self.assertIn("1536x1024", p)
        self.assertTrue(all(ord(ch) < 0x2000 or ch in "·静" for ch in p))

    def test_login_status_roundtrip(self):
        C.record_login(False)
        self.assertFalse(C.login_status()["logged_in"])
        C.record_login(True)
        st = C.login_status()
        self.assertTrue(st["logged_in"])
        self.assertTrue(st["checked_at"])
        # chỉ lưu có/không + thời điểm — không có tài khoản / cookie
        raw = C._login_file().read_text(encoding="utf-8")
        self.assertEqual(sorted(__import__("json").loads(raw)), ["checked_at", "logged_in"])


class _Loc:
    def __init__(self, n):
        self.n = n
        self.first = self

    def count(self):
        return self.n

    def is_visible(self):
        return self.n > 0


class _Page:
    def __init__(self, url="https://chatgpt.com/", challenge=False, visible=()):
        self.url = url
        self.challenge = challenge
        self.visible = set(visible)

    def evaluate(self, js, *a):
        return self.challenge

    def locator(self, sel):
        return _Loc(1 if sel in self.visible else 0)


class PageStateTests(unittest.TestCase):
    sels = {"login_button": ["#login"], "profile_button": ["#me"], "prompt_box": ["#box"]}

    def test_states(self):
        st = lambda **kw: C.page_state(_Page(**kw), self.sels)  # noqa: E731
        self.assertEqual(st(challenge=True, visible={"#box"}), "challenge")
        self.assertEqual(st(url="https://auth.openai.com/log-in"), "login_flow")
        self.assertEqual(st(visible={"#login", "#box"}), "logged_out")
        self.assertEqual(st(visible={"#me", "#box"}), "logged_in")
        self.assertEqual(st(visible={"#box"}), "ready")
        self.assertEqual(st(), "loading")


class DriverTests(unittest.TestCase):
    """Logic chờ của _ChatGPT với trang giả — không bấm gì trên trang thật."""

    def setUp(self):
        for p in (mock.patch.object(browser_base, "wait_ms"),
                  mock.patch.object(browser_base, "query_first", return_value=None),
                  mock.patch.object(C, "_visible", return_value=False)):
            p.start()
            self.addCleanup(p.stop)
        self.logs: list[str] = []
        self.drv = C._ChatGPT(self.logs.append, login_wait_sec=60)
        self.drv.page = mock.MagicMock()
        self.drv.page.evaluate.return_value = False          # không có xác minh
        self.drv._alert = mock.MagicMock()

    def test_waits_for_final_stable_image(self):
        img = {"src": "https://files.example/x.png", "w": 1536, "h": 1024}
        snaps = [{"imgs": [], "creating": True},
                 {"imgs": [{**img, "blurred": True}]},              # xem trước mờ
                 {"imgs": [{**img, "blurred": False}]},
                 {"imgs": [{**img, "blurred": False}]}]             # ổn định 2 lượt
        with mock.patch.object(self.drv, "_snapshot", side_effect=snaps), \
                mock.patch.object(self.drv, "_download", return_value=b"PNG") as dl:
            self.assertEqual(self.drv._wait_image(), b"PNG")
        dl.assert_called_once_with(img["src"])

    def test_limit_reply_is_not_retryable(self):
        snap = {"imgs": [], "reply": "You've hit the image generation limit, try again later"}
        clock = iter(range(0, 10_000, 30))
        with mock.patch.object(self.drv, "_snapshot", return_value=snap), \
                mock.patch.object(C.time, "time", side_effect=lambda: next(clock)):
            with self.assertRaises(T.ThumbAIError) as cm:
                self.drv._wait_image()
        self.assertFalse(cm.exception.retryable)
        self.assertIn("hết lượt", str(cm.exception))

    def test_text_only_reply_nudges_once_then_no_image(self):
        snap = {"imgs": [], "reply": "Sure! Here is a description of the thumbnail."}
        box = mock.MagicMock()
        clock = iter(range(0, 10_000, 20))
        with mock.patch.object(self.drv, "_snapshot", return_value=snap), \
                mock.patch.object(browser_base, "query_first", return_value=box), \
                mock.patch.object(self.drv, "_type") as typ, \
                mock.patch.object(self.drv, "_send") as send, \
                mock.patch.object(C.time, "time", side_effect=lambda: next(clock)):
            with self.assertRaises(C._NoImage):
                self.drv._wait_image()
        typ.assert_called_once_with(box, C.NUDGE)
        send.assert_called_once()

    def test_logged_out_waits_for_human_then_reloads(self):
        states = iter(["logged_out", "logged_out", "logged_in", "logged_in"])
        with mock.patch.object(C, "page_state", side_effect=lambda *a: next(states)):
            self.drv._ensure_ready("https://chatgpt.com/?temporary-chat=true")
        self.drv._alert.assert_called_once()                   # kéo cửa sổ lên 1 lần
        self.drv.page.goto.assert_called_once()                # mở lại sau đăng nhập
        self.drv.page.click.assert_not_called()                # KHÔNG bấm gì
        self.assertTrue(C.login_status()["logged_in"])

    def test_challenge_timeout_never_clicks(self):
        clock = iter(range(0, 10_000, 30))
        with mock.patch.object(C, "page_state", return_value="challenge"), \
                mock.patch.object(C.time, "time", side_effect=lambda: next(clock)):
            with self.assertRaisesRegex(T.ThumbAIError, "xác minh"):
                self.drv._ensure_ready("https://chatgpt.com/")
        self.drv.page.click.assert_not_called()
        self.drv.page.mouse.click.assert_not_called()

    def test_default_is_regular_chat(self):
        # 2026-10-25: chat tạm thời không có công cụ vẽ ảnh → mặc định chat thường.
        with mock.patch.object(C.config, "load_overrides", return_value={}):
            self.assertFalse(C._ChatGPT(self.logs.append, 60).temporary)
        with mock.patch.object(C.config, "load_overrides",
                               return_value={"chatgpt_temporary_chat": True}):
            self.assertTrue(C._ChatGPT(self.logs.append, 60).temporary)
        self.drv.temporary = False
        self.drv.sess = object()
        calls = []

        def once(text, ref_path, temporary):
            calls.append(temporary)
            return b"IMG"
        with mock.patch.object(self.drv, "_draw_once", side_effect=once):
            self.assertEqual(self.drv.draw("prompt", b"jpeg"), b"IMG")
        self.assertEqual(calls, [False])
        self.drv.sess = None
        self.drv.close()

    def test_temporary_chat_refusal_goes_to_regular_chat_not_limit(self):
        # Lời đáp thật 2026-10-25 + câu sau lượt nhắc ("can't generate" khớp
        # _LIMIT_RE) → trong chat tạm thời KHÔNG được kết luận hết lượt.
        for reply in ("I can't access the image-generation tool in this temporary chat, "
                      "and the source image isn't available to edit here.",
                      "I can't generate images in a temporary chat."):
            snap = {"imgs": [], "reply": reply}
            clock = iter(range(0, 10_000, 30))
            self.drv._in_temp = True
            with mock.patch.object(self.drv, "_snapshot", return_value=snap), \
                    mock.patch.object(self.drv, "_type") as typ, \
                    mock.patch.object(C.time, "time", side_effect=lambda: next(clock)):
                with self.assertRaises(C._NoImage):
                    self.drv._wait_image()
            typ.assert_not_called()                            # không nhắc trong chat tạm
        self.drv._in_temp = False

    def test_temporary_chat_without_image_falls_back_to_normal_chat(self):
        self.drv.temporary = True                              # override bật chat tạm
        self.drv.sess = object()                               # coi như đã mở
        calls = []

        def once(text, ref_path, temporary):
            calls.append(temporary)
            if temporary:
                raise C._NoImage("no image")
            return b"IMG"
        with mock.patch.object(self.drv, "_draw_once", side_effect=once):
            self.assertEqual(self.drv.draw("prompt", b"jpeg"), b"IMG")
        self.assertEqual(calls, [True, False])
        self.drv.sess = None
        self.drv.close()


class GenerateTests(unittest.TestCase):
    def setUp(self):
        tmp = TemporaryDirectory(prefix="chatgpt-thumb-gen-")
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        self.cover = root / "images" / "0.png"
        self.cover.parent.mkdir(parents=True)
        Image.new("RGB", (1376, 768), (90, 120, 100)).save(self.cover)
        self.out = root / "final" / "thumbnail.png"
        self.out.parent.mkdir(parents=True)
        p = mock.patch.object(T.time, "sleep")
        p.start()
        self.addCleanup(p.stop)

    def _gen(self, **kw):
        args = dict(title="Moon Lake", subtitle="Sleep · Calm", recipe=RECIPE,
                    cover=self.cover, out=self.out, settings=_settings())
        args.update(kw)
        return C.generate(**args)

    def test_draws_crops_marks_and_caches_without_browser(self):
        with mock.patch.object(C._ChatGPT, "draw", return_value=_png()) as draw, \
                mock.patch.object(C._ChatGPT, "open") as opened:
            rec = self._gen()
            self.assertEqual(rec["engine"], "chatgpt")
            self.assertEqual(rec["model"], C.MODEL_TAG)
            with Image.open(self.out) as im:
                self.assertEqual(im.size, (1280, 720))
            self.assertEqual(T.read_meta(self.out.parent)["current"], "chatgpt")
            again = self._gen()                               # ghép lại → dùng lại
            self.assertTrue(again["cached"])
            self.assertEqual(draw.call_count, 1)
            self._gen(force=True)                             # nút bấm → vẽ mới
            self.assertEqual(draw.call_count, 2)
        opened.assert_not_called()                            # draw đã mock

    def test_reuses_paid_api_image_when_default_switches(self):
        with mock.patch.object(T, "call_model", return_value=_png(2752, 1536)) as call:
            T.generate(title="Moon Lake", subtitle="Sleep · Calm", recipe=RECIPE,
                       cover=self.cover, out=self.out, settings=_settings(engine="api"))
        self.assertEqual(call.call_count, 1)
        with mock.patch.object(C._ChatGPT, "draw") as draw:
            rec = self._gen()
        draw.assert_not_called()
        self.assertTrue(rec["cached"])
        self.assertEqual(T.read_meta(self.out.parent)["current"], "ai")

    def test_operation_cancelled_becomes_job_cancelled_and_restores_check(self):
        prev = lambda: False  # noqa: E731
        browser_base.set_cancel_check(prev)
        self.addCleanup(browser_base.set_cancel_check, None)
        with mock.patch.object(C._ChatGPT, "draw",
                               side_effect=browser_base.OperationCancelled()):
            with self.assertRaises(JobCancelled):
                self._gen(cancel_check=lambda: True)
        self.assertIs(browser_base._cancel_local.fn, prev)


class RenderChainTests(unittest.TestCase):
    """service.render_thumbnail: ChatGPT → (API nếu bật + có key) → PIL."""

    def setUp(self):
        tmp = TemporaryDirectory(prefix="chatgpt-thumb-svc-")
        self.addCleanup(tmp.cleanup)
        p = mock.patch.object(config, "MEDIA_ROOT", Path(tmp.name))
        p.start()
        self.addCleanup(p.stop)
        img = config.images_dir(78, "Moon Lake")
        img.mkdir(parents=True)
        Image.new("RGB", (1376, 768), (30, 40, 60)).save(img / "0.png")
        self.final = config.final_dir(78, "Moon Lake")
        p = mock.patch.object(T.time, "sleep")
        p.start()
        self.addCleanup(p.stop)

    def _render(self, settings, chat=None, api=None, **kw):
        with mock.patch.object(T, "load_settings", return_value=settings), \
                mock.patch.object(service, "_title_look",
                                  return_value=(None, "Calm · Sleep", {}, RECIPE)), \
                mock.patch.object(C._ChatGPT, "draw", **(chat or {"return_value": _png()})) as d, \
                mock.patch.object(T, "call_model", **(api or {"return_value": _png()})) as a, \
                mock.patch("backend.video.thumbnail.render_title", return_value=True) as pil:
            service.render_thumbnail(78, "Moon Lake", **kw)
        return d, a, pil

    def test_default_engine_is_chatgpt(self):
        d, a, pil = self._render(_settings())
        d.assert_called_once()
        a.assert_not_called()
        pil.assert_not_called()
        self.assertEqual(T.read_meta(self.final)["current"], "chatgpt")

    def test_chatgpt_error_falls_back_to_api_then_pil(self):
        err = {"side_effect": T.ThumbAIError("Chưa đăng nhập ChatGPT")}
        d, a, pil = self._render(_settings(), chat=err)
        a.assert_called_once()                               # API bật + có key
        self.assertEqual(T.read_meta(self.final)["current"], "ai")
        d, a, pil = self._render(_settings(enabled=False), chat=err, force_ai=True)
        a.assert_not_called()                                # API tắt tự động
        pil.assert_called_once()
        meta = T.read_meta(self.final)
        self.assertEqual(meta["current"], "pil")
        self.assertIn("ChatGPT lỗi", meta["current_note"])

    def test_explicit_chatgpt_never_falls_to_paid_api(self):
        err = {"side_effect": T.ThumbAIError("x")}
        d, a, pil = self._render(_settings(), chat=err, engine="chatgpt", force_ai=True)
        a.assert_not_called()
        pil.assert_called_once()

    def test_pil_engine_setting_skips_browser(self):
        d, a, pil = self._render(_settings(engine="pil"))
        d.assert_not_called()
        a.assert_not_called()
        pil.assert_called_once()

    def test_cancel_is_not_swallowed_into_pil(self):
        with self.assertRaises(JobCancelled):
            self._render(_settings(), chat={"side_effect": JobCancelled()})


class MonitorStateTests(unittest.TestCase):
    def test_ready_for_six_seconds_counts_as_logged_in(self):
        from backend.video import monitor_browser as M
        since = [None]
        with mock.patch.object(C, "page_state", return_value="ready"), \
                mock.patch.object(M.time, "time", side_effect=[100, 107]):
            self.assertEqual(M._chatgpt_state(None, {}, since), "ready")
            self.assertEqual(M._chatgpt_state(None, {}, since), "logged_in")
        self.assertTrue(C.login_status()["logged_in"])
        with mock.patch.object(C, "page_state", return_value="logged_out"):
            self.assertEqual(M._chatgpt_state(None, {}, [None]), "logged_out")
        self.assertFalse(C.login_status()["logged_in"])

    def test_login_file_written_on_change_or_every_30s(self):
        from backend.video import monitor_browser as M
        rec = [None, 0.0]
        with mock.patch.object(C, "page_state", return_value="logged_out"), \
                mock.patch.object(M.time, "time", side_effect=[1000, 1010, 1031]), \
                mock.patch.object(C, "record_login") as rl:
            for _ in range(3):
                M._chatgpt_state(None, {}, [None], rec)
        self.assertEqual(rl.call_count, 2)          # lượt 1 (đổi) + lượt 3 (≥30s)
        with mock.patch.object(C, "page_state", return_value="loading"), \
                mock.patch.object(C, "record_login") as rl:
            M._chatgpt_state(None, {}, [None], rec)
        rl.assert_not_called()                       # trạng thái chưa rõ → không ghi


class EndpointTests(unittest.TestCase):
    def setUp(self):
        from fastapi.testclient import TestClient
        from sqlmodel import Session, SQLModel, create_engine
        from backend.database import get_session
        from backend.main import app
        from backend.models import Project
        tmp = TemporaryDirectory(prefix="chatgpt-thumb-ep-")
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
        self.jm = mock.MagicMock()
        self.jm.is_busy.return_value = False
        self.jm.get.return_value = None
        for p in (mock.patch.object(config, "MEDIA_ROOT", root / "media"),
                  mock.patch("backend.routers.video.video_job_manager", self.jm),
                  mock.patch("backend.video.job_manager.video_job_manager", self.jm)):
            p.start()
            self.addCleanup(p.stop)
        self.client = TestClient(app)

    def test_settings_engine_get_patch(self):
        body = self.client.get("/api/settings/thumbnail-ai").json()
        self.assertEqual(body["engine"], "chatgpt")
        self.assertEqual([e["key"] for e in body["engines"]], ["chatgpt", "api", "pil"])
        self.assertIn("chatgpt_logged_in", body)
        r = self.client.patch("/api/settings/thumbnail-ai", json={"engine": "api"})
        self.assertEqual(r.json()["engine"], "api")
        r = self.client.patch("/api/settings/thumbnail-ai", json={"engine": "gemini"})
        self.assertEqual(r.status_code, 400)

    def test_open_chatgpt_window_job_zero_and_busy_guard(self):
        r = self.client.post("/api/settings/thumbnail-chatgpt/open")
        self.assertEqual(r.status_code, 200)
        args, kw = self.jm.submit.call_args
        self.assertEqual(args[:2], (0, "browser-chatgpt"))
        self.assertEqual(kw["site"], "chatgpt")
        self.jm.is_busy.return_value = True
        self.assertEqual(self.client.post("/api/settings/thumbnail-chatgpt/open").status_code,
                         409)
        self.assertEqual(self.client.post("/api/settings/thumbnail-chatgpt/close").status_code,
                         409)                                 # không có cửa sổ nào mở

    def test_video_thumbnail_default_engine_is_chatgpt(self):
        img = config.images_dir(self.pid, "Moon Lake")
        img.mkdir(parents=True)
        Image.new("RGB", (64, 36)).save(img / "0.png")
        with mock.patch.object(T, "load_settings", return_value=T.ThumbAISettings()):
            r = self.client.post(f"/api/projects/{self.pid}/video/thumbnail", json={})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["engine"], "chatgpt")       # không cần API key
        self.assertEqual(self.jm.submit.call_args.args[-1], "chatgpt")


if __name__ == "__main__":
    unittest.main()
