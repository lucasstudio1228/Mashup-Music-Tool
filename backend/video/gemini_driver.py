"""
video/gemini_driver.py — Tự động hoá Gemini web (gemini.google.com) để tạo ảnh.

Chọn model 3.1 Pro (2 bước: '3.1 Pro' rồi 'Tư duy mở rộng'), sinh 20 ảnh:
  0 = thumbnail CÓ CHỮ biến thiên theo project; 1..14 = các shot liên kết,
  đồng bộ nhân vật, bối cảnh và diễn tiến ánh sáng.
Lưu vào media/<project>/images/{i}.png.

Nhận & TẢI ảnh (bền, không phụ thuộc selector CSS mong manh):
  - Chờ tới khi có 1 ảnh THẬT mới (naturalWidth/Height >= MIN_IMAGE_DIM) & ổn định.
  - Chờ Gemini TRẢ LỜI XONG rồi mới tải: hover vào ảnh để hiện thanh công cụ,
    thử tải bằng menu ⋯ → 'Tải hình ảnh xuống'; nếu chưa được thì CHUỘT PHẢI
    vào ảnh → 'Tải hình ảnh xuống'; cuối cùng fallback tải trực tiếp từ src.
    Lặp lại tới khi tải được (vì thanh nút xuất hiện trễ sau khi ảnh render).
"""
from __future__ import annotations
import time
from pathlib import Path
from typing import Callable, Optional

from . import config
from .browser_base import (
    BrowserSession, fill_first, click_first, wait_for_manual_login,
)

MIN_IMAGE_DIM = 400

_JS_LARGE_IMAGES = """
(minDim) => {
  return [...document.images]
    .filter(im => im.naturalWidth >= minDim && im.naturalHeight >= minDim
                  && im.src
                  && (im.src.startsWith('http') || im.src.startsWith('blob:')))
    .map(im => ({ src: im.src, w: im.naturalWidth, h: im.naturalHeight }));
}
"""

# Toạ độ tâm ảnh theo src (để hover / chuột phải đúng ảnh mới nhất).
_JS_IMAGE_RECT = """
(src) => {
  const im = [...document.images].find(i => i.src === src);
  if (!im) return null;
  const r = im.getBoundingClientRect();
  return { x: r.x + r.width/2, y: r.y + r.height/2,
           top: r.y, bottom: r.y + r.height, w: r.width, h: r.height };
}
"""


# Thu thập nhãn nút thật để chẩn đoán khi không tìm được nút tải.
_JS_DIAG = """
() => {
  const labels = new Set();
  document.querySelectorAll('button[aria-label]').forEach(b => {
    const a = b.getAttribute('aria-label'); if (a) labels.add(a);
  });
  const texts = new Set();
  document.querySelectorAll("[role='menuitem'],[role='menuitemradio'],button,a,span")
    .forEach(e => {
      const t = (e.textContent || '').trim();
      if (t && t.length < 60 &&
          (t.includes('Tải') || t.toLowerCase().includes('download'))) texts.add(t);
    });
  return { labels: [...labels].slice(0, 40), texts: [...texts].slice(0, 20) };
}
"""


def _diagnostic(page) -> str:
    try:
        d = page.evaluate(_JS_DIAG)
        return (f"aria-labels nút: {d.get('labels')}; "
                f"chữ có 'Tải/download': {d.get('texts')}")[:600]
    except Exception:
        return "(không đọc được nhãn nút)"


def _thumbnail_keywords() -> str:
    import random
    kws = (config.load_overrides().get("thumbnail_keywords")
           or config.THUMBNAIL_KEYWORDS)
    n = min(3, len(kws))
    return " · ".join(random.sample(list(kws), n)) if n else ""


def _prompt_for(index: int, topic: str, params: config.VideoParams,
                prompts_override: Optional[dict] = None) -> str:
    # Ưu tiên prompt do AI sinh từ ý tưởng người dùng (prompts_override),
    # rồi tới overrides file, cuối cùng là DEFAULT_PROMPTS.
    prompts = (prompts_override
               or config.load_overrides().get("prompts")
               or config.DEFAULT_PROMPTS)
    tmpl = prompts.get(str(index))
    fmt = {"topic": topic, "keywords": _thumbnail_keywords()}
    if tmpl:
        try:
            return tmpl.format(**fmt)
        except Exception:
            return tmpl.replace("{topic}", topic)
    return (f"Tạo ảnh nền cảnh thiên nhiên/thành phố/mây trời số {index}, "
            f"phong cách {params.image_style}, chủ đề '{topic}', "
            f"tỉ lệ {params.aspect_ratio}, không chữ.")


