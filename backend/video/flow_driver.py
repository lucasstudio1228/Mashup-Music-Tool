"""
video/flow_driver.py — Tự động hoá Google Flow (flow.google.com) tạo 15 video
bằng chế độ INGREDIENTS/THÀNH PHẦN → clip 8s, Veo 3.1 Fast, 16:9, x1.

Luồng đã kiểm chứng trực tiếp trên Flow (giao diện tiếng Anh/Việt):
  1. Vào Flow → tạo dự án mới.
  2. Mở Settings → Ingredients, 16:9, Veo 3.1 - Fast, 8s, x1.
  3. Upload TẤT CẢ ảnh 0..n vào project qua nút "+" (Trình đơn thêm nội dung
     nghe nhìn) → "Tải lên" (file chooser).
  4. Với từng clip trong kế hoạch 20 clip 1:1, chạy TUẦN TỰ:
       - Xóa prompt cũ → thêm đúng một ảnh nguyên liệu.
       - Nhập prompt chuyển động → "Bắt đầu tạo".
       - Chờ clip render xong → tải xuống media/<project>/clips/clip_XX.mp4.

Không gửi dồn nhiều lượt: Flow chỉ bảo đảm nhận lượt kế khi clip trước đã xong.
Mọi selector nằm trong config.get_selectors('flow').
"""
from __future__ import annotations
import json
import time
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from . import config
from .browser_base import (
    BrowserSession, query_first, click_first, fill_first, wait_for_manual_login,
)

# ── LOG RA FILE (để giám sát được vì không nhìn thấy cửa sổ Chromium/console) ──
_LOGP: Optional[Path] = None


def _log(msg: str) -> None:
    """Ghi 1 dòng có timestamp vào clips/_flow.log (best-effort)."""
    line = f"[{datetime.now().strftime('%H:%M:%S')}] {msg}"
    try:
        print("FLOW:", line, flush=True)
    except UnicodeEncodeError:
        # Console Windows có thể vẫn là CP1252 khi chạy ngoài launcher.
        safe = line.encode("ascii", "backslashreplace").decode("ascii")
        print("FLOW:", safe, flush=True)
    if _LOGP is not None:
        try:
            with _LOGP.open("a", encoding="utf-8") as f:
                f.write(line + "\n")
        except Exception:
            pass


# JS: liệt kê MỌI nút đang hiển thị (text + aria-label) để chẩn đoán ô khung.
_JS_BUTTONS = """
() => {
  const out = [];
  document.querySelectorAll('button,[role="button"],[role="radio"]').forEach(b => {
    const r = b.getBoundingClientRect();
    if (r.width < 2 || r.height < 2) return;
    const style = getComputedStyle(b);
    if (style.visibility === 'hidden' || style.display === 'none') return;
    const t = (b.textContent || '').trim().slice(0, 40);
    const a = b.getAttribute('aria-label') || '';
    if (!t && !a) return;
    out.push({ t, a, x: Math.round(r.x), y: Math.round(r.y) });
  });
  return out.slice(0, 80);
}
"""


def _dump_buttons(page, label: str) -> None:
    """Ghi danh sách nút hiển thị vào log để soi DOM khi kẹt."""
    try:
        btns = page.evaluate(_JS_BUTTONS)
    except Exception as e:
        _log(f"[DIAG {label}] không đọc được nút: {e}")
        return
    _log(f"[DIAG {label}] {len(btns)} nút hiển thị:")
    for b in btns:
        aria = f" aria='{b['a']}'" if b["a"] else ""
        txt = f" text='{b['t']}'" if b["t"] else ""
        _log(f"    ({b['x']},{b['y']}){txt}{aria}")


