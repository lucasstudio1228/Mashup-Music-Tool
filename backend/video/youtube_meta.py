"""
video/youtube_meta.py — Sinh title / description / hashtags cho video bằng AI
(GPT-4o vision), ĐỌC thumbnail để mô tả đúng nội dung.

Yêu cầu của người dùng:
  • Ngôn ngữ: Anh–Mỹ (US English).
  • AI phải "có bộ nhớ": tránh trùng lặp với các title đã đăng trước của cùng
    kênh (past_titles) → mỗi video một title/description KHÁC nhau.
  • Description phải "soi tên kênh" → nhắc channel_name.
  • Nếu thiếu API key / không đọc được ảnh → vẫn trả metadata mặc định hợp lý
    (dựa trên tên kênh + nhạc cụ + phong cách) để pipeline không gãy.
"""
from __future__ import annotations

import base64
import json
import mimetypes
from pathlib import Path
from typing import Optional

_FALLBACK_MODEL = "gpt-4o-mini"     # hỗ trợ vision + JSON mode


def _log_fn(log):
    def _l(m: str):
        if log:
            try:
                log(m)
            except Exception:
                pass
    return _l


def _data_uri(image_path: str) -> Optional[str]:
    p = Path(image_path)
    if not p.exists():
        return None
    mime = mimetypes.guess_type(str(p))[0] or "image/png"
    try:
        b64 = base64.b64encode(p.read_bytes()).decode("ascii")
    except Exception:
        return None
    return f"data:{mime};base64,{b64}"


def _fallback_meta(channel_name: str, instrument: str, music_style: str,
                   default_hashtags: str) -> dict:
    ch = (channel_name or "our channel").strip()
    instr = (instrument or "relaxing").strip()
    style = (music_style or "healing").strip()
    title = f"Peaceful {instr.title()} Music for {style.title()} & Relaxation"
    description = (
        f"Relax and unwind with soothing {instr} music from {ch}. "
        f"This {style} soundscape is perfect for meditation, deep sleep, "
        f"stress relief, studying and calming the mind.\n\n"
        f"Thank you for listening to {ch}. Subscribe for more healing music.\n\n"
        f"{default_hashtags}".strip()
    )
    return {
        "title": title[:100],
        "description": description,
        "hashtags": (default_hashtags or "#relaxingmusic #healing #meditation"),
    }


def generate_metadata(
    thumbnail_path: str,
    channel_name: str,
    instrument: str,
    music_style: str,
    default_hashtags: str,
    past_titles: list[str],
    api_config,
    log=None,
) -> dict:
    """Trả {'title','description','hashtags'} (US English). Không raise —
    lỗi/khuyết cấu hình → dùng _fallback_meta."""
    _log = _log_fn(log)
    fallback = _fallback_meta(channel_name, instrument, music_style,
                              default_hashtags)

    if not api_config or not getattr(api_config, "api_key", None):
        _log("YT-meta: chưa có API key — dùng metadata mặc định.")
        return fallback

    try:
        from openai import OpenAI
    except Exception as e:
        _log(f"YT-meta: không import được openai ({e}) — dùng mặc định.")
        return fallback

    model = getattr(api_config, "model", None) or _FALLBACK_MODEL
    if model.lower().startswith("claude"):
        model = _FALLBACK_MODEL

    try:
        client = OpenAI(**api_config.to_client_kwargs())
    except Exception as e:
        _log(f"YT-meta: không tạo được client ({e}) — dùng mặc định.")
        return fallback

    recent = "\n".join(f"- {t}" for t in (past_titles or [])[:20]) or "(none yet)"
    sys_msg = (
        "You are a YouTube growth copywriter for a relaxing / healing music "
        "channel. You write in natural US English. You return STRICT JSON only."
    )
    user_text = (
        "Write metadata for a new long-form relaxing music video.\n"
        f"Channel name: {channel_name or '(unknown)'}\n"
        f"Instrument: {instrument or '(unspecified)'}\n"
        f"Music style / mood: {music_style or '(unspecified)'}\n"
        f"Default hashtags to include: {default_hashtags or '(none)'}\n\n"
        "Look at the attached thumbnail image and describe the actual scene "
        "(setting, mood, colors) in the description.\n\n"
        "STRICT REQUIREMENTS:\n"
        "1. Language: US English.\n"
        "2. title: catchy, <= 100 characters, evokes calm/healing/sleep/focus. "
        "Must be DIFFERENT from every recent title below (no duplicates, vary "
        "wording and angle).\n"
        "3. description: 3-5 short paragraphs. Mention the channel name "
        f"\"{channel_name}\" naturally at least once. Describe the thumbnail "
        "scene. Suggest uses (sleep, study, meditation, spa, stress relief). "
        "End with a subscribe call-to-action and then the hashtags on the last "
        "line.\n"
        "4. hashtags: a single string of 8-15 space-separated #tags relevant to "
        "the instrument, style and relaxation niche; include the default "
        "hashtags. Lowercase, no spaces inside a tag.\n\n"
        "Recent titles already used on this channel (AVOID repeating these):\n"
        f"{recent}\n\n"
        "Return JSON with EXACTLY these keys: "
        "{\"title\": str, \"description\": str, \"hashtags\": str}"
    )

    content: list[dict] = [{"type": "text", "text": user_text}]
    uri = _data_uri(thumbnail_path)
    if uri:
        content.append({"type": "image_url", "image_url": {"url": uri}})
    else:
        _log("YT-meta: không đọc được thumbnail — viết mô tả không ảnh.")

    try:
        resp = client.chat.completions.create(
            model=model,
            max_tokens=1500,
            temperature=0.9,          # đa dạng, tránh trùng lặp
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": sys_msg},
                {"role": "user", "content": content},
            ],
        )
        text = resp.choices[0].message.content or ""
        data = json.loads(text)
    except Exception as e:
        _log(f"YT-meta: gọi AI thất bại ({type(e).__name__}: {e}) — dùng mặc định.")
        return fallback

    title = str(data.get("title") or "").strip()[:100]
    description = str(data.get("description") or "").strip()
    hashtags = str(data.get("hashtags") or "").strip()
    if not title or not description:
        _log("YT-meta: AI trả thiếu title/description — dùng mặc định.")
        return fallback
    if not hashtags:
        hashtags = default_hashtags or "#relaxingmusic #healing #meditation"
    # Đảm bảo hashtag mặc định luôn có mặt.
    if default_hashtags:
        for tag in default_hashtags.split():
            if tag and tag.lower() not in hashtags.lower():
                hashtags = f"{hashtags} {tag}".strip()
    return {"title": title, "description": description, "hashtags": hashtags}
