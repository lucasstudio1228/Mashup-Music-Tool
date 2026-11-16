"""Mashup Music Tool — ứng dụng desktop (.exe), KHÔNG cần mở trình duyệt.

Double-click MashupMusicTool.exe:
  1. Chạy backend (uvicorn, cổng 8000) NGẦM bằng .venv cạnh file exe — không
     có cửa sổ đen; log ghi vào data/logs/backend.log.
     Nếu backend đã chạy sẵn (vd mở bằng start.bat) thì dùng luôn, và khi đóng
     app sẽ KHÔNG tắt backend đó.
  2. Mở cửa sổ ứng dụng riêng (WebView2 có sẵn trong Windows 10/11) hiển thị tool.
     Backend có sẵn đó bị tắt (đóng cửa sổ start.bat…) → app TỰ bật backend
     riêng rồi tải lại trang (từ đây app sở hữu backend).
  3. Đóng cửa sổ → tắt backend do app đã bật. Đang có tác vụ chạy (Suno, Mix,
     Video, Upload, lô sản xuất…) thì hỏi lại trước khi đóng.

Build lại exe:  build_exe.bat   (→ MashupMusicTool.exe ở thư mục gốc)
Chạy thử không cần build:  .venv\\Scripts\\python.exe launcher.py
"""
from __future__ import annotations

import ctypes
import json
import os
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

APP_NAME = "Mashup Music Tool"
HOST, PORT = "127.0.0.1", 8000
URL = f"http://{HOST}:{PORT}"
HEALTH = f"{URL}/api/projects"
TASKS = f"{URL}/api/tasks/active"
START_TIMEOUT_SEC = 180

MB_OK, MB_YESNO, MB_ICONERROR, MB_ICONWARNING = 0x0, 0x4, 0x10, 0x30
MB_TOPMOST, IDYES = 0x40000, 6
CREATE_NO_WINDOW = 0x08000000


def base_dir() -> Path:
    if getattr(sys, "frozen", False):          # đã đóng gói .exe
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def message_box(text: str, title: str = APP_NAME, flags: int = MB_OK) -> int:
    """Hộp thoại Windows gốc — gọi được từ mọi luồng (kể cả lúc đóng cửa sổ)."""
    try:
        return ctypes.windll.user32.MessageBoxW(None, text, title, flags | MB_TOPMOST)
    except Exception:                          # noqa: BLE001
        print(text)
        return IDYES


def single_instance() -> bool:
    """Chỉ 1 cửa sổ app cùng lúc (mutex có tên của Windows)."""
    try:
        ctypes.windll.kernel32.CreateMutexW(None, False, "Local\\MashupMusicToolDesktop")
        return ctypes.windll.kernel32.GetLastError() != 183   # ERROR_ALREADY_EXISTS
    except Exception:                          # noqa: BLE001
        return True


def _get(url: str, timeout: float = 1.5):
    with urllib.request.urlopen(url, timeout=timeout) as r:   # noqa: S310 — localhost
        return r.status, r.read()


def backend_alive() -> bool:
    try:
        return _get(HEALTH)[0] < 500
    except Exception:                          # noqa: BLE001
        return False


def active_tasks() -> list[dict]:
    try:
        return json.loads(_get(TASKS, timeout=3)[1]).get("tasks") or []
    except Exception:                          # noqa: BLE001
        return []


class Backend:
    """Tiến trình uvicorn do app bật (None nếu dùng backend có sẵn)."""

    def __init__(self, root: Path):
        self.root = root
        self.proc: subprocess.Popen | None = None
        self.log_path = root / "data" / "logs" / "backend.log"

    def start(self) -> None:
        py = self.root / ".venv" / "Scripts" / "python.exe"
        if not py.exists():
            raise RuntimeError(
                f"Không thấy {py}.\nHãy chạy install.bat trước, và để "
                f"MashupMusicTool.exe trong thư mục tool (cạnh start.bat).")
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        if self.log_path.exists():             # giữ log lần chạy trước
            self.log_path.replace(self.log_path.with_suffix(".prev.log"))
        env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
        log = open(self.log_path, "ab", buffering=0)          # noqa: SIM115
        self.proc = subprocess.Popen(
            [str(py), "-m", "uvicorn", "backend.main:app",
             "--host", HOST, "--port", str(PORT)],
            cwd=str(self.root), env=env, stdin=subprocess.DEVNULL,
            stdout=log, stderr=subprocess.STDOUT, creationflags=CREATE_NO_WINDOW)

    def exited(self) -> bool:
        return self.proc is not None and self.proc.poll() is not None

    def log_tail(self, n: int = 25) -> str:
        try:
            lines = self.log_path.read_text(encoding="utf-8", errors="replace").splitlines()
            return "\n".join(lines[-n:])
        except OSError:
            return ""

    def stop(self) -> None:
        """Tắt backend + mọi tiến trình con của nó (ffmpeg, trình duyệt
        Playwright…) — giống đóng cửa sổ start.bat."""
        if self.proc is None or self.proc.poll() is not None:
            return
        subprocess.run(["taskkill", "/PID", str(self.proc.pid), "/T", "/F"],
                       capture_output=True, creationflags=CREATE_NO_WINDOW)
        try:
            self.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.proc.kill()