# JS: soi dialog "Chọn một hình ảnh khung" — options, nút, ảnh, chữ.
_JS_FRAME_DIALOG = """
() => {
  const opts = [...document.querySelectorAll("[role='option']")]
    .map(o => (o.textContent||'').trim().slice(0,40)).filter(Boolean);
  const dlg = document.querySelector("[role='dialog']");
  const btns = (dlg ? [...dlg.querySelectorAll('button')] : [])
     .map(b => (b.getAttribute('aria-label') || (b.textContent||'').trim()).slice(0,30))
     .filter(Boolean);
  return {
    hasDialog: !!dlg,
    dlgImgs: dlg ? dlg.querySelectorAll('img').length : 0,
    dlgText: dlg ? (dlg.textContent||'').trim().slice(0,160) : '',
    opts: [...new Set(opts)].slice(0,30),
    btns: [...new Set(btns)].slice(0,25),
  };
}
"""

# JS: đếm ảnh thumbnail đã gán vào khung soạn (nửa dưới màn hình).
_JS_COMPOSER_IMGS = """
() => [...document.querySelectorAll('img')].filter(im => {
  const r = im.getBoundingClientRect();
  return r.top > window.innerHeight*0.55 && r.width > 20 && r.width < 180;
}).length
"""


def _dump_frame_dialog(page, which: str) -> None:
    try:
        d = page.evaluate(_JS_FRAME_DIALOG)
    except Exception as e:
        _log(f"    [dialog {which}] lỗi đọc: {e}")
        return
    _log(f"    [dialog {which}] hasDialog={d['hasDialog']} imgs={d['dlgImgs']} "
         f"opts={d['opts']} btns={d['btns']}")
    if d.get("dlgText"):
        _log(f"    [dialog {which}] text='{d['dlgText']}'")


def _composer_imgs(page) -> int:
    try:
        return page.evaluate(_JS_COMPOSER_IMGS)
    except Exception:
        return -1


def _click(page, selectors, timeout_ms=6000) -> bool:
    return click_first(page, selectors, timeout_ms=timeout_ms)


def _configure_settings(page, sels) -> None:
    """Mở bảng cài đặt và set: THÀNH PHẦN, 16:9, Veo 3.1 Fast, 8s, x1."""
    if not _click(page, sels["settings_trigger"], 8000):
        _dump_buttons(page, "settings-trigger")
        raise RuntimeError("Không mở được Settings của Flow.")
    page.wait_for_timeout(600)
    def ensure_radio(key: str, label: str, summary_token: str = "") -> None:
        loc = query_first(page, sels[key], 2500)
        if loc is not None:
            try:
                if loc.get_attribute("aria-checked") == "true":
                    _log(f"    {label}: đã chọn sẵn")
                    return
            except Exception:
                pass
            try:
                loc.click()
                page.wait_for_timeout(300)
                return
            except Exception:
                pass

        # Duration/output thường đã là mặc định và hiện ngay trên chip Settings.
        if summary_token:
            trigger = query_first(page, sels["settings_trigger"], 1000)
            try:
                if trigger is not None and summary_token in trigger.inner_text():
                    _log(f"    {label}: xác nhận từ chip Settings")
                    return
            except Exception:
                pass
        _dump_buttons(page, f"setting-{key}")
        raise RuntimeError(f"Không chọn được {label}.")

    ensure_radio("mode_ingredients", "mode Ingredients/Thành phần")
    ensure_radio("aspect_option_169", "tỉ lệ 16:9")

    model = query_first(page, sels["model_selector"], 5000)
    if model is None:
        raise RuntimeError("Không mở được danh sách model Flow.")
    try:
        current_model = model.inner_text()
    except Exception:
        current_model = ""
    if "Veo 3.1 - Fast" not in current_model:
        model.click()
        page.wait_for_timeout(500)
        if not _click(page, sels["model_option_veo31_fast"], 6000):
            raise RuntimeError("Không chọn được Veo 3.1 - Fast.")
        page.wait_for_timeout(500)
    else:
        _log("    model Veo 3.1 - Fast: đã chọn sẵn")

    ensure_radio("duration_8s", "thời lượng 8s", "8s")
    ensure_radio("outputs_x1", "số lượng x1", "x1")
    # Đóng bảng cài đặt
    try:
        page.keyboard.press("Escape")
    except Exception:
        pass
    page.wait_for_timeout(400)


