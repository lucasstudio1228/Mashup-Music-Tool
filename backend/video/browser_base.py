"""
video/browser_base.py — Tiện ích Playwright (sync API) dùng chung cho ChatGPT
và Flow. Dùng persistent profile để GIỮ ĐĂNG NHẬP giữa các lần chạy.

Chạy trong worker thread (ThreadPoolExecutor) — không có asyncio loop nên sync
API hợp lệ (giống stem_service/demucs).

⚠️  Lần đầu: trình duyệt mở ra (headless=False), bạn TỰ ĐĂNG NHẬP ChatGPT/Flow
trong cửa sổ đó. Session được lưu vào .browser_profile nên các lần sau khỏi
đăng nhập lại.
"""
from __future__ import annotations
import time
import sys
from pathlib import Path
from typing import Optional

from .config import BROWSER, PROFILE_DIR

# Backend trên Windows có thể chạy bằng Python hệ thống khi virtualenv cũ bị
# lệch interpreter. Nạp Playwright 3.11 được đóng cục bộ cùng project.
_LOCAL_RUNTIME = Path(__file__).resolve().parents[2] / ".runtime_packages311"
if _LOCAL_RUNTIME.exists() and str(_LOCAL_RUNTIME) not in sys.path:
    sys.path.insert(0, str(_LOCAL_RUNTIME))


def _chromium_installed(pw) -> bool:
    """True nếu Chromium bundled của Playwright đã tải về."""
    try:
        exe = pw.chromium.executable_path
        return bool(exe) and Path(exe).exists()
    except Exception:
        return False


def _install_chromium() -> None:
    """Tự tải Chromium cho Playwright (lần đầu). Chạy Chromium RIÊNG, độc lập
    với trình duyệt mặc định (Cốc Cốc) của máy."""
    import subprocess
    import sys
    print("  ⬇️  Lần đầu: đang tải Chromium riêng cho tool (~150MB)...")
    subprocess.run(
        [sys.executable, "-m", "playwright", "install", "chromium"],
        check=True,
    )
    print("  ✅ Đã cài Chromium riêng cho tool.")


class BrowserSession:
    """Bọc 1 persistent browser context. Dùng như context manager."""

    def __init__(self, headless: Optional[bool] = None):
        self._pw = None
        self.context = None
        self._old_policy = None
        self.headless = BROWSER.headless if headless is None else headless

    def __enter__(self) -> "BrowserSession":
        try:
            from playwright.sync_api import sync_playwright
        except Exception as e:
            raise RuntimeError(
                "Chưa cài Playwright. Trong .venv chạy:\n"
                "  .venv\\Scripts\\pip.exe install playwright\n"
                "  .venv\\Scripts\\playwright.exe install chromium"
            ) from e

        # Windows: Playwright cần ProactorEventLoop để spawn subprocess trình
        # duyệt. Backend đặt WindowsSelectorEventLoopPolicy toàn cục (cho uvicorn),
        # mà Selector loop KHÔNG hỗ trợ create_subprocess_exec → NotImplementedError.
        # Ép Proactor cho loop Playwright tạo trong worker thread này; loop chính
        # của uvicorn đã tạo từ trước nên không bị ảnh hưởng.
        import asyncio as _asyncio
        import sys as _sys
        if _sys.platform == "win32":
            try:
                self._old_policy = _asyncio.get_event_loop_policy()
                _asyncio.set_event_loop_policy(
                    _asyncio.WindowsProactorEventLoopPolicy())
            except Exception:
                self._old_policy = None

        PROFILE_DIR.mkdir(parents=True, exist_ok=True)
        self._pw = sync_playwright().start()

        # Tự tải Chromium riêng nếu chưa có (chỉ 1 lần) → tool luôn chạy
        # Chromium độc lập, không dùng Cốc Cốc mặc định của máy.
        if not _chromium_installed(self._pw):
            try:
                _install_chromium()
            except Exception as e:
                try:
                    self._pw.stop()
                except Exception:
                    pass
                raise RuntimeError(
                    "Chưa tải được Chromium cho tool. Hãy chạy thủ công 1 lần:\n"
                    "  .venv\\Scripts\\playwright.exe install chromium\n"
                    f"(chi tiết: {type(e).__name__}: {e})"
                ) from e

        base_kwargs = dict(
            user_data_dir=str(PROFILE_DIR),
            headless=self.headless,
            slow_mo=BROWSER.slow_mo_ms,
            args=["--start-maximized", "--disable-blink-features=AutomationControlled"],
            no_viewport=True,
        )

        # Thử lần lượt: kênh cấu hình (nếu có) → Chromium bundled → chrome → msedge
        attempts: list[dict] = []
        if BROWSER.chrome_channel:
            attempts.append({**base_kwargs, "channel": BROWSER.chrome_channel})
        attempts.append(dict(base_kwargs))               # bundled chromium
        attempts.append({**base_kwargs, "channel": "chrome"})
        attempts.append({**base_kwargs, "channel": "msedge"})

        last_err: Exception | None = None
        for kw in attempts:
            try:
                self.context = self._pw.chromium.launch_persistent_context(**kw)
                last_err = None
                break
            except Exception as e:               # noqa: BLE001 - thử phương án kế
                last_err = e
                continue

        if last_err is not None or self.context is None:
            try:
                self._pw.stop()
            except Exception:
                pass
            raise RuntimeError(
                "Không mở được trình duyệt cho automation. Hãy tải trình duyệt "
                "cho Playwright (1 lần):\n"
                "  .venv\\Scripts\\playwright.exe install chromium\n"
                f"(chi tiết: {type(last_err).__name__}: {last_err})"
            ) from last_err

        self.context.set_default_timeout(BROWSER.action_timeout_ms)
        self.context.set_default_navigation_timeout(BROWSER.nav_timeout_ms)
        return self

    def __exit__(self, *exc):
        try:
            if self.context:
                self.context.close()
        finally:
            if self._pw:
                self._pw.stop()
            # Khôi phục policy Selector cho phần còn lại của tiến trình.
            try:
                import asyncio as _asyncio
                if self._old_policy is not None:
                    _asyncio.set_event_loop_policy(self._old_policy)
            except Exception:
                pass

    def new_page(self):
        return self.context.new_page()


