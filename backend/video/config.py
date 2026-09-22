"""
video/config.py — Toàn bộ tham số & selector cho module Video, gom 1 chỗ.

⚠️  QUAN TRỌNG: Các SELECTOR của ChatGPT và Flow là "best-effort" — 2 site này
đổi giao diện thường xuyên. Khi automation lỗi, sửa selector Ở ĐÂY (không phải
rải rác trong driver). Có thể override qua file JSON video_overrides.json nếu có.
"""
from __future__ import annotations
import json
import re
from dataclasses import dataclass, field, asdict
from pathlib import Path

# ── Thư mục ─────────────────────────────────────────────────────
_ROOT       = Path(__file__).parent.parent.parent          # meditation_mixer/
MEDIA_ROOT  = _ROOT / "media"                              # media/<project_name>/...
PROFILE_DIR = _ROOT / ".browser_profile"                  # Chrome profile (giữ login)
OVERRIDES   = _ROOT / "data" / "video_overrides.json"     # override selector (tùy chọn)

STEM_IMAGE  = "images"    # media/<project>/images/0.png..40.png
STEM_CLIP   = "clips"     # media/<pid>/clips/clip_00.mp4..
STEM_FINAL  = "final"     # media/<pid>/final/final.mp4
STEM_UPLOAD = "uploads"   # media/<pid>/uploads/<file> — nhạc nền upload cho video

# Đuôi file audio chấp nhận cho nhạc nền video (ffmpeg đọc trực tiếp được).
AUDIO_EXTS = (".wav", ".flac", ".mp3", ".m4a", ".aac", ".ogg", ".opus", ".wma")


def project_folder_name(project_name: str | None, project_id: int) -> str:
    """
    Tên thư mục media: tên project + id để VỪA dễ đọc VỪA ỔN ĐỊNH.
    Kèm '#<id>' để đổi tên project KHÔNG trỏ nhầm sang folder cũ, và 2 project
    trùng tên KHÔNG dùng chung folder (trước đây gây lỗi render lấy ảnh của
    project khác / bản cũ).
    """
    name = (project_name or "").strip()
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(" .")
    return f"{name} (#{project_id})" if name else f"project_{project_id}"

def project_dir(project_id: int, project_name: str | None = None) -> Path:
    return MEDIA_ROOT / project_folder_name(project_name, project_id)

def images_dir(project_id: int, project_name: str | None = None) -> Path:
    return project_dir(project_id, project_name) / STEM_IMAGE

def clips_dir(project_id: int, project_name: str | None = None) -> Path:
    return project_dir(project_id, project_name) / STEM_CLIP

def final_dir(project_id: int, project_name: str | None = None) -> Path:
    return project_dir(project_id, project_name) / STEM_FINAL

def uploads_dir(project_id: int, project_name: str | None = None) -> Path:
    return project_dir(project_id, project_name) / STEM_UPLOAD


# ── Tham số sinh ảnh / video ────────────────────────────────────
@dataclass
class VideoParams:
    # Ảnh: 0 = thumbnail, 1..(image_count-1) = cảnh nền
    image_count:     int   = 41             # 0..40; mỗi ảnh tạo đúng 1 clip
    aspect_ratio:    str   = "16:9"
    image_style:     str   = "hoạt hình (animation/cartoon), màu sắc dịu, điện ảnh"

    # Video (Flow / Veo) — CHẾ ĐỘ "THÀNH PHẦN" (ingredients): mỗi clip tạo từ
    # 1 ảnh nguyên liệu (image-to-video), KHÔNG dùng khung đầu/cuối nữa.
    flow_model:      str   = "Omni 1.1 Flash"
    flow_mode:       str   = "Ingredients"   # Flow có thể hiện tiếng Anh/Việt
    clip_seconds:    int   = 8
    outputs_per_run: int   = 1               # x1 video
    # Giữ field này để tương thích API/UI cũ; pipeline 1:1 dùng image_count.
    rest_clips:      int   = 40              # ảnh 1..40, mỗi ảnh đúng 1 clip
    min_total_clips: int   = 41

    # Ghép/mix: blend (crossfade) + làm chậm
    t_window:        int   = 10             # T+10: không lặp trong 10 clip kế
    blend_seconds:   float = 1.0            # thời lượng crossfade blend giữa 2 clip
    slow_speed:      float = 0.7            # tốc độ phát (0.7 = chậm lại, mượt)

    @property
    def total_clips(self) -> int:
        return self.image_count


def generate_frame_pairs(image_count: int, total_clips: int,
                         seed: int | None = None) -> list[tuple[int, int]]:
    """
    Sinh danh sách cặp khung (start_img, end_img) cho các clip, sao cho:
      - CLIP ĐẦU TIÊN luôn BẮT ĐẦU từ ảnh 0 (thumbnail): cặp[0] = (0, 1).
      - Mỗi ảnh đều có ÍT NHẤT 1 "đường ra" (cạnh nền vòng tròn 0→1→…→n-1→0),
        để lúc ghép luôn nối tiếp được (ảnh cuối clip = ảnh đầu clip kế → liền mạch).
      - Thêm các cặp NGẪU NHIÊN (vd 4→1, 1→3) cho đa dạng.
    Trả về total_clips cặp; cặp[0] cố định (0→1), phần còn lại trộn ngẫu nhiên.
    """
    import random
    rng = random.Random(seed)
    n = max(image_count, 2)

    first = (0, 1 % n)                                     # clip đầu: 0 → 1
    # cạnh nền vòng tròn còn lại (1→2, …, (n-1)→0) để đảm bảo liền mạch
    rest: list[tuple[int, int]] = [(i, (i + 1) % n) for i in range(1, n)]
    while len(rest) + 1 < total_clips:
        a = rng.randrange(n)
        b = rng.randrange(n)
        if a != b:
            rest.append((a, b))
    rng.shuffle(rest)
    pairs = [first] + rest
    return pairs[:total_clips]


def generate_ingredient_plan(image_count: int, rest_clips: int,
                             seed: int | None = None) -> list[int]:
    """
    Chế độ "Thành phần": mỗi clip = 1 ảnh nguyên liệu.
      - Clip ĐẦU TIÊN dùng ẢNH 0 (thumbnail) — chỉ 1 lần duy nhất.
      - Các clip còn lại (rest_clips) dùng ẢNH 1..(n-1), chia đều + xáo trộn,
        cố gắng KHÔNG lặp ảnh ngay liền kề (để mỗi clip một cảnh khác nhau).
    Trả về danh sách chỉ số ảnh cho từng clip, ví dụ [0, 3, 1, 5, 2, 4, 1, ...].
    """
    import random
    rng = random.Random(seed)
    n = max(image_count, 2)
    scenery = list(range(1, n))                 # [1..n-1]
    if not scenery:
        scenery = [0]

    plan: list[int] = [0]                        # clip 0 = ảnh 0
    last = 0
    while len(plan) < 1 + rest_clips:
        cycle = scenery[:]
        rng.shuffle(cycle)
        for im in cycle:
            if len(plan) >= 1 + rest_clips:
                break
            if im == last and len(scenery) > 1:  # tránh lặp liền kề
                continue
            plan.append(im)
            last = im
    return plan


# ── 2 PHONG CÁCH: 2D & 3D ───────────────────────────────────────
# Vấn đề: khi prompt mô tả nhân vật quá "thực" (chất liệu vải, màu da, mã màu
# hex) và từ khoá phong cách nằm cuối/quá yếu, Gemini render ra ẢNH THẬT
# (photorealistic). Nên ta ÉP một câu lệnh phong cách mạnh vào ĐẦU mọi prompt
# (nơi model coi trọng nhất) + một câu PHỦ ĐỊNH ở cuối cấm ảnh thật.
# Người dùng chọn 2D hoặc 3D theo từng project → quyết định prefix/negative,
# lời hướng dẫn cho AI viết prompt, và gợi ý phong cách cho clip (Flow/Veo).
# Đổi khi thay đổi định nghĩa phong cách. Cache prompts.json có style_version
# KHÁC giá trị này (hoặc khác style_key) sẽ bị coi là cũ → sinh lại prompt.
STYLE_VERSION = "styles-v7"
DEFAULT_STYLE = "2d"