def _clear_ingredients(page, sels) -> None:
    """Xoá hết chip nguyên liệu đang có trong ô soạn (trước khi thêm ảnh mới)."""
    # Flow hiện tại có Clear prompt: ổn định hơn nút xóa chip (có thể còn ẩn
    # trong DOM sau khi bấm). Nó xóa cả chữ lẫn nguyên liệu của lượt trước.
    if _click(page, sels.get("clear_prompt", []), 1500):
        page.wait_for_timeout(500)
        return
    for _ in range(4):
        removed = False
        for sel in sels.get("remove_ingredient", []):
            try:
                loc = page.locator(sel)
                if loc.count() > 0 and loc.first.is_visible():
                    loc.first.click()
                    removed = True
                    page.wait_for_timeout(300)
                    break
            except Exception:
                pass
        if not removed:
            break


def _add_ingredient(page, sels, index: int) -> None:
    """
    Mode Thành phần: bấm '+' thêm thành phần → chọn ảnh '{index}.png' →
    'Thêm vào câu lệnh'. (Chỉ thêm 1 ảnh/clip.)
    """
    before = _composer_imgs(page)
    if not _click(page, sels["add_ingredient"], 6000):
        _dump_buttons(page, f"add-ingredient-{index}")
        raise RuntimeError("Không bấm được '+ Thêm thành phần' (add_ingredient).")
    page.wait_for_timeout(1000)

    name = f"{index}.png"
    clicked = False
    for sel in (f"[role='option']:has-text('{name}')",
                f"button:has-text('{name}')"):
        try:
            loc = page.locator(sel).first
            if loc.count() > 0:
                loc.click()
                clicked = True
                break
        except Exception:
            continue
    if not clicked:
        _dump_frame_dialog(page, f"ingredient-{index}")
        raise RuntimeError(f"Không chọn được ảnh {name} khi thêm thành phần.")
    page.wait_for_timeout(500)
    # Tùy phiên bản Flow, chọn option có thể tự thêm và đóng picker; phiên bản
    # khác vẫn hiện nút Add to prompt/Thêm vào câu lệnh.
    confirmed = _click(page, sels["frame_confirm"], 1500)
    page.wait_for_timeout(900)
    got = _composer_imgs(page)
    ingredient_chip = query_first(page, sels.get("remove_ingredient", []), 1200)
    landed = (before >= 0 and got > before) or ingredient_chip is not None
    _log(f"    thêm nguyên liệu ảnh {index}: confirm={confirmed}, "
         f"ảnh trong ô soạn {before}->{got}, landed={landed}")
    if not landed:
        _dump_buttons(page, f"ingredient-not-landed-{index}")
        raise RuntimeError(f"Ảnh {name} chưa được thêm vào prompt Flow.")


def _upload_images(page, sels, img_paths: list[str],
                   progress_cb=None) -> None:
    """Upload một lần toàn bộ ảnh; file chooser của Flow hỗ trợ multi-select."""
    if progress_cb:
        progress_cb(f"Upload {len(img_paths)} ảnh vào Flow...", 7.0)
    if not _click(page, sels["add_media_menu"], 6000):
        raise RuntimeError("Không mở được menu '+' (add_media_menu).")
    page.wait_for_timeout(500)
    try:
        with page.expect_file_chooser(timeout=15_000) as fc:
            if not _click(page, sels["upload_menu_item"], 6000):
                raise RuntimeError("Không bấm được Upload/Tải lên.")
        fc.value.set_files(img_paths)
    except Exception as e:
        raise RuntimeError(f"Upload ảnh thất bại (file chooser): {e}") from e
    page.wait_for_timeout(max(3000, len(img_paths) * 700))


def _open_frame_slot(page, sels, which: str) -> bool:
    """
    Mở dialog chọn khung cho ô Bắt đầu/Kết thúc.
    Ô này ban đầu hiện chữ 'Bắt đầu'/'Kết thúc'; SAU KHI đã chọn ảnh (từ clip
    thứ 2) nó hiển thị THUMBNAIL nên mất chữ → thử thêm các selector theo
    aria-label để bấm lại được ô đã điền.
    """
    base = (sels["start_frame_button"] if which == "start"
            else sels["end_frame_button"])
    kw = "đầu" if which == "start" else "thúc"
    extra = [
        f"button[aria-label*='{kw}']",
        f"[role='button'][aria-label*='{kw}']",
        f"button:has-text('{'Bắt đầu' if which == 'start' else 'Kết thúc'}')",
    ]
    for sel in list(base) + extra:
        try:
            loc = page.locator(sel)
            if loc.count() > 0 and loc.first.is_visible():
                loc.first.click()
                return True
        except Exception:
            continue
    return False


