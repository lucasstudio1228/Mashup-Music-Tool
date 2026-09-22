"""
video/youtube_driver.py — Điều khiển YouTube Studio (qua trình duyệt GPMLogin đã
đăng nhập sẵn) để TẢI VIDEO LÊN và LƯU BẢN NHÁP.

Kết nối tới trình duyệt của profile GPMLogin bằng Playwright connect_over_cdp
(endpoint do gpm_client.start_profile trả về). KHÔNG tự đăng nhập Google — profile
phải đã đăng nhập sẵn kênh.

⚠️  AN TOÀN: driver này TUYỆT ĐỐI KHÔNG bấm Publish/Xuất bản/Next-to-public. Nó
chỉ điền metadata rồi ĐÓNG dialog → YouTube tự lưu thành Draft (bản nháp). Người
dùng tự xuất bản sau.

Selector rất dễ đổi + phụ thuộc ngôn ngữ tài khoản → gom trong
config.YOUTUBE_SELECTORS (EN + VN). Bước không tìm thấy selector KHÔNG-bắt-buộc
sẽ được ghi vào 'warnings' và bỏ qua; bước bắt buộc (đăng nhập, ô tiêu đề, upload)
thất bại thì raise.
"""
from __future__ import annotations

import sys
import time
from typing import Callable, Optional

# Import browser_base TRƯỚC để nó chèn .runtime_packages311 vào sys.path (nếu có)
# và để tái dùng helper selector.
from . import browser_base as _bb
from . import config


def _log_fn(log):
    def _l(m: str):
        if log:
            try:
                log(m)
            except Exception:
                pass
    return _l


def _type_into(page, loc, text: str) -> bool:
    """Xoá sạch rồi gõ text vào 1 ô (contenteditable hoặc input)."""
    try:
        loc.click()
        page.keyboard.press("Control+A")
        page.keyboard.press("Delete")
        loc.type(text, delay=8)
        return True
    except Exception:
        try:
            loc.fill(text)
            return True
        except Exception:
            return False


class _CDPPlaywright:
    """Context manager: mở Playwright + connect_over_cdp, dọn dẹp đúng cách.
    Đặt WindowsProactorEventLoopPolicy giống browser_base (cần cho worker thread)."""

    def __init__(self, cdp_endpoint: str):
        self.cdp_endpoint = cdp_endpoint
        self._pw = None
        self.browser = None
        self._old_policy = None

    def __enter__(self):
        import asyncio as _asyncio
        if sys.platform == "win32":
            try:
                self._old_policy = _asyncio.get_event_loop_policy()
                _asyncio.set_event_loop_policy(
                    _asyncio.WindowsProactorEventLoopPolicy())
            except Exception:
                self._old_policy = None
        from playwright.sync_api import sync_playwright
        self._pw = sync_playwright().start()
        self.browser = self._pw.chromium.connect_over_cdp(
            self.cdp_endpoint, timeout=60_000)
        return self

    def __exit__(self, *exc):
        # KHÔNG close browser (đó là trình duyệt của profile — để gpm_client
        # close_profile lo). Chỉ dừng playwright + khôi phục event loop policy.
        try:
            if self._pw:
                self._pw.stop()
        finally:
            try:
                import asyncio as _asyncio
                if self._old_policy is not None:
                    _asyncio.set_event_loop_policy(self._old_policy)
            except Exception:
                pass


