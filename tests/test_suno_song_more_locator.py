"""Nút ⋯ trên trang bài Suno — giao diện mới 2026-11-17.

Dò LIVE (data/_probe_song_header_1117.py): nút ⋯ đầu trang bài có aria-label
«More options for <tên bài>»; trang bài thêm mục «Similar songs», mỗi hàng có ⋯
(aria đúng bằng «More options») + link tới bài KHÁC. Selector cũ khớp nguyên văn
«More options» → bấm nhầm ⋯ của Similar songs (0/15 bài tải được).

Dựng lại DOM đó trên Chromium headless THẬT; trang được phục vụ qua page.route
(không ra mạng, không mở profile Suno, 0 credit).
"""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).parent.parent))
from backend.video import browser_base as bb      # noqa: E402 - nạp .runtime_packages311
from backend.video import config                  # noqa: E402
from backend.video.suno_driver import SunoDriver  # noqa: E402

SONG = "0a1b2c3d-0000-4000-8000-00000000abcd"
URL = f"https://suno.test/song/{SONG}"


def _btn(id_, aria, x, y, size=40):
    return (f'<button id="{id_}" aria-label="{aria}" aria-haspopup="menu" '
            f'style="position:absolute;left:{x}px;top:{y}px;width:{size}px;'
            f'height:{size}px">⋯</button>')


def _page(header_aria="More options for Sleeping by the Lake", with_header=True,
          similar_y=500):
    # Hàng Similar songs: link bài khác + ⋯ «More options» (cả hàng ĐẶT TRÊN
    # nút đầu trang ở 1 test để chắc chắn lọc theo link chứ không theo độ cao).
    rows = "".join(
        f'<div class="row"><a href="/song/other-{i}" style="position:absolute;'
        f'left:900px;top:{similar_y + i * 60}px">Other {i}</a>'
        f'{_btn(f"sim{i}", "More options", 1200, similar_y + i * 60, 28)}</div>'
        for i in range(3))
    header = (f'<div class="header"><h1>Sleeping by the Lake</h1>'
              f'<button aria-label="Like">♥</button>'
              f'{_btn("head", header_aria, 1500, 200, 56)}</div>') if with_header else ""
    return f"""<html><body style="margin:0;width:1600px;height:1000px">
<nav>{_btn("side", "More options", 10, 300)}</nav>
<main><div class="page">{header}<section><h2>Similar songs</h2>{rows}</section></div></main>
<footer><a href="/song/{SONG}" style="position:absolute;left:300px;top:960px">Now playing</a>
{_btn("player", "More options for Sleeping by the Lake", 1400, 950)}</footer>
</body></html>"""


class SongMoreLocatorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
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
        self.ctx = self.browser.new_context(viewport={"width": 1600, "height": 1000})
        self.addCleanup(self.ctx.close)
        self.page = self.ctx.new_page()
        self.drv = object.__new__(SunoDriver)
        self.drv.page = self.page
        self.drv.sel = config.get_selectors("suno")

    def _load(self, html):
        self.page.route("**/*", lambda r: r.fulfill(status=200, body=html,
                                                    content_type="text/html"))
        self.page.goto(URL)

    def _marked(self):
        ok = self.page.evaluate(SunoDriver._MARK_HEADER_MORE_JS, SONG)
        ids = self.page.evaluate(
            "() => [...document.querySelectorAll('[data-mm-song-more]')].map(e => e.id)")
        return ok, ids

    def test_new_header_aria_beats_similar_songs(self):
        self._load(_page())
        self.assertEqual(self._marked(), (True, ["head"]))
        self.assertEqual(self.drv._locate_song_more(SONG).get_attribute("id"), "head")

    def test_similar_rows_above_header_still_skipped(self):
        # Nút «More options» của bài khác nằm CAO hơn nút đầu trang.
        self._load(_page(header_aria="More options", similar_y=60))
        self.assertEqual(self._marked(), (True, ["head"]))

    def test_never_marks_similar_sidebar_or_player(self):
        self._load(_page(with_header=False))
        ok, ids = self._marked()
        self.assertFalse(ok)
        self.assertEqual(ids, [])

    def test_wrong_page_marks_nothing(self):
        self._load(_page())
        self.assertFalse(self.page.evaluate(SunoDriver._MARK_HEADER_MORE_JS,
                                            "ffffffff-0000"))

    def test_config_prefers_named_header_selector(self):
        sel = config.SUNO_SELECTORS["song_more_options"] \
            if hasattr(config, "SUNO_SELECTORS") else None
        if sel is None:
            self.skipTest("không tìm thấy bảng selector Suno trong config")
        self.assertTrue(sel[0].startswith("button[aria-label^='More options for"))


if __name__ == "__main__":
    unittest.main()