def _select_frame(page, sels, which: str, index: int) -> None:
    """
    Bấm ô Bắt đầu/Kết thúc → chọn ảnh theo tên '{index}.png' → 'Thêm vào câu lệnh'.
    (Ảnh trong dialog là button[role=option] có text đúng tên file.)
    """
    before = _composer_imgs(page)
    if not _open_frame_slot(page, sels, which):
        _dump_buttons(page, f"open-{which}")
        raise RuntimeError(
            f"Không bấm được ô {which} (đã thử text + aria-label). "
            f"Xem _flow.log phần [DIAG open-{which}] để lấy selector đúng.")
    page.wait_for_timeout(1200)
    _dump_frame_dialog(page, which)   # soi dialog để biết cách chọn ảnh đúng

    name = f"{index}.png"
    clicked = False
    for sel in (f"[role='option']:has-text('{name}')",
                f"button:has-text('{name}')"):
        try:
            loc = page.locator(sel).first
            if loc.count() > 0:
                loc.click()
                clicked = True
                break
        except Exception:
            continue
    if not clicked:
        # Dự phòng: tìm rồi chọn kết quả đầu tiên
        fill_first(page, sels["frame_search"], str(index), timeout_ms=4000)
        page.wait_for_timeout(1000)
        try:
            loc = page.locator("[role='option']").first
            if loc.count() > 0:
                loc.click()
                clicked = True
        except Exception:
            pass
    if not clicked:
        _dump_buttons(page, f"pick-{which}-{index}")
        raise RuntimeError(
            f"Không chọn được ảnh {name} trong dialog chọn khung.")
    page.wait_for_timeout(500)
    confirmed = _click(page, sels["frame_confirm"], 5000)
    page.wait_for_timeout(1000)
    after = _composer_imgs(page)
    landed = after > before if (before >= 0 and after >= 0) else None
    _log(f"    chọn khung {which}={index}: clickImg=OK confirm={confirmed} "
         f"ảnh_khung {before}->{after} (đã vào khung: {landed})")
    if landed is False:
        # Ảnh KHÔNG vào khung → dump để biết đúng cách. Không raise để còn
        # thấy toàn cảnh clip 1 (start+end) trong log.
        _dump_buttons(page, f"after-confirm-{which}")


# JS: số clip ĐÃ RENDER XONG & TẢI ĐƯỢC.
# ⚠️ Icon 'play_circle' là <mat-icon> (google-symbols), KHÔNG phải <button>.
# ⚠️ Tile ĐANG render cũng có 'play_circle' NGAY nhưng CHƯA có nút ⋯
#    'Tuỳ chọn khác' → phải đòi CẢ HAI (play_circle + ⋯) mới tính là xong.
_JS_DONE_CLIPS = """
() => [...document.querySelectorAll('flow-grid-tile-container')].filter(tile =>
  tile.querySelector("img[alt='Generated video thumbnail'], " +
                     "video[aria-label='Generated video']")
).length
"""


def _clip_play_count(page) -> int:
    """Số container video đã render xong; không đếm trùng thumbnail/player."""
    try:
        return page.evaluate(_JS_DONE_CLIPS)
    except Exception:
        return 0