# Mỗi phong cách gồm:
#   icon/label/desc → hiển thị ở UI (bộ chọn cuộn được)
#   prefix   → chèn vào ĐẦU mọi prompt ảnh
#   negative → chèn vào CUỐI mọi prompt ảnh (ép đúng phong cách, cấm phong cách khác)
#   brief    → đưa vào lời nhắc AI viết prompt (prompt_gen). Với phong cách TẢ THỰC,
#              brief PHẢI chứa "tả thực" hoặc "photoreal" để prompt_gen bật nhánh
#              photoreal (xem prompt_gen._is_photoreal).
#   motion   → gợi ý phong cách gắn vào prompt chuyển động của clip (Flow/Veo)
STYLES: dict[str, dict[str, str]] = {
    "2d": {
        "icon": "🎨",
        "label": "2D (tranh vẽ / anime / cartoon)",
        "desc": "Tranh vẽ tay 2D, tô màu phẳng, anime/cartoon",
        "prefix": (
            "TRANH HOẠT HÌNH 2D (2D animation / cartoon / anime-style "
            "illustration), vẽ tay nét mềm, tô màu phẳng kiểu cel-shading, màu "
            "pastel dịu, ánh sáng điện ảnh. ĐÂY LÀ TRANH HOẠT HÌNH 2D, KHÔNG "
            "PHẢI ẢNH CHỤP THẬT. "
        ),
        "negative": (
            " || Phong cách BẮT BUỘC: hoạt hình 2D vẽ tay / cartoon / anime "
            "(flat cel-shading). TUYỆT ĐỐI KHÔNG photorealistic, KHÔNG ảnh chụp "
            "thật (photo/photograph/realistic photo), KHÔNG người/da/vải/tóc "
            "chân thực như đời thật, KHÔNG 3D render thực, KHÔNG hyperrealism. "
            "Nếu phân vân, luôn nghiêng về nét vẽ tay 2D phẳng."
        ),
        "brief": (
            "tranh hoạt hình 2D vẽ tay / cartoon / anime-style illustration, "
            "tô màu phẳng cel-shading, nét viền mềm, màu pastel dịu"
        ),
        "motion": "2D hand-drawn animated cartoon look",
    },
    "3d": {
        "icon": "🧊",
        "label": "3D (Pixar / Disney CGI)",
        "desc": "Khối 3D mềm mại kiểu Pixar / Disney",
        "prefix": (
            "PHIM HOẠT HÌNH 3D (3D animated movie / Pixar-Disney style / "
            "stylized 3D CGI render), nhân vật & cảnh vật tạo khối 3D mềm mại "
            "đáng yêu, đổ bóng dịu, màu pastel ấm, ánh sáng điện ảnh. ĐÂY LÀ "
            "ẢNH HOẠT HÌNH 3D, KHÔNG PHẢI ẢNH CHỤP THẬT. "
        ),
        "negative": (
            " || Phong cách BẮT BUỘC: hoạt hình 3D kiểu Pixar/Disney (stylized "
            "3D CGI). TUYỆT ĐỐI KHÔNG photorealistic, KHÔNG ảnh chụp thật (photo"
            "/photograph/realistic photo), KHÔNG người/da/vải/tóc chân thực như "
            "đời thật, KHÔNG hyperrealism, KHÔNG tranh vẽ 2D phẳng. Nếu phân "
            "vân, luôn nghiêng về khối 3D cách điệu kiểu phim hoạt hình."
        ),
        "brief": (
            "phim hoạt hình 3D kiểu Pixar/Disney (stylized 3D CGI), khối 3D mềm "
            "mại đáng yêu, đổ bóng dịu, subsurface scattering nhẹ, màu pastel ấm"
        ),
        "motion": "stylized 3D Pixar-style animated film look",
    },
    "ghibli": {
        "icon": "🌿",
        "label": "Ghibli (anime vẽ tay)",
        "desc": "Studio Ghibli, nền màu nước mộng mơ",
        "prefix": (
            "TRANH ANIME VẼ TAY KIỂU STUDIO GHIBLI (hand-painted Ghibli-style "
            "anime), nền vẽ tay như tranh màu nước, mây khối mềm, ánh sáng ấm "
            "hoài niệm, chi tiết thiên nhiên tỉ mỉ, màu trong trẻo. ĐÂY LÀ "
            "TRANH ANIME VẼ TAY, KHÔNG PHẢI ẢNH CHỤP THẬT. "
        ),
        "negative": (
            " || Phong cách BẮT BUỘC: anime vẽ tay kiểu Ghibli (painterly, "
            "watercolor backgrounds). TUYỆT ĐỐI KHÔNG photorealistic, KHÔNG ảnh "
            "chụp thật, KHÔNG 3D CGI, KHÔNG hyperrealism. Nếu phân vân, nghiêng "
            "về nét vẽ tay màu nước mộng mơ."
        ),
        "brief": (
            "tranh anime vẽ tay kiểu Studio Ghibli, nền màu nước vẽ tay, mây "
            "mềm, ánh sáng ấm hoài niệm, chi tiết thiên nhiên tỉ mỉ"
        ),
        "motion": "hand-painted Ghibli-style anime look, gentle painterly motion",
    },
    "anime": {
        "icon": "✨",
        "label": "Anime điện ảnh (Makoto Shinkai)",
        "desc": "Bầu trời rực rỡ, ánh sáng lung linh",
        "prefix": (
            "ANIME ĐIỆN ẢNH HIỆN ĐẠI (modern cinematic anime, Makoto Shinkai "
            "style), bầu trời rực rỡ chi tiết, ánh sáng lens-flare lung linh, "
            "màu bão hoà đẹp, hiệu ứng ánh sáng điện ảnh, nét vẽ sắc. ĐÂY LÀ "
            "TRANH ANIME, KHÔNG PHẢI ẢNH CHỤP THẬT. "
        ),
        "negative": (
            " || Phong cách BẮT BUỘC: anime điện ảnh hiện đại (Makoto Shinkai). "
            "TUYỆT ĐỐI KHÔNG photorealistic, KHÔNG ảnh chụp thật, KHÔNG 3D CGI "
            "kiểu Pixar, KHÔNG hyperrealism. Nếu phân vân, nghiêng về nét anime "
            "bầu trời rực rỡ."
        ),
        "brief": (
            "anime điện ảnh hiện đại kiểu Makoto Shinkai, bầu trời rực rỡ chi "
            "tiết, ánh sáng lens-flare, màu bão hoà, nét vẽ sắc điện ảnh"
        ),
        "motion": "cinematic modern anime look (Makoto Shinkai), luminous skies",
    },
    "watercolor": {
        "icon": "🖌️",
        "label": "Màu nước (watercolor)",
        "desc": "Màu loang mềm trên giấy, trong trẻo",
        "prefix": (
            "TRANH MÀU NƯỚC (watercolor painting illustration), màu loang mềm "
            "trên giấy, viền ướt, mảng màu trong suốt chồng lớp, khoảng trắng "
            "thở, nét bút lỏng. ĐÂY LÀ TRANH MÀU NƯỚC VẼ TAY, KHÔNG PHẢI ẢNH "
            "CHỤP THẬT. "
        ),
        "negative": (
            " || Phong cách BẮT BUỘC: tranh màu nước vẽ tay (watercolor). "
            "TUYỆT ĐỐI KHÔNG photorealistic, KHÔNG ảnh chụp thật, KHÔNG 3D CGI, "
            "KHÔNG nét vector phẳng cứng. Nếu phân vân, nghiêng về màu loang "
            "mềm trên giấy."
        ),
        "brief": (
            "tranh màu nước vẽ tay, màu loang mềm trên giấy, viền ướt, mảng màu "
            "trong suốt chồng lớp, khoảng trắng thở"
        ),
        "motion": "hand-painted watercolor illustration look, soft bleeding pigments",
    },
    "oil": {
        "icon": "🖼️",
        "label": "Sơn dầu (oil painting)",
        "desc": "Nét cọ impasto dày, màu giàu chiều sâu",
        "prefix": (
            "TRANH SƠN DẦU (oil painting), nét cọ dày impasto nhìn thấy rõ, màu "
            "giàu chiều sâu, ánh sáng ấm kiểu hội hoạ cổ điển, chất sơn dày. "
            "ĐÂY LÀ TRANH SƠN DẦU VẼ TAY, KHÔNG PHẢI ẢNH CHỤP THẬT. "
        ),
        "negative": (
            " || Phong cách BẮT BUỘC: tranh sơn dầu vẽ tay (oil painting, "
            "impasto). TUYỆT ĐỐI KHÔNG photorealistic, KHÔNG ảnh chụp thật, "
            "KHÔNG 3D CGI, KHÔNG nét số phẳng. Nếu phân vân, nghiêng về nét cọ "
            "dày sơn dầu."
        ),
        "brief": (
            "tranh sơn dầu vẽ tay, nét cọ impasto dày rõ, màu giàu chiều sâu, "
            "ánh sáng ấm kiểu hội hoạ cổ điển"
        ),
        "motion": "oil painting look, visible brushstroke texture",
    },
    "ink": {
        "icon": "🖋️",
        "label": "Thuỷ mặc (ink wash Á Đông)",
        "desc": "Mực loang tối giản, nhiều khoảng trống",
        "prefix": (
            "TRANH THUỶ MẶC Á ĐÔNG (East-Asian ink wash painting, sumi-e), mực "
            "đen loang trên giấy xuyến, nhiều khoảng trống, nét bút tối giản "
            "thanh thoát, điểm nhấn màu nhạt. ĐÂY LÀ TRANH MỰC VẼ TAY, KHÔNG "
            "PHẢI ẢNH CHỤP THẬT. "
        ),
        "negative": (
            " || Phong cách BẮT BUỘC: tranh thuỷ mặc Á Đông (ink wash / "
            "sumi-e). TUYỆT ĐỐI KHÔNG photorealistic, KHÔNG ảnh chụp thật, "
            "KHÔNG 3D CGI, KHÔNG màu bão hoà rực. Nếu phân vân, nghiêng về mực "
            "loang tối giản nhiều khoảng trống."
        ),
        "brief": (
            "tranh thuỷ mặc Á Đông (ink wash / sumi-e), mực loang trên giấy, "
            "nhiều khoảng trống, nét bút tối giản thanh thoát"
        ),
        "motion": "East-Asian ink wash painting look, flowing ink, minimalist",
    },
    "lofi": {
        "icon": "🎧",
        "label": "Lofi aesthetic",
        "desc": "Cozy hoài niệm, hạt film ấm",
        "prefix": (
            "TRANH MINH HOẠ LOFI (lo-fi anime aesthetic illustration), tông "
            "màu ấm hoài niệm, hạt nhiễu film nhẹ, ánh đèn dịu cozy, chi tiết "
            "đời thường ấm cúng, nét vẽ 2D mềm. ĐÂY LÀ TRANH MINH HOẠ, KHÔNG "
            "PHẢI ẢNH CHỤP THẬT. "
        ),
        "negative": (
            " || Phong cách BẮT BUỘC: tranh minh hoạ lofi aesthetic (2D). "
            "TUYỆT ĐỐI KHÔNG photorealistic, KHÔNG ảnh chụp thật, KHÔNG 3D CGI "
            "thực. Nếu phân vân, nghiêng về nét minh hoạ 2D ấm hoài niệm."
        ),
        "brief": (
            "tranh minh hoạ lofi aesthetic 2D, tông ấm hoài niệm, hạt nhiễu "
            "film nhẹ, ánh đèn dịu cozy, chi tiết đời thường ấm cúng"
        ),
        "motion": "lo-fi aesthetic 2D illustration look, cozy warm ambience",
    },
    "real": {
        "icon": "📷",
        "label": "Tả thực điện ảnh (cinematic photoreal)",
        "desc": "Như ảnh chụp / phim điện ảnh thật",
        "prefix": (
            "ẢNH TẢ THỰC ĐIỆN ẢNH (cinematic photorealistic, hyperrealistic "
            "photo-real render), ánh sáng điện ảnh, chiều sâu trường ảnh "
            "(shallow depth of field / bokeh), chất liệu, da, vải, tóc, lông "
            "chi tiết chân thực như đời thật, độ phân giải cao, tông màu phim. "
            "ĐÂY LÀ ẢNH TẢ THỰC NHƯ CHỤP THẬT, KHÔNG PHẢI TRANH HOẠT HÌNH. "
        ),
        "negative": (
            " || Phong cách BẮT BUỘC: tả thực điện ảnh / photorealistic (như "
            "ảnh chụp/phim điện ảnh thật). TUYỆT ĐỐI KHÔNG hoạt hình, KHÔNG "
            "cartoon/anime, KHÔNG tranh vẽ 2D phẳng (cel-shading), KHÔNG 3D "
            "kiểu Pixar/Disney cách điệu, KHÔNG nét vẽ tay hay tô màu phẳng. "
            "Nếu phân vân, luôn nghiêng về ảnh chụp thật với chi tiết chân "
            "thực và ánh sáng điện ảnh."
        ),
        "brief": (
            "ảnh tả thực điện ảnh (cinematic photorealistic), ánh sáng điện "
            "ảnh, chiều sâu trường ảnh/bokeh, chất liệu và da/vải/tóc chi tiết "
            "chân thực như thật, tông màu phim, độ chi tiết cao"
        ),
        "motion": (
            "cinematic photorealistic live-action look, realistic natural "
            "lighting, shallow depth of field"
        ),
    },
    "vintage": {
        "icon": "📽️",
        "label": "Phim nhựa cổ điển (vintage film)",
        "desc": "Ảnh chụp phim analog cổ, hạt film hoài niệm",
        "prefix": (
            "ẢNH CHỤP PHIM NHỰA CỔ ĐIỂN (vintage analog film photograph, "
            "35mm), tả thực như ảnh chụp thật, hạt film rõ, màu ngả hoài niệm "
            "(faded warm tones), vignette nhẹ, ánh sáng tự nhiên mềm. ĐÂY LÀ "
            "ẢNH CHỤP THẬT KIỂU PHIM CỔ, KHÔNG PHẢI TRANH HOẠT HÌNH. "
        ),
        "negative": (
            " || Phong cách BẮT BUỘC: ảnh chụp phim nhựa analog cổ điển, tả "
            "thực (photorealistic). TUYỆT ĐỐI KHÔNG hoạt hình, KHÔNG "
            "cartoon/anime, KHÔNG 3D CGI cách điệu, KHÔNG tranh vẽ. Nếu phân "
            "vân, nghiêng về ảnh chụp phim thật với hạt film và màu hoài niệm."
        ),
        "brief": (
            "ảnh chụp phim nhựa analog cổ điển 35mm, tả thực như ảnh chụp thật "
            "(photorealistic), hạt film, màu ngả hoài niệm, vignette nhẹ"
        ),
        "motion": "vintage analog film photograph look, realistic 35mm film grain",
    },
}