# ── Helper thao tác selector (thử nhiều selector, fallback dần) ──

def query_first(page, selectors: list[str], timeout_ms: int = 8000):
    """Trả về locator đầu tiên tìm thấy trong danh sách selector, hoặc None."""
    deadline = time.time() + timeout_ms / 1000.0
    while time.time() < deadline:
        for sel in selectors:
            loc = page.locator(sel).first
            try:
                if loc.count() > 0 and loc.is_visible():
                    return loc
            except Exception:
                continue
        page.wait_for_timeout(300)
    return None


def click_first(page, selectors: list[str], timeout_ms: int = 8000) -> bool:
    loc = query_first(page, selectors, timeout_ms)
    if loc is None:
        return False
    try:
        loc.click()
        return True
    except Exception:
        return False


def fill_first(page, selectors: list[str], text: str,
               timeout_ms: int = 8000) -> bool:
    loc = query_first(page, selectors, timeout_ms)
    if loc is None:
        return False
    try:
        loc.click()
        # contenteditable dùng type; textarea dùng fill
        try:
            loc.fill(text)
        except Exception:
            loc.type(text, delay=10)
        return True
    except Exception:
        return False


def upload_first(page, selectors: list[str], file_path: str,
                 timeout_ms: int = 8000) -> bool:
    """Set file cho input[type=file] đầu tiên khớp."""
    deadline = time.time() + timeout_ms / 1000.0
    while time.time() < deadline:
        for sel in selectors:
            loc = page.locator(sel).first
            try:
                if loc.count() > 0:
                    loc.set_input_files(file_path)
                    return True
            except Exception:
                continue
        page.wait_for_timeout(300)
    return False


def wait_for_manual_login(page, ready_selectors: list[str],
                          site_name: str, timeout_sec: int = 300) -> None:
    """
    Chờ tới khi UI đã sẵn sàng (đã đăng nhập). Nếu quá timeout → raise với
    hướng dẫn rõ ràng.
    """
    if query_first(page, ready_selectors, timeout_ms=timeout_sec * 1000):
        return
    raise RuntimeError(
        f"Chưa đăng nhập {site_name} hoặc giao diện đã đổi. "
        f"Hãy đăng nhập trong cửa sổ trình duyệt vừa mở, rồi chạy lại. "
        f"Nếu đã đăng nhập mà vẫn lỗi → cần cập nhật selector trong "
        f"backend/video/config.py."
    )
