"""
video/service.py — Điều phối pipeline Video:
  1) Tạo 20 ảnh liên kết (Gemini) → media/<project>/images/0..19.png
  2) Tạo 20 clip 1:1 (Flow/Veo)   → media/<project>/clips/clip_00..19.mp4
  3) Shuffle T+5 theo duration audio + ghép + gắn nhạc → media/<pid>/final/final.mp4

Chạy trong worker thread (Playwright sync API + ffmpeg).

⚠️  Quy ước tham số: job_manager gọi fn(project_id, progress_cb, *args).
    Vì vậy MỌI hàm step_* phải có chữ ký (project_id, progress_cb, ...extra).
"""
from __future__ import annotations
import json
import random
from pathlib import Path
from typing import Callable, Optional

from . import config
from .shuffle import build_video_sequence, build_chained_sequence
from .assembler import (assemble_video, assemble_video_blend, probe_duration,
                        overlay_title_on_clip)

_ROOT = Path(__file__).parent.parent.parent
OUTPUTS_ROOT = _ROOT / "outputs"       # nơi phần Audio lưu mix.wav


def _thumbnail_keywords(n: int = 3) -> str:
    kws = (config.load_overrides().get("thumbnail_keywords")
           or config.THUMBNAIL_KEYWORDS)
    k = min(n, len(kws))
    return " · ".join(random.sample(list(kws), k)) if k else ""


def _resolve_prompts(
    project_id: int,
    project_name: str | None,
    idea: str | None,
    progress_cb: Callable[[str, float], None] | None = None,
    style_key: str | None = None,
) -> Optional[dict]:
    """
    Quyết định bộ prompt tạo ảnh:
      - CÓ ý tưởng → nhờ OpenAI (GPT) sinh prompt bám sát ý tưởng, lưu vào
        images/prompts.json. Nếu đã có prompts.json từ ĐÚNG ý tưởng này thì tái
        dùng. Nếu AI thất bại → RAISE (KHÔNG fallback về nhân vật mặc định, để
        không tạo video lệch ý tưởng).
      - KHÔNG có ý tưởng → tái dùng prompts.json cũ nếu có; nếu không → None
        (driver dùng DEFAULT_PROMPTS = bộ phim mặc định).
    """
    def _log(m: str):
        if progress_cb:
            progress_cb(m, 1.0)

    img_dir = config.images_dir(project_id, project_name)
    img_dir.mkdir(parents=True, exist_ok=True)
    prompts_path = img_dir / "prompts.json"
    idea = (idea or "").strip()
    style_key = config.normalize_style(style_key)

    def _cache_ok(saved: dict) -> bool:
        # Cache chỉ dùng lại khi ĐÚNG phong cách + đúng phiên bản style hiện tại.
        return (saved.get("style_version") == config.STYLE_VERSION
                and config.normalize_style(saved.get("style_key")) == style_key)

    if not idea:
        # Không có ý tưởng mới → dùng lại prompts.json nếu tồn tại VÀ đúng phong
        # cách hiện tại. Cache phong cách cũ (vd 2D/photoreal) → bỏ, để driver
        # rơi về DEFAULT_PROMPTS (đã kèm wrap_style 3D) thay vì body cũ.
        if prompts_path.exists():
            try:
                saved = json.loads(prompts_path.read_text(encoding="utf-8"))
                pr = saved.get("prompts")
                if isinstance(pr, dict) and pr and _cache_ok(saved):
                    _log("Dùng lại bộ prompt đã lưu từ ý tưởng trước.")
                    return {str(k): v for k, v in pr.items()}
                if isinstance(pr, dict) and pr:
                    _log("Bộ prompt cũ khác phong cách hiện tại — bỏ qua cache.")
            except Exception:
                pass
        return None

    # Có ý tưởng mới → sinh prompt bằng OpenAI (GPT).
    from backend.core_bridge import get_api_config_from_db
    from backend.database import DB_PATH
    from .prompt_gen import generate_prompts

    # Nếu đã có prompts.json sinh từ ĐÚNG ý tưởng này → tái dùng (resume): giữ
    # nhân vật nhất quán và khỏi gọi lại API. Cache khác ý tưởng thì BỎ, không
    # được dùng vì sẽ lệch idea hiện tại.
    if prompts_path.exists():
        try:
            saved = json.loads(prompts_path.read_text(encoding="utf-8"))
            same_idea = (saved.get("idea") or "").strip() == idea
            if same_idea and _cache_ok(saved):
                pr = saved.get("prompts")
                if isinstance(pr, dict) and pr:
                    _log("Dùng lại bộ prompt đã sinh từ đúng ý tưởng này.")
                    return {str(k): v for k, v in pr.items()}
            elif same_idea:
                # Đúng ý tưởng nhưng prompt sinh theo phong cách CŨ (vd đổi
                # 2D↔3D) → sinh lại theo phong cách hiện tại.
                _log("Ý tưởng khớp nhưng khác phong cách — sinh lại prompt.")
        except Exception:
            pass

    P = config.PARAMS
    title = (project_name or "Healing").strip()
    result = generate_prompts(
        idea=idea,
        title=title,
        keywords=_thumbnail_keywords(),
        image_count=P.image_count,
        aspect_ratio=P.aspect_ratio,
        style=config.style_info(style_key)["brief"],
        api_config=get_api_config_from_db(str(DB_PATH)),
        log=lambda m: _log(m),
    )
    if result:
        prompts, motions = result
        try:
            prompts_path.write_text(
                json.dumps({"idea": idea, "title": title,
                            "style_version": config.STYLE_VERSION,
                            "style_key": style_key,
                            "prompts": prompts,
                            "motions": motions},
                           ensure_ascii=False, indent=2),
                encoding="utf-8")
        except Exception:
            pass
        return prompts

    # QUAN TRỌNG — người dùng ĐÃ nhập ý tưởng nhưng AI không sinh được prompt.
    # TUYỆT ĐỐI KHÔNG âm thầm fallback về DEFAULT_PROMPTS (nhân vật cabin/hoodie/
    # mèo) vì sẽ tạo cả 20 ảnh + 20 clip SAI HOÀN TOÀN ý tưởng — đúng lỗi người
    # dùng gặp. Dừng có thông báo rõ để họ kiểm tra API rồi thử lại.
    raise RuntimeError(
        "AI chưa viết được prompt từ ý tưởng của bạn nên đã DỪNG để tránh tạo "
        "video sai nhân vật/bối cảnh. Hãy kiểm tra API key & model ở ⚙️ Settings "
        "(model phải là GPT, vd gpt-4o-mini/gpt-4o), rồi bấm tạo lại. "
        "Nếu muốn dùng bộ phim mặc định, hãy để trống ô ý tưởng."
    )


