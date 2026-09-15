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

def _system_for(style_brief: str) -> str:
    return (
        "Bạn là đạo diễn hình ảnh cấp cao (senior visual director) chuyên PHIM "
        f"HOẠT HÌNH ({style_brief}) chill/relaxing/meditation/lofi. Bạn có tư "
        "duy điện ảnh (cinematic thinking): mỗi shot là một KHUNG HÌNH PHIM có "
        "MỤC ĐÍCH kể chuyện — không phải miêu tả khô khan. Bạn đồng thời là đạo "
        "diễn CHUYỂN ĐỘNG: với mỗi ảnh, hình dung nó sẽ được làm động thành clip "
        "8s và viết prompt chuyển động hợp logic vật lý, tinh tế, thiền. "
        "NGUYÊN TẮC TỐI THƯỢNG 1: mọi shot BẮT BUỘC đúng phong cách "
        f"'{style_brief}' — TUYỆT ĐỐI KHÔNG photorealistic, KHÔNG ảnh chụp "
        "thật, KHÔNG người/da/vải/tóc chân thực như đời thật, KHÔNG "
        "hyperrealism. Khi tả nhân vật/vật liệu, luôn tả theo NGÔN NGỮ của "
        "phong cách hoạt hình đã chọn, không tả như người/vật thật. "
        "NGUYÊN TẮC TỐI THƯỢNG 2: phải BÁM SÁT ý tưởng người dùng (đúng nhân "
        "vật, đạo cụ, bối cảnh, hành động); TUYỆT ĐỐI không thay bằng nhân "
        "vật/bối cảnh mặc định nào khác. Bạn CHỈ trả về JSON hợp lệ, không kèm "
        "giải thích, không markdown."
    )

# Model dự phòng khi cấu hình lỡ để tên model của Claude cũ.
_FALLBACK_MODEL = "gpt-4o-mini"

# Model đôi khi trả JSON hỏng/thiếu key MỘT CÁCH NGẪU NHIÊN (1 lần hỏng KHÔNG
# nghĩa là API/model sai). Thử lại vài lần trước khi bỏ cuộc để pipeline không bị
# dừng oan với "AI chưa viết được prompt".
_MAX_ATTEMPTS = 3


def _act_ranges(n: int) -> str:
    """Chia shot cảnh (1..n-1) thành 4 hồi theo tỉ lệ, mô tả ĐỘNG theo n.
    Giữ đúng tỉ lệ của bản 20 shot cũ (intro ~21%, hành trình ~42%, cao trào
    ~26%, outro phần còn lại) để phim dài (41 shot) vẫn có nhịp hợp lý."""
    m = n - 1                                   # số shot cảnh (bỏ shot 0)
    if m < 4:
        return f"toàn bộ shot 1–{n - 1} kể liền một mạch"
    e1 = max(1, round(m * 0.21))                # kết hồi 1
    e2 = max(e1 + 1, round(m * 0.63))           # kết hồi 2
    e3 = max(e2 + 1, round(m * 0.89))           # kết hồi 3
    e3 = min(e3, m - 1)                          # chừa ít nhất 1 shot cho outro
    return (f"GIỚI THIỆU (shot 1–{e1}) → HÀNH TRÌNH / PHÁT TRIỂN "
            f"({e1 + 1}–{e2}) → CAO TRÀO / ĐÍCH ĐẾN ({e2 + 1}–{e3}) → "
            f"LẮNG ĐỌNG / OUTRO ({e3 + 1}–{n - 1})")