def normalize_style(style: str | None) -> str:
    """Chuẩn hoá key phong cách; giá trị lạ → DEFAULT_STYLE."""
    s = (style or "").strip().lower()
    return s if s in STYLES else DEFAULT_STYLE


def style_info(style: str | None) -> dict[str, str]:
    return STYLES[normalize_style(style)]


def wrap_style(prompt: str, style: str | None = None) -> str:
    """Bọc prompt bằng prefix phong cách (2D/3D) + phủ định cấm ảnh thật."""
    s = style_info(style)
    return f"{s['prefix']}{prompt}{s['negative']}"


# ── Prompt sinh ảnh (chỉnh theo ý muốn) ─────────────────────────
# {topic} = tên project / chủ đề nhạc.  Ảnh 0 là thumbnail.
# 41 ảnh là 41 biến thể của CÙNG một bố cục phim hoạt hình chill. Mỗi prompt
# tự chứa danh tính + bố cục, kể cả khi Gemini mở chat mới; không dựa vào trí
# nhớ hội thoại. Ảnh 0 sạch chữ: tiêu đề thật được chèn ở bước thumbnail/intro.
_CONTINUITY = (
    "Đây là shot tiếp theo của CÙNG MỘT phim hoạt hình chill chủ đề '{topic}'. "
    "Giữ chính xác CÙNG nhân vật chính: một người trẻ tóc đen ngắn hơi rối, "
    "áo hoodie xanh sage rộng, quần kem, giày trắng và tai nghe màu be; giữ "
    "nguyên khuôn mặt, vóc dáng, trang phục và tỉ lệ nhân vật ở mọi shot. Giữ "
    "CÙNG bối cảnh: căn cabin gỗ cạnh hồ trong thung lũng rừng thông, cửa sổ "
    "lớn, bàn gỗ, đèn vàng, cây cảnh, cốc trà và chú mèo tam thể. BỐ CỤC KHÓA: "
    "wide shot ngang tầm mắt, góc nhìn ba phần tư từ cùng một vị trí trong "
    "cabin; nhân vật ngồi bên bàn ở một phần ba PHẢI, mèo cuộn tròn trên bệ "
    "cửa sổ bên phải nhân vật, cốc trà bên trái bàn, đèn phía sau cốc, cây "
    "cảnh ở mép phải; hồ, rừng thông và núi nhìn qua cửa sổ chiếm nửa TRÁI. "
    "Không di chuyển camera, nhân vật, đồ vật hoặc kiến trúc. Cùng bảng màu "
    "xanh sage–kem–vàng hổ phách, cùng hoàng hôn dịu; chỉ biến thiên cực nhỏ "
    "của ánh sáng và thiên nhiên. Nét mềm, cozy, lofi, serene, relaxing, "
    "meditation, khung hình 16:9; tư thế nghỉ bình yên và nhịp rất chậm. "
    "Khi có ảnh tham chiếu, giữ continuity đúng ảnh đó; mọi đặc điểm khóa "
    "ở trên vẫn bắt buộc kể cả trong chat mới. KHÔNG thêm nhân vật mới, "
    "KHÔNG đổi trang phục, KHÔNG chữ, logo hay watermark. "
)


def _scene(description: str) -> str:
    return _CONTINUITY + description