def _select_model(page, sels) -> None:
    """2 bước: (1) '3.1 Pro'; (2) bật 'Tư duy mở rộng'. Best-effort."""
    if click_first(page, sels["model_selector"], timeout_ms=6000):
        page.wait_for_timeout(600)
        click_first(page, sels["model_option_pro"], timeout_ms=6000)
        page.wait_for_timeout(800)
    if click_first(page, sels["model_selector"], timeout_ms=6000):
        page.wait_for_timeout(600)
        click_first(page, sels["model_option_thinking"], timeout_ms=6000)
        page.wait_for_timeout(800)


def _wait_new_image(page, seen: set[str], timeout_sec: int) -> Optional[str]:
    """Chờ 1 ảnh THẬT mới (đủ lớn, chưa có) và đã load ổn định. Trả về src."""
    deadline = time.time() + timeout_sec
    last: Optional[str] = None
    stable = 0
    while time.time() < deadline:
        try:
            cands = page.evaluate(_JS_LARGE_IMAGES, MIN_IMAGE_DIM)
        except Exception:
            cands = []
        new = [c for c in cands if c.get("src") and c["src"] not in seen]
        if new:
            new.sort(key=lambda c: c["w"] * c["h"], reverse=True)
            top = new[0]["src"]
            if top == last:
                stable += 1
            else:
                last, stable = top, 1
            if stable >= 2:
                return top
        page.wait_for_timeout(3000)
    return None


def _find_menu_item(page, selectors: list[str]):
    for sel in selectors:
        try:
            loc = page.locator(sel)
            if loc.count() > 0 and loc.first.is_visible():
                return loc.first
        except Exception:
            continue
    return None


def _find_last_visible(page, selectors: list[str]):
    for sel in selectors:
        try:
            loc = page.locator(sel)
            if loc.count() > 0:
                cand = loc.last
                if cand.is_visible():
                    return cand
        except Exception:
            continue
    return None


def _click_menu_download(page, dest: Path, sels) -> bool:
    """Menu vừa mở → bấm 'Tải hình ảnh xuống', bắt sự kiện download."""
    item = _find_menu_item(page, sels.get("download_menu_item", []))
    if item is None:
        try:
            page.keyboard.press("Escape")
        except Exception:
            pass
        return False
    try:
        with page.expect_download(timeout=60_000) as di:
            item.click()
        di.value.save_as(str(dest))
        return dest.exists() and dest.stat().st_size > 2000
    except Exception:
        try:
            page.keyboard.press("Escape")
        except Exception:
            pass
        return False


def _download_new_image(page, sels, src: str, dest: Path,
                        wait_sec: int) -> bool:
    """
    Tải ảnh (theo src) đúng cách Gemini, chờ tới khi thanh công cụ sẵn sàng.
    Thử: hover → nút ⋯ → 'Tải hình ảnh xuống'; rồi chuột phải ảnh → menu;
    cuối cùng fallback tải từ src.
    """
    deadline = time.time() + wait_sec
    while time.time() < deadline:
        rect = None
        try:
            rect = page.evaluate(_JS_IMAGE_RECT, src)
        except Exception:
            rect = None

        # Hover vào ảnh để hiện thanh nút hành động
        if rect:
            try:
                page.mouse.move(rect["x"], rect["y"])
                page.wait_for_timeout(500)
                # rê xuống mép dưới ảnh nơi thanh nút thường xuất hiện
                page.mouse.move(rect["x"], min(rect["bottom"] + 20, rect["bottom"]))
                page.wait_for_timeout(500)
            except Exception:
                pass

        # Nút ⋯ "Hiện thêm tuỳ chọn" → menu → 'Tải hình ảnh xuống'
        more = _find_last_visible(page, sels.get("more_button", []))
        if more is not None:
            try:
                more.click()
                page.wait_for_timeout(700)
                if _click_menu_download(page, dest, sels):
                    return True
            except Exception:
                pass

        page.wait_for_timeout(2000)   # thanh nút có thể xuất hiện trễ → thử lại

    # Fallback cuối: tải từ src (chỉ dùng được nếu là http, KHÔNG dùng cho blob:)
    if src.startswith("http"):
        try:
            resp = page.context.request.get(src)
            if resp.ok:
                dest.write_bytes(resp.body())
                if dest.stat().st_size > 2000:
                    return True
        except Exception:
            pass
    return False