def _build_user_prompt(idea: str, title: str, keywords: str,
                       image_count: int, aspect_ratio: str,
                       style: str) -> str:
    n = image_count
    return f"""Ý TƯỞNG NGƯỜI DÙNG: "{idea}"

Tiêu đề dự án (dùng cho thumbnail): "{title}"
Từ khoá healing gợi ý cho thumbnail: {keywords}

═══ NHIỆM VỤ ═══
Thiết kế một "phim hoạt hình ngắn" chill/relaxing gồm ĐÚNG {n} shot (0..{n - 1}).
Mỗi shot = 1 prompt tạo ảnh CỰC KÌ CHI TIẾT.

═══ NGUYÊN TẮC BẮT BUỘC ═══

1. BÁM SÁT Ý TƯỞNG TUYỆT ĐỐI
   Đúng nhân vật (giới tính, tuổi, dân tộc, trang phục, đạo cụ, ngoại hình), đúng
   bối cảnh (địa điểm, thời tiết, thiên nhiên), đúng hành trình/diễn tiến mà
   ý tưởng mô tả. VÍ DỤ: "cậu bé đội nón lá cầm sáo trúc trèo đèo lội suối"
   → nhân vật PHẢI là cậu bé + nón lá + sáo trúc, các shot PHẢI đi theo hành
   trình suối → đèo → đỉnh.
   NGHIÊM CẤM bịa nhân vật/bối cảnh không có trong ý tưởng (đặc biệt KHÔNG được
   mặc định thành "người trẻ hoodie sage + cabin gỗ + mèo tam thể" — đó là mẫu cũ).

2. CHARACTER BIBLE CỐ ĐỊNH (ghi vào trường "character_bible")
   Trước khi viết bất kì prompt nào, bạn PHẢI khoá hồ sơ nhân vật chính:
   • Ngoại hình CỤ THỂ: tuổi, tóc (kiểu/dài/ngắn/màu), mắt (màu/hình dáng),
     da, vóc dáng, biểu cảm mặc định
   • Trang phục CỐ ĐỊNH: mô tả TỪNG MÓN (chất liệu, màu chính xác, hoạ tiết nếu
     có, cách mặc/đội/cầm), phụ kiện + đạo cụ
   • Bối cảnh & hành trình: các mốc chính của câu chuyện (mở – diễn biến – cao
     trào – lắng)
   • BẢNG MÀU CHÍNH: 5–7 màu chủ đạo (ghi tên cụ thể, VD "xanh ngọc bích #6EC4A7"
     thay vì chỉ "xanh"), kèm vai trò (màu chính nhân vật / màu nền / màu accent)
   Mọi shot phải dùng CHÍNH XÁC thông tin này — một nhân vật, một phim.

3. MỖI PROMPT PHẢI TỰ CHỨA + CỰC KÌ CHI TIẾT (90–140 từ)
   Mỗi prompt PHẢI tự lặp lại đầy đủ ngoại hình nhân vật + trang phục + đạo cụ
   (KHÔNG viết "nhân vật như trên"). Đồng thời PHẢI bao gồm TẤT CẢ các lớp sau:
   a) GÓC MÁY & BỐ CỤC: loại shot (extreme wide / wide / medium / close-up /
      extreme close-up / over-the-shoulder / low-angle / bird's-eye / dutch angle),
      vị trí nhân vật trong khung (rule of thirds, centered, silhouette...)
   b) ÁNH SÁNG CỤ THỂ: nguồn sáng (golden hour / rim light / volumetric fog /
      dappled sunlight xuyên tán lá / backlit / soft diffused...), hướng chiếu
      (trước/sau/bên), nhiệt độ màu (ấm/lạnh), shadow (mềm/sắc/dài), highlight
   c) ATMOSPHERE & PARTICLES: sương mù (độ dày, vị trí), bụi phấn, tia nắng,
      hạt mưa, đom đóm, phấn hoa, tuyết... — tuỳ bối cảnh
   d) TEXTURE KIỂU HOẠT HÌNH: tả chất liệu theo ĐÚNG phong cách "{style}", KHÔNG
      phải ảnh thật — mô tả bề mặt/vải/đá/nước/lá bằng ngôn ngữ tranh hoạt hình
      của phong cách đó (nét vẽ/tô màu phẳng nếu 2D; khối cách điệu đổ bóng dịu
      nếu 3D). TRÁNH mọi từ gợi ảnh thật (photoreal texture, lỗ chân lông, sợi
      vải thật, 4K photograph...)
   e) FOREGROUND / MIDGROUND / BACKGROUND: 3 lớp riêng biệt (VD: foreground =
      cành hoa mờ bokeh, midground = nhân vật, background = núi xa mây cuộn)
   f) CẢM XÚC & HÀNH ĐỘNG: biểu cảm cụ thể của nhân vật (mắt lim dim, miệng hé
      mỉm cười, trán hơi nhíu...), tư thế cơ thể, hành động chính
   g) ÂM THANH THỊ GIÁC: gợi ý thính giác qua hình ảnh (gợn sóng = tiếng suối,
      lá rung = gió thổi, sáo đưa lên môi = giai điệu...)

4. DIỄN TIẾN CÓ LOGIC & TƯ DUY ĐIỆN ẢNH
   - Câu chuyện phải có cấu trúc 4 hồi: {_act_ranges(n)}
   - Ánh sáng DIỄN TIẾN theo thời gian thực: sớm tinh mơ → bình minh → nắng sáng
     → trưa → chiều vàng → hoàng hôn (hoặc ngược lại) — phải LIÊN TỤC, không nhảy
   - Góc máy XOAY luân phiên: KHÔNG lặp cùng loại 2 shot liền (VD wide → wide).
     Mỗi cặp shot kề nhau phải khác góc
   - Nhịp kể chuyện tăng dần: shot đầu chậm rãi → giữa phim có chuyển động →
     cao trào lắng lại, ending peace
   - Mỗi shot phải LIÊN KẾT với shot trước & sau (cùng bối cảnh hoặc chuyển cảnh
     mượt — VD shot 6 kết ở bìa rừng → shot 7 mở ở sâu trong rừng)

5. PHONG CÁCH & QUY TẮC HÌNH ẢNH
   - PHONG CÁCH BẮT BUỘC (nhắc lại trong TỪNG prompt, đặt ở ĐẦU mỗi prompt):
     {style}. Đây là ẢNH HOẠT HÌNH, KHÔNG phải ảnh chụp. TUYỆT ĐỐI KHÔNG
     photorealistic / photo thật.
   - Bảng màu ghi hex là màu chủ đạo tô theo phong cách hoạt hình đã chọn —
     KHÔNG phải chi tiết chân thực như đời thật.
   - Tỉ lệ: {aspect_ratio}
   - Shot 0 = THUMBNAIL: cùng nhân vật, bố cục hút mắt, giàu cảm xúc. TUYỆT ĐỐI KHÔNG vẽ chữ/tiêu đề/typography/logo/watermark trong ảnh (tiêu đề "{title}"
     sẽ được chèn bằng phần mềm sau — model sinh ảnh không đánh vần được nên
     KHÔNG được tự vẽ chữ). Đặt nhân vật lệch sang PHẢI và CHỪA khoảng trống
     thoáng (negative space, nền dịu ít chi tiết) ở NỬA TRÁI khung để chèn tiêu
     đề — tránh chi tiết rối ở vùng đó.
   - Shot 1..{n - 1}: TUYỆT ĐỐI KHÔNG chữ/typography trong ảnh
   - Viết prompt bằng tiếng Việt, giàu chi tiết thị giác

6. PROMPT CHUYỂN ĐỘNG (MOTION) CHO TỪNG CLIP — CỰC KÌ QUAN TRỌNG & CHẶT CHẼ
   Mỗi ảnh k sẽ được model video (Veo, image-to-video) làm động thành 1 clip
   8 GIÂY. Bạn PHẢI viết cho MỖI ảnh k một prompt chuyển động ("motions"[k])
   50–100 từ, RIÊNG cho nội dung ảnh đó. Đây là phần HAY BỊ LỖI NHẤT (model
   video rất dễ "ảo giác": bốc khói từ sáo, đầu quay ngược, tay méo mó) nên
   phải viết CỰC KÌ CHÍNH XÁC, RÀNG BUỘC VẬT LÝ và CHỦ ĐỘNG CẤM các lỗi đó.

   a) BÁM ĐÚNG KHUNG HÌNH: mở đầu motion bằng 1 mệnh đề khoá bối cảnh — nêu lại
      NGẮN GỌN nhân vật đang làm gì trong ảnh (VD "cậu bé ngồi thổi sáo trên
      mỏm đá"), để model KHÔNG tự bịa hành động mới.
   b) HỢP LOGIC & THỰC TẾ VẬT LÝ (bắt buộc): chỉ mô tả chuyển động CÓ THỂ xảy ra
      tự nhiên, đúng giải phẫu, trong đúng khung hình đó. Nhân vật giữ NGUYÊN
      danh tính, khuôn mặt, trang phục, đạo cụ và vị trí. Đầu chỉ nghiêng/quay
      RẤT NHẸ trong biên độ tự nhiên (KHÔNG quay ngược/180°). Bàn tay & ngón tay
      giữ đúng số lượng và hình dạng. KHÔNG biến hình, KHÔNG xuất hiện/biến mất
      người-vật, KHÔNG đổi bối cảnh, KHÔNG cắt cảnh, KHÔNG chữ; camera KHÔNG
      xuyên qua vật thể.
   c) 3 TẦNG CHUYỂN ĐỘNG cụ thể: (1) VI CHUYỂN ĐỘNG nhân vật hợp hành động (VD
      đang thổi sáo: các ngón tay bấm lỗ sáo nhấp nhẹ theo nhịp, vai và lồng
      ngực nhô lên hạ xuống RẤT KHẼ và TỰ NHIÊN — KHÔNG có hơi/khói thoát ra,
      mi mắt khẽ chớp, vài sợi tóc mai và vạt áo lay theo gió); (2) MÔI TRƯỜNG
      (lá đung đưa, sương/mây trôi ngang chậm, mặt nước gợn lăn tăn, nắng lung
      linh, bụi phấn/đom đóm bay chậm); (3) CAMERA duy nhất MỘT động tác chậm
      mượt (push-in rất chậm / lia trái-phải nhẹ / tilt-up / dolly lùi /
      parallax nhẹ) — nêu RÕ hướng & tốc độ "rất chậm".
   d) CHỦ ĐỘNG CẤM (viết luôn 1 cụm phủ định NGẮN ở cuối mỗi motion): không khói
      /hơi/lửa từ miệng hay nhạc cụ, không đầu quay ngược, không méo tay/mặt,
      không thừa ngón/chi, không biến hình, không nhân bản nhân vật, không cắt
      cảnh (no smoke, no vapor, no fire, no head spinning, no distorted hands).
   e) NHỊP & KHÔNG KHÍ: chậm, thiền, thư giãn, liền mạch, lặp được (loop-friendly),
      không giật cục, không chuyển động mạnh/đột ngột. Ăn khớp thời điểm trong
      ngày & cảm xúc của ảnh (VD hoàng hôn → chuyển động lắng, gió nhẹ hơn).
   f) TÔNG: mô tả bằng tiếng Việt, thêm vài từ khoá tiếng Anh cho Veo khi cần
      (slow motion, subtle, cinematic, seamless loop, anatomically correct).
      GẮN đúng phong cách "{style}" (giữ nét hoạt hình, không làm thật hoá).
   Motion[0] (thumbnail) cũng cần 1 chuyển động nền tinh tế (ambient) hợp cảnh,
   tuân thủ đủ các quy tắc trên.

═══ OUTPUT FORMAT ═══
CHỈ trả về JSON (không giải thích, không markdown):
{{
  "character_bible": "hồ sơ nhân vật cố định (ngoại hình + trang phục + đạo cụ + bảng màu 5-7 màu + bối cảnh + hành trình)",
  "prompts": {{
    "0": "prompt thumbnail 90-140 từ, KHÔNG chữ, chừa negative space nửa trái",
    "1": "prompt shot 1 (90-140 từ, không chữ, tự chứa, đủ 7 lớp chi tiết)",
    "...": "...",
    "{n - 1}": "prompt shot cuối (90-140 từ, không chữ)"
  }},
  "motions": {{
    "0": "prompt chuyển động clip từ ảnh 0 (50-100 từ, hợp logic vật lý, 3 tầng + cụm phủ định cuối)",
    "1": "prompt chuyển động clip từ ảnh 1 (50-100 từ)",
    "...": "...",
    "{n - 1}": "prompt chuyển động clip cuối (50-100 từ)"
  }}
}}
Bắt buộc: "prompts" VÀ "motions" đều đủ {n} khoá "0".."{n - 1}". Mỗi prompt
ảnh 90–140 từ; mỗi prompt motion 50–100 từ, hợp logic vật lý, có cụm phủ định."""