DEFAULT_PROMPTS: dict[str, str] = {
    "0": _scene("Ảnh nền thumbnail mở đầu, HOÀN TOÀN KHÔNG CÓ CHỮ. Nhân vật rõ nhưng không quá lớn ở PHẢI; mặt hồ phẳng với mảng sáng dịu ở nửa TRÁI tạo negative space để phần mềm chèn tiêu đề sau. Ánh viền hổ phách thật nhẹ trên tóc; không thay đổi vị trí đã khóa."),
    "1": _scene("Biến thể mặt hồ gần như phẳng, vài gợn nước dài thưa phản chiếu màu kem của trời; nhân vật thư thái, đôi tay nghỉ trên bàn."),
    "2": _scene("Biến thể gợn nước lan rất nhẹ phía bờ xa, các phản chiếu thông mềm và kéo dài; ánh đèn cabin ổn định."),
    "3": _scene("Biến thể một dải mây mỏng màu kem nằm ngang trên núi xa; hồ phản chiếu dải mây, mọi vật tiền cảnh giữ nguyên."),
    "4": _scene("Biến thể ánh hoàng hôn xuyên nhẹ qua mép mây, vệt sáng mềm nằm trên mặt bàn cạnh cốc trà; không tia sáng gắt."),
    "5": _scene("Biến thể lá thông ngoài cửa sổ hơi nghiêng vì gió nhẹ; mèo vẫn cuộn tròn ngủ, nhân vật giữ nguyên tư thế nghỉ."),
    "6": _scene("Biến thể sương mỏng chỉ ở chân núi xa, không che hồ hoặc nhân vật; độ tương phản toàn cảnh rất dịu."),
    "7": _scene("Biến thể phản chiếu mây hơi đứt đoạn bởi gợn nước nhỏ; sắc xanh sage của hồ giữ nhất quán."),
    "8": _scene("Biến thể ánh đèn hổ phách tạo vùng sáng tròn mềm hơn trên bàn; bóng cốc trà mờ, không thêm hay dời đạo cụ."),
    "9": _scene("Biến thể rèm cửa ở rìa khung hơi cong do gió nhẹ, nhân vật nhìn bình yên về hồ; rèm không che khuôn mặt."),
    "10": _scene("Biến thể mặt nước trong gần bờ phản chiếu đường cửa sổ thật mờ; núi xa nằm nguyên trên đường chân trời."),
    "11": _scene("Biến thể vài hạt bụi sáng nhỏ trong tia sáng cạnh cửa sổ, rất thưa và tinh tế; không hiệu ứng kỳ ảo."),
    "12": _scene("Biến thể mép mây màu hổ phách nhạt, phần giữa mây màu kem; ánh sáng bên trong vẫn ấm dịu và ổn định."),
    "13": _scene("Biến thể cụm lá cây cảnh tại mép phải bắt sáng mềm; nhân vật và mèo vẫn đúng tỷ lệ, khuôn mặt không đổi."),
    "14": _scene("Biến thể sương bên bờ hồ xa thành dải ngang mỏng, phản chiếu rừng thông vẫn nhìn thấy rõ; không sương trong cabin."),
    "15": _scene("Biến thể các gợn hồ tạo đường cong rộng, khoảng nước giữa các gợn phẳng và tĩnh; tổng thể thư giãn, không sóng lớn."),
    "16": _scene("Biến thể bóng khung cửa mềm rơi trên mép bàn, tai nghe be phản sáng nhẹ; ánh sáng và hướng bóng giữ đúng góc đã khóa."),
    "17": _scene("Biến thể mây thưa hơn trên đỉnh núi nhưng cùng sắc trời hoàng hôn; nhân vật vẫn ngồi nghỉ cạnh cốc trà."),
    "18": _scene("Biến thể mép tai mèo có viền sáng hổ phách rất nhẹ, mèo ngủ đúng vị trí trên bệ cửa; không đổi hoa văn tam thể."),
    "19": _scene("Biến thể hồ phản chiếu vùng trời kem rộng hơn, hàng thông giữ đường viền mềm; không chuyển sang đêm hoặc bình minh."),
    "20": _scene("Biến thể vài gợn nước nhỏ giao nhau ở giữa hồ, ánh phản chiếu tan mềm; bố cục cabin và nhân vật không đổi sau shot trước."),
    "21": _scene("Biến thể ánh vàng trên cạnh cốc trà dịu hơn, mặt bàn hiện vân gỗ minh họa mềm; không zoom hoặc cắt thành cận cảnh."),
    "22": _scene("Biến thể lớp mây mỏng thứ hai xa phía sau đỉnh núi; chiều sâu nhẹ nhàng, không thêm núi hay đổi địa hình."),
    "23": _scene("Biến thể lá cây cảnh hơi hạ xuống tự nhiên, bóng lá nhòe nhẹ trên cạnh bàn; nhân vật giữ tư thế, trang phục và tai nghe."),
    "24": _scene("Biến thể phản chiếu hàng thông thành các nét dọc mềm trên mặt hồ, những khoảng nước trống tạo cảm giác tĩnh lặng."),
    "25": _scene("Biến thể ánh trời kem qua cửa sổ hòa nhẹ với đèn vàng ở tay áo sage; biểu cảm nhân vật bình yên và không thay đổi danh tính."),
    "26": _scene("Biến thể làn sương mỏng hơi tách thành hai dải ở bờ xa; giữ vùng mặt hồ phía trước sáng và thoáng, không tạo khói từ người hoặc cốc."),
    "27": _scene("Biến thể rèm cửa gần trở lại thẳng, mép vải mềm trong ánh hổ phách; mọi vật và camera đều đứng yên."),
    "28": _scene("Biến thể vài chấm đom đóm nhỏ rất thưa bên ngoài cạnh bờ hồ, không vào cabin, không biến thành bokeh che nhân vật."),
    "29": _scene("Biến thể bề mặt hồ mịn hơn ở vùng trái khung, gợn nước thưa gần bờ phải; không thay đổi khung hình rộng đã khóa."),
    "30": _scene("Biến thể ánh viền ấm trên đường vai hoodie phản chiếu đèn bàn, gương mặt sáng dịu; nhân vật vẫn ngồi thả lỏng."),
    "31": _scene("Biến thể phần chân mây mềm hòa với sương núi xa, sắc độ thấp và dễ chịu; không thêm mưa hay bão."),
    "32": _scene("Biến thể bóng mèo trên bệ cửa mờ hơn vì ánh sáng tán xạ; mèo tam thể ngủ cuộn tròn đúng chỗ cũ."),
    "33": _scene("Biến thể một vệt phản chiếu vàng nhạt nằm ngang xa trên hồ; không chói, không đổi hướng mặt trời hay thời điểm hoàng hôn."),
    "34": _scene("Biến thể hạt bụi sáng tập trung rất thưa ở mép cửa trên cao; không có hạt sáng trên mặt hoặc cơ thể nhân vật."),
    "35": _scene("Biến thể đầu cành thông ngoài cửa sổ nhẹ cong rồi thả tự nhiên, khoảng trời kem vẫn rộng; cảm giác slow tempo."),
    "36": _scene("Biến thể bóng lá cây cảnh tạo hình mềm nhỏ ở mép bàn phải; cốc, đèn, nhân vật, mèo và hồ giữ nguyên vị trí."),
    "37": _scene("Biến thể phản chiếu núi hiện rõ hơn một chút giữa các gợn nước thưa; giữ màu sage–kem và độ tương phản thấp."),
    "38": _scene("Biến thể vài chấm đom đóm xa thưa dần, ánh đèn cabin vẫn đều; không chuyển sang cảnh tối hoặc thêm nguồn sáng mới."),
    "39": _scene("Biến thể mây mỏng phủ nhẹ vệt nắng, toàn cảnh dịu như một nhịp thở; giữ nguyên tất cả nét nhận dạng và hình học cabin."),
    "40": _scene("Biến thể mặt hồ trở lại phẳng với gợn dài rất thưa, ánh hoàng hôn ổn định gần shot đầu để nối lặp tự nhiên; nhân vật và mèo vẫn nghỉ trong cùng bố cục."),
}

# Từ khoá healing ghép vào tiêu đề thumbnail (dòng nhỏ). Mỗi lần chọn ngẫu
# nhiên vài từ. Có thể override bằng "thumbnail_keywords" trong overrides.
THUMBNAIL_KEYWORDS = [
    "Meditation", "Zen", "Ambient", "Focus", "Study", "Healing",
    "Relaxing", "Sleep", "Calm", "Stress Relief", "Deep Sleep",
    "Slow Living", "Lofi", "Peaceful Mind",
]