def upload_draft(
    cdp_endpoint: str,
    video_path: str,
    title: str,
    description: str,
    hashtags: str,
    language: str = "en-US",
    progress_cb: Optional[Callable[[str, float], None]] = None,
    log=None,
) -> dict:
    """Tải video lên YouTube Studio của profile đang mở & LƯU BẢN NHÁP.
    Trả {'status':'draft','warnings':[...]}. Raise nếu lỗi bắt buộc."""
    _log = _log_fn(log)

    def _p(msg: str, pct: float):
        _log(msg)
        if progress_cb:
            try:
                progress_cb(msg, pct)
            except Exception:
                pass

    sel = config.get_selectors("youtube")
    warnings: list[str] = []

    with _CDPPlaywright(cdp_endpoint) as cdp:
        browser = cdp.browser
        # Dùng context sẵn có của profile (đã đăng nhập), tạo tab mới.
        context = browser.contexts[0] if browser.contexts else browser.new_context()
        context.set_default_timeout(config.BROWSER.action_timeout_ms)
        context.set_default_navigation_timeout(config.BROWSER.nav_timeout_ms)
        page = context.new_page()

        _p("Mở YouTube Studio…", 5.0)
        page.goto(config.YOUTUBE_STUDIO_URL, wait_until="domcontentloaded")

        # 1) Kiểm tra đã đăng nhập (nút Create hiện ra).
        if not _bb.query_first(page, sel["studio_ready"], timeout_ms=30_000):
            raise RuntimeError(
                "Chưa vào được YouTube Studio (profile có thể CHƯA đăng nhập "
                "kênh, hoặc giao diện đổi). Hãy mở profile này trong GPMLogin và "
                "đăng nhập YouTube 1 lần, rồi thử lại.")

        # 2) Create → Upload videos
        _p("Bấm Tạo → Tải video lên…", 12.0)
        if not _bb.click_first(page, sel["create_button"], timeout_ms=15_000):
            raise RuntimeError("Không thấy nút 'Tạo' (Create) trong Studio.")
        page.wait_for_timeout(800)
        _bb.click_first(page, sel["upload_menu_item"], timeout_ms=8_000)

        # 3) Set file video vào input[type=file]
        _p("Chọn file video để upload…", 18.0)
        if not _bb.upload_first(page, sel["file_input"], video_path,
                                timeout_ms=20_000):
            raise RuntimeError(
                "Không tìm thấy ô chọn file để upload video trong dialog Studio.")

        # 4) Chờ dialog Details (ô tiêu đề) xuất hiện
        _p("Chờ hộp thoại chi tiết…", 28.0)
        title_loc = _bb.query_first(page, sel["title_box"], timeout_ms=60_000)
        if not title_loc:
            raise RuntimeError(
                "Upload rồi nhưng không thấy ô Tiêu đề (giao diện Studio đổi?).")

        # 5) Tiêu đề (xoá tên file mặc định trước)
        _p("Điền tiêu đề…", 40.0)
        if not _type_into(page, title_loc, title):
            warnings.append("Không điền được tiêu đề.")

        # 6) Mô tả (đã chứa hashtag ở cuối do youtube_meta ghép sẵn)
        desc_loc = _bb.query_first(page, sel["description_box"], timeout_ms=8_000)
        if desc_loc:
            _p("Điền mô tả…", 48.0)
            if not _type_into(page, desc_loc, description):
                warnings.append("Không điền được mô tả.")
        else:
            warnings.append("Không thấy ô mô tả.")

        # 7) Hiện thêm (Show more) để mở cấu hình mở rộng
        _p("Mở 'Hiện thêm'…", 55.0)
        if not _bb.click_first(page, sel["show_more"], timeout_ms=8_000):
            warnings.append("Không bấm được 'Hiện thêm' (một số mục có thể bỏ qua).")
        page.wait_for_timeout(600)

        # 8) Thẻ (tags) từ hashtags — bỏ dấu '#'
        tags_loc = _bb.query_first(page, sel["tags_box"], timeout_ms=5_000)
        if tags_loc and hashtags:
            tag_text = ", ".join(
                t.lstrip("#") for t in hashtags.split() if t.strip())
            try:
                tags_loc.click()
                tags_loc.type(tag_text[:480], delay=5)
            except Exception:
                warnings.append("Không điền được thẻ (tags).")

        # 9) Đối tượng: KHÔNG dành cho trẻ em
        _p("Chọn 'Không dành cho trẻ em'…", 62.0)
        if not _bb.click_first(page, sel["mfk_no"], timeout_ms=8_000):
            warnings.append("Không đặt được 'Không dành cho trẻ em' (BẮT BUỘC — "
                            "hãy kiểm tra thủ công trước khi xuất bản).")

        # 10) Nội dung có yếu tố AI (Altered content) = Có
        _p("Khai báo có yếu tố AI…", 68.0)
        # Mở mục nếu là dòng bấm để mở radios.
        _bb.click_first(page, sel["altered_content_section"], timeout_ms=3_000)
        page.wait_for_timeout(300)
        if not _bb.click_first(page, sel["altered_content_yes"], timeout_ms=6_000):
            warnings.append("Không đặt được 'Nội dung có yếu tố AI = Có' — kiểm "
                            "tra thủ công.")

        # 11) 'Không có sản phẩm được quảng cáo' = để mặc định (KHÔNG tick) → OK.

        # 12) Ngôn ngữ video = English (United States)
        _p("Đặt ngôn ngữ video (English US)…", 74.0)
        if _bb.click_first(page, sel["video_language_dropdown"], timeout_ms=6_000):
            page.wait_for_timeout(400)
            try:
                page.keyboard.type("English (United States)", delay=10)
            except Exception:
                pass
            if not _bb.click_first(page, sel["video_language_en_us"],
                                   timeout_ms=6_000):
                warnings.append("Không chọn được ngôn ngữ English (United States).")
        else:
            warnings.append("Không mở được dropdown ngôn ngữ video.")

        # 13) Chờ upload xử lý một chút để draft được ghi lại chắc chắn.
        _p("Chờ upload xử lý…", 82.0)
        page.wait_for_timeout(4000)

        # 14) ĐÓNG dialog → YouTube TỰ LƯU DRAFT. Không bao giờ bấm Next→Publish.
        _p("Đóng hộp thoại để lưu bản nháp…", 90.0)
        closed = _bb.click_first(page, sel["close_dialog"], timeout_ms=10_000)
        if not closed:
            warnings.append("Không thấy nút Đóng — video có thể vẫn được lưu nháp "
                            "tự động. Hãy kiểm tra tab 'Nội dung' trong Studio.")
        page.wait_for_timeout(1500)
        # Nếu hiện confirm 'saved as draft' thì tốt; không thì cũng đã auto-save.
        _bb.click_first(page, sel["saved_draft_confirm"], timeout_ms=2_000)

        _p("Đã lưu bản nháp trên YouTube.", 98.0)
        try:
            page.close()
        except Exception:
            pass

    return {"status": "draft", "warnings": warnings}