def _motion_system(style_brief: str) -> str:
    return (
        "Bạn là ĐẠO DIỄN CHUYỂN ĐỘNG cấp cao cho phim hoạt hình "
        f"({style_brief}) chill/thiền. Nhiệm vụ: với MỖI ảnh tĩnh cho sẵn, viết "
        "một prompt chuyển động (image-to-video, clip 8 giây) để model video "
        "(Veo) làm động ảnh đó. Đây là khâu DỄ LỖI NHẤT: model video hay 'ảo "
        "giác' (bốc khói/hơi từ sáo hay miệng, đầu quay ngược, tay/ngón méo mó, "
        "thừa chi, biến hình). Vì vậy prompt của bạn phải CỰC KÌ CHÍNH XÁC, "
        "RÀNG BUỘC VẬT LÝ, và CHỦ ĐỘNG CẤM các lỗi đó. CHỈ trả về JSON hợp lệ."
    )


def _build_motions_user_prompt(idea: str, style: str,
                               prompts: dict[str, str]) -> str:
    n = len(prompts)
    lines = []
    for i in range(n):
        p = (prompts.get(str(i)) or "").strip()
        lines.append(f'ẢNH {i}: "{p}"')
    joined = "\n\n".join(lines)
    return f"""Ý TƯỞNG TỔNG THỂ: "{idea}"
Phong cách: {style}

Dưới đây là {n} ẢNH TĨNH (đã tạo xong) của cùng một phim. Với MỖI ảnh, viết một
prompt CHUYỂN ĐỘNG 50–100 từ để làm động ĐÚNG ảnh đó thành clip 8 giây.

QUY TẮC BẮT BUỘC cho mỗi motion:
1. BÁM ĐÚNG ẢNH: mở đầu bằng mệnh đề khoá bối cảnh nêu lại NGẮN GỌN nhân vật
   đang làm gì trong ảnh đó (để model không bịa hành động mới). Chuyển động phải
   khớp CHÍNH XÁC nội dung/không gian của ảnh.
2. HỢP LOGIC & ĐÚNG GIẢI PHẪU: chỉ chuyển động tự nhiên, khả thi. Nhân vật giữ
   nguyên danh tính, mặt, trang phục, đạo cụ, vị trí. Đầu chỉ nghiêng/quay RẤT
   NHẸ trong biên độ tự nhiên (KHÔNG quay ngược/180°). Tay & ngón giữ đúng số
   lượng, hình dạng. KHÔNG biến hình, KHÔNG thêm/bớt người-vật, KHÔNG đổi bối
   cảnh, KHÔNG cắt cảnh, camera KHÔNG xuyên vật thể.
3. 3 TẦNG: (a) vi-chuyển-động nhân vật hợp hành động (VD thổi sáo: ngón tay bấm
   lỗ sáo nhấp nhẹ theo nhịp, vai & lồng ngực nhô lên hạ xuống RẤT KHẼ TỰ NHIÊN
   — KHÔNG có hơi/khói thoát ra, mi mắt khẽ chớp, vài sợi tóc & vạt áo lay theo
   gió); (b) môi trường (lá đung đưa, sương/mây trôi ngang chậm, nước gợn, nắng
   lung linh, đom đóm/bụi phấn bay chậm); (c) DUY NHẤT một động tác camera chậm
   mượt (push-in rất chậm / lia trái-phải nhẹ / tilt-up / dolly lùi / parallax
   nhẹ) — nêu rõ hướng & "rất chậm".
4. NHỊP thiền, chậm, mượt, liền mạch, lặp được (seamless loop); không giật cục,
   không đột ngột. Ăn khớp thời điểm trong ngày & cảm xúc của ảnh.
5. KẾT MỖI MOTION bằng 1 cụm phủ định ngắn: "no smoke, no vapor, no fire, no
   breath vapor, no smoke from flute or mouth, no head spinning, no reversed
   head, no distorted or extra fingers, no morphing, no scene cut."
6. Viết tiếng Việt, thêm vài từ khoá tiếng Anh cho Veo (slow motion, subtle,
   cinematic, seamless loop, anatomically correct). Giữ đúng nét hoạt hình
   "{style}", KHÔNG làm thật hoá.

CÁC ẢNH:
{joined}

═══ OUTPUT ═══ CHỈ trả JSON (không giải thích, không markdown):
{{
  "motions": {{
    "0": "motion cho ảnh 0 (50-100 từ)",
    "...": "...",
    "{n - 1}": "motion cho ảnh {n - 1} (50-100 từ)"
  }}
}}
Bắt buộc đủ {n} khoá "0".."{n - 1}"."""