def _wait_and_download_clip(page, sels, dest: Path, wait_sec: int,
                            prev_count: int) -> bool:
    """
    Chờ clip MỚI render xong (số nút play_circle tăng) → định vị tile clip mới
    nhất → hover → ⋯ More options/Tuỳ chọn khác → Download/Tải xuống → chọn
    độ phân giải (mặc định 720p gốc)
    → bắt sự kiện download.
    """
    deadline = time.time() + wait_sec
    t0 = time.time()
    # ⚠️ NGAY sau khi bấm tạo, Flow hiện 1 tile "đang tạo" NHẤP NHÁY có
    # play_circle + ⋯ trong ~1-2s rồi chuyển sang trạng thái render (mất ⋯).
    # → BỎ QUA giai đoạn đầu để không đếm nhầm tile nhấp nháy là clip xong.
    _log(f"    đã bấm tạo — bỏ qua 12s nhấp nháy đầu, rồi chờ render (>{prev_count})")
    page.wait_for_timeout(12000)

    # Chỉ chấp nhận khi số clip xong > prev_count ỔN ĐỊNH (2 lần liên tiếp,
    # cách nhau ~4s) → tránh mọi nhấp nháy tạm thời.
    found = False
    streak = 0
    last_log = 0.0
    while time.time() < deadline:
        cnt = _clip_play_count(page)
        if cnt > prev_count:
            streak += 1
            if streak >= 2:
                _log(f"    ✓ clip render XONG sau {time.time()-t0:.0f}s "
                     f"(clip xong={cnt})")
                found = True
                break
        else:
            streak = 0
        if time.time() - last_log > 30:
            last_log = time.time()
            _log(f"    ... đang render ({time.time()-t0:.0f}s, clip xong={cnt})")
        page.wait_for_timeout(4000)
    if not found:
        _log(f"    ✗ HẾT {wait_sec}s chưa thấy clip xong (clip xong hiện = "
             f"{_clip_play_count(page)}). Có thể Veo còn render hoặc bị chặn.")
        _dump_buttons(page, "wait-timeout")
        return False
    page.wait_for_timeout(1500)   # để tile ổn định

    # 2) Định vị tile clip MỚI NHẤT (tổ tiên của play_circle đầu tiên có chứa
    #    nút ⋯) → đánh dấu nút ⋯ của đúng tile đó + lấy toạ độ để hover.
    #    Thử lại vài lần vì ⋯ có thể gắn trễ sau khi render xong.
    _JS_MARK_TILE = """() => {
      const tiles = [...document.querySelectorAll('flow-grid-tile-container')]
        .filter(tile => tile.querySelector(
          "img[alt='Generated video thumbnail'], video[aria-label='Generated video']"));
      const tile = tiles[0];
      if (!tile) return null;
      document.querySelectorAll('[data-mm-tile]').forEach(
        x => x.removeAttribute('data-mm-tile'));
      tile.setAttribute('data-mm-tile', '1');
      const r = tile.getBoundingClientRect();
      return { x: r.x + r.width / 2, y: r.y + 24 };
    }"""
    box = None
    for _try in range(6):
        box = page.evaluate(_JS_MARK_TILE)
        if box:
            break
        page.wait_for_timeout(1500)
    if not box:
        _log("    ✗ không định vị được tile clip mới (nút ⋯ 'Tuỳ chọn khác').")
        _dump_buttons(page, "no-tile")
        return False

    res = (config.load_overrides().get("flow_download_resolution")
           or config.FLOW_DOWNLOAD_RESOLUTION)          # vd "720p", "1080p"
    res_sel = f"[role='menuitem']:has-text('{res}')"

    def open_resolution_item():
        """Mở menu của đúng tile mới và trả về item độ phân giải."""
        try:
            # Upscale làm Flow render lại tile, vì vậy data-mm-dl/box cũ có thể
            # mất. Luôn đánh dấu lại tile hoàn tất mới nhất trước mỗi lần mở.
            current_box = page.evaluate(_JS_MARK_TILE)
            if not current_box:
                return None
            page.mouse.move(current_box["x"], current_box["y"])
            page.wait_for_timeout(600)
            more = query_first(page, [
                "[data-mm-tile='1'] button[aria-label='More options']",
                "[data-mm-tile='1'] button[aria-label='Tuỳ chọn khác']",
            ], 6000)
            if more is None:
                return None
            more.click()
            page.wait_for_timeout(700)
        except Exception:
            return None

        dl = None
        for sel in sels.get("video_download", []):
            try:
                loc = page.locator(sel)
                if loc.count() > 0 and loc.first.is_visible():
                    dl = loc.first
                    break
            except Exception:
                continue
        if dl is None:
            return None
        try:
            dl.hover()
            page.wait_for_timeout(800)
        except Exception:
            pass
        if page.locator(res_sel).count() == 0:
            try:
                dl.click()
                page.wait_for_timeout(800)
            except Exception:
                return None
        item = page.locator(res_sel).first
        try:
            return item if item.count() > 0 and item.is_visible() else None
        except Exception:
            return None

    def close_menus() -> None:
        for _ in range(2):
            try:
                page.keyboard.press("Escape")
                page.wait_for_timeout(200)
            except Exception:
                pass

    # 3–5) 720p tải ngay. 1080p lần đầu khởi tạo job upscale; sau khi thông báo
    # "Upscaling your video" biến mất, mở menu lại để tải file đã upscale.
    res_item = open_resolution_item()
    if res_item is None:
        _log(f"    ✗ không thấy tùy chọn tải '{res}'.")
        _dump_buttons(page, "no-resolution")
        close_menus()
        return False
    try:
        _log(f"    tải {res}...")
        # Nếu video đã upscale sẵn, download xuất hiện ngay.
        with page.expect_download(timeout=15_000) as di:
            res_item.click()
        di.value.save_as(str(dest))
    except Exception as e:
        if "1080" not in res:
            _log(f"    ✗ tải xuống lỗi: {e}")
            close_menus()
            return False
        _log("    1080p chưa có sẵn — Flow đang upscale, chờ hoàn tất...")
        close_menus()
        notice = page.get_by_text("Upscaling your video", exact=False)
        try:
            notice.wait_for(state="hidden", timeout=wait_sec * 1000)
        except Exception:
            _log(f"    ✗ upscale 1080p chưa xong sau {wait_sec}s")
            return False
        page.wait_for_timeout(1500)
        res_item = open_resolution_item()
        if res_item is None:
            _log("    ✗ upscale xong nhưng không mở lại được mục 1080p.")
            return False
        try:
            with page.expect_download(timeout=120_000) as di:
                res_item.click()
            di.value.save_as(str(dest))
        except Exception as retry_error:
            _log(f"    ✗ tải 1080p sau upscale lỗi: {retry_error}")
            close_menus()
            return False

    close_menus()

    return dest.exists() and dest.stat().st_size > 10_000


