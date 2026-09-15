"""
video/thumbnail.py — Vẽ tiêu đề lên ảnh thumbnail (ảnh 0) bằng Pillow.

LÝ DO: model sinh ảnh (Gemini) KHÔNG đánh vần được chữ → tiêu đề "vẽ" vào ảnh
ra gibberish ("Bubaıng cou ın" thay vì "Bamboo Flute"). Cách xử lý triệt để:
bảo Gemini vẽ ảnh nền KHÔNG chữ, rồi overlay tiêu đề ở đây bằng font thật →
chính tả + dấu tiếng Việt luôn đúng, nét chữ sắc, tương phản tốt.

Best-effort: nếu thiếu Pillow/font hoặc lỗi → giữ nguyên ảnh, KHÔNG làm vỡ
pipeline (chỉ log cảnh báo).
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

# Font ưu tiên (đều hỗ trợ dấu tiếng Việt trên Windows). Đặt bold trước cho
# tiêu đề, semibold/regular cho dòng phụ. Có thể override qua overrides.json.
_TITLE_FONTS = [
    "C:/Windows/Fonts/segoeuib.ttf",   # Segoe UI Bold
    "C:/Windows/Fonts/arialbd.ttf",    # Arial Bold
    "C:/Windows/Fonts/ariblk.ttf",     # Arial Black
]
_SUBTITLE_FONTS = [
    "C:/Windows/Fonts/seguisb.ttf",    # Segoe UI Semibold
    "C:/Windows/Fonts/segoeui.ttf",    # Segoe UI
    "C:/Windows/Fonts/arial.ttf",      # Arial
]

# Bảng màu chữ 3D "hấp dẫn": mặt chữ gradient VÀNG GOLD ấm (nổi bật trên nền
# healing xanh), thân 3D teal đậm, viền tối sắc nét, quầng sáng ấm. Có thể chỉnh
# qua overrides.json (title_face_top/title_face_bottom/title_extrude/...).
_FACE_TOP = (255, 248, 214)       # kem sáng (mặt trên chữ)
_FACE_BOTTOM = (255, 190, 58)     # vàng gold (mặt dưới chữ)
_EXTRUDE = (13, 46, 37)           # teal đậm — thân khối 3D
_OUTLINE = (6, 20, 15)            # viền tối, tạo nét sắc
_GLOW = (255, 176, 32)            # quầng sáng ấm sau chữ
_SUB_FACE = (242, 249, 245)       # dòng phụ: trắng ngà


def _vgrad_region(w: int, h: int, y0: int, y1: int, top, bottom):
    """Ảnh RGB gradient dọc: hàng y0→top color, y1→bottom color (clamp ngoài
    khoảng). Dùng để tô mặt chữ theo đúng chiều cao khối tiêu đề → hiệu ứng gold
    đậm hơn là gradient trải cả canvas."""
    from PIL import Image
    span = max(y1 - y0, 1)
    col = Image.new("RGB", (1, h))
    px = col.load()
    for yy in range(h):
        t = min(1.0, max(0.0, (yy - y0) / span))
        px[0, yy] = tuple(int(top[i] + (bottom[i] - top[i]) * t) for i in range(3))
    return col.resize((w, h))


def _load_font(candidates: list[str], size: int):
    from PIL import ImageFont
    for path in candidates:
        try:
            if Path(path).exists():
                return ImageFont.truetype(path, size)
        except Exception:
            continue
    try:
        return ImageFont.load_default()
    except Exception:
        return None


def _fit_font(candidates: list[str], text: str, max_w: int, start_size: int,
              min_size: int = 14):
    """Chọn cỡ font lớn nhất để `text` vừa trong `max_w` (giảm dần)."""
    from PIL import ImageDraw, Image
    scratch = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    size = start_size
    while size > min_size:
        font = _load_font(candidates, size)
        if font is None:
            return None
        w = scratch.textlength(text, font=font)
        if w <= max_w:
            return font
        size -= 2
    return _load_font(candidates, min_size)


def _wrap(text: str, font, draw, max_w: int) -> list[str]:
    """Ngắt dòng theo từ để mỗi dòng vừa max_w."""
    words = text.split()
    if not words:
        return []
    lines: list[str] = []
    cur = words[0]
    for w in words[1:]:
        trial = f"{cur} {w}"
        if draw.textlength(trial, font=font) <= max_w:
            cur = trial
        else:
            lines.append(cur)
            cur = w
    lines.append(cur)
    return lines


def _compose_title(base, title: str, subtitle: str, tf: list[str],
                   sf: list[str]):
    """Vẽ lớp tối gradient + tiêu đề (viền + bóng mềm) lên ảnh RGBA `base`.
    Trả về ảnh RGBA MỚI đã composite, hoặc None nếu không nạp được font.

    Dùng CHUNG cho: (1) thumbnail — `base` là ảnh nền; (2) overlay video —
    `base` trong suốt để ffmpeg chồng chữ lên clip intro. Nhờ vậy chữ trên
    intro video khớp y hệt thumbnail."""
    from PIL import Image, ImageDraw, ImageFilter, ImageChops

    W, H = base.size
    margin = int(W * 0.06)
    text_max_w = int(W * 0.62)              # chừa phải cho nhân vật

    # 1) Lớp tối gradient bên trái (đảm bảo tương phản, vẫn giữ nét healing)
    grad = Image.new("L", (W, 1), 0)
    gpx = grad.load()
    fade_to = int(W * 0.72)
    max_alpha = 150
    for x in range(W):
        gpx[x, 0] = 0 if x >= fade_to else int(max_alpha * (1 - x / fade_to))
    grad = grad.resize((W, H))
    shade = Image.new("RGBA", (W, H), (10, 20, 15, 0))
    shade.putalpha(grad)
    base = Image.alpha_composite(base, shade)

    draw = ImageDraw.Draw(base)

    # 2) Chọn cỡ + ngắt dòng tiêu đề
    title_font = _fit_font(tf, title, text_max_w, int(H * 0.16))
    if title_font is None:
        return None
    title_lines = _wrap(title, title_font, draw, text_max_w)

    sub_font = None
    sub_lines: list[str] = []
    if subtitle.strip():
        sub_font = _fit_font(sf, subtitle.strip(), text_max_w, int(H * 0.062))
        if sub_font is not None:
            sub_lines = _wrap(subtitle.strip(), sub_font, draw, text_max_w)

    def _line_h(font) -> int:
        b = draw.textbbox((0, 0), "Ag", font=font)
        return b[3] - b[1]

    title_lh = _line_h(title_font)
    gap = int(title_lh * 0.22)
    sub_lh = _line_h(sub_font) if sub_font else 0
    sub_gap = int(title_lh * 0.5)
    x = margin

    block_h = len(title_lines) * (title_lh + gap) - gap
    if sub_lines:
        block_h += sub_gap + len(sub_lines) * (sub_lh + gap) - gap

    y = max(margin, (H - block_h) // 2)

    stroke_w = max(3, int(title_lh * 0.055))     # viền sắc
    depth = max(4, int(title_lh * 0.12))          # độ dày khối 3D (px)
    title_top = y
    title_bot = y + len(title_lines) * (title_lh + gap) - gap

    def _draw_title(target, dx, dy, fill, stroke_fill=None, sw=0):
        """Vẽ toàn bộ dòng tiêu đề tại offset (dx,dy)."""
        yy = y
        for ln in title_lines:
            target.text((x + dx, yy + dy), ln, font=title_font, fill=fill,
                        stroke_width=sw,
                        stroke_fill=stroke_fill if stroke_fill else fill)
            yy += title_lh + gap

    # ---- Dựng chữ 3D theo lớp (sau → trước) ----
    # (a) Bóng đổ mềm phía dưới-phải: tách chữ khỏi nền
    shadow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    _draw_title(ImageDraw.Draw(shadow), depth + 2, depth + 5,
                (0, 0, 0, 175), sw=stroke_w)
    shadow = shadow.filter(ImageFilter.GaussianBlur(depth * 1.4))
    base = Image.alpha_composite(base, shadow)

    # (b) Quầng sáng ấm sau chữ → nổi bật, "hấp dẫn"
    glow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    _draw_title(ImageDraw.Draw(glow), 0, 0, _GLOW + (255,),
                stroke_fill=_GLOW + (255,), sw=stroke_w)
    glow = glow.filter(ImageFilter.GaussianBlur(depth * 2.0))
    base = Image.alpha_composite(base, glow)

    # (c) Thân khối 3D: vẽ chữ tối lặp lại, dịch dần từ sâu (depth) về 1
    extrude = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    edraw = ImageDraw.Draw(extrude)
    for i in range(depth, 0, -1):
        _draw_title(edraw, i, i, _EXTRUDE + (255,))
    base = Image.alpha_composite(base, extrude)

    # (d) Viền tối sắc nét ngay tại mặt chữ (nền cho gradient)
    outline = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    _draw_title(ImageDraw.Draw(outline), 0, 0, _OUTLINE + (255,),
                stroke_fill=_OUTLINE + (255,), sw=stroke_w)
    base = Image.alpha_composite(base, outline)

    # (e) Mặt chữ: tô gradient GOLD qua mask (giữ viền tối làm rìa)
    face_mask = Image.new("L", (W, H), 0)
    _draw_title(ImageDraw.Draw(face_mask), 0, 0, 255)   # glyph đặc, không viền
    grad = _vgrad_region(W, H, title_top, title_bot, _FACE_TOP, _FACE_BOTTOM)
    base.paste(grad, (0, 0), face_mask)

    # (f) Vệt sáng mép trên (bevel): mặt chữ TRỪ mặt chữ dịch xuống → dải sáng
    k = max(1, depth // 2)
    m_full = Image.new("L", (W, H), 0)
    _draw_title(ImageDraw.Draw(m_full), 0, 0, 255)
    m_down = Image.new("L", (W, H), 0)
    _draw_title(ImageDraw.Draw(m_down), 0, k, 255)
    edge = ImageChops.subtract(m_full, m_down).point(lambda v: int(v * 0.55))
    hi = Image.new("RGBA", (W, H), (255, 255, 255, 0))
    hi.putalpha(edge)
    base = Image.alpha_composite(base, hi)

    # ---- Dòng phụ: chữ trắng ngà + bóng mềm + viền tối (đọc rõ, tinh tế) ----
    if sub_lines:
        sub_sw = max(2, stroke_w // 2)
        yy0 = title_bot + sub_gap
        s_shadow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        sdr = ImageDraw.Draw(s_shadow)
        yy = yy0
        for ln in sub_lines:
            sdr.text((x + 2, yy + 3), ln, font=sub_font, fill=(0, 0, 0, 170),
                     stroke_width=sub_sw, stroke_fill=(0, 0, 0, 170))
            yy += sub_lh + gap
        s_shadow = s_shadow.filter(ImageFilter.GaussianBlur(sub_sw * 2))
        base = Image.alpha_composite(base, s_shadow)

        fdr = ImageDraw.Draw(base)
        yy = yy0
        for ln in sub_lines:
            fdr.text((x, yy), ln, font=sub_font, fill=_SUB_FACE + (255,),
                     stroke_width=sub_sw, stroke_fill=_OUTLINE + (255,))
            yy += sub_lh + gap

    return base


def render_title(
    image_path: str | Path,
    title: str,
    subtitle: str = "",
    *,
    title_fonts: Optional[list[str]] = None,
    subtitle_fonts: Optional[list[str]] = None,
    log=None,
) -> bool:
    """
    Overlay `title` (lớn) + `subtitle` (nhỏ) lên ảnh, lưu đè lại chính file.
    Bố cục: khối chữ căn TRÁI, giữa theo chiều dọc (giống thumbnail mẫu), có
    lớp tối gradient nhẹ bên trái để chữ luôn đọc rõ trên mọi nền, chữ trắng
    kèm viền tối + đổ bóng mềm. Trả True nếu vẽ được.
    """
    def _log(m: str):
        if log:
            try:
                log(m)
            except Exception:
                pass

    title = (title or "").strip()
    if not title:
        return False
    p = Path(image_path)
    if not p.exists():
        _log(f"thumbnail: không thấy ảnh {p}")
        return False

    try:
        from PIL import Image
    except Exception as e:
        _log(f"thumbnail: thiếu Pillow ({e}) — bỏ qua overlay")
        return False

    tf = title_fonts or _TITLE_FONTS
    sf = subtitle_fonts or _SUBTITLE_FONTS

    try:
        base = Image.open(p).convert("RGBA")
        out = _compose_title(base, title, subtitle or "", tf, sf)
        if out is None:
            _log("thumbnail: không nạp được font — bỏ qua overlay")
            return False
        out.convert("RGB").save(p, format="PNG")
        _log(f"thumbnail: đã overlay tiêu đề '{title}'"
             + (f" · '{subtitle}'" if subtitle.strip() else ""))
        return True
    except Exception as e:
        _log(f"thumbnail: lỗi overlay ({type(e).__name__}: {e}) — giữ ảnh gốc")
        return False


def render_title_overlay_png(
    width: int,
    height: int,
    title: str,
    subtitle: str,
    out_path: str | Path,
    *,
    title_fonts: Optional[list[str]] = None,
    subtitle_fonts: Optional[list[str]] = None,
    log=None,
) -> bool:
    """Tạo PNG TRONG SUỐT (RGBA) chỉ gồm lớp tối gradient + tiêu đề, kích thước
    width×height — để ffmpeg chồng lên clip intro. Cần thiết vì Veo tạo lại cảnh
    từ ảnh 0 nên chữ nướng sẵn trong ảnh 0 KHÔNG còn ở clip_00. Trả True nếu tạo
    được file overlay."""
    def _log(m: str):
        if log:
            try:
                log(m)
            except Exception:
                pass

    title = (title or "").strip()
    if not title or width <= 0 or height <= 0:
        return False
    try:
        from PIL import Image
    except Exception as e:
        _log(f"thumbnail: thiếu Pillow ({e}) — bỏ qua overlay intro")
        return False

    tf = title_fonts or _TITLE_FONTS
    sf = subtitle_fonts or _SUBTITLE_FONTS
    try:
        base = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        out = _compose_title(base, title, subtitle or "", tf, sf)
        if out is None:
            _log("thumbnail: không nạp được font — bỏ qua overlay intro")
            return False
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        out.save(out_path, format="PNG")          # GIỮ alpha
        return True
    except Exception as e:
        _log(f"thumbnail: lỗi tạo overlay intro ({type(e).__name__}: {e})")
        return False