def generate_motions_for_prompts(
    prompts: dict[str, str],
    idea: str,
    style: str,
    api_config,
    log=None,
) -> Optional[dict[str, str]]:
    """Sinh LẠI bộ motion (khớp với các ảnh ĐÃ có) từ chính prompt ảnh hiện tại
    — dùng khi muốn cải thiện chuyển động mà KHÔNG tạo lại ảnh. Trả dict motions
    đủ N khoá, hoặc None nếu lỗi/không đủ."""
    def _log(m: str):
        if log:
            try:
                log(m)
            except Exception:
                pass

    if not prompts:
        return None
    if not api_config or not getattr(api_config, "api_key", None):
        _log("Chưa cấu hình API key — không sinh lại motion được.")
        return None
    try:
        from openai import OpenAI
    except Exception as e:  # pragma: no cover
        _log(f"Không import được openai ({e}).")
        return None

    model = api_config.model
    if not model or model.lower().startswith("claude"):
        model = _FALLBACK_MODEL
    n = len(prompts)
    try:
        client = OpenAI(**api_config.to_client_kwargs())
        resp = client.chat.completions.create(
            model=model,
            max_tokens=16000,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": _motion_system(style)},
                {"role": "user",
                 "content": _build_motions_user_prompt(idea, style, prompts)},
            ],
        )
        text = resp.choices[0].message.content or ""
    except Exception as e:
        _log(f"Gọi OpenAI (motion) thất bại ({type(e).__name__}: {e}).")
        return None

    data = _extract_json(text)
    if not isinstance(data, dict):
        _log("OpenAI (motion) trả về không phải JSON.")
        return None
    raw = data.get("motions")
    if not isinstance(raw, dict):
        _log("JSON (motion) thiếu 'motions'.")
        return None
    out: dict[str, str] = {}
    for i in range(n):
        mv = raw.get(str(i)) or raw.get(i)
        if not isinstance(mv, str) or not mv.strip():
            _log(f"Thiếu motion cho ảnh {i}.")
            return None
        out[str(i)] = mv.strip()
    _log(f"Đã sinh lại {n} prompt chuyển động khớp ảnh hiện tại ({model}).")
    return out