_SPLASH = """<!doctype html><html><head><meta charset="utf-8"><style>
html,body{margin:0;height:100%;background:#0f1117;color:#e5e7eb;
font-family:'Segoe UI',sans-serif;display:flex;align-items:center;justify-content:center}
.box{text-align:center}.t{font-size:26px;font-weight:600;margin-bottom:10px}
.s{color:#9ca3af;font-size:14px}.sp{width:38px;height:38px;margin:22px auto 0;
border:4px solid #374151;border-top-color:#818cf8;border-radius:50%;animation:r 1s linear infinite}
@keyframes r{to{transform:rotate(360deg)}}</style></head><body><div class="box">
<div class="t">🎵 Mashup Music Tool</div><div class="s" id="s">Đang khởi động…</div>
<div class="sp"></div></div></body></html>"""


def main() -> int:
    if not single_instance():
        message_box("Mashup Music Tool đang mở rồi — xem trên thanh tác vụ.")
        return 0
    try:
        import webview
    except ImportError:
        message_box("Thiếu thư viện pywebview.\nChạy build_exe.bat (hoặc: "
                    ".venv\\Scripts\\pip install pywebview) rồi mở lại.",
                    flags=MB_ICONERROR)
        return 1

    root = base_dir()
    backend = Backend(root)
    state = {"owned": not backend_alive()}
    owned = state["owned"]
    if owned:
        try:
            backend.start()
        except Exception as e:                 # noqa: BLE001
            message_box(str(e), flags=MB_ICONERROR)
            return 1

    webview.settings["ALLOW_DOWNLOADS"] = True                 # nút ⬇ WAV/CUE…
    webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = True

    window = webview.create_window(
        APP_NAME, html=_SPLASH, width=1480, height=940, min_size=(1100, 700),
        background_color="#0f1117", text_select=True)

    def watchdog() -> None:
        """Backend có sẵn (không do app bật) bị tắt → app tự bật backend riêng."""
        misses = 0
        while not state["owned"]:
            time.sleep(3)
            misses = 0 if backend_alive() else misses + 1
            if misses < 3:
                continue
            try:
                backend.start()
            except Exception as e:             # noqa: BLE001
                message_box(str(e), flags=MB_ICONERROR)
                return
            state["owned"] = True
            t0 = time.time()
            while time.time() - t0 < START_TIMEOUT_SEC and not backend.exited():
                if backend_alive():
                    window.load_url(URL)
                    return
                time.sleep(1)
            message_box("Không khởi động lại được backend.\n\n" + backend.log_tail()
                        + f"\n\nLog đầy đủ: {backend.log_path}", flags=MB_ICONERROR)
            return

    def wait_ready() -> None:
        t0 = time.time()
        while time.time() - t0 < START_TIMEOUT_SEC:
            if backend_alive():
                window.load_url(URL)
                if not state["owned"]:
                    threading.Thread(target=watchdog, daemon=True).start()
                return
            if backend.exited():
                break
            try:
                window.evaluate_js(
                    f"document.getElementById('s').textContent="
                    f"'Đang khởi động backend… {int(time.time() - t0)}s'")
            except Exception:                  # noqa: BLE001
                pass
            time.sleep(1)
        message_box("Không khởi động được backend.\n\n" + backend.log_tail()
                    + f"\n\nLog đầy đủ: {backend.log_path}", flags=MB_ICONERROR)
        window.destroy()

    def on_closing():
        if not state["owned"]:
            return True
        tasks = active_tasks()
        if not tasks:
            return True
        names = "\n".join(f"• {t.get('label')}"
                          + (f" — {t['project_name']}" if t.get("project_name") else "")
                          for t in tasks[:8])
        ans = message_box(
            f"Đang có {len(tasks)} tác vụ chạy:\n\n{names}\n\n"
            "Đóng ứng dụng sẽ DỪNG các tác vụ này (Suno/Video có thể Resume sau).\n"
            "Vẫn đóng?", flags=MB_YESNO | MB_ICONWARNING)
        return ans == IDYES

    window.events.closing += on_closing
    storage = root / "data" / "webview"
    storage.mkdir(parents=True, exist_ok=True)
    try:
        # private_mode=False: giữ localStorage (lô đang chạy…) giữa các lần mở.
        webview.start(wait_ready, gui="edgechromium", private_mode=False,
                      storage_path=str(storage))
    finally:
        if state["owned"]:
            backend.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
