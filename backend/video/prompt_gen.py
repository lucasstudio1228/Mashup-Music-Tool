"""
video/prompt_gen.py — Dùng OpenAI GPT để biến IDEA của người dùng thành bộ
prompt ảnh liên kết cho một "phim hoạt hình ngắn" chill/relaxing.

Đầu ra: dict {"0": <thumbnail có chữ>, "1": <scene>, ... "N-1": <scene>}.
- Ảnh 0  : thumbnail 16:9 CÓ tiêu đề (title) + vài từ khoá healing.
- Ảnh 1..: các shot nối tiếp, KHÔNG chữ, giữ NGUYÊN nhân vật/bối cảnh/bảng màu.

Mọi prompt là "tự chứa" (self-contained): mỗi prompt tự mô tả lại nhân vật &
bối cảnh, vì Gemini web không nhớ ngữ cảnh giữa các lượt → nhờ vậy nhân vật
đồng bộ xuyên suốt.

An toàn: thiếu API key / lỗi mạng / JSON hỏng → trả về None để caller fallback
về DEFAULT_PROMPTS (pipeline không bao giờ vỡ vì bước này).
"""
from __future__ import annotations

import json
import re
from typing import Optional

_SYSTEM = (
    "Bạn là đạo diễn kiêm biên kịch hình ảnh cho các video hoạt hình chill, "
    "relaxing (kiểu lofi / meditation / healing). Nhiệm vụ: từ Ý TƯỞNG của "
    "người dùng, dựng một 'bộ phim hoạt hình ngắn' gồm nhiều shot liên kết, "
    "và viết prompt tạo ẢNH cho từng shot. Bạn CHỈ trả về JSON hợp lệ, không "
    "kèm giải thích, không markdown."
)

# Model dự phòng khi cấu hình lỡ để tên model của Claude cũ.
_FALLBACK_MODEL = "gpt-4o-mini"


def _build_user_prompt(idea: str, title: str, keywords: str,
                       image_count: int, aspect_ratio: str,
                       style: str) -> str:
    n = image_count
    return f"""Ý TƯỞNG NGƯỜI DÙNG: "{idea}"

Tiêu đề dự án (dùng cho thumbnail): "{title}"
Từ khoá healing gợi ý cho thumbnail: {keywords}

Hãy thiết kế một phim hoạt hình ngắn chill/relaxing gồm ĐÚNG {n} shot (đánh
số 0..{n - 1}), rồi viết prompt tạo ảnh cho từng shot.

YÊU CẦU BẮT BUỘC:
- Bám sát Ý TƯỞNG người dùng ở trên (chủ đề, không khí, bối cảnh, nhân vật nếu
  người dùng có nêu). Nếu ý tưởng còn chung chung, hãy sáng tạo hợp lý.
- Trước tiên tự dựng "hồ sơ nhân vật & bối cảnh" (character bible): mô tả CỐ
  ĐỊNH ngoại hình nhân vật chính (tóc, trang phục, phụ kiện), bối cảnh chính,
  và BẢNG MÀU. Mọi shot phải dùng LẠI y hệt các chi tiết này để đồng bộ xuyên
  suốt — coi như cùng một nhân vật, cùng một phim.
- Mỗi prompt phải TỰ CHỨA: tự mô tả lại nhân vật + bối cảnh + bảng màu (đừng
  chỉ nói 'nhân vật như trên'), để tạo ảnh độc lập vẫn giữ đúng nhân vật.
- Phong cách hình ảnh: {style}. Tỉ lệ khung hình: {aspect_ratio}. Nhịp phim
  chậm rãi, êm dịu, cozy, cinematic; ánh sáng diễn tiến mượt theo câu chuyện.
- Các shot phải NỐI TIẾP nhau như một câu chuyện có mở đầu → phát triển → lắng
  đọng (không kết thúc gấp). Đa dạng góc máy (wide, medium, close-up, over-the-
  shoulder...) nhưng cùng nhân vật/bối cảnh.
- Shot 0 là THUMBNAIL: cùng nhân vật & bối cảnh, bố cục hút mắt, BẮT BUỘC có
  typography rõ ràng đúng chính tả — tiêu đề lớn "{title}" và một dòng phụ gồm
  vài từ khoá healing. Chữ dễ đọc, tương phản tốt, không che mặt nhân vật,
  không logo, không watermark.
- Shot 1..{n - 1}: TUYỆT ĐỐI KHÔNG có chữ/typography nào trong ảnh.
- Viết prompt bằng tiếng Việt, giàu chi tiết thị giác, mỗi prompt 50–90 từ.

CHỈ trả về JSON đúng cấu trúc sau (không thêm gì khác):
{{
  "character_bible": "mô tả cố định nhân vật + bối cảnh + bảng màu",
  "prompts": {{
    "0": "prompt thumbnail (có tiêu đề + từ khoá)",
    "1": "prompt shot 1 (không chữ)",
    "...": "...",
    "{n - 1}": "prompt shot {n - 1} (không chữ)"
  }}
}}
Bắt buộc đủ {n} khoá từ "0" đến "{n - 1}"."""