def _clip_done(out_dir, idx: int) -> bool:
    p = out_dir / f"clip_{idx:02d}.mp4"
    try:
        return p.exists() and p.stat().st_size > 10_000
    except OSError:
        return False


def generate_clips(
    project_id: int,
    params: Optional[config.VideoParams] = None,
    progress_cb: Optional[Callable[[str, float], None]] = None,
    resume: bool = False,
    project_name: str | None = None,
) -> list[str]:
    """
    Tạo clip (chế độ THÀNH PHẦN). Trả về danh sách path clip.
    resume=True: GIỮ clip đã tải, dùng lại kế hoạch cũ (_plan.json) và chỉ tạo
    tiếp clip còn thiếu. Nếu đã đủ → không mở trình duyệt.
    restart (mặc định): XOÁ clip cũ + kế hoạch cũ, làm lại từ đầu.
    """
    global _LOGP
    params = params or config.PARAMS
    sels = config.get_selectors("flow")
    img_dir = config.images_dir(project_id, project_name)
    out_dir = config.clips_dir(project_id, project_name)
    out_dir.mkdir(parents=True, exist_ok=True)
    plan_file = out_dir / "_plan.json"

    for i in range(params.image_count):
        if not (img_dir / f"{i}.png").exists():
            raise FileNotFoundError(
                f"Thiếu ảnh {i}.png trong {img_dir} — hãy tạo ảnh trước.")

    # Kế hoạch 1:1 bắt buộc: N ảnh khác nhau → N clip; clip 0 dùng ảnh 0.
    plan: list[int] = []
    if resume and plan_file.exists():
        try:
            raw = json.loads(plan_file.read_text(encoding="utf-8"))
            if isinstance(raw, dict) and isinstance(raw.get("plan"), list):
                plan = [int(x) for x in raw["plan"]]
        except Exception:
            plan = []
    valid_plan = (len(plan) == params.image_count and plan[:1] == [0]
                  and set(plan) == set(range(params.image_count)))
    # Cho phép mở rộng pipeline (ví dụ 15 -> 20) mà vẫn giữ clip cũ. Với ánh
    # xạ 1:1 chuẩn, clip_k luôn dùng ảnh k; chỉ tạo tiếp các index còn thiếu.
    old_plan_can_extend = (
        bool(plan) and plan[:1] == [0]
        and len(set(plan)) == len(plan)
        and set(plan).issubset(set(range(params.image_count)))
        and all(_clip_done(out_dir, k) for k in range(len(plan)))
    )
    if resume and not valid_plan and old_plan_can_extend:
        # Giữ nguyên mapping clip cũ; nối thêm đúng những ảnh chưa từng dùng.
        plan = [*plan, *(i for i in range(params.image_count) if i not in plan)]
        valid_plan = True
    if not valid_plan:
        plan = config.generate_ingredient_plan(
            params.image_count, params.image_count - 1)

    if not resume:
        # RESTART: xoá clip cũ + kế hoạch + log
        for old in out_dir.glob("clip_*.mp4"):
            try:
                old.unlink()
            except OSError:
                pass

    _LOGP = out_dir / "_flow.log"
    try:                                    # bắt đầu log mới mỗi lần chạy
        _LOGP.write_text("", encoding="utf-8")
    except Exception:
        pass
    _log(f"=== BẮT ĐẦU tạo clip project {project_id} "
         f"({'RESUME' if resume else 'RESTART'}) ===")

    def _p(msg, pct):
        if progress_cb:
            progress_cb(msg, pct)

    total = len(plan)
    # Lưu kế hoạch đầy đủ ngay để resume lần sau dùng lại đúng plan.
    plan_map: dict[str, int] = {f"clip_{k:02d}.mp4": im
                                for k, im in enumerate(plan)
                                if _clip_done(out_dir, k)}
    try:
        plan_file.write_text(json.dumps(
            {"plan": plan, "clips": plan_map}, ensure_ascii=False),
            encoding="utf-8")
    except Exception:
        pass

    # Resume mà đã đủ clip → khỏi mở trình duyệt.
    if resume and all(_clip_done(out_dir, k) for k in range(total)):
        _log("Đã đủ clip — bỏ qua (resume)")
        _p("Đã đủ clip (resume)", 100.0)
        return [str(out_dir / f"clip_{k:02d}.mp4") for k in range(total)]
    # Prompt chuyển động: nhẹ nhàng, chậm, mượt (video sẽ còn được làm chậm 0.7x
    # và blend khi ghép). Xoay vòng vài biến thể cho đa dạng.
    base_motion = (config.load_overrides().get("flow_motion_prompt")
                   or config.FLOW_MOTION_PROMPT)
    motion_variants = [
        base_motion,
        base_motion + ", lia máy sang phải rất chậm",
        base_motion + ", đẩy máy tiến vào từ từ (push-in)",
        base_motion + ", nâng máy lên nhẹ (tilt-up)",
        base_motion + ", lia máy sang trái rất chậm",
    ]
    # Một project Flow có thể chỉ hiện khoảng 15 media đầu và tự đổi tên phần
    # vượt giới hạn. Khi Resume, chỉ upload ảnh thực sự cần cho clip còn thiếu.
    # Nhờ đó nâng 15 -> 20 vẫn tạo tiếp 5 clip mà không upload lại 20 ảnh.
    todo_images = sorted({
        img for clip_idx, img in enumerate(plan)
        if not (resume and _clip_done(out_dir, clip_idx))
    })
    img_paths = [str(img_dir / f"{i}.png") for i in todo_images]

    _log(f"kế hoạch nguyên liệu ({total}): {plan}")
    if resume:
        done_idx = [k for k in range(total) if _clip_done(out_dir, k)]
        _log(f"resume: đã có {len(done_idx)} clip {done_idx}, tạo tiếp phần thiếu")
    saved: list[str] = []
    with BrowserSession() as sess:
        page = sess.new_page()
        _p("Mở Google Flow...", 2.0)
        page.goto(config.FLOW_URL, wait_until="domcontentloaded")
        # Một số tài khoản vào trang /about trước dashboard.
        _click(page, sels.get("flow_entry", []), 5000)
        wait_for_manual_login(
            page, sels["new_project"] + sels["add_media_menu"], "Flow",
            timeout_sec=300)

        _p("Tạo dự án Flow mới...", 4.0)
        _log("tạo dự án mới")
        if not _click(page, sels["new_project"], 8000):
            _dump_buttons(page, "new-project")
            raise RuntimeError("Không bấm được New project/Dự án mới.")
        page.wait_for_timeout(2500)

        _p("Cài đặt Thành phần · 16:9 · Veo 3.1 Fast · 8s · x1...", 6.0)
        _log("cài đặt Thành phần/16:9/Veo3.1Fast/8s/x1")
        _configure_settings(page, sels)

        _p("Upload ảnh vào Flow...", 8.0)
        _log(f"upload {len(img_paths)} ảnh cần dùng: {todo_images}")
        _upload_images(page, sels, img_paths, progress_cb)
        _log("upload ảnh xong")

        for clip_idx, img in enumerate(plan):
            pct = 10.0 + (clip_idx / max(total, 1)) * 88.0
            dest = out_dir / f"clip_{clip_idx:02d}.mp4"
            # RESUME: clip đã có sẵn → bỏ qua, không tạo lại.
            if resume and _clip_done(out_dir, clip_idx):
                _log(f"--- CLIP {clip_idx+1}/{total} (ảnh {img}) — ĐÃ CÓ, bỏ qua")
                saved.append(str(dest))
                plan_map[f"clip_{clip_idx:02d}.mp4"] = img
                continue
            _log(f"--- CLIP {clip_idx+1}/{total}  (ảnh {img}) ---")
            _p(f"[{clip_idx+1}/{total}] Clip từ ảnh {img}: thêm nguyên liệu...",
               pct)
            # Đảm bảo không còn menu/overlay & xoá nguyên liệu vòng trước
            try:
                page.keyboard.press("Escape")
                page.wait_for_timeout(300)
            except Exception:
                pass
            _clear_ingredients(page, sels)
            _add_ingredient(page, sels, img)
            motion = motion_variants[clip_idx % len(motion_variants)]
            if motion:
                fill_first(page, sels["prompt_box"], motion, 5000)
            prev_count = _clip_play_count(page)   # số clip xong trước khi tạo
            _log(f"    bấm tạo (clip xong hiện tại={prev_count})")
            _p(f"[{clip_idx+1}/{total}] Đang tạo clip từ ảnh {img}...", pct)
            if not _click(page, sels["generate_button"], 8000):
                _dump_buttons(page, "no-generate")
                raise RuntimeError("Không bấm được 'Bắt đầu tạo' (generate_button).")
            dest = out_dir / f"clip_{clip_idx:02d}.mp4"
            if not _wait_and_download_clip(
                    page, sels, dest, config.BROWSER.clip_wait_sec, prev_count):
                raise RuntimeError(
                    f"Clip {clip_idx} (ảnh {img}): không tạo/tải được trong "
                    f"{config.BROWSER.clip_wait_sec}s.")
            _log(f"    ✓ ĐÃ LƯU {dest.name} "
                 f"({dest.stat().st_size//1024} KB)")
            saved.append(str(dest))
            plan_map[f"clip_{clip_idx:02d}.mp4"] = img
            # Lưu kế hoạch + mapping sau mỗi clip (giữ 'plan' để resume dùng lại)
            try:
                plan_file.write_text(json.dumps(
                    {"plan": plan, "clips": plan_map}, ensure_ascii=False),
                    encoding="utf-8")
            except Exception:
                pass

    _p("Xong tạo clip", 100.0)
    return saved