def find_latest_audio(project_id: int,
                      project_name: str | None = None) -> Optional[str]:
    """Nhạc nền mới nhất cho video: xét CẢ mix.wav (phần Audio) LẪN file upload
    trực tiếp (media/<project>/uploads/*). Trả về file có mtime mới nhất — nên
    upload 1 track dài xong là dùng ngay được, không cần render mix 15 track."""
    candidates: list[Path] = []

    pdir = OUTPUTS_ROOT / str(project_id)
    if pdir.exists():
        candidates.extend(pdir.glob("*/mix.wav"))

    up = config.uploads_dir(project_id, project_name)
    if up.exists():
        candidates.extend(p for p in up.iterdir()
                          if p.is_file() and p.suffix.lower() in config.AUDIO_EXTS)

    if not candidates:
        return None
    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return str(candidates[0])


def list_media(project_id: int, project_name: str | None = None) -> dict:
    """Tổng quan trạng thái media của project (để UI hiển thị)."""
    img_dir = config.images_dir(project_id, project_name)
    clip_dir = config.clips_dir(project_id, project_name)
    final_d = config.final_dir(project_id, project_name)
    final = final_d / "final.mp4"
    thumb = final_d / "thumbnail.png"
    images = sorted(str(p) for p in img_dir.glob("*.png")) if img_dir.exists() else []
    clips = sorted(str(p) for p in clip_dir.glob("clip_*.mp4")) if clip_dir.exists() else []
    audio = find_latest_audio(project_id, project_name)
    audio_dur = None
    if audio:
        try:
            audio_dur = probe_duration(audio)
        except Exception:
            audio_dur = None
    return {
        "images": images,
        "image_count": len(images),
        "clips": clips,
        "clip_count": len(clips),
        "final_exists": final.exists(),
        "final_path": str(final) if final.exists() else None,
        "thumbnail_path": str(thumb) if thumb.exists() else None,
        "audio_path": audio,
        "audio_duration": audio_dur,
    }


