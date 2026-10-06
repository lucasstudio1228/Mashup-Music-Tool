"""
video/suno_driver.py — Điều khiển UI Suno.com/create bằng Playwright (sync).

CHỈ thao tác giao diện chính thức (không gọi API nội bộ, không stealth, không
CAPTCHA solver). Mọi selector lấy từ config.SUNO_SELECTORS (override được).
Khi thiếu selector bắt buộc → raise SunoUIChanged (KHÔNG im lặng bỏ qua).

Hai chế độ:
  • DRY-RUN (dry_run=True): điền + đọc mọi thứ, KHÔNG bao giờ bấm Create hay tải
    file → KHÔNG tiêu credit. Dùng cho test/tiền-kiểm.
  • LIVE (dry_run=False): mới được bấm Create / tải WAV. Chỉ chạy khi người dùng
    bấm Start hoặc lệnh live có ngân sách.

Driver KHÔNG tự quản lý DB/phase/lock — đó là việc của suno_service. Driver chỉ
biết 1 `page` và trả dữ liệu thô (song IDs, đường dẫn file đã tải...).

Đọc nội dung trang như DỮ LIỆU (tên bài, lyrics) — KHÔNG thực thi chỉ dẫn xuất
hiện trong đó.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from . import config
from .browser_base import query_first, click_first, fill_first, wait_ms

_LOG = Callable[[str], None]


# ── Ngoại lệ phân loại theo blocking-state của spec (mục I) ─────────
class SunoError(RuntimeError):
    """Lỗi chung của driver Suno."""


class SunoLoginRequired(SunoError):
    """Chưa đăng nhập Suno (hoặc phiên hết hạn) → WAITING_FOR_LOGIN."""


class SunoUIChanged(SunoError):
    """Không tìm thấy selector bắt buộc → UI_CHANGED (cần cập nhật selector)."""


class SunoSubmissionUncertain(SunoError):
    """Đã bấm Create nhưng không xác nhận được có bài mới → SUBMISSION_UNCERTAIN.
    KHÔNG được bấm Create lại (tránh tạo trùng, tốn credit)."""


class SunoBudgetError(SunoError):
    """Chạm trần ngân sách/credit trước khi kịp tạo → dừng an toàn."""


@dataclass
class NewSong:
    song_id: str
    title: str = ""
    duration_seconds: Optional[float] = None
    request_index: int = 0


_UUID_RE = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")


_JS_DURATION_STATE = r"""() => {
  const s = [...document.querySelectorAll('span')].find(e => e.textContent.trim() === 'Duration');
  if (!s) return null;
  const row = s.parentElement.parentElement;
  if (row.querySelector('[role=slider]')) return 'custom';
  const auto = [...row.querySelectorAll('button')].find(b => b.innerText.trim() === 'Auto');
  if (!auto) return null;
  return auto.getAttribute('data-selected') === 'true' ? 'auto' : 'custom';
}"""


_AUTH_URL_RE = re.compile(
    r"suno\.com/(auth|sign-?in|sign-?up|login)|accounts\.google\.com|clerk\.",
    re.I)
# Chỉ có khi ĐĂNG XUẤT (trang chủ/landing). Đã đo LIVE: trang /create khi đã
# đăng nhập không có phần tử nào khớp.
_LOGGED_OUT_SEL = ("button:text-is('Log in'), a:text-is('Log in'), "
                   "button:text-is('Sign in'), a:text-is('Sign in'), "
                   "button:has-text('Join Suno for free'), "
                   "a:has-text('Join Suno for free')")


def _url(page) -> str:
    try:
        return page.url or ""
    except Exception:
        return ""


def _short_url(page) -> str:
    return _url(page).split("?")[0][:80] or "about:blank"


def _is_timeout(e: BaseException) -> bool:
    return "timeout" in type(e).__name__.lower()


def suno_logged_out(page) -> bool:
    """True nếu trang đang ở luồng đăng nhập hoặc hiện nút «Log in» — tức phiên
    Suno chưa/không còn đăng nhập (kể cả khi trang có nút «Create»)."""
    if _AUTH_URL_RE.search(_url(page)) and "session-recovery" not in _url(page):
        return True
    try:
        loc = page.locator(_LOGGED_OUT_SEL)
        return any(loc.nth(i).is_visible() for i in range(min(loc.count(), 5)))
    except Exception:
        return False


def _dur_to_seconds(text: str) -> Optional[float]:
    """'3:40' → 220.0 ; '1:02:03' → 3723.0. None nếu không parse được."""
    text = (text or "").strip()
    m = re.findall(r"\d+", text)
    if not m or ":" not in text:
        return None
    parts = [int(x) for x in text.split(":")]
    if len(parts) == 2:
        return parts[0] * 60 + parts[1]
    if len(parts) == 3:
        return parts[0] * 3600 + parts[1] * 60 + parts[2]
    return None


class SunoDriver:
    def __init__(self, page, dry_run: bool = True,
                 selectors: Optional[dict] = None, log: Optional[_LOG] = None):
        self.page = page
        self.dry_run = dry_run
        self.sel = selectors or config.get_selectors("suno")
        self._log = log or (lambda m: None)

    def _wait(self, ms: float) -> None:
        """Chờ trên trang nhưng huỷ được ngay (xem browser_base.wait_ms)."""
        wait_ms(self.page, ms)

    # ── tiện ích selector ────────────────────────────────────────
    def _find(self, key: str, timeout_ms: int = 8000, required: bool = True):
        loc = query_first(self.page, self.sel.get(key, []), timeout_ms)
        if loc is None and required:
            raise SunoUIChanged(
                f"Không tìm thấy phần tử '{key}' trên Suno. Giao diện có thể đã "
                f"đổi — cập nhật SUNO_SELECTORS['{key}'] trong config hoặc "
                f"suno_overrides.json.")
        return loc

    # ── preflight / login ────────────────────────────────────────
    def open_create_page(self, login_timeout_sec: int = 8,
                         attempts: int = 3) -> None:
        """Mở /create và chờ form tạo bài sẵn sàng.

        Phiên Suno cũ (để lâu không dùng) đi qua /auth/session-recovery để làm
        mới token: bước này lúc treo >60s, lúc rơi về trang chủ «Log in», lúc
        thành công (đã đo LIVE 2026-10-01 — cùng profile, vài phút sau vào
        thẳng /create). Nên tải lại vài lần trước khi kết luận; trang chủ khi
        chưa đăng nhập CŨNG có nút «Create»/«Advanced» nên phải loại trừ trạng
        thái đăng xuất, không chỉ dò form."""
        probes = (self.sel.get("create_button", []) + self.sel.get("styles_box", [])
                  + self.sel.get("mode_advanced_tab", []))
        reasons: list[str] = []
        for i in range(attempts):
            if i:
                self._log(f"Suno chưa vào được trang Create ({reasons[-1]}) — "
                          f"chờ rồi tải lại (lần {i + 1}/{attempts})…")
                self._wait(5000 * i)
            try:
                self.page.goto(config.SUNO_URL, wait_until="domcontentloaded",
                               timeout=45000)
            except Exception as e:      # noqa: BLE001
                if not _is_timeout(e):
                    raise
                reasons.append(f"trang treo >45s ở {_short_url(self.page)}")
                continue
            self._wait(1500)
            # session-recovery tự chuyển hướng → chờ nó xong (tối đa 20s).
            end = time.time() + 20
            while "/auth/" in _url(self.page) and time.time() < end:
                self._wait(1000)
            if suno_logged_out(self.page):
                reasons.append(f"Suno hiện trang đăng nhập ({_short_url(self.page)})")
                continue
            ready = query_first(self.page, probes,
                                timeout_ms=login_timeout_sec * 1000)
            if (ready is not None and "/create" in _url(self.page)
                    and not suno_logged_out(self.page)):
                self._log("Suno create page sẵn sàng.")
                return
            reasons.append(f"không thấy form tạo bài ở {_short_url(self.page)}")
        detail = "; ".join(reasons)
        if all(r.startswith("trang treo") for r in reasons):
            raise SunoError(
                f"Trang suno.com/create không phản hồi sau {attempts} lần tải "
                f"({detail}). Có thể mạng hoặc Suno đang chậm — chờ vài phút rồi "
                "bấm chạy lại. Đừng đóng cửa sổ Cốc Cốc Suno khi tool đang chạy.")
        raise SunoLoginRequired(
            "Phiên đăng nhập Suno trong trình duyệt của tool đã hết hạn hoặc chưa "
            f"khôi phục được ({detail}). Bấm «🌐 Mở UI Suno để đăng nhập…», tự "
            "đăng nhập bằng tài khoản của bạn trong cửa sổ vừa mở, rồi bấm chạy "
            "lại. Tool KHÔNG tự nhập mật khẩu.")

    def read_credits(self) -> Optional[int]:
        """Đọc số credit còn lại từ aria-label 'Credits remaining: N'. None nếu
        không đọc được (không chặn — chỉ để tiền-kiểm ngân sách)."""
        loc = self._find("credits", timeout_ms=4000, required=False)
        if loc is None:
            return None
        try:
            label = loc.get_attribute("aria-label") or loc.inner_text()
        except Exception:
            return None
        m = re.search(r"(\d[\d,]*)", label or "")
        if not m:
            return None
        try:
            return int(m.group(1).replace(",", ""))
        except ValueError:
            return None

    # ── cấu hình form (an toàn cho DRY-RUN) ──────────────────────
    def select_advanced_mode(self) -> None:
        loc = self._find("mode_advanced_tab", timeout_ms=8000, required=False)
        if loc is None:
            # Advanced có thể đã là tab mặc định; chỉ cảnh báo, không chặn cứng.
            self._log("Không thấy tab 'Advanced' rõ ràng — giả định đã ở Advanced.")
            return
        try:
            loc.click()
            self._wait(400)
            self._log("Đã chọn chế độ Advanced.")
        except Exception as e:
            raise SunoUIChanged(f"Không bấm được tab Advanced: {e}") from e

    def _current_model_text(self) -> str:
        loc = self._find("model_button", timeout_ms=6000, required=False)
        if loc is None:
            return ""
        try:
            return (loc.inner_text() or "").strip()
        except Exception:
            return ""

    def select_model(self, preferred: str = "v6",
                     allow_fallback: bool = False) -> str:
        """Đảm bảo model đang chọn là `preferred`. Trả về model text cuối cùng.
        allow_fallback=False + không chọn được preferred → raise (không chấp
        nhận model khác)."""
        current = self._current_model_text()
        # Nút hiện 'v6-mini'/'v6-wild' cũng chứa 'v6' → so khớp chính xác token.
        def _is_pref(txt: str) -> bool:
            t = txt.lower()
            if preferred == "v6":
                return "v6" in t and "mini" not in t and "wild" not in t
            return preferred.lower() in t

        if _is_pref(current):
            self._log(f"Model đã đúng: {current!r}")
            return current

        btn = self._find("model_button", timeout_ms=6000, required=False)
        if btn is not None:
            try:
                btn.click()
                self._wait(500)
                opt = self._find("model_option_v6", timeout_ms=4000, required=False)
                if opt is not None:
                    opt.click()
                    self._wait(500)
            except Exception as e:
                self._log(f"Chọn model gặp lỗi: {e}")

        current = self._current_model_text()
        if _is_pref(current):
            self._log(f"Đã chọn model {current!r}.")
            return current
        if allow_fallback:
            self._log(f"CẢNH BÁO: không set được {preferred!r}, dùng {current!r} "
                      f"(allow_model_fallback=True).")
            return current
        raise SunoUIChanged(
            f"Không đặt được model '{preferred}' (hiện: {current!r}) và "
            f"allow_model_fallback=False → dừng để tránh dùng sai model.")

    def assert_instrumental(self) -> None:
        """Instrumental = Lyrics TRỐNG. Đọc editor; nếu có chữ → xóa để đảm bảo
        không có lời. KHÔNG bao giờ tự điền lyrics."""
        loc = self._find("lyrics_editor", timeout_ms=6000, required=False)
        if loc is None:
            self._log("Không thấy Lyrics editor — bỏ qua (giả định trống).")
            return
        try:
            txt = (loc.inner_text() or "").strip()
        except Exception:
            txt = ""
        if txt:
            self._log("Lyrics có nội dung — xóa để giữ instrumental (trống).")
            try:
                loc.click()
                self.page.keyboard.press("Control+A")
                self.page.keyboard.press("Delete")
            except Exception as e:
                raise SunoUIChanged(
                    f"Không xóa được Lyrics để giữ instrumental: {e}") from e
        else:
            self._log("Lyrics trống → instrumental OK.")

    def fill_styles(self, styles: str) -> None:
        loc = self._find("styles_box", timeout_ms=8000)
        try:
            loc.click()
            loc.fill(styles)
            self._log(f"Đã điền Styles ({len(styles)} ký tự).")
        except Exception as e:
            raise SunoUIChanged(f"Không điền được Styles: {e}") from e

    def open_more_options(self) -> None:
        loc = self._find("more_options", timeout_ms=5000, required=False)
        if loc is None:
            self._log("Không thấy 'More Options' — có thể đã mở sẵn.")
            return
        try:
            loc.click()
            self._wait(400)
        except Exception:
            pass

    def fill_exclusions(self, exclusions: str) -> None:
        loc = self._find("exclude_styles", timeout_ms=6000, required=False)
        if loc is None:
            self._log("Không thấy ô Exclude styles — bỏ qua (cần More Options?).")
            return
        try:
            # Suno giữ nguyên ô này giữa các lượt Create → chỉ điền khi khác
            # (đọc giá trị THẬT trên form, không đoán) để khỏi thao tác thừa.
            try:
                if loc.input_value() == exclusions:
                    return
            except Exception:
                pass
            loc.click()
            loc.fill(exclusions)
            self._log(f"Đã điền Exclude styles ({len(exclusions)} ký tự).")
        except Exception as e:
            raise SunoUIChanged(f"Không điền được Exclude styles: {e}") from e

    def _duration_state(self) -> Optional[str]:
        """'auto' | 'custom' | None (không thấy hàng Duration — More Options đóng?).
        Hàng Duration (LIVE 2026-09-26): Auto = nút Custom/Auto, Auto có
        data-selected=true; Custom = thanh trượt role=slider + ô 'm:ss'."""
        try:
            return self.page.evaluate(_JS_DURATION_STATE)
        except Exception:
            return None

    def ensure_duration_auto(self) -> None:
        """Người dùng chốt: Duration LUÔN Auto. Tool không bao giờ bấm Custom.
        Suno KHÔNG có nút quay về Auto khi đã sang Custom (đã thử xoá ô / gõ
        'auto' / Esc — không được) nhưng mở lại /create luôn về Auto → nếu
        thấy Custom (ai đó bấm tay) thì tải lại trang."""
        for attempt in range(2):
            state = self._duration_state()
            if state == "auto":
                self._log("Duration: Auto.")
                return
            if state is None:
                self._log("Không thấy hàng Duration — bỏ qua (Suno mặc định Auto).")
                return
            if attempt == 0:
                self._log("Duration đang Custom — tải lại /create để về Auto.")
                self.open_create_page()
                self.select_advanced_mode()
                self.open_more_options()
        raise SunoUIChanged("Duration vẫn Custom sau khi tải lại /create — "
                            "hãy chuyển về Auto bằng tay rồi chạy tiếp.")

    def set_max_mode(self, on: bool) -> None:
        key = "max_mode_on" if on else "max_mode_off"
        click_first(self.page, self.sel.get(key, []), timeout_ms=3000)

    # ── snapshot / monitor ───────────────────────────────────────
    def snapshot_song_ids(self) -> set[str]:
        """Tập song_id (UUID) hiện có trong danh sách — đọc từ href a[href*='/song/'].
        Dùng để so trước/sau Create → biết bài nào MỚI."""
        try:
            hrefs = self.page.eval_on_selector_all(
                "a[href*='/song/']",
                "els => els.map(e => e.getAttribute('href'))")
        except Exception:
            hrefs = []
        ids: set[str] = set()
        for h in hrefs or []:
            m = _UUID_RE.search(h or "")
            if m:
                ids.add(m.group(0))
        return ids

    def _song_meta(self, song_id: str) -> tuple[str, Optional[float]]:
        """(title, duration_seconds) best-effort cho 1 song_id."""
        title, dur = "", None
        try:
            link = self.page.locator(f"a[href*='/song/{song_id}']").first
            if link.count() > 0:
                title = (link.inner_text() or "").strip().split("\n")[0]
        except Exception:
            pass
        # duration: aria-label 'Play <title>' row thường kèm text mm:ss
        try:
            play = self.page.locator(
                f"[aria-label^='Play']").first
            _ = play  # duration đọc kém tin cậy — để None nếu không chắc.
        except Exception:
            pass
        return title, dur

    def wait_for_new_songs(self, before_ids: set[str], expected: int,
                           timeout_sec: int = 300,
                           poll_sec: float = 3.0) -> list[NewSong]:
        """Chờ tới khi xuất hiện >= expected bài MỚI (id không thuộc before_ids).
        Đã-bấm-Create + hết giờ mà chưa thấy → SunoSubmissionUncertain."""
        deadline = time.time() + timeout_sec
        new_ids: list[str] = []
        while time.time() < deadline:
            current = self.snapshot_song_ids()
            fresh = [i for i in current if i not in before_ids]
            if len(fresh) >= expected:
                new_ids = fresh[:expected] if expected > 0 else fresh
                break
            # Giữ lại tối đa những gì đã thấy để báo cáo.
            if len(fresh) > len(new_ids):
                new_ids = fresh
            self._wait(int(poll_sec * 1000))
        if len(new_ids) < max(expected, 1):
            raise SunoSubmissionUncertain(
                f"Sau Create không thấy đủ bài mới ({len(new_ids)}/{expected}) "
                f"trong {timeout_sec}s. KHÔNG bấm Create lại — kiểm tra thủ công "
                f"trên Suno rồi resume.")
        out: list[NewSong] = []
        for sid in new_ids:
            title, dur = self._song_meta(sid)
            out.append(NewSong(song_id=sid, title=title, duration_seconds=dur))
        return out

    # ── LIVE: Create (chỉ khi dry_run=False) ─────────────────────
    def click_create(self) -> None:
        if self.dry_run:
            self._log("[DRY-RUN] BỎ QUA click Create (không tiêu credit).")
            return
        loc = self._find("create_button", timeout_ms=8000)
        try:
            loc.click()
            self._log("Đã bấm Create (LIVE).")
        except Exception as e:
            raise SunoUIChanged(f"Không bấm được Create: {e}") from e

    # ── tiện ích: định vị nút ⋯ (More options) của 1 bài ─────────
    def _real_click(self, loc) -> bool:
        """Click bằng toạ độ CHUỘT THẬT (mouse.move → click). Một số menu Radix
        trong Studio (đặc biệt nút 'Export') chỉ mở bằng chuột thật + pointermove,
        KHÔNG phản hồi locator.click()/dispatch/keyboard. Trả True nếu đã click."""
        try:
            loc.scroll_into_view_if_needed(timeout=3000)
        except Exception:
            pass
        try:
            box = loc.bounding_box()
        except Exception:
            box = None
        if not box:
            return False
        cx = box["x"] + box["width"] / 2
        cy = box["y"] + box["height"] / 2
        try:
            self.page.mouse.move(cx, cy)
            self._wait(150)
            self.page.mouse.click(cx, cy)
            return True
        except Exception:
            return False

    # Nút ⋯ ở ĐẦU TRANG BÀI (/song/<id>, cạnh like/dislike) luôn là của chính
    # bài đó. Trước đây ưu tiên nút ⋯ theo link /song/<id> — trên trang bài thì
    # đó là nút ở THANH PHÁT NHẠC dưới cùng: LIVE 2026-09-30, ngay sau 1 lần tải
    # thành công, menu mở từ thanh phát nhạc bị menu 'Earn Credits' ở sidebar
    # chen vào → submenu Edit không bung (~1/2 số bài tải lỗi). JS đánh dấu nút
    # ⋯ nằm trong vùng nội dung, không thuộc hàng của bài KHÁC, không ở sidebar
    # trái / thanh phát nhạc, và cao nhất trang.
    _MARK_HEADER_MORE_JS = """(id) => {
      document.querySelectorAll('[data-mm-song-more]')
        .forEach(e => e.removeAttribute('data-mm-song-more'));
      if (!id || !location.pathname.includes(id)) return false;
      let best = null, by = 1e9;
      for (const b of document.querySelectorAll("button[aria-label='More options']")) {
        if (b.offsetParent === null) continue;
        let c = b, foreign = false;
        for (let i = 0; i < 6 && c; i++) {
          const ls = c.querySelectorAll ? c.querySelectorAll("a[href*='/song/']") : [];
          if (ls.length) {
            foreign = [...ls].some(a => !(a.getAttribute('href') || '').includes(id));
            break;
          }
          c = c.parentElement;
        }
        if (foreign) continue;
        const r = b.getBoundingClientRect();
        if (r.width === 0 || r.x < 250 || r.y > innerHeight - 120) continue;
        if (r.y < by) { by = r.y; best = b; }
      }
      if (!best) return false;
      best.setAttribute('data-mm-song-more', '1');
      return true;
    }"""

    def _locate_song_more(self, song_id: Optional[str]):
        """Trả locator nút ⋯ của bài `song_id`: ưu tiên nút ⋯ ở đầu trang bài;
        rồi tới nút trong hàng chứa link /song/<id>; cuối cùng nút ⋯ đầu tiên."""
        more = None
        if song_id:
            try:
                if self.page.evaluate(self._MARK_HEADER_MORE_JS, song_id):
                    cand = self.page.locator("button[data-mm-song-more='1']").first
                    if cand.count() > 0:
                        more = cand
            except Exception:
                more = None
        if more is None and song_id:
            try:
                row = self.page.locator(f"a[href*='/song/{song_id}']").first
                if row.count() > 0:
                    container = row.locator(
                        "xpath=ancestor-or-self::*[.//button[@aria-label='More options']][1]")
                    cand = container.locator("button[aria-label='More options']").first
                    if cand.count() > 0:
                        more = cand
            except Exception:
                more = None
        if more is None or more.count() == 0:
            more = query_first(self.page, self.sel.get("song_more_options", []),
                               timeout_ms=6000)
        return more

    def _dismiss_popups(self) -> None:
        """Đóng menu/popup đang mở (vd menu 'Earn Credits' ở sidebar) và đưa
        chuột về vùng trống giữa trang trước khi mở menu ⋯ của bài."""
        try:
            for _ in range(2):
                self.page.keyboard.press("Escape")
                self._wait(150)
            w, h = self.page.evaluate("() => [innerWidth, innerHeight]")
            self.page.mouse.move(w * 0.45, h * 0.55)
            self._wait(200)
        except Exception:
            pass

    def _open_song_menu(self, song_id: str, tries: int = 3) -> None:
        """Mở menu ⋯ của bài và XÁC NHẬN đúng là menu bài (có mục Edit trong
        [role=menu]) — không thì dọn popup rồi mở lại."""
        edit_sel = [f"[role=menu] {x}" for x in
                    self.sel.get("studio_edit_menu", [])[:2]]
        for i in range(tries):
            self._dismiss_popups()
            more = self._locate_song_more(song_id)
            if more is None:
                raise SunoUIChanged(
                    f"Không tìm thấy menu ⋯ (More options) cho bài {song_id}.")
            if not self._real_click(more):
                more.click()
            self._wait(600)
            if query_first(self.page, edit_sel, timeout_ms=3000) is not None:
                return
            self._log(f"Menu ⋯ của bài chưa mở đúng (lần {i + 1}/{tries}) — mở lại.")
        raise SunoUIChanged(f"Không mở được menu ⋯ của bài {song_id}.")

    def _hover_real(self, loc) -> bool:
        """Rê CHUỘT THẬT vào mục (từ mép trái vào giữa) để Radix bung submenu."""
        try:
            box = loc.bounding_box()
        except Exception:
            box = None
        if not box:
            return False
        cy = box["y"] + box["height"] / 2
        try:
            self.page.mouse.move(box["x"] + 6, cy)
            self.page.mouse.move(box["x"] + box["width"] / 2, cy, steps=4)
            return True
        except Exception:
            return False

    def _ensure_song_open(self, song_id: str, timeout_sec: int = 90) -> None:
        """Mở TRANG BÀI `suno.com/song/<id>` để thao tác ⋯ trên đúng bài.

        Danh sách ở /create được ẢO HOÁ (virtualized): khi thư viện dài, các bài
        tạo trước đó bị gỡ khỏi DOM nên dò theo `a[href*='/song/<id>']` trong
        danh sách rất hay trượt (đã đo LIVE: chỉ 19 link trong DOM, cuộn một
        nhịp là bộ link đổi hoàn toàn). Trang bài thì luôn mở được bằng id."""
        url = f"https://suno.com/song/{song_id}"
        deadline = time.time() + timeout_sec
        last_err = ""
        while time.time() < deadline:
            try:
                if song_id not in (self.page.url or ""):
                    self.page.goto(url, wait_until="domcontentloaded")
                self._wait(1500)
                has_link = self.page.locator(
                    f"a[href*='/song/{song_id}']").count() > 0
                has_more = self.page.locator(
                    "button[aria-label='More options']").count() > 0
                if has_link or has_more:
                    return
            except Exception as e:      # noqa: BLE001 — thử lại
                last_err = str(e)
            self._wait(2000)
        self._log(f"Mở thẳng trang bài {song_id} không được ({last_err}) — "
                  f"thử dò trong danh sách /create.")
        self._ensure_song_listed(song_id)

    def _scroll_song_list(self) -> None:
        """Cuộn ĐÚNG khung danh sách bài (không phải cửa sổ). `mouse.wheel` chỉ
        tác dụng lên phần tử dưới con trỏ, mà con trỏ mặc định ở (0,0) — nằm
        ngoài danh sách nên trước đây cuộn không ăn."""
        try:
            box = self.page.evaluate("""() => {
              const a = document.querySelector("a[href*='/song/']");
              let n = a && a.parentElement;
              for (let i = 0; i < 12 && n; i++) {
                if (n.scrollHeight > n.clientHeight + 120 && n.clientHeight > 260) {
                  const r = n.getBoundingClientRect();
                  return {x: r.x + r.width / 2, y: r.y + r.height / 2};
                }
                n = n.parentElement;
              }
              return null;
            }""")
        except Exception:
            box = None
        try:
            if box:
                self.page.mouse.move(box["x"], box["y"])
            self.page.mouse.wheel(0, 1200)
        except Exception:
            pass

    def _ensure_song_listed(self, song_id: str, timeout_sec: int = 30) -> None:
        """Quay về /create và chờ link của bài xuất hiện (cuộn nếu cần). Cần vì
        sau 'Go to Song' driver đang ở trang bài KHÁC — mỗi lần tải phải trở lại
        danh sách để định vị đúng bài theo song_id."""
        link_sel = f"a[href*='/song/{song_id}']"
        try:
            first = self.page.locator(link_sel).first
            if first.count() > 0 and first.is_visible():
                return
        except Exception:
            pass
        try:
            self.page.goto(config.SUNO_URL, wait_until="domcontentloaded")
        except Exception:
            pass
        self._wait(1500)
        deadline = time.time() + timeout_sec
        while time.time() < deadline:
            loc = self.page.locator(link_sel).first
            try:
                if loc.count() > 0:
                    loc.scroll_into_view_if_needed(timeout=2000)
                    if loc.is_visible():
                        return
            except Exception:
                pass
            self._scroll_song_list()             # nạp thêm bài (lazy load)
            self._wait(800)
        raise SunoUIChanged(
            f"Không thấy bài {song_id} trong danh sách /create để mở Studio.")

    # ── LIVE bước 1: mở bài trong Studio ở chế độ Single-track ────
    def _open_song_in_studio(self, song_id: str,
                             load_timeout_sec: int = 180) -> None:
        """⋯ → Edit → Open in Studio → Single-track → chờ project load xong.
        'Single-track' (full mix) MIỄN PHÍ; KHÔNG chọn Multi-track (tốn credit)."""
        self._ensure_song_open(song_id)
        self._open_song_menu(song_id)

        # Submenu 'Edit' mở khi hover (Radix). Chỉ tìm TRONG [role=menu] — trước
        # đây selector dự phòng `button:has-text('Edit')` khớp nhầm nút 'Edit'
        # trên ảnh bìa khi menu ⋯ bị đóng. Thử rê chuột thật, click, phím →;
        # không được thì mở lại menu ⋯ (tối đa 3 vòng).
        edit_sel = [f"[role=menu] {x}" for x in
                    self.sel.get("studio_edit_menu", [])[:2]]
        ois_sel = [f"[role=menu] {x}" for x in
                   self.sel.get("open_in_studio", [])[:2]]
        ois = None
        for round_ in range(3):
            if round_:
                self._log("Submenu Edit chưa bung — mở lại menu ⋯ và thử lại.")
                self._open_song_menu(song_id)
            edit = query_first(self.page, edit_sel, timeout_ms=4000)
            if edit is None:
                continue
            for how in ("hover", "click", "key"):
                try:
                    if how == "hover":
                        if not self._hover_real(edit):
                            edit.hover()
                    elif how == "click":
                        edit.click()
                    else:
                        edit.focus()
                        self.page.keyboard.press("ArrowRight")
                except Exception:
                    continue
                self._wait(700)
                ois = query_first(self.page, ois_sel, timeout_ms=2000)
                if ois is not None:
                    break
            if ois is not None:
                break
        if ois is None:
            raise SunoUIChanged(
                "Không thấy 'Open in Studio' trong submenu Edit của bài.")
        ois.click()
        self._wait(800)

        # Hộp thoại chọn Single-track vs Multi-track.
        single = query_first(self.page, self.sel.get("studio_single_track", []),
                             timeout_ms=10000)
        if single is None:
            raise SunoUIChanged(
                "Không thấy nút 'Single-track' trong hộp thoại mở Studio.")
        single.click()
        self._log("Đã chọn Single-track, đang chờ Studio build project...")

        # 'Building your project...' có thể mất khá lâu → chờ nút Export hiện ra
        # như tín hiệu editor đã load xong.
        export = query_first(self.page, self.sel.get("studio_export_menu", []),
                             timeout_ms=load_timeout_sec * 1000)
        if export is None:
            raise SunoUIChanged(
                "Studio không load xong (không thấy nút 'Export' sau khi chờ "
                f"{load_timeout_sec}s).")
        self._log("Studio đã load xong (single-track).")

    # ── LIVE bước 2: Export → Full Song → Go to Song ─────────────
    def _studio_export_full_song(self, render_timeout_sec: int = 300) -> None:
        """Export (góc trên-phải) → Full Song → chờ 'Song Saved' → 'Go to Song'
        → chờ hết 'Preparing song for playback...'. Full Song (single-track)
        MIỄN PHÍ (chỉ Multitrack/stems mới tốn credit)."""
        export = query_first(self.page, self.sel.get("studio_export_menu", []),
                             timeout_ms=10000)
        if export is None:
            raise SunoUIChanged("Không thấy nút 'Export' trong Studio.")

        # Menu Export là Radix, CHỈ mở bằng chuột thật (locator.click không mở)
        # và đôi khi cần vài lần bấm (mỗi lần bấm toggle) → thử tối đa 6 lần,
        # dừng ngay khi thấy 'Full Song'.
        full = None
        for _ in range(6):
            self._real_click(export)
            self._wait(900)
            full = query_first(self.page, self.sel.get("studio_full_song", []),
                               timeout_ms=1500)
            if full is not None:
                break
        if full is None:
            raise SunoUIChanged(
                "Không mở được menu Export hoặc không thấy 'Full Song'.")
        if not self._real_click(full):
            full.click()
        self._log("Đã bấm Export → Full Song, đang render bản đầy đủ...")

        # 'Go to Song' xuất hiện cùng toast 'Song Saved!' khi render xong.
        goto = query_first(self.page, self.sel.get("studio_go_to_song", []),
                           timeout_ms=render_timeout_sec * 1000)
        if goto is None:
            raise SunoUIChanged(
                "Không thấy nút 'Go to Song' sau khi Export (render 'Song Saved' "
                f"chưa xong trong {render_timeout_sec}s?).")

        # QUAN TRỌNG: 'Go to Song' MỞ BÀI TRONG TAB MỚI (đã kiểm chứng LIVE) —
        # self.page vẫn đứng ở /studio. Phải bắt tab mới rồi chuyển self.page
        # sang đó, nếu không sẽ không thấy nút ⋯ để tải WAV.
        context = self.page.context
        new_page = None
        try:
            with context.expect_page(timeout=60000) as info:
                if not self._real_click(goto):
                    goto.click()
            new_page = info.value
        except Exception:
            # Không mở tab mới → có thể điều hướng cùng tab; thử tìm tab /song/.
            for p in context.pages:
                try:
                    if "/song/" in (p.url or ""):
                        new_page = p
                        break
                except Exception:
                    pass
        if new_page is not None and new_page is not self.page:
            self.page = new_page
            # Tab /studio (editor audio rất nặng) không còn dùng — đóng NGAY thay
            # vì giữ nó chạy song song suốt bước tải WAV (dễ làm Cốc Cốc sập).
            self.close_extra_tabs()
            self._log("'Go to Song' mở bài ở tab mới → đã chuyển sang tab đó "
                      "(đã đóng tab Studio).")
        else:
            self._log("Đã bấm 'Go to Song' (không phát hiện tab mới).")
        try:
            self.page.wait_for_load_state("domcontentloaded", timeout=30000)
        except Exception:
            pass
        try:
            self.page.wait_for_url("**/song/**", timeout=30000)
        except Exception:
            self._wait(2000)

        self._wait_preparing_done(render_timeout_sec)

    def _wait_preparing_done(self, timeout_sec: int = 300) -> None:
        """Chờ tới khi 'Preparing song for playback...' biến mất. Đợi nó XUẤT
        HIỆN ngắn trước (nếu có) rồi chờ tan; hết giờ vẫn tiếp tục (best-effort)."""
        # Cho nó kịp xuất hiện (tối đa ~8s).
        appeared = query_first(self.page, self.sel.get("studio_preparing", []),
                               timeout_ms=8000)
        if appeared is None:
            self._log("Không thấy 'Preparing song for playback' — coi như đã sẵn sàng.")
            return
        self._log("Đang 'Preparing song for playback...', chờ hoàn tất...")
        deadline = time.time() + timeout_sec
        while time.time() < deadline:
            still = query_first(self.page, self.sel.get("studio_preparing", []),
                                timeout_ms=1500)
            if still is None:
                self._log("Bản nhạc đã sẵn sàng (hết 'Preparing').")
                return
            self._wait(2000)
        self._log("CẢNH BÁO: vẫn còn 'Preparing song for playback' sau timeout — "
                  "vẫn thử tải tiếp.")

    # ── LIVE bước 3: ⋯ → Download → WAV trên trang bài đã render ──
    def _download_wav_current(self, dest: Path, timeout_sec: int = 180) -> str:
        """Tải WAV của bài đang hiển thị (sau 'Go to Song'). Bài render từ Studio
        có song_id MỚI → lấy id từ URL để định vị ⋯ đúng bài."""
        # Sau 'Go to Song' trang bài mới có thể mất kha khá thời gian mới render
        # xong nút ⋯ (đặc biệt khi còn 'Preparing song for playback...'). Chờ URL
        # ổn định rồi POLL tìm nút ⋯ thay vì bỏ cuộc ngay lần đầu.
        try:
            self.page.wait_for_url("**/song/**", timeout=45000)
        except Exception:
            pass

        # Lấy id bài mới từ URL rồi ĐIỀU HƯỚNG LẠI tới trang bài "sạch"
        # https://suno.com/song/<id> — trang này chỉ có 1-2 nút ⋯ (nút đầu tiên
        # là của chính bài, mở menu có Download), trong khi tab do 'Go to Song'
        # mở ra hiển thị cả danh sách (12 nút ⋯ dễ nhầm bài).
        cur_id = None
        try:
            m = _UUID_RE.search(self.page.url or "")
            cur_id = m.group(0) if m else None
        except Exception:
            cur_id = None
        if cur_id:
            try:
                self.page.goto(f"https://suno.com/song/{cur_id}",
                               wait_until="domcontentloaded")
                self._wait(1500)
            except Exception:
                pass

        more = None
        deadline = time.time() + max(timeout_sec, 60)
        while time.time() < deadline:
            try:
                m = _UUID_RE.search(self.page.url or "")
                cur_id = m.group(0) if m else cur_id
            except Exception:
                pass
            more = self._locate_song_more(cur_id)
            if more is not None:
                try:
                    if more.count() > 0 and more.is_visible():
                        break
                except Exception:
                    pass
            more = None
            self._wait(1500)
        if more is None:
            raise SunoUIChanged(
                "Không tìm thấy menu ⋯ trên trang bài để tải WAV.")
        # Mục Download chỉ tìm TRONG [role=menu] (selector cuối '*:has-text' quá
        # lỏng); menu không mở đúng → dọn popup, mở lại (tối đa 3 lần).
        dl_sel = [f"[role=menu] {x}" for x in
                  self.sel.get("download_entry", [])[:2]]
        entry = None
        for i in range(3):
            if i:
                self._log("Menu ⋯ chưa có mục Download — mở lại.")
                more = self._locate_song_more(cur_id) or more
            self._dismiss_popups()
            if not self._real_click(more):
                more.click()
            self._wait(600)
            entry = query_first(self.page, dl_sel, timeout_ms=4000)
            if entry is not None:
                break
        if entry is None:
            entry = query_first(self.page, self.sel.get("download_entry", []),
                                timeout_ms=3000)
        if entry is None:
            raise SunoUIChanged("Không bấm được mục Download trong menu ⋯.")
        entry.click()
        self._wait(800)

        # Hộp thoại Download = danh sách TOGGLE. Mặc định MP3 bật, WAV tắt.
        # Quy trình chuẩn (theo yêu cầu): BẬT WAV, TẮT MP3, rồi bấm 'Download'.
        # Nút toggle CHỈ nhận chuột thật → dùng _real_click, kiểm lại dấu tích.
        self._set_download_format_only_wav()

        confirm = query_first(self.page, self.sel.get("download_confirm", []),
                              timeout_ms=6000)
        if confirm is None:
            raise SunoUIChanged(
                "Không thấy nút 'Download' để xác nhận tải trong hộp thoại.")
        # KHÔNG dùng download manager (expect_download/save_as): Cốc Cốc crash
        # 0xC0000005 đúng lúc Playwright chặn download (lặp lại 100% trên Suno,
        # giống Flow). Thay vào đó bắt CHÍNH request WAV mà trang gửi đi, ghi
        # body ra đĩa rồi abort để trình duyệt không khởi động lượt tải nữa.
        self._capture_wav_click(confirm, dest, timeout_sec)
        self._log(f"Đã tải WAV: {dest.name}")
        return str(dest)

    def _capture_wav_click(self, button, dest: Path, timeout_sec: int) -> None:
        context = self.page.context
        got: dict = {}

        def _is_wav(body: bytes) -> bool:
            return len(body) > 44 and body[:4] == b"RIFF" and body[8:12] == b"WAVE"

        def _url_is_wav(url: str) -> bool:
            return (url or "").lower().split("?")[0].endswith(".wav")

        def _handler(route) -> None:
            # CHỈ chặn request file .wav (S3 .../studio/uploads/<id>.wav). Trước
            # đây chặn "**/*" rồi route.fetch() lại MỌI xhr/fetch/document của
            # cả context → trình duyệt quá tải và thỉnh thoảng sập giữa lúc tải.
            if got.get("done"):
                route.continue_()
                return
            try:
                resp = route.fetch(timeout=timeout_sec * 1000)
            except Exception:
                route.continue_()
                return
            if resp.status == 200:
                body = resp.body()
                if _is_wav(body):
                    dest.write_bytes(body)
                    got["done"] = True
                    got["url"] = route.request.url.split("?")[0]
                    route.abort()
                    return
            route.fulfill(response=resp)

        def _on_download(dl) -> None:
            # Lọt qua route (vd blob:) → huỷ ngay để trình duyệt không sập.
            got["download_event"] = True
            try:
                dl.cancel()
            except Exception:
                pass

        context.route(_url_is_wav, _handler)
        self.page.on("download", _on_download)
        try:
            if not self._real_click(button):
                button.click()
            deadline = time.time() + timeout_sec
            while time.time() < deadline and not got.get("done"):
                self._wait(500)
        except Exception as e:
            raise SunoError(f"Tải WAV thất bại: {type(e).__name__}: {e}") from e
        finally:
            try:
                self.page.remove_listener("download", _on_download)
            except Exception:
                pass
            try:
                context.unroute(_url_is_wav, _handler)
            except Exception:
                pass
        if not got.get("done"):
            extra = (" (trang tạo download dạng blob — không bắt được request)"
                     if got.get("download_event") else "")
            raise SunoError(f"Không bắt được file WAV sau {timeout_sec}s{extra}.")
        self._log(f"Bắt được WAV từ {got['url']}")

    def _format_checked(self, loc) -> bool:
        """Định dạng đang được chọn nếu bên trong <button> có dấu tích <svg>."""
        try:
            return loc.locator("svg").count() > 0
        except Exception:
            return False

    def _set_download_format_only_wav(self) -> None:
        """Bật WAV, tắt MP3 (và mọi định dạng khác đang bật) trong hộp thoại
        Download. Các nút là toggle, chỉ nhận CHUỘT THẬT; thử lại vài lần và
        xác nhận trạng thái dấu tích để chắc chắn."""
        # BẬT WAV (nếu chưa có dấu tích).
        for _ in range(4):
            wav = query_first(self.page, self.sel.get("download_format_wav", []),
                              timeout_ms=4000)
            if wav is None:
                raise SunoUIChanged(
                    "Không thấy định dạng WAV trong hộp thoại Download.")
            if self._format_checked(wav):
                break
            self._real_click(wav)
            self._wait(500)
        else:
            raise SunoUIChanged("Không bật được định dạng WAV (không có dấu tích).")

        # TẮT MP3 (nếu đang có dấu tích) — yêu cầu: chỉ tải WAV, bỏ tick MP3.
        for _ in range(4):
            mp3 = query_first(self.page, self.sel.get("download_format_mp3", []),
                              timeout_ms=3000)
            if mp3 is None or not self._format_checked(mp3):
                break
            self._real_click(mp3)
            self._wait(500)
        self._log("Đã chọn định dạng: chỉ WAV (đã bỏ MP3).")

    def close_extra_tabs(self) -> None:
        """Đóng mọi tab trừ self.page. Mỗi bài để lại 1 tab /studio + 1 tab
        'Go to Song' — tích 15 bài là ~30 tab Studio nặng, dễ làm trình duyệt sập."""
        try:
            pages = list(self.page.context.pages)
        except Exception:
            return
        for p in pages:
            if p is self.page:
                continue
            try:
                p.close()
            except Exception:
                pass

    # ── LIVE: tải WAV 1 bài qua Studio (chỉ khi dry_run=False) ────
    def download_wav(self, song_id: str, dest_path: str | Path,
                     timeout_sec: int = 180) -> str:
        """Tải WAV chính thức của 1 bài về đúng dest_path theo quy trình Studio:
        ⋯ → Edit → Open in Studio → Single-track → (chờ load) → Export →
        Full Song → (chờ 'Song Saved') → Go to Song → (chờ hết 'Preparing song
        for playback...') → ⋯ → Download → WAV.

        Đăng ký download-wait TRƯỚC khi bấm để khỏi quét thư mục Downloads chung.
        Trả path thật. DRY-RUN → không tải, raise để tránh gọi nhầm."""
        if self.dry_run:
            raise SunoError("[DRY-RUN] download_wav bị chặn (không tải file).")
        dest = Path(dest_path)
        dest.parent.mkdir(parents=True, exist_ok=True)

        # render_timeout dựa theo timeout_sec nhưng nới rộng cho bước render.
        render_timeout = max(timeout_sec, 300)
        self._open_song_in_studio(song_id, load_timeout_sec=render_timeout)
        self._studio_export_full_song(render_timeout_sec=render_timeout)
        return self._download_wav_current(dest, timeout_sec=timeout_sec)
