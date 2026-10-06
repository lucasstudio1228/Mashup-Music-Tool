"""Mở cửa sổ Flow/Gemini bằng ĐÚNG profile tool dùng — để người dùng theo dõi,
tự đăng nhập, kiểm tra tài khoản. Chạy như 1 job video (worker duy nhất) nên
không bao giờ tranh profile với tác vụ ảnh/clip. KHÔNG tự đăng nhập, không
nhập mật khẩu, không thao tác gì trên trang."""
import time
from typing import Callable

from . import config
from .browser_base import BrowserSession

SITES = {
    "flow":   ("Flow",   config.FLOW_URL),
    "gemini": ("Gemini", config.GEMINI_URL),
}


def run_monitor_browser(project_id: int, progress_cb: Callable[[str, float], None],
                        site: str = "flow", hold_seconds: int = 1800) -> dict:
    """Giữ cửa sổ mở tới khi bấm 'Đóng trình duyệt' (huỷ hợp tác), người dùng
    tự đóng cửa sổ, hoặc hết hold_seconds."""
    from .job_manager import JobCancelled

    label, url = SITES[site]
    progress_cb(f"Đang mở trình duyệt {label} (profile của tool)…", 5.0)
    with BrowserSession(headless=False, site=site) as bs:
        page = bs.new_page()
        try:
            page.goto(url, wait_until="domcontentloaded")
        except Exception:
            pass
        deadline = time.time() + hold_seconds
        try:
            while time.time() < deadline:
                left = int(deadline - time.time()) // 60
                progress_cb(f"Cửa sổ {label} đang mở — tự đăng nhập/kiểm tra nếu cần "
                            f"(tool KHÔNG nhập mật khẩu). Bấm 'Đóng trình duyệt' khi xong "
                            f"(tự đóng sau ~{left} phút).", 50.0)
                try:
                    if page.is_closed() and not bs.context.pages:
                        progress_cb(f"Cửa sổ {label} đã bị đóng tay.", 95.0)
                        return {"site": site, "closed_by": "user"}
                    live = page if not page.is_closed() else bs.context.pages[0]
                    live.wait_for_timeout(1500)
                    page = live
                except JobCancelled:
                    raise
                except Exception:
                    progress_cb(f"Cửa sổ {label} đã đóng.", 95.0)
                    return {"site": site, "closed_by": "user"}
        except JobCancelled:
            progress_cb("Đang đóng trình duyệt…", 95.0)
            raise
    return {"site": site, "closed_by": "timeout"}