# ── Các bước riêng lẻ (progress_cb luôn ở vị trí thứ 2) ─────────

def step_images(project_id: int,
                progress_cb: Callable[[str, float], None],
                topic: str,
                mode: str = "restart",
                project_name: str | None = None,
                idea: str | None = None,
                style_key: str | None = None) -> list[str]:
    # Tạo ảnh bằng Gemini web (model 3.1 Pro - tư duy mở rộng).
    # mode="resume": bỏ qua ảnh đã có, chỉ tạo ảnh còn thiếu.
    # idea: ý tưởng người dùng → Claude viết prompt bám theo (đồng bộ nhân vật).
    # style_key: "2d"/"3d" → quyết định phong cách ép vào mọi prompt ảnh.
    from .gemini_driver import generate_images
    style_key = config.normalize_style(style_key)
    prompts_override = _resolve_prompts(project_id, project_name, idea,
                                        progress_cb, style_key)
    # Ảnh 0 giữ SẠCH (không chữ). Tiêu đề chỉ overlay ở bước ghép video
    # (step_assemble) lên clip intro + thumbnail.png — tránh chữ nướng sẵn bị
    # Veo tạo lại thành bóng ma/chồng chéo với overlay ghép.
    return generate_images(project_id, topic, config.PARAMS, progress_cb,
                           resume=(mode == "resume"), project_name=project_name,
                           prompts_override=prompts_override, style=style_key)


def step_clips(project_id: int,
               progress_cb: Callable[[str, float], None],
               mode: str = "restart",
               project_name: str | None = None,
               style_key: str | None = None) -> list[str]:
    # mode="resume": giữ clip đã tạo, chỉ tạo tiếp clip còn thiếu.
    # style_key: "2d"/"3d" → gợi ý phong cách gắn vào prompt chuyển động.
    from .flow_driver import generate_clips
    return generate_clips(project_id, config.PARAMS, progress_cb,
                          resume=(mode == "resume"), project_name=project_name,
                          style=config.normalize_style(style_key))


def step_assemble(project_id: int,
                  progress_cb: Callable[[str, float], None],
                  audio_path: Optional[str] = None,
                  seed: Optional[int] = None,
                  project_name: str | None = None) -> dict:
    audio = audio_path or find_latest_audio(project_id, project_name)
    if not audio:
        raise RuntimeError("Không tìm thấy audio. Hãy render 1 bản mix ở phần "
                           "Audio, HOẶC upload 1 track nhạc nền, trước khi ghép "
                           "video (hoặc chỉ định audio_path).")
    P = config.PARAMS
    clip_dir = config.clips_dir(project_id, project_name)
    expected = [clip_dir / f"clip_{i:02d}.mp4"
                for i in range(P.total_clips)]
    missing = [p.name for p in expected if not p.exists()]
    if missing:
        raise RuntimeError(
            f"Chưa đủ {P.total_clips} clip 1:1 để ghép; còn thiếu: "
            f"{', '.join(missing)}.")
    clips = [str(p) for p in expected]

    dur = probe_duration(audio)

    # clip_00 là intro và chỉ xuất hiện đúng MỘT lần trong toàn video. Các clip
    # 01..40 (40 cảnh) được trộn ngẫu nhiên thành cycle riêng rồi loop; mỗi clip
    # xuất hiện đúng một lần/cycle. Vì cycle có 40 clip phân biệt nên cả trong
    # cycle lẫn qua ranh giới loop đều thỏa T+10 (không lặp trong 10 clip kế).
    import random
    rest = clips[1:]
    random.Random(seed).shuffle(rest)

    # Intro = clip_00. Veo tạo lại cảnh từ ảnh 0 nên MẤT tiêu đề đã nướng trong
    # ảnh 0 → chồng lại tiêu đề (khớp thumbnail) lên clip intro bằng font thật.
    intro = clips[0]
    title = (project_name or "Healing").strip()
    subtitle = _thumbnail_keywords()
    titled_intro = config.final_dir(project_id, project_name) / "_intro_titled.mp4"
    if title and overlay_title_on_clip(
            intro, title, subtitle, str(titled_intro),
            log=lambda m: progress_cb(m, 4.0)):
        intro = str(titled_intro)

    # Thumbnail riêng: overlay tiêu đề (CÙNG title/subtitle với intro) lên ảnh 0
    # SẠCH → final/thumbnail.png. Dùng chung 1 subtitle nên chữ khớp intro video.
    try:
        import shutil
        from .thumbnail import render_title
        src0 = config.images_dir(project_id, project_name) / "0.png"
        if title and src0.exists():
            thumb = config.final_dir(project_id, project_name) / "thumbnail.png"
            shutil.copyfile(src0, thumb)
            render_title(thumb, title, subtitle,
                         log=lambda m: progress_cb(m, 4.5))
    except Exception as e:
        progress_cb(f"Bỏ qua tạo thumbnail.png ({e})", 4.5)
    seq = [intro, *rest]
    progress_cb(
        f"Intro clip 00 một lần; shuffle T+{P.t_window} cycle "
        f"{len(seq) - 1} clip còn lại, blend 0.7x, lặp đến {dur:.0f}s", 5.0)

    out = config.final_dir(project_id, project_name) / "final.mp4"
    return assemble_video_blend(
        seq, audio, str(out), target_seconds=dur,
        blend_seconds=P.blend_seconds, slow_speed=P.slow_speed,
        progress_cb=lambda m, p: progress_cb(m, 5.0 + p * 0.95),
    )


