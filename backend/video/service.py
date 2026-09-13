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
from .assembler import assemble_video, assemble_video_blend, probe_duration

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
) -> Optional[dict]:
    """
    Quyết định bộ prompt tạo ảnh:
      - Có ý tưởng mới → nhờ Claude sinh prompt, lưu vào images/prompts.json.
      - Không có ý tưởng → tái dùng prompts.json cũ (nếu có) để giữ đúng câu
        chuyện khi 'resume'.
      - Thất bại/không cấu hình → None (driver dùng DEFAULT_PROMPTS).
    """
    def _log(m: str):
        if progress_cb:
            progress_cb(m, 1.0)

    img_dir = config.images_dir(project_id, project_name)
    img_dir.mkdir(parents=True, exist_ok=True)
    prompts_path = img_dir / "prompts.json"
    idea = (idea or "").strip()

    if not idea:
        # Không có ý tưởng mới → dùng lại prompts.json nếu tồn tại.
        if prompts_path.exists():
            try:
                saved = json.loads(prompts_path.read_text(encoding="utf-8"))
                pr = saved.get("prompts")
                if isinstance(pr, dict) and pr:
                    _log("Dùng lại bộ prompt đã lưu từ ý tưởng trước.")
                    return {str(k): v for k, v in pr.items()}
            except Exception:
                pass
        return None

    # Có ý tưởng mới → sinh prompt bằng Claude.
    from backend.core_bridge import get_api_config_from_db
    from backend.database import DB_PATH
    from .prompt_gen import generate_prompts

    P = config.PARAMS
    title = (project_name or "Healing").strip()
    prompts = generate_prompts(
        idea=idea,
        title=title,
        keywords=_thumbnail_keywords(),
        image_count=P.image_count,
        aspect_ratio=P.aspect_ratio,
        style=P.image_style,
        api_config=get_api_config_from_db(str(DB_PATH)),
        log=lambda m: _log(m),
    )
    if prompts:
        try:
            prompts_path.write_text(
                json.dumps({"idea": idea, "title": title, "prompts": prompts},
                           ensure_ascii=False, indent=2),
                encoding="utf-8")
        except Exception:
            pass
        return prompts

    # AI thất bại nhưng đã có prompts.json cũ → dùng lại; nếu không → None.
    if prompts_path.exists():
        try:
            saved = json.loads(prompts_path.read_text(encoding="utf-8"))
            pr = saved.get("prompts")
            if isinstance(pr, dict) and pr:
                return {str(k): v for k, v in pr.items()}
        except Exception:
            pass
    return None


def find_latest_audio(project_id: int) -> Optional[str]:
    """Tìm mix.wav mới nhất của project (từ phần Audio)."""
    pdir = OUTPUTS_ROOT / str(project_id)
    if not pdir.exists():
        return None
    candidates = sorted(pdir.glob("*/mix.wav"),
                        key=lambda p: p.stat().st_mtime, reverse=True)
    return str(candidates[0]) if candidates else None


def list_media(project_id: int, project_name: str | None = None) -> dict:
    """Tổng quan trạng thái media của project (để UI hiển thị)."""
    img_dir = config.images_dir(project_id, project_name)
    clip_dir = config.clips_dir(project_id, project_name)
    final = config.final_dir(project_id, project_name) / "final.mp4"
    images = sorted(str(p) for p in img_dir.glob("*.png")) if img_dir.exists() else []
    clips = sorted(str(p) for p in clip_dir.glob("clip_*.mp4")) if clip_dir.exists() else []
    audio = find_latest_audio(project_id)
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
        "audio_path": audio,
        "audio_duration": audio_dur,
    }


# ── Các bước riêng lẻ (progress_cb luôn ở vị trí thứ 2) ─────────

def step_images(project_id: int,
                progress_cb: Callable[[str, float], None],
                topic: str,
                mode: str = "restart",
                project_name: str | None = None,
                idea: str | None = None) -> list[str]:
    # Tạo ảnh bằng Gemini web (model 3.1 Pro - tư duy mở rộng).
    # mode="resume": bỏ qua ảnh đã có, chỉ tạo ảnh còn thiếu.
    # idea: ý tưởng người dùng → Claude viết prompt bám theo (đồng bộ nhân vật).
    from .gemini_driver import generate_images
    prompts_override = _resolve_prompts(project_id, project_name, idea, progress_cb)
    return generate_images(project_id, topic, config.PARAMS, progress_cb,
                           resume=(mode == "resume"), project_name=project_name,
                           prompts_override=prompts_override)


def step_clips(project_id: int,
               progress_cb: Callable[[str, float], None],
               mode: str = "restart",
               project_name: str | None = None) -> list[str]:
    # mode="resume": giữ clip đã tạo, chỉ tạo tiếp clip còn thiếu.
    from .flow_driver import generate_clips
    return generate_clips(project_id, config.PARAMS, progress_cb,
                          resume=(mode == "resume"), project_name=project_name)


def step_assemble(project_id: int,
                  progress_cb: Callable[[str, float], None],
                  audio_path: Optional[str] = None,
                  seed: Optional[int] = None,
                  project_name: str | None = None) -> dict:
    audio = audio_path or find_latest_audio(project_id)
    if not audio:
        raise RuntimeError("Không tìm thấy audio (mix.wav). Hãy render 1 bản "
                           "mix ở phần Audio trước, hoặc chỉ định audio_path.")
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
    # 01..19 được trộn thành cycle riêng rồi loop; mỗi clip xuất hiện đúng một
    # lần/cycle nên cả trong cycle lẫn qua ranh giới loop đều thỏa T+5.
    import random
    rest = clips[1:]
    random.Random(seed).shuffle(rest)
    seq = [clips[0], *rest]
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
                   idea: str | None = None) -> dict:
    """Chạy trọn: ảnh → clip → ghép. progress_cb(msg, percent 0..100)."""
    def scaled(lo, hi):
        return lambda m, p: progress_cb(m, lo + (p / 100.0) * (hi - lo))

    progress_cb("Bắt đầu — tạo ảnh", 1.0)
    step_images(project_id, scaled(1.0, 30.0), topic,
                project_name=project_name, idea=idea)

    progress_cb("Tạo clip video (Flow/Veo)", 30.0)
    step_clips(project_id, scaled(30.0, 80.0), project_name=project_name)

    progress_cb("Ghép video + gắn nhạc", 80.0)
    result = step_assemble(project_id, scaled(80.0, 100.0),
                           audio_path=audio_path, seed=seed,
                           project_name=project_name)
    progress_cb("Hoàn thành", 100.0)
    return result