# Prompt mô tả chuyển động cho Flow (áp cho mọi clip; có thể để rỗng).
FLOW_MOTION_PROMPT = (
    "chill meditative animated film, very slow tempo, locked camera; "
    "no zoom, pan or orbit; gentle natural in-place micro-motion of the character, "
    "soft ambient motion of leaves, drifting mist, water ripples and light only; "
    "preserve the exact same character identity, face, hairstyle, outfit, props, "
    "scenery, color palette and original composition; seamless slow loop, "
    "no scene cut, no sudden motion, no dialogue, no lip sync, no new character"
)

# Phủ định CỨNG gắn vào CUỐI mọi prompt chuyển động gửi Veo/Flow. Chặn các lỗi
# "ảo giác" Veo hay tạo ra: khói/hơi/lửa bốc ra từ miệng hay nhạc cụ, đầu xoay
# ngược, méo mó mặt/tay/ngón, thừa chi, biến hình, nhân bản, cắt cảnh, chữ...
# Áp dụng cho MỌI clip (cả motion AI lẫn motion mặc định).
MOTION_NEGATIVE = (
    " || NGHIÊM CẤM (negative — tránh tuyệt đối): KHÔNG khói, hơi nước, hơi thở "
    "thành khói, lửa, tàn lửa hay sương khói bốc ra từ miệng, mũi, sáo hoặc bất "
    "kỳ nhạc cụ/vật thể nào; KHÔNG đầu hay cổ xoay ngược, xoay 180°, giật ngược "
    "bất thường; KHÔNG méo mó/biến dạng khuôn mặt, mắt, răng, tay, ngón tay; "
    "KHÔNG thừa hay thiếu ngón/chi; KHÔNG mọc thêm người, chi hay vật; KHÔNG "
    "biến hình (morph), KHÔNG nhân bản/tách đôi nhân vật; KHÔNG đổi trang phục "
    "hay đạo cụ giữa chừng; KHÔNG cắt cảnh, KHÔNG nháy hình, KHÔNG chuyển động "
    "giật cục hay đột ngột; KHÔNG mấp máy môi như đang nói; KHÔNG chữ, logo, "
    "watermark. negative prompt: no smoke, no steam, no vapor, no breath vapor, "
    "no fog from mouth, no smoke from flute or instrument, no fire, no head "
    "spinning, no reversed head, no 180-degree head turn, no face distortion, "
    "no warped or melting hands, no extra fingers, no extra limbs, no morphing, "
    "no duplicated character, no scene cut, no flicker, no text. Keep anatomy "
    "correct, physically plausible, and the character identity fully consistent."
)


# ── Cấu hình trình duyệt ────────────────────────────────────────
@dataclass
class BrowserConfig:
    headless:        bool = False          # phải False để tự đăng nhập lần đầu
    slow_mo_ms:      int  = 120            # làm chậm thao tác cho ổn định
    nav_timeout_ms:  int  = 60_000
    action_timeout_ms: int = 30_000
    # Thời gian tối đa chờ 1 ảnh / 1 clip render xong (giây)
    image_wait_sec:  int  = 180
    clip_wait_sec:   int  = 900            # Veo có thể lâu vài phút
    # Cứ tạo bao nhiêu ảnh thì mở CHAT MỚI để cắt ngữ cảnh dài (0 = không cắt).
    # Hội thoại quá dài (vd ảnh ~37/41 cùng 1 chat) khiến Gemini trả chữ/từ chối
    # → time-out. Mỗi prompt tự chứa danh tính nhân vật nên cắt chat vẫn đồng bộ.
    image_new_chat_every: int = 8
    # None = dùng Chromium bundled của Playwright (ổn định nhất, chạy
    # `playwright install chromium` 1 lần). Đặt "chrome" nếu muốn dùng Google
    # Chrome đã cài; "msedge" cho Edge. (Cốc Cốc không hỗ trợ trực tiếp.)
    chrome_channel:  str | None = None


# ── URL ─────────────────────────────────────────────────────────
CHATGPT_URL = "https://chatgpt.com/"
GEMINI_URL  = "https://gemini.google.com/app"
FLOW_URL    = "https://flow.google.com/"
YOUTUBE_STUDIO_URL = "https://studio.youtube.com/"

# Model dùng để tạo ảnh trên Gemini web (chọn trong dropdown model).
# "tư duy mở rộng" = bản thinking/Pro.
GEMINI_IMAGE_MODEL = "3.1 Pro"

# Độ phân giải khi TẢI clip từ Flow (menu Tải xuống có menu con):
#   "720p" = kích thước gốc (tải NGAY, KHÔNG upscale); "1080p"/"4K" = upscale
#   (chờ "Upscaling your video", 4K tốn tín dụng). Omni 1.1 Flash render gốc
#   720p và chỉ có 360p/720p → dùng "720p" để tải trực tiếp, tránh upscale.
#   Override qua "flow_download_resolution" trong video_overrides.json.
FLOW_DOWNLOAD_RESOLUTION = "720p"


# ── SELECTOR (best-effort — SỬA Ở ĐÂY khi automation lỗi) ────────
# Mỗi giá trị là danh sách selector thử theo thứ tự (fallback dần).
CHATGPT_SELECTORS: dict[str, list[str]] = {
    "prompt_box": [
        "#prompt-textarea",
        "div[contenteditable='true']",
        "textarea[data-testid='prompt-textarea']",
    ],
    "send_button": [
        "button[data-testid='send-button']",
        "button[aria-label*='Send']",
        "button[aria-label*='Gửi']",
    ],
    # Ảnh sinh ra trong luồng hội thoại
    "generated_image": [
        "img[alt*='Generated']",
        "div[data-testid*='image'] img",
        "img[src*='oaiusercontent']",
    ],
    # Nút tải ảnh (nếu có), hoặc ta lấy src trực tiếp
    "image_download": [
        "a[download]",
        "button[aria-label*='Download']",
        "button[aria-label*='Tải']",
    ],
}

GEMINI_SELECTORS: dict[str, list[str]] = {
    # Dropdown chọn model + option 3.1 Pro (tư duy mở rộng)
    "model_selector": [
        "button[aria-label*='model']",
        "button:has-text('Gemini')",
        "[data-test-id='bard-mode-menu-button']",
        "button:has-text('2.5')",
        "button:has-text('3.1')",
    ],
    "model_option_pro": [
        "[role='menuitemradio']:has-text('3.1 Pro')",
        "[role='menuitem']:has-text('3.1 Pro')",
        "[role='option']:has-text('3.1 Pro')",
        "*:has-text('3.1 Pro')",
    ],
    # Bước 2: mở lại Model, bật "Tư duy mở rộng" (extended thinking)
    "model_option_thinking": [
        "[role='menuitemradio']:has-text('Tư duy mở rộng')",
        "[role='menuitem']:has-text('Tư duy mở rộng')",
        "[role='option']:has-text('Tư duy mở rộng')",
        "button:has-text('Tư duy mở rộng')",
        "*:has-text('Tư duy mở rộng')",
        "*:has-text('Thinking')",
    ],
    # Ô nhập prompt
    "prompt_box": [
        "div.ql-editor[contenteditable='true']",
        "rich-textarea div[contenteditable='true']",
        "div[contenteditable='true']",
        "textarea",
    ],
    "send_button": [
        "button[aria-label*='Send']",
        "button[aria-label*='Gửi']",
        "button.send-button",
        "button[mattooltip*='Send']",
    ],
    # Ảnh sinh ra trong câu trả lời
    "generated_image": [
        "img[src*='googleusercontent']",
        "generated-image img",
        "img[alt*='image']",
        "message-content img",
    ],
    "image_download": [
        "button[aria-label*='Download']",
        "button[aria-label*='Tải']",
        "a[download]",
    ],
    # Nút "Trò chuyện mới" — cắt ngữ cảnh dài (hội thoại quá dài khiến Gemini
    # hay trả CHỮ/từ chối). Không bấm được → driver reload thẳng GEMINI_URL.
    "new_chat": [
        "button[aria-label*='Cuộc trò chuyện mới']",
        "a[aria-label*='Cuộc trò chuyện mới']",
        "button[aria-label*='Trò chuyện mới']",
        "a[aria-label*='Trò chuyện mới']",
        "button[aria-label*='New chat']",
        "a[aria-label*='New chat']",
        "button[aria-label*='New conversation']",
        "[data-test-id='new-chat-button']",
    ],
    # Nút "⋯" dưới ảnh — aria-label THẬT: "Hiện thêm tuỳ chọn".
    "more_button": [
        "button[aria-label='Hiện thêm tuỳ chọn']",
        "button[aria-label*='thêm tuỳ chọn']",
        "button[aria-label*='tuỳ chọn']",
        "button[aria-label*='tùy chọn']",
        "button[aria-label*='More options']",
        "button[aria-label*='Show more']",
    ],
    # Mục menu "Tải hình ảnh xuống" (thẻ <gem-menu-item role=menuitem>).
    "download_menu_item": [
        "gem-menu-item:has-text('Tải hình ảnh xuống')",
        "[role='menuitem']:has-text('Tải hình ảnh xuống')",
        "*:has-text('Tải hình ảnh xuống')",
        "gem-menu-item:has-text('Download')",
        "[role='menuitem']:has-text('Download')",
    ],
}