# ── Pipeline đầy đủ ─────────────────────────────────────────────

def run_full_video(project_id: int,
                   progress_cb: Callable[[str, float], None],
                   topic: str,
                   audio_path: Optional[str] = None,
                   seed: Optional[int] = None,
                   project_name: str | None = None,
                   idea: str | None = None,
                   style_key: str | None = None) -> dict:
    """Chạy trọn: ảnh → clip → ghép. progress_cb(msg, percent 0..100)."""
    def scaled(lo, hi):
        return lambda m, p: progress_cb(m, lo + (p / 100.0) * (hi - lo))

    style_key = config.normalize_style(style_key)
    progress_cb("Bắt đầu — tạo ảnh", 1.0)
    step_images(project_id, scaled(1.0, 30.0), topic,
                project_name=project_name, idea=idea, style_key=style_key)

    progress_cb("Tạo clip video (Flow/Veo)", 30.0)
    step_clips(project_id, scaled(30.0, 80.0), project_name=project_name,
               style_key=style_key)

    progress_cb("Ghép video + gắn nhạc", 80.0)
    result = step_assemble(project_id, scaled(80.0, 100.0),
                           audio_path=audio_path, seed=seed,
                           project_name=project_name)
    progress_cb("Hoàn thành", 100.0)
    return result


def run_video_from_images(project_id: int,
                          progress_cb: Callable[[str, float], None],
                          audio_path: Optional[str] = None,
                          seed: Optional[int] = None,
                          project_name: str | None = None,
                          style_key: str | None = None,
                          clips_mode: str = "restart") -> dict:
    """Tạo lại VIDEO từ ảnh ĐÃ CÓ (KHÔNG tạo lại ảnh): clip → ghép.
    clips_mode="restart": tạo lại toàn bộ clip từ ảnh sẵn có; "resume": chỉ tạo
    tiếp clip còn thiếu. progress_cb(msg, percent 0..100)."""
    def scaled(lo, hi):
        return lambda m, p: progress_cb(m, lo + (p / 100.0) * (hi - lo))

    style_key = config.normalize_style(style_key)
    mode = "resume" if clips_mode == "resume" else "restart"
    progress_cb("Tạo clip video từ ảnh đã có (Flow/Veo)", 1.0)
    step_clips(project_id, scaled(1.0, 80.0), mode=mode,
               project_name=project_name, style_key=style_key)

    progress_cb("Ghép video + gắn nhạc", 80.0)
    result = step_assemble(project_id, scaled(80.0, 100.0),
                           audio_path=audio_path, seed=seed,
                           project_name=project_name)
    progress_cb("Hoàn thành", 100.0)
    return result