def _extract_json(text: str) -> Optional[dict]:
    """Lấy object JSON đầu tiên trong text (chịu được rào ```json)."""
    if not text:
        return None
    # bỏ hàng rào code nếu có
    # Greedy \{.*\}: JSON của ta có object lồng nhau → non-greedy (.*?) sẽ dừng ở
    # dấu } ĐẦU TIÊN và cắt cụt object → JSON hỏng.
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL)
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
) -> Optional[tuple[dict[str, str], dict[str, str]]]:
    """
    Trả về (prompts, motions) — mỗi cái là dict {"0":..., ..., "N-1":...} —
    hoặc None nếu không dùng được AI. `prompts` là prompt ảnh (bắt buộc đủ N);
    `motions` là prompt chuyển động clip (best-effort, có thể rỗng nếu AI thiếu).
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
    except Exception as e:
        _log(f"Không khởi tạo được client OpenAI ({type(e).__name__}: {e}) — "
             f"dùng prompt mặc định.")
        return None
    user_msg = _build_user_prompt(idea, title, keywords, image_count,
                                  aspect_ratio, style)
    sys_msg = _system_for(style)

    # Thử lại _MAX_ATTEMPTS lần: model thỉnh thoảng trả JSON hỏng/cắt cụt/thiếu
    # key một cách ngẫu nhiên — 1 lần hỏng không phải lỗi cấu hình. Chỉ trả None
    # (→ RuntimeError ở service) sau khi đã thử hết.
    last_reason = "không rõ"
    for attempt in range(1, _MAX_ATTEMPTS + 1):
        try:
            resp = client.chat.completions.create(
                model=model,
                # Đủ rộng cho 41 prompt ảnh chi tiết (90-140 từ tiếng Việt) +
                # 41 prompt chuyển động + character_bible; tránh JSON bị cắt cụt
                # (finish_reason=length) → thiếu key. Cần model có giới hạn
                # output lớn (vd gpt-4.1: 32768) khi tạo đủ 41 shot.
                max_tokens=32000,
                # BẮT model trả JSON hợp lệ (không prose/markdown). Không có cờ
                # này, gpt-4.1 qua proxy THỈNH THOẢNG trả kèm văn xuôi → parse
                # hỏng "không phải JSON hợp lệ" ngẫu nhiên (gặp cả khi count nhỏ).
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": sys_msg},
                    {"role": "user", "content": user_msg},
                ],
            )
            text = resp.choices[0].message.content or ""
            finish = getattr(resp.choices[0], "finish_reason", None)
        except Exception as e:
            last_reason = f"gọi OpenAI thất bại ({type(e).__name__}: {e})"
            _log(f"[{attempt}/{_MAX_ATTEMPTS}] {last_reason} — thử lại…")
            continue

        if finish == "length":
            last_reason = "JSON bị cắt cụt (finish_reason=length)"
            _log(f"[{attempt}/{_MAX_ATTEMPTS}] {last_reason} — thử lại…")
            continue

        data = _extract_json(text)
        if not isinstance(data, dict):
            last_reason = "trả về không phải JSON hợp lệ"
            _log(f"[{attempt}/{_MAX_ATTEMPTS}] {last_reason} — thử lại…")
            continue

        prompts = data.get("prompts")
        if not isinstance(prompts, dict):
            last_reason = "JSON thiếu 'prompts'"
            _log(f"[{attempt}/{_MAX_ATTEMPTS}] {last_reason} — thử lại…")
            continue

        # Chuẩn hoá key về str và kiểm tra đủ 0..N-1
        out: dict[str, str] = {}
        missing = None
        for i in range(image_count):
            val = prompts.get(str(i)) or prompts.get(i)
            if not isinstance(val, str) or not val.strip():
                missing = i
                break
            out[str(i)] = val.strip()
        if missing is not None:
            last_reason = (f"thiếu prompt cho ảnh {missing} "
                           f"({len(out)}/{image_count})")
            _log(f"[{attempt}/{_MAX_ATTEMPTS}] {last_reason} — thử lại…")
            continue

        # Motion prompts (best-effort): thiếu/không đủ → để trống, flow_driver
        # dùng câu lệnh chuyển động mặc định. KHÔNG làm hỏng bước ảnh vì thiếu.
        motions_raw = data.get("motions")
        motions: dict[str, str] = {}
        if isinstance(motions_raw, dict):
            for i in range(image_count):
                mv = motions_raw.get(str(i)) or motions_raw.get(i)
                if isinstance(mv, str) and mv.strip():
                    motions[str(i)] = mv.strip()
        if len(motions) == image_count:
            _log(f"Đã sinh {image_count} prompt ảnh + {len(motions)} prompt "
                 f"chuyển động từ ý tưởng bằng OpenAI ({model}, lần {attempt}).")
        else:
            _log(f"Đã sinh {image_count} prompt ảnh ({model}, lần {attempt}); "
                 f"motion {len(motions)}/{image_count} — phần thiếu dùng mặc định.")
        return out, motions

    _log(f"AI không sinh được prompt sau {_MAX_ATTEMPTS} lần thử "
         f"(lý do cuối: {last_reason}) — dùng prompt mặc định.")
    return None
