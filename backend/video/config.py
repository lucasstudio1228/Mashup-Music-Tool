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
STYLE_VERSION = "styles-v4"
DEFAULT_STYLE = "2d"

STYLES: dict[str, dict[str, str]] = {
    "2d": {
        "label": "2D (tranh vẽ / anime / cartoon)",
        # Chèn vào ĐẦU mọi prompt ảnh.
        "prefix": (
            "TRANH HOẠT HÌNH 2D (2D animation / cartoon / anime-style "
            "illustration), vẽ tay nét mềm, tô màu phẳng kiểu cel-shading, màu "
            "pastel dịu, ánh sáng điện ảnh. ĐÂY LÀ TRANH HOẠT HÌNH 2D, KHÔNG "
            "PHẢI ẢNH CHỤP THẬT. "
        ),
        # Chèn vào CUỐI mọi prompt ảnh.
        "negative": (
            " || Phong cách BẮT BUỘC: hoạt hình 2D vẽ tay / cartoon / anime "
            "(flat cel-shading). TUYỆT ĐỐI KHÔNG photorealistic, KHÔNG ảnh chụp "
            "thật (photo/photograph/realistic photo), KHÔNG người/da/vải/tóc "
            "chân thực như đời thật, KHÔNG 3D render thực, KHÔNG hyperrealism. "
            "Nếu phân vân, luôn nghiêng về nét vẽ tay 2D phẳng."
        ),
        # Đưa vào lời nhắc AI viết prompt (prompt_gen).
        "brief": (
            "tranh hoạt hình 2D vẽ tay / cartoon / anime-style illustration, "
            "tô màu phẳng cel-shading, nét viền mềm, màu pastel dịu"
        ),
        # Gợi ý phong cách gắn vào prompt chuyển động của clip (Flow/Veo).
        "motion": "2D hand-drawn animated cartoon look",
    },
    "3d": {
        "label": "3D (Pixar / Disney CGI)",
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
}


def normalize_style(style: str | None) -> str:
    """Chuẩn hoá key phong cách về '2d'/'3d'; giá trị lạ → DEFAULT_STYLE."""
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
# 20 ảnh là 20 shot của CÙNG một phim hoạt hình chill. Gemini tạo tuần tự trong
# một cuộc trò chuyện nên mọi prompt sau đều yêu cầu dùng ảnh trước làm reference.
_CONTINUITY = (
    "Đây là shot tiếp theo của CÙNG MỘT phim hoạt hình chill chủ đề '{topic}'. "
    "Giữ chính xác CÙNG nhân vật chính: một người trẻ tóc đen ngắn hơi rối, "
    "áo hoodie xanh sage rộng, quần kem, giày trắng và tai nghe màu be; giữ "
    "nguyên khuôn mặt, vóc dáng, trang phục và tỉ lệ nhân vật ở mọi shot. Giữ "
    "CÙNG bối cảnh: căn cabin gỗ cạnh hồ trong thung lũng rừng thông, cửa sổ "
    "lớn, bàn gỗ, đèn vàng, cây cảnh, cốc trà và chú mèo tam thể. Cùng thiết "
    "kế kiến trúc, vị trí đồ vật, bảng màu xanh sage–kem–vàng hổ phách, ánh "
    "sáng hoàng hôn chuyển dần sang đêm. Cinematic 2D/3D animation, nét mềm, "
    "cozy, lofi, serene, khung hình 16:9. Dùng các ảnh đã tạo trước trong cuộc "
    "trò chuyện làm tham chiếu continuity nghiêm ngặt. KHÔNG thêm nhân vật mới, "
    "KHÔNG đổi trang phục, KHÔNG chữ, logo hay watermark. "
)


def _scene(description: str) -> str:
    return _CONTINUITY + description


DEFAULT_PROMPTS: dict[str, str] = {
    "0": (
        "Tạo thumbnail YouTube 16:9 mở đầu cho phim hoạt hình chill chủ đề "
        "'{topic}'. Nhân vật chính tóc đen ngắn hơi rối, áo hoodie xanh sage, "
        "quần kem, giày trắng, tai nghe be đang ngồi bên cửa sổ lớn trong cabin "
        "gỗ cạnh hồ; mèo tam thể nằm bên cạnh, rừng thông và núi phản chiếu ngoài "
        "hồ lúc hoàng hôn. Cozy cinematic animation, màu sage–kem–vàng hổ phách. "
        "BẮT BUỘC có typography tiếng Anh rõ, đúng chính tả: tiêu đề lớn "
        "\"{topic}\" và dòng phụ \"{keywords}\". Chữ dễ đọc, tương phản tốt, "
        "không che mặt nhân vật. Không logo, không watermark."
    ),
    "1": _scene("Extreme-wide establishing shot từ trên cao: cabin, hồ, rừng thông và dãy núi; nhân vật nhỏ đang đi về cabin, lá và mây gợi chuyển động rất chậm."),
    "2": _scene("Wide shot ngang mặt hồ: nhân vật đi trên cầu gỗ về cabin, mèo chờ ở hiên; gợn nước và cỏ lay nhẹ, bố cục nối tiếp shot trước."),
    "3": _scene("Wide interior shot qua cửa cabin: nhân vật đặt ba lô xuống đúng cạnh bàn, mèo bước theo; hồ và rừng vẫn thấy qua cửa sổ lớn."),
    "4": _scene("Medium shot: nhân vật bật chiếc đèn bàn vàng bên cửa sổ, ánh sáng ấm dần lan trên bàn gỗ; giữ nguyên mọi vật thể và hướng ánh sáng."),
    "5": _scene("Close-up đôi tay nhân vật pha trà trong cùng chiếc cốc, hơi nước uốn chậm; tai nghe be và tay áo hoodie sage hiện rõ."),
    "6": _scene("Medium-wide shot: nhân vật ngồi vào bàn, đeo tai nghe và mở sổ; mèo cuộn tròn bên mép cửa sổ, hoàng hôn ngoài hồ đậm hơn."),
    "7": _scene("Over-the-shoulder shot từ sau vai nhân vật nhìn ra sổ và mặt hồ; bút di chuyển trên trang giấy, rèm cửa lay rất nhẹ."),
    "8": _scene("Close-up nghiêng gương mặt CÙNG nhân vật, biểu cảm bình yên và tập trung; phản chiếu rừng thông trên kính cửa, bokeh ấm."),
    "9": _scene("Medium shot bên hông: nhân vật dừng viết, nâng cốc trà và nhìn hồ; mèo vẫn nằm đúng vị trí, ánh hoàng hôn chuyển xanh tím."),
    "10": _scene("Wide shot ngoài hiên cabin: nhân vật và mèo ngồi nhìn mặt hồ, đom đóm bắt đầu xuất hiện; cabin phía sau giữ đúng thiết kế."),
    "11": _scene("Low-angle shot gần mặt nước: phản chiếu nhân vật, cabin và rừng; gợn sóng chậm, đom đóm trôi nhẹ, trời vừa lên sao."),
    "12": _scene("Medium-wide interior night shot: nhân vật trở lại bàn, đèn vàng và bầu trời xanh đêm tạo tương phản dịu; mèo ngủ cạnh sổ."),
    "13": _scene("Close-up tĩnh vật có liên kết: cốc trà, trang sổ, tai nghe be, bàn tay nhân vật và mèo trong cùng khung; hơi trà và bụi sáng gợi slow motion."),
    "14": _scene("Wide shot từ ngoài cửa sổ nhìn vào CÙNG cabin: nhân vật thư thái bên bàn, mèo ngủ, hồ và trời sao bao quanh; giữ nhịp phim êm dịu, chưa kết thúc câu chuyện."),
    "15": _scene("Top-down shot trong cùng cabin: nhân vật nằm thư giãn trên thảm cạnh cửa sổ, tai nghe be vẫn đeo, mèo tam thể nằm sát bên; ánh trăng và đèn vàng tạo mảng sáng mềm."),
    "16": _scene("Medium shot từ cuối bàn gỗ: CÙNG nhân vật khép sổ, đặt bút xuống và hít thở chậm; cốc trà, cây cảnh, đèn bàn và cửa sổ giữ đúng vị trí continuity."),
    "17": _scene("Wide shot từ hiên cabin nhìn ra hồ đêm: CÙNG nhân vật quấn chăn kem ngồi cạnh mèo, đom đóm và sương mỏng chuyển động rất chậm; cabin vẫn cùng kiến trúc."),
    "18": _scene("Close-up cinematic: bàn tay CÙNG nhân vật vuốt nhẹ mèo tam thể, tay áo hoodie sage, tai nghe be và ánh đèn hổ phách hiện rõ; bokeh hồ đêm phía sau."),
    "19": _scene("Final wide establishing shot lúc đêm sâu: CÙNG cabin gỗ phát ánh vàng bên hồ, CÙNG nhân vật thấy rõ qua cửa sổ cùng mèo; rừng thông, núi và sao giữ nguyên bố cục, cảm giác meditation và healing trọn vẹn."),
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
    "chill meditative animated film, very slow tempo, exactly one ultra-slow "
    "smooth camera move; gentle natural in-place micro-motion of the character, "
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
    # None = dùng Chromium bundled của Playwright (ổn định nhất, chạy
    # `playwright install chromium` 1 lần). Đặt "chrome" nếu muốn dùng Google
    # Chrome đã cài; "msedge" cho Edge. (Cốc Cốc không hỗ trợ trực tiếp.)
    chrome_channel:  str | None = None


# ── URL ─────────────────────────────────────────────────────────
CHATGPT_URL = "https://chatgpt.com/"
GEMINI_URL  = "https://gemini.google.com/app"
FLOW_URL    = "https://flow.google.com/"

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


def load_overrides() -> dict:
    """Đọc video_overrides.json nếu có để override selector/tham số."""
    try:
        if OVERRIDES.exists():
            return json.loads(OVERRIDES.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def get_selectors(site: str) -> dict[str, list[str]]:
    """site = 'chatgpt' | 'gemini' | 'flow'. Merge override (nếu có) lên default."""
    table = {
        "chatgpt": CHATGPT_SELECTORS,
        "gemini":  GEMINI_SELECTORS,
        "flow":    FLOW_SELECTORS,
    }
    base = dict(table.get(site, FLOW_SELECTORS))
    ov = load_overrides().get("selectors", {}).get(site, {})
    for k, v in ov.items():
        base[k] = v if isinstance(v, list) else [v]
    return base


# Instance mặc định dùng chung
PARAMS  = VideoParams()
BROWSER = BrowserConfig()
