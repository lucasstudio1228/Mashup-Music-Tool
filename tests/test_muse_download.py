"""Tải clip Muse theo giao diện 2026-10-25 (BỎ nút «Download video»).

Dò live: khung video = ô role=button «Open animated video <tên>.mp4» có
data-hatch-video-src="blob:…"; lệnh tải nằm trong menu «More options» của tin
nhắn ([role=menuitem] «Download»). Test dựng lại DOM đó trên Chromium headless
THẬT (không mở GPM, không vào muse.ai) + luồng generate_clips_muse giả: tải lỗi
thì tải lại cùng video, KHÔNG gửi lại prompt (mỗi lần gửi = 1 lượt Muse).
"""
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent))
from backend.video import browser_base as bb      # noqa: E402 - nạp .runtime_packages311
from backend.video import config                  # noqa: E402
from backend.video import muse_video as mv        # noqa: E402

OLD = 30_000      # byte video cũ (trước tin user cuối)
NEW = 45_000      # byte video mới

# Mô phỏng chat Muse: video cũ → tin user → video mới; «More options» mở menu Download.
_HTML = """<html><body>
<div data-message-item="true" data-message-role="assistant" data-message-id="a1">
  <div data-testid="hatch-chat-attachment-presentation-video"><div id="old" role="button"
       aria-label="Open animated video old.mp4"><video></video></div></div>
  <button aria-label="More options">⋯</button></div>
<div data-message-item="true" data-message-role="user" data-message-id="u1">prompt</div>
<div data-message-item="true" data-message-role="assistant" data-message-id="a2">
  <div data-testid="hatch-chat-attachment-presentation-video"><div id="new" role="button"
       aria-label="Open animated video new.mp4"><video></video></div></div>
  <button aria-label="React">r</button>
  <button aria-label="More options" onclick="openMenu(this)">⋯</button></div>
<script>
const mk = n => URL.createObjectURL(new Blob([new Uint8Array(n).fill(n % 251)], {type: 'video/mp4'}));
const urls = {old: mk(%OLD%), new: mk(%NEW%)};
function setSrc(id, url) {
  const w = document.getElementById(id);
  w.setAttribute('data-hatch-video-src', url); w.querySelector('video').src = url;
}
function openMenu(btn) {
  const m = document.createElement('div'); m.setAttribute('role', 'menu');
  const it = document.createElement('div'); it.setAttribute('role', 'menuitem');
  it.textContent = 'Download';
  const id = btn.parentElement.querySelector('[role=button]').id;
  it.onclick = () => { const a = document.createElement('a'); a.href = urls[id];
    a.download = id + '.mp4'; document.body.appendChild(a); a.click(); };
  m.appendChild(it); document.body.appendChild(m);
}
</script></body></html>""".replace("%OLD%", str(OLD)).replace("%NEW%", str(NEW))


class RealDomDownloadTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Như BrowserSession: test khác nạp backend.main (đặt Selector policy toàn
        # cục) → Playwright cần Proactor để spawn trình duyệt.
        import asyncio
        cls._old_policy = asyncio.get_event_loop_policy()
        if sys.platform == "win32":
            asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
        cls.pw = bb.start_playwright()
        exe = config.resolve_browser_exe()
        try:
            cls.browser = cls.pw.chromium.launch(
                headless=True, **({"executable_path": exe} if exe else {}))
        except Exception as e:                          # noqa: BLE001
            cls.pw.stop()
            asyncio.set_event_loop_policy(cls._old_policy)
            raise unittest.SkipTest(f"không mở được Chromium headless: {e}")

    @classmethod
    def tearDownClass(cls):
        import asyncio
        cls.browser.close()
        cls.pw.stop()
        asyncio.set_event_loop_policy(cls._old_policy)

    def setUp(self):
        self.ctx = self.browser.new_context(accept_downloads=True)
        self.page = self.ctx.new_page()
        self.page.set_content(_HTML)
        self._tmp = TemporaryDirectory(prefix="mv-dl-")
        self.raw = Path(self._tmp.name) / "_muse_raw" / "clip_00.mp4"

    def tearDown(self):
        self.ctx.close()
        self._tmp.cleanup()

    def test_blob_of_newest_video_after_last_user_message(self):
        self.page.evaluate("setSrc('old', urls.old); setSrc('new', urls.new)")
        with patch.object(mv, "_download_via_menu") as menu:
            mv._download(self.page, self.raw)
        menu.assert_not_called()
        self.assertEqual(self.raw.stat().st_size, NEW, "phải tải video MỚI, không phải video cũ")

    def test_menu_download_when_blob_unreadable(self):
        """blob không đọc được (fetch lỗi) → menu «More options» → «Download»
        của ĐÚNG tin nhắn chứa video mới."""
        self.page.evaluate("setSrc('old', urls.old); setSrc('new', 'blob:null/missing')")
        mv._download(self.page, self.raw)
        self.assertEqual(self.raw.stat().st_size, NEW)
        self.assertEqual(self.page.locator("[data-mm-dl='1'] #new").count(), 1)

    def test_video_not_loaded_yet_waits_for_src(self):
        """Khung ngoài màn hình là skeleton chưa có blob → đợi src rồi mới tải."""
        self.page.evaluate("setSrc('old', urls.old); setTimeout(() => setSrc('new', urls.new), 1500)")
        mv._download(self.page, self.raw)
        self.assertEqual(self.raw.stat().st_size, NEW)


class _Page:
    def wait_for_timeout(self, ms):
        pass


class _Sess:
    def __init__(self, pid):
        self.page = _Page()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class NoResendOnDownloadErrorTests(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory(prefix="mv-gen-")
        root = Path(self._tmp.name)
        self.patches = [patch.object(config, "MEDIA_ROOT", root / "media"),
                        patch.object(mv, "DEFAULT_MUSE_PROFILE_ID", "test-profile")]
        for p in self.patches:
            p.start()
        img = config.images_dir(1, "P")
        img.mkdir(parents=True)
        for i in range(3):
            (img / f"{i}.png").write_bytes(b"png")
        (img / "prompts.json").write_text(json.dumps(
            {"motions": {str(i): f"Scene {i} by the river." for i in range(40)}}),
            encoding="utf-8")
        self.clips = config.clips_dir(1, "P")
        self.clips.mkdir(parents=True)
        self.sent = []

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self._tmp.cleanup()

    def _gen(self, fake_dl):
        info = {"width": 1920, "height": 1080, "duration": 8.0, "audio": False}
        with patch.object(mv, "_GPMSession", _Sess), \
             patch.object(mv, "wait_connected", lambda *a: None), \
             patch.object(mv, "_baseline", lambda page: {}), \
             patch.object(mv, "_send", lambda page, paths, prompt: self.sent.append(prompt) or "x"), \
             patch.object(mv, "_wait_video", return_value=("ready", {})), \
             patch.object(mv, "_download", fake_dl), \
             patch.object(mv, "probe_streams", lambda raw: info), \
             patch.object(mv, "finalize_clip", lambda raw, dest, max_sec=None:
                          (dest.write_bytes(b"0" * 30_000), info)[1]):
            return mv.generate_clips_muse(1, config.PARAMS, None, project_name="P",
                                          style="2d", only=[0])

    def test_download_error_retries_same_video_without_resend(self):
        calls = []

        def flaky(page, raw):
            calls.append(raw)
            if len(calls) < 3:
                raise TimeoutError("Locator.click: Timeout 30000ms exceeded")
            raw.parent.mkdir(parents=True, exist_ok=True)
            raw.write_bytes(b"0" * 30_000)

        self._gen(flaky)
        self.assertEqual(len(calls), 3)
        self.assertEqual(len(self.sent), 1, "tải lỗi KHÔNG được gửi lại prompt")
        self.assertTrue((self.clips / "clip_00.mp4").exists())

    def test_download_broken_stops_after_one_send(self):
        """Giao diện Muse đổi (live 2026-10-25): trước đây gửi lại 3 lần = đốt 3
        lượt Muse cho cùng 1 clip; giờ dừng sau 1 lần gửi + 3 lần tải."""
        def broken(page, raw):
            raise TimeoutError("waiting for locator(\"button[aria-label='Download video']\")")

        with self.assertRaises(RuntimeError) as cm:
            self._gen(broken)
        self.assertEqual(len(self.sent), 1)
        msg = str(cm.exception)
        self.assertIn("KHÔNG gửi lại", msg)
        self.assertIn("Tạo tiếp clip thiếu", msg)


if __name__ == "__main__":
    unittest.main()