# Selector Flow đã kiểm chứng trực tiếp; ưu tiên tiếng Anh và giữ fallback tiếng Việt.
FLOW_SELECTORS: dict[str, list[str]] = {
    # Trang giới thiệu có thể xuất hiện trước dashboard.
    "flow_entry": [
        "button:has-text('Create with Google Flow')",
        "a:has-text('Create with Google Flow')",
    ],
    # Tạo dự án mới từ dashboard
    "new_project": [
        "button:has-text('New project')",
        "button:has-text('Dự án mới')",
        "div[role='button']:has-text('Dự án mới')",
        "a:has-text('Dự án mới')",
    ],
    # Mở bảng cài đặt (chip "Video · 720p · 8 giây · x1")
    "settings_trigger": [
        "button[aria-label='Settings trigger']",
        "button[aria-label='Điều kiện kích hoạt cài đặt']",
        # Flow đôi khi render chip Settings KHÔNG phải thẻ <button> (div/role),
        # nên khớp mọi thẻ theo aria-label + qua role engine (khớp accessible
        # name, đã chuẩn hoá khoảng trắng) cho bền với thay đổi UI.
        "[aria-label='Settings trigger']",
        "[aria-label='Điều kiện kích hoạt cài đặt']",
        "role=button[name='Settings trigger']",
        "role=button[name='Điều kiện kích hoạt cài đặt']",
        # Fallback cuối: chip cạnh nút 'Start generation' chứa 'crop_16_9'/tỉ lệ.
        "[role='button']:has-text('crop_16_9')",
    ],
    # Chế độ Khung hình (start+end frame)
    "mode_frames": [
        "[role='radio']:has-text('Frames')",
        "[role='radio']:has-text('Khung hình')",
    ],
    # Chế độ Thành phần (ingredients: mỗi clip 1 ảnh nguyên liệu)
    "mode_ingredients": [
        "[role='radio']:has-text('Ingredients')",
        "[role='radio']:has-text('Thành phần')",
    ],
    # Nút "+" thêm ảnh nguyên liệu (mở dialog chọn ảnh) trong mode Thành phần
    "add_ingredient": [
        "button[aria-label='Add ingredients to the prompt box']",
        "button[aria-label='Thêm thành phần vào ô nhập câu lệnh']",
    ],
    # Nút xoá chip nguyên liệu đã thêm (icon 'cancel', aria 'Thành phần')
    "remove_ingredient": [
        "button[aria-label='Ingredient']",
        "button[aria-label='Thành phần']",
    ],
    "clear_prompt": [
        "button[aria-label='Clear prompt']",
        "button[aria-label='Xóa câu lệnh']",
        "button[aria-label='Xoá câu lệnh']",
    ],
    # Tỉ lệ 16:9
    "aspect_option_169": [
        "[role='radio']:has-text('16:9')",
    ],
    # Dropdown model. Option model + radio thời lượng được dựng ĐỘNG trong
    # flow_driver._configure_settings từ config.PARAMS.flow_model /
    # clip_seconds (EN+VN), nên không hardcode ở đây nữa.
    "model_selector": [
        "button[aria-label='Select model family']",
        "button[aria-label='Chọn nhóm mô hình']",
    ],
    "outputs_x1": [
        "[role='radio']:text-is('x1')",
    ],
    # Upload ảnh vào project: nút "+" trên cùng → menu "Tải lên" (mở file chooser)
    "add_media_menu": [
        "button[aria-label='Add media menu']",
        "button[aria-label='Trình đơn thêm nội dung nghe nhìn']",
    ],
    "upload_menu_item": [
        "[role='menuitem']:has-text('Upload')",
        "[role='menuitem']:has-text('Tải lên')",
    ],
    # Ô Bắt đầu / Kết thúc (mở dialog "Chọn một hình ảnh khung")
    "start_frame_button": [
        "button:text-is('Bắt đầu')",
    ],
    "end_frame_button": [
        "button:text-is('Kết thúc')",
    ],
    # Dialog "Chọn một hình ảnh khung": ô tìm (dự phòng). Ảnh chọn theo tên
    # file "{i}.png" dựng động trong driver ([role='option']:has-text('0.png')).
    "frame_search": [
        "input[placeholder='Search assets']",
        "input[placeholder='Tìm kiếm thành phần']",
    ],
    # Nút xác nhận sau khi chọn ảnh trong dialog khung.
    "frame_confirm": [
        "button:has-text('Add to prompt')",
        "button:has-text('Thêm vào câu lệnh')",
    ],
    # Ô prompt mô tả chuyển động (contenteditable, có placeholder overlay).
    "prompt_box": [
        "div[contenteditable='true']",
        "textarea",
    ],
    # Nút tạo
    "generate_button": [
        "button[aria-label='Start generation']",
        "button[aria-label='Bắt đầu tạo']",
    ],
    # Video kết quả trong lưới
    "result_video": [
        "flow-grid-tile-container:has(flow-video-tile)",
        "video",
    ],
    # Nút ⋯ trên clip đã render (khác Gemini): aria-label 'Tuỳ chọn khác'
    "more_button": [
        "button[aria-label='More options']",
        "button[aria-label='Tuỳ chọn khác']",
    ],
    # Mục 'Tải xuống' trong menu clip → tải trực tiếp (expect_download)
    "video_download": [
        "[role='menuitem']:has-text('Download')",
        "[role='menuitem']:has-text('Tải xuống')",
    ],
}


# Selector YouTube Studio (upload → điền metadata → lưu Draft). RẤT dễ đổi và
# phụ thuộc ngôn ngữ tài khoản (EN/VN) → để cả hai. SỬA Ở ĐÂY khi automation lỗi;
# có thể override qua video_overrides.json ("selectors.youtube.<key>").
YOUTUBE_SELECTORS: dict[str, list[str]] = {
    # Nút "Tạo" (Create) góc trên phải Studio.
    "create_button": [
        "ytcp-button#create-icon",
        "button[aria-label='Create']",
        "button[aria-label='Tạo']",
        "ytcp-button[aria-label='Create']",
        "#create-icon",
    ],
    # Mục "Tải video lên" trong menu Create.
    "upload_menu_item": [
        "tp-yt-paper-item#text-item-0",
        "tp-yt-paper-item:has-text('Upload videos')",
        "tp-yt-paper-item:has-text('Tải video lên')",
        "[role='menuitem']:has-text('Upload video')",
        "[role='menuitem']:has-text('Tải video lên')",
    ],
    # Input file (ẩn) để set video. Dialog upload dùng input[type=file].
    "file_input": [
        "input[type='file']",
    ],
    # Ô tiêu đề (contenteditable) trong dialog Details.
    "title_box": [
        "ytcp-social-suggestions-textbox[label*='title'] #textbox",
        "ytcp-mention-textbox[label*='title'] #textbox",
        "#title-textarea #textbox",
        "div#textbox[aria-label*='title']",
        "div#textbox[aria-label*='tiêu đề']",
        "#textbox[contenteditable='true']",
    ],
    # Ô mô tả (contenteditable).
    "description_box": [
        "ytcp-social-suggestions-textbox[label*='description'] #textbox",
        "ytcp-mention-textbox[label*='description'] #textbox",
        "#description-textarea #textbox",
        "div#textbox[aria-label*='description']",
        "div#textbox[aria-label*='mô tả']",
    ],
    # Nút "Hiện thêm" (Show more) mở phần cấu hình mở rộng.
    "show_more": [
        "ytcp-button#toggle-button",
        "#toggle-button",
        "button:has-text('Show more')",
        "button:has-text('Hiện thêm')",
        "ytcp-button:has-text('Show more')",
        "ytcp-button:has-text('Hiện thêm')",
    ],
    # "Không, đây không phải nội dung dành cho trẻ em" (Made for kids = No).
    "mfk_no": [
        "tp-yt-paper-radio-button[name='VIDEO_MADE_FOR_KIDS_NOT_MFK']",
        "#audience #radioContainer tp-yt-paper-radio-button:nth-of-type(2)",
        "tp-yt-paper-radio-button:has-text(\"not Made for Kids\")",
        "tp-yt-paper-radio-button:has-text('không dành cho trẻ em')",
        "[name='VIDEO_MADE_FOR_KIDS_NOT_MFK']",
    ],
    # Ô nhập Tags (thẻ) — trong phần Show more.
    "tags_box": [
        "ytcp-form-input-container#tags-container #text-input",
        "input[aria-label*='tags']",
        "input[aria-label*='thẻ']",
        "#tags-container input",
    ],
    # Nội dung có yếu tố AI / Altered content: chọn "Có" (Yes).
    # Nút mở phần Altered content (nếu là 1 dòng bấm để mở radios).
    "altered_content_section": [
        "#altered-content",
        "ytcp-button:has-text('Altered content')",
        "*:has-text('Altered content')",
        "*:has-text('Nội dung đã bị thay đổi')",
    ],
    "altered_content_yes": [
        "tp-yt-paper-radio-button[name='VIDEO_ALTERED_CONTENT_YES']",
        "#altered-content tp-yt-paper-radio-button:has-text('Yes')",
        "tp-yt-paper-radio-button:has-text('Yes')",
        "tp-yt-paper-radio-button:has-text('Có')",
        "[name='VIDEO_ALTERED_CONTENT_YES']",
    ],
    # Ngôn ngữ video → dropdown → English (United States).
    "video_language_dropdown": [
        "#video-language ytcp-dropdown-trigger",
        "ytcp-form-select#video-language",
        "#video-language",
    ],
    "video_language_en_us": [
        "tp-yt-paper-item:has-text('English (United States)')",
        "[role='option']:has-text('English (United States)')",
        "tp-yt-paper-item:has-text('English (US)')",
        "*:has-text('English (United States)')",
    ],
    # Đóng dialog upload → YouTube TỰ LƯU thành Draft (bản nháp). Đây là bước
    # "lưu nháp": KHÔNG bao giờ bấm Next tới Publish/Xuất bản.
    "close_dialog": [
        "ytcp-button#close-button",
        "#close-button",
        "button[aria-label='Close']",
        "button[aria-label='Đóng']",
        "ytcp-icon-button[aria-label='Close']",
    ],
    # Hộp thoại xác nhận sau khi đóng: nút "Lưu bản nháp / Đã lưu dưới dạng nháp"
    # hoặc chỉ cần đóng. Xác nhận đã lưu draft (nếu hiện).
    "saved_draft_confirm": [
        "*:has-text('saved as a draft')",
        "*:has-text('lưu dưới dạng bản nháp')",
        "*:has-text('Draft saved')",
    ],
    # Xác nhận đã đăng nhập Studio (avatar / nút Create hiện ra).
    "studio_ready": [
        "ytcp-button#create-icon",
        "#create-icon",
        "button[aria-label='Create']",
        "button[aria-label='Tạo']",
    ],
    # Trạng thái xử lý upload (chờ upload xong mới điền/đóng an toàn).
    "upload_progress": [
        ".progress-label",
        "ytcp-video-upload-progress",
        "span.ytcp-video-upload-progress",
    ],
}