def _extract_json(text: str) -> Optional[dict]:
    """Lấy object JSON đầu tiên trong text (chịu được rào ```json)."""
    if not text:
        return None
    # bỏ hàng rào code nếu có
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    raw = fenced.group(1) if fenced else None
    if raw is None:
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            return None
        raw = text[start:end + 1]
    try:
        return json.loads(raw)
    except Exception:
        return None


def generate_prompts(
    idea: str,
    title: str,
    keywords: str,
    image_count: int,
    aspect_ratio: str,
    style: str,
    api_config,
    log=None,
) -> Optional[dict[str, str]]:
    """
    Trả về dict {"0":..., ..., "N-1":...} hoặc None nếu không dùng được AI.
    `api_config` là instance APIConfig (có api_key/base_url/model).
    `log` (tùy chọn): callable(msg) để ghi log tiến trình.
    """
    def _log(m: str):
        if log:
            try:
                log(m)
            except Exception:
                pass

    idea = (idea or "").strip()
    if not idea:
        _log("Không có ý tưởng — dùng prompt mặc định.")
        return None
    if not api_config or not getattr(api_config, "api_key", None):
        _log("Chưa cấu hình OPENAI_API_KEY — dùng prompt mặc định.")
        return None

    try:
        from openai import OpenAI
    except Exception as e:  # pragma: no cover
        _log(f"Không import được openai ({e}) — dùng prompt mặc định.")
        return None

    # Nếu model còn để tên Claude cũ thì đổi sang GPT mặc định.
    model = api_config.model
    if not model or model.lower().startswith("claude"):
        model = _FALLBACK_MODEL

    try:
        client = OpenAI(**api_config.to_client_kwargs())
        user_msg = _build_user_prompt(idea, title, keywords, image_count,
                                      aspect_ratio, style)
        resp = client.chat.completions.create(
            model=model,
            max_tokens=8000,
            messages=[
                {"role": "system", "content": _SYSTEM},
                {"role": "user", "content": user_msg},
            ],
        )
        text = resp.choices[0].message.content or ""
    except Exception as e:
        _log(f"Gọi OpenAI thất bại ({type(e).__name__}: {e}) — dùng prompt mặc định.")
        return None

    data = _extract_json(text)
    if not isinstance(data, dict):
        _log("OpenAI trả về không phải JSON hợp lệ — dùng prompt mặc định.")
        return None

    prompts = data.get("prompts")
    if not isinstance(prompts, dict):
        _log("JSON thiếu 'prompts' — dùng prompt mặc định.")
        return None

    # Chuẩn hoá key về str và kiểm tra đủ 0..N-1
    out: dict[str, str] = {}
    for i in range(image_count):
        val = prompts.get(str(i)) or prompts.get(i)
        if not isinstance(val, str) or not val.strip():
            _log(f"Thiếu prompt cho ảnh {i} — dùng prompt mặc định.")
            return None
        out[str(i)] = val.strip()

    _log(f"Đã sinh {image_count} prompt từ ý tưởng bằng OpenAI ({model}).")
    return out