def _has_image(out_dir, i: int) -> bool:
    p = out_dir / f"{i}.png"
    try:
        return p.exists() and p.stat().st_size > 2000
    except OSError:
        return False


def generate_images(
    project_id: int,
    topic: str,
    params: Optional[config.VideoParams] = None,
    progress_cb: Optional[Callable[[str, float], None]] = None,
    resume: bool = False,
    project_name: str | None = None,
    prompts_override: Optional[dict] = None,
) -> list[str]:
    """
    Trả về danh sách đường dẫn ảnh đã tạo (0..n) qua Gemini web.
    resume=True: BỎ QUA ảnh đã có (chỉ tạo ảnh còn thiếu). Nếu đã đủ → không
    mở trình duyệt.
    """
    params = params or config.PARAMS
    sels = config.get_selectors("gemini")
    out_dir = config.images_dir(project_id, project_name)
    out_dir.mkdir(parents=True, exist_ok=True)

    def _p(msg, pct):
        if progress_cb:
            progress_cb(msg, pct)

    n = params.image_count
    todo = [i for i in range(n) if not (resume and _has_image(out_dir, i))]
    if resume and not todo:
        _p("Đã đủ ảnh — bỏ qua (resume)", 100.0)
        return [str(out_dir / f"{i}.png") for i in range(n) if _has_image(out_dir, i)]
    if resume:
        _p(f"Resume: còn thiếu ảnh {todo}", 1.0)

    saved: list[str] = []
    seen: set[str] = set()
    with BrowserSession() as sess:
        page = sess.new_page()
        _p("Mở Gemini...", 2.0)
        page.goto(config.GEMINI_URL, wait_until="domcontentloaded")
        wait_for_manual_login(page, sels["prompt_box"], "Gemini",
                              timeout_sec=300)

        _p(f"Chọn model {config.GEMINI_IMAGE_MODEL} (tư duy mở rộng)...", 4.0)
        _select_model(page, sels)

        try:
            for c in page.evaluate(_JS_LARGE_IMAGES, MIN_IMAGE_DIM):
                if c.get("src"):
                    seen.add(c["src"])
        except Exception:
            pass

        for k, i in enumerate(todo):
            pct = 6.0 + (k / max(len(todo), 1)) * 90.0
            _p(f"[{k+1}/{len(todo)}] Gửi prompt ảnh {i}...", pct)
            prompt = _prompt_for(i, topic, params, prompts_override)

            if not fill_first(page, sels["prompt_box"], prompt):
                raise RuntimeError("Không tìm thấy ô nhập prompt Gemini "
                                   "(cập nhật selector 'prompt_box').")
            page.wait_for_timeout(400)
            if not click_first(page, sels["send_button"], timeout_ms=4000):
                page.keyboard.press("Enter")

            _p(f"[{i+1}/{n}] Đang chờ Gemini tạo ảnh {i}...", pct)
            src = _wait_new_image(page, seen, config.BROWSER.image_wait_sec)
            if not src:
                raise RuntimeError(
                    f"Ảnh {i}: chờ {config.BROWSER.image_wait_sec}s không thấy "
                    f"ảnh mới đủ lớn. Có thể Gemini trả lời bằng chữ (model chưa "
                    f"bật tạo ảnh) hoặc bị giới hạn.")

            _p(f"[{i+1}/{n}] Đang tải ảnh {i}...", pct + 3.0)
            dest = out_dir / f"{i}.png"
            # Cho thêm thời gian để Gemini trả lời XONG (thanh nút ⋯ xuất hiện).
            if not _download_new_image(page, sels, src, dest, wait_sec=90):
                raise RuntimeError(
                    f"Ảnh {i}: không tải được (menu ⋯ / chuột phải / src đều "
                    f"thất bại). CHẨN ĐOÁN → {_diagnostic(page)}")
            seen.add(src)
            saved.append(str(dest))
            _p(f"[{i+1}/{n}] Đã lưu ảnh {i}", pct + 6.0)

    _p("Xong tạo ảnh", 100.0)
    # Trả về TẤT CẢ ảnh hiện có (gồm ảnh cũ khi resume + ảnh mới tạo).
    return [str(out_dir / f"{i}.png") for i in range(n) if _has_image(out_dir, i)]