# ════════════════════════════════════════════════════════════════
# SUNO — Tạo nhạc nền (STEP 0 của pipeline). Nguồn nhạc: Suno.com.
# Người dùng có gói Premier, làm nhạc instrumental relaxing/Lofi, mỗi
# project cần ĐÚNG 15 file WAV tải trực tiếp từ Suno (không MP3, không
# đổi đuôi, không tự convert). Tất cả selector/tham số gom ở đây.
# Cơ sở: docs/suno-ui-observation.md (quan sát UI thật 2026-09-20).
# ════════════════════════════════════════════════════════════════

SUNO_URL = "https://suno.com/create"
# Profile Playwright RIÊNG cho Suno — KHÔNG dùng chung .browser_profile của
# ChatGPT/Flow, KHÔNG dùng chung session với extension Chrome cá nhân.
SUNO_PROFILE_DIR = _ROOT / ".browser_profile_suno"
# State/manifest bền vững cho batch Suno (chống trùng, resume). Ngoài thư mục
# media auto-watch để input chỉ publish khi cả batch sẵn sàng.
SUNO_STAGING_ROOT = _ROOT / "data" / "suno_batches"


@dataclass
class SunoConfig:
    """Tham số STEP 0 (mục E của spec). Đây là DEFAULT; có thể override per-batch
    khi người dùng bấm Start (preset, target, model, limits...)."""
    source:              str  = "suno"          # 'local' (import tay) | 'suno'
    target_tracks:       int  = 15              # ĐÚNG 15 output cho mỗi project
    preset:              str  = "ambient"       # key trong SUNO_PRESETS
    preferred_model:     str  = "v6"            # map tới mục 'v6' (Pro) ở ALL MODELS
    allow_model_fallback: bool = False          # False = không chấp nhận model khác
    instrumental:        bool = True            # True ⇒ để Lyrics TRỐNG (không có toggle)
    allow_bass:          bool = False           # không có UI switch → đưa vào Exclude styles
    allow_drums:         bool = False           # không có UI switch → đưa vào Exclude styles
    generation_strategy: str  = "paired_outputs"  # 2 bài/request nhưng CHỈ chọn 1 (2 bài na ná) → cần 15 request cho 15
    variety:             int  = 0               # chỉ set nếu UI có control (Suno KHÔNG có)
    max_mode:            bool = False           # Max Mode = Off theo mặc định
    concurrency_per_account: int = 1            # 1 worker/profile — lock chống 2 worker
    auto_continue_workflow: bool = True         # sau import → chạy tiếp pipeline hiện có
    preferred_duration_seconds_min: int = 180   # 3:00 (soft — Duration Custom)
    preferred_duration_seconds_max: int = 300   # 5:00 (soft)
    minimum_duration_seconds: int = 120         # WAV ngắn hơn 2:00 bị loại
    # ── Ngân sách / chốt chặn an toàn (không tự mua thêm credit) ──
    max_create_actions:      int = 15           # mỗi Create chọn 1 bài → cần 15 lần Create cho 15 bài
    max_generation_credits:  int = 100          # trần credit tiêu cho 1 batch
    max_new_song_downloads:  int = 15           # trần số WAV tải về/batch


# Preset lưu NGUYÊN VĂN Styles + Exclusions tiếng Anh đã gửi (mục E). KHÔNG
# sửa chữ. Lyrics luôn để trống ở chế độ instrumental. allow_bass/allow_drums
# đưa vào Exclude styles vì Suno không có switch bật/tắt stem lúc tạo.
SUNO_PRESETS: dict[str, dict] = {
    "ambient": {
        "label": "Ambient (beatless, no drum — slow tempo)",
        "styles": (
            "Instrumental ambient music, beatless and drumless, very slow tempo "
            "around 55-65 BPM, soft evolving pads and gentle textures, warm and "
            "spacious atmosphere, long sustained tones, subtle melodic movement, "
            "deep reverb and wide stereo field, calm meditative mood, smooth "
            "gradual build, soft natural ending. "
            "Purely instrumental, no vocals, no accompaniment."
        ),
        "exclusions": (
            "vocals, singing, spoken word, humming, chanting, choir, vocal samples, "
            "drums, percussion, kick, snare, hi-hats, 808, beat, "
            "harsh distortion, aggressive drops"
        ),
    },
    "lofi": {
        "label": "Lofi (beat nhẹ — slow tempo)",
        "styles": (
            "Instrumental lo-fi music with a gentle, laid-back beat, slow tempo "
            "around 70-80 BPM, soft mellow chords, warm tape and vinyl texture, "
            "relaxed swing groove, subtle rounded bassline, dusty drums kept light "
            "and soft, cozy late-night mood, smooth transitions, soft natural "
            "ending. Purely instrumental, no vocals."
        ),
        "exclusions": (
            "vocals, singing, spoken word, humming, chanting, choir, vocal samples, "
            "harsh distortion, aggressive drops, loud percussion, heavy 808"
        ),
    },
}


