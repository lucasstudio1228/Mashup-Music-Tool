"""
video/gemini_driver.py — Tự động hoá Gemini web (gemini.google.com) để tạo ảnh.

Chọn model 3.1 Pro (2 bước: '3.1 Pro' rồi 'Tư duy mở rộng'), sinh N ảnh:
  0 = ảnh nền thumbnail (KHÔNG chữ); 1..N-1 = các shot liên kết,
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
                prompts_override: Optional[dict] = None,
                style: Optional[str] = None) -> str:
    # Ưu tiên prompt do AI sinh từ ý tưởng người dùng (prompts_override),
    # rồi tới overrides file, cuối cùng là DEFAULT_PROMPTS.
    prompts = prompts_override
    if prompts is None:
        prompts = config.load_overrides().get("prompts")
    if prompts is None:
        prompts = config.DEFAULT_PROMPTS
    if not 0 <= index < params.image_count:
        raise ValueError(f"Chỉ số ảnh {index} ngoài bộ {params.image_count} ảnh.")
    tmpl = prompts.get(str(index)) if isinstance(prompts, dict) else None
    if not isinstance(tmpl, str) or not tmpl.strip():
        raise ValueError(
            f"Thiếu prompt ảnh {index}/{params.image_count - 1}. "
            "Hãy tạo lại bộ prompt đầy đủ; không dùng cảnh mặc định rời rạc "
            "vì sẽ làm mất đồng bộ nhân vật và bối cảnh.")
    fmt = {"topic": topic, "keywords": _thumbnail_keywords()}
    try:
        body = tmpl.format(**fmt)
    except Exception:
        body = tmpl.replace("{topic}", topic)
    # ÉP phong cách hoạt hình (2D/3D) vào ĐẦU + phủ định cấm ảnh thật ở CUỐI,
    # áp dụng cho MỌI prompt (AI-sinh, overrides file, DEFAULT_PROMPTS).
    wrapped = config.wrap_style(body, style)
    if index == 0:
        # Thumbnail: model sinh ảnh KHÔNG đánh vần được → cấm tuyệt đối vẽ chữ,
        # tiêu đề sẽ được overlay bằng font thật sau (thumbnail.render_title).
        # Đặt CUỐI cùng để ưu tiên cao nhất; yêu cầu chừa khoảng trống bên trái.
        wrapped += (
            " || QUAN TRỌNG NHẤT (ưu tiên tuyệt đối): ẢNH PHẢI HOÀN TOÀN KHÔNG "
            "CÓ CHỮ. TUYỆT ĐỐI KHÔNG vẽ bất kỳ chữ/tiêu đề/tên/typography/"
            "caption/watermark/logo/số nào trong ảnh — không một ký tự nào. Bố "
            "cục thumbnail hút mắt, nhân vật lệch sang PHẢI, CHỪA khoảng trống "
            "thoáng (negative space) ở NỬA TRÁI để chèn tiêu đề sau."
        )
    return wrapped


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


def _start_new_chat(page) -> None:
    """Bắt đầu cuộc trò chuyện MỚI để CẮT ngữ cảnh dài. Hội thoại quá dài (vd
    ảnh 37/41 trong cùng 1 chat) khiến Gemini hay trả CHỮ / từ chối / chậm →
    time-out. Mỗi prompt đã tự chứa khối danh tính nhân vật nên KHÔNG cần giữ
    mạch chat. Ưu tiên bấm nút 'Trò chuyện mới'; không có → reload thẳng
    GEMINI_URL (URL /app luôn mở chat trống)."""
    sels = config.get_selectors("gemini")
    clicked = False
    try:
        clicked = click_first(page, sels.get("new_chat", []), timeout_ms=3000)
    except Exception:
        clicked = False
    if not clicked:
        try:
            page.goto(config.GEMINI_URL, wait_until="domcontentloaded")
        except Exception:
            pass
    page.wait_for_timeout(1500)


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
    style: Optional[str] = None,
    only: Optional[list[int]] = None,
) -> list[str]:
    """
    Trả về danh sách đường dẫn ảnh đã tạo (0..n) qua Gemini web.
    resume=True: BỎ QUA ảnh đã có (chỉ tạo ảnh còn thiếu). Nếu đã đủ → không
    mở trình duyệt.
    only=[i,...]: CHỈ tạo lại đúng các ảnh trong danh sách (ghi đè), GIỮ NGUYÊN
    mọi ảnh khác — dùng cho nút "Tạo lại 1 ảnh" lẻ.
    Ảnh 0 giữ SẠCH (không chữ): tiêu đề được overlay ở bước ghép video
    (service.step_assemble) lên clip intro + thumbnail.png bằng font thật, tránh
    chữ nướng sẵn bị Veo tạo lại thành bóng ma / chồng chéo.
    """
    params = params or config.PARAMS
    sels = config.get_selectors("gemini")
    out_dir = config.images_dir(project_id, project_name)
    out_dir.mkdir(parents=True, exist_ok=True)

    def _p(msg, pct):
        if progress_cb:
            progress_cb(msg, pct)

    n = params.image_count
    only_set = None
    if only is not None:
        only_set = sorted({int(i) for i in only if 0 <= int(i) < n})
        if not only_set:
            _p("Không có ảnh hợp lệ để tạo lại.", 100.0)
            return [str(out_dir / f"{i}.png") for i in range(n) if _has_image(out_dir, i)]
    # RESTART: XOÁ hết ảnh cũ trước khi tạo, để KHÔNG bao giờ trộn ảnh mới với
    # ảnh cũ/của lần render trước. Nếu run mới bị dở dang thì bước clip sẽ báo
    # "thiếu ảnh" rõ ràng thay vì âm thầm dùng ảnh cũ để ghép.
    # only-mode KHÔNG xoá gì (chỉ ghi đè đúng ảnh được chọn).
    if not resume and only_set is None:
        removed = 0
        for old in out_dir.glob("*.png"):
            try:
                old.unlink()
                removed += 1
            except OSError:
                pass
        if removed:
            _p(f"Xoá {removed} ảnh cũ (restart)", 1.0)
    if only_set is not None:
        todo = only_set
        _p(f"Tạo lại ảnh cụ thể: {todo}", 1.0)
    else:
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

        # BỀN HOÁ: mỗi ảnh thử tối đa _IMAGE_ATTEMPTS lần; và cứ _NEW_CHAT_EVERY
        # ảnh lại mở chat mới để CẮT ngữ cảnh dài (nguyên nhân gốc khiến ảnh ~37/41
        # bị time-out: hội thoại quá dài → Gemini trả chữ/từ chối). Ảnh lưu NGAY
        # từng cái nên nếu vẫn fail thì resume tạo tiếp, KHÔNG mất ảnh đã có.
        _IMAGE_ATTEMPTS = 3
        _NEW_CHAT_EVERY = int(getattr(config.BROWSER, "image_new_chat_every", 8) or 8)
        done_count = 0
        for k, i in enumerate(todo):
            pct = 6.0 + (k / max(len(todo), 1)) * 90.0
            prompt = _prompt_for(i, topic, params, prompts_override, style)

            # Cắt ngữ cảnh định kỳ (trước khi gửi), tránh chat phình quá dài.
            if _NEW_CHAT_EVERY > 0 and done_count > 0 \
                    and done_count % _NEW_CHAT_EVERY == 0:
                _p(f"Mở cuộc trò chuyện mới (cắt ngữ cảnh dài) trước ảnh {i}...", pct)
                _start_new_chat(page)
                _select_model(page, sels)

            last_err = ""
            saved_ok = False
            dest = out_dir / f"{i}.png"
            for attempt in range(1, _IMAGE_ATTEMPTS + 1):
                tag = f"(lần {attempt}/{_IMAGE_ATTEMPTS})"
                if attempt > 1:
                    # Thử lại: mở CHAT MỚI + chọn lại model + chờ hồi (rate-limit),
                    # rồi gửi lại prompt tự-chứa-danh-tính này từ ngữ cảnh sạch.
                    _p(f"[{k+1}/{len(todo)}] Ảnh {i} lỗi ({last_err[:70]}) — "
                       f"thử lại {tag}...", pct)
                    _start_new_chat(page)
                    _select_model(page, sels)
                    page.wait_for_timeout(min(6000 * attempt, 18000))

                _p(f"[{k+1}/{len(todo)}] Gửi prompt ảnh {i} {tag}...", pct)
                if not fill_first(page, sels["prompt_box"], prompt):
                    last_err = "không tìm thấy ô nhập prompt"
                    continue
                page.wait_for_timeout(400)
                if not click_first(page, sels["send_button"], timeout_ms=4000):
                    page.keyboard.press("Enter")

                _p(f"[{i+1}/{n}] Đang chờ Gemini tạo ảnh {i} {tag}...", pct)
                src = _wait_new_image(page, seen, config.BROWSER.image_wait_sec)
                if not src:
                    last_err = (f"chờ {config.BROWSER.image_wait_sec}s không thấy "
                                f"ảnh mới (Gemini có thể trả chữ / bị giới hạn)")
                    continue

                _p(f"[{i+1}/{n}] Đang tải ảnh {i} {tag}...", pct + 3.0)
                if not _download_new_image(page, sels, src, dest, wait_sec=90):
                    # Đã có ảnh nhưng tải hỏng → đánh dấu src đã thấy để lần thử
                    # sau nhận ĐÚNG ảnh mới, không "dính" lại ảnh này.
                    seen.add(src)
                    last_err = "không tải được ảnh (menu ⋯/chuột phải/src đều fail)"
                    continue

                seen.add(src)
                saved.append(str(dest))
                saved_ok = True
                done_count += 1
                _p(f"[{i+1}/{n}] Đã lưu ảnh {i} {tag}", pct + 6.0)
                break
                # Ảnh 0 giữ SẠCH — tiêu đề overlay ở bước ghép (step_assemble).

            if not saved_ok:
                raise RuntimeError(
                    f"Ảnh {i}: thất bại sau {_IMAGE_ATTEMPTS} lần thử "
                    f"({last_err}). Đã tạo {done_count}/{len(todo)} ảnh — bấm "
                    f"'Tạo tiếp ảnh thiếu' (resume) để làm nốt phần còn lại mà "
                    f"KHÔNG mất ảnh đã có. CHẨN ĐOÁN → {_diagnostic(page)}")

    _p("Xong tạo ảnh", 100.0)
    # Trả về TẤT CẢ ảnh hiện có (gồm ảnh cũ khi resume + ảnh mới tạo).
    return [str(out_dir / f"{i}.png") for i in range(n) if _has_image(out_dir, i)]