# Selector Suno — best-effort từ 1 lần quan sát DOM thật (docs/suno-ui-observation.md
# §4). Suno đổi UI thường xuyên → mọi step driver phải BÁO UI_CHANGED khi thiếu
# selector, KHÔNG im lặng bỏ qua. Override qua suno_overrides.json / video_overrides.
SUNO_SELECTORS: dict[str, list[str]] = {
    # Tab chế độ tạo (role=tab). Spec gọi "Custom/Advanced" = tab "Advanced".
    "mode_advanced_tab": [
        "[role=tab][aria-selected]:has-text('Advanced')",
        "button[role=tab]:has-text('Advanced')",
        "[role=tab]:has-text('Advanced')",
    ],
    "mode_simple_tab": [
        "button[role=tab]:has-text('Simple')",
        "[role=tab]:has-text('Simple')",
    ],
    # Nút hiển thị model hiện tại (góc trên-phải panel, text = version vd 'v6').
    "model_button": [
        "button:has-text('v6-mini')",
        "button:has-text('v6-wild')",
        "button:has-text('v6')",
    ],
    # Mục chọn model 'v6' trong popover ALL MODELS.
    "model_option_v6": [
        "[role=menuitem]:has-text('v6'):not(:has-text('mini')):not(:has-text('wild'))",
        "[role=option]:has-text('v6'):not(:has-text('mini')):not(:has-text('wild'))",
        "*:has-text('Powerful. Versatile. Refined.')",
    ],
    # Ô tiêu đề bài (tùy chọn) — KHÔNG bắt buộc.
    "song_title": [
        "input[placeholder='Song Title (Optional)']",
        "input[placeholder*='Song Title']",
    ],
    # Lyrics editor — ĐỂ TRỐNG = instrumental. Chỉ dùng để KIỂM TRA đã trống.
    # Suno hiện gọi ô này là "Cowriter prompt" (textarea data-cowrite-input);
    # giữ selector cũ 'Lyrics editor' làm dự phòng cho UI phiên bản khác.
    "lyrics_editor": [
        "textarea[data-cowrite-input='true']",
        "textarea[aria-label='Cowriter prompt']",
        "[role=textbox][aria-label='Lyrics editor']",
        "div[aria-label='Lyrics editor']",
    ],
    # Ô Styles (textarea, 1000 ký tự). Placeholder là VÍ DỤ XOAY VÒNG (đổi liên
    # tục) → KHÔNG match theo placeholder. Neo theo data-testid ổn định của
    # wrapper (verified LIVE 2026-09-21), rồi maxlength=1000 làm dự phòng.
    "styles_box": [
        "[data-testid='create-form-styles-wrapper'] textarea",
        "textarea[maxlength='1000']",
        "textarea[placeholder*='city pop']",
        "textarea[placeholder*='epic build-up']",
        "textarea[data-testid='tag-input-textarea']",
    ],
    # Nút mở "More Options".
    "more_options": [
        "[role=button]:has-text('More Options')",
        "button:has-text('More Options')",
        "*:has-text('More Options')",
    ],
    # Exclude styles — nơi ghi 'no drums, no bass...' khi allow_*=False.
    "exclude_styles": [
        "input[placeholder='Exclude styles']",
        "input[placeholder*='Exclude']",
    ],
    # Duration: nút Custom/Auto + ô số giây.
    "duration_custom": ["button:has-text('Custom')", "[role=button]:has-text('Custom')"],
    "duration_auto":   ["button:has-text('Auto')", "[role=button]:has-text('Auto')"],
    "duration_seconds_input": [
        "input[type=number][placeholder='Auto']",
        "input[type=number]",
    ],
    # Max Mode Off/On.
    "max_mode_off": ["button:has-text('Off')"],
    "max_mode_on":  ["button:has-text('On')"],
    # Nút Create.
    "create_button": [
        "button[aria-label='Create song']",
        "button:has-text('Create')",
    ],
    "clear_form": ["button[aria-label='Clear all form inputs']"],
    # Credits còn lại (đọc để tiền-kiểm ngân sách).
    "credits": ["button[aria-label^='Credits remaining']"],
    # Workspace (gom 15 bài của project vào 1 workspace nếu UI hỗ trợ).
    "new_workspace": ["*:has-text('Create new workspace')"],
    # Danh sách bài + hành động per-song.
    "song_row_play": ["div[role=button][aria-label^='Play ']"],
    "song_more_options": ["button[aria-label='More options']"],
    # Menu ⋯ → Download → dialog format WAV.
    "download_entry": [
        "div.context-menu-item:has-text('Download')",
        "button[aria-label='Download']",
        "[role=menuitem]:has-text('Download')",
        "*:has-text('Download')",
    ],
    # Hộp thoại Download là DANH SÁCH TOGGLE (không phải bấm-là-tải): mỗi định
    # dạng là 1 <button>, được chọn khi bên trong có 1 <svg> dấu tích. Mặc định
    # MP3 đang bật. Quy trình chuẩn: BẬT WAV, TẮT MP3, rồi bấm nút 'Download'
    # cuối. Các nút này CHỈ nhận chuột thật (JS/locator.click không đăng ký).
    "download_dialog": [
        "[role=dialog]:has-text('Download')",
    ],
    "download_format_wav": [
        "[role=dialog] button:has-text('WAV')",
        "button:has-text('WAV')",
    ],
    "download_format_mp3": [
        "[role=dialog] button:has-text('MP3')",
        "button:has-text('MP3')",
    ],
    "download_confirm": [
        "[role=dialog] button:has-text('Download')",
        "button:has-text('Download')",
    ],
    # Giữ selector cũ để tương thích ngược (không còn dùng trực tiếp).
    "download_wav": [
        "[role=dialog] button:has-text('WAV')",
        "button:has-text('WAV')",
        "*:has-text('WAV')",
    ],

    # ── Luồng tải WAV MỚI qua Suno Studio (verified LIVE 2026-09-21) ─────
    # Quy trình: ⋯ → Edit → Open in Studio → Single-track → (chờ load) →
    # Export → Full Song → (chờ 'Song Saved') → Go to Song → (chờ hết
    # 'Preparing song for playback...') → ⋯ → Download → WAV.
    # Menu ⋯ dùng cấu trúc `div.context-menu-item > button.hxc-btn-base`
    # (KHÔNG phải role=menuitem). Submenu 'Edit' mở khi HOVER.
    "studio_edit_menu": [
        "div.context-menu-item:has-text('Edit')",
        "button.hxc-btn-base:has-text('Edit')",
    ],
    # 'Open in Studio' nằm trong submenu Edit (kèm badge 'New' → text là
    # 'Open in StudioNew', :has-text khớp chuỗi con nên vẫn trúng).
    "open_in_studio": [
        "div.context-menu-item:has-text('Open in Studio')",
        "button.hxc-btn-base:has-text('Open in Studio')",
    ],
    # Hộp thoại chọn kiểu mở trong Studio: Single-track (Use the full mix,
    # MIỄN PHÍ) vs Multi-track (Separate stems, 50 credits → KHÔNG dùng).
    # Dialog có aria-label 'Edit <title> in Studio'.
    "studio_single_track": [
        "[role=dialog] button:has-text('Single-track')",
        "button:has-text('Single-track')",
    ],
    # Nút Export (góc trên-phải editor Studio). Là tín hiệu 'đã load xong'.
    "studio_export_menu": [
        "button[aria-label='Export menu']",
        "button:has-text('Export')",
    ],
    # Menu Export → 'Full Song' (còn có 'Selected Time Range', 'Multitrack').
    "studio_full_song": [
        "div.context-menu-item:has-text('Full Song')",
        "button.hxc-btn-base:has-text('Full Song')",
    ],
    # Thông báo 'Song Saved' sau khi render xong (best-effort, để chờ/log).
    "studio_song_saved": [
        "text=Song Saved",
        "*:has-text('Song Saved')",
    ],
    # Nút 'Go to Song' xuất hiện cùng/ sau 'Song Saved'.
    "studio_go_to_song": [
        "button:has-text('Go to Song')",
        "a:has-text('Go to Song')",
        "[role=button]:has-text('Go to Song')",
        "div.context-menu-item:has-text('Go to Song')",
    ],
    # Trạng thái 'Preparing song for playback...' — CHỜ tới khi biến mất mới tải.
    "studio_preparing": [
        "text=Preparing song for playback",
        "*:has-text('Preparing song for playback')",
    ],
}


def load_overrides() -> dict:
    """Đọc video_overrides.json nếu có để override selector/tham số."""
    try:
        if OVERRIDES.exists():
            return json.loads(OVERRIDES.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def save_selector_overrides(site: str, discovered: dict[str, list[str]]) -> None:
    """Ghi/gộp các selector đã dò được vào video_overrides.json dưới
    selectors.<site>.<key>. KHÔNG xoá các key khác (giữ nguyên override sẵn có
    của site khác/tham số khác). Dùng cho tính năng tự cập nhật selector khi
    giao diện Suno đổi."""
    if not discovered:
        return
    data = load_overrides()
    selectors = data.setdefault("selectors", {})
    site_sel = selectors.setdefault(site, {})
    for key, value in discovered.items():
        site_sel[key] = value if isinstance(value, list) else [value]
    OVERRIDES.parent.mkdir(parents=True, exist_ok=True)
    OVERRIDES.write_text(
        json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def get_selectors(site: str) -> dict[str, list[str]]:
    """site = 'chatgpt' | 'gemini' | 'flow'. Merge override (nếu có) lên default."""
    table = {
        "chatgpt": CHATGPT_SELECTORS,
        "gemini":  GEMINI_SELECTORS,
        "flow":    FLOW_SELECTORS,
        "youtube": YOUTUBE_SELECTORS,
        "suno":    SUNO_SELECTORS,
    }
    base = dict(table.get(site, FLOW_SELECTORS))
    ov = load_overrides().get("selectors", {}).get(site, {})
    for k, v in ov.items():
        base[k] = v if isinstance(v, list) else [v]
    return base


# Instance mặc định dùng chung
PARAMS  = VideoParams()
BROWSER = BrowserConfig()
SUNO    = SunoConfig()
