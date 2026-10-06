"""
video/service.py — Điều phối pipeline Video:
  0) Chuẩn bị hồ sơ chung + prompt đầy đủ và kiểm tra chống trùng.
  1) Tạo 3 ảnh (Gemini): 0 = thumbnail, 1 = bảng phân tích nhân vật chính,
     2 = bảng phân tích linh thú.
  2) Tạo config.PARAMS.total_clips clip (Flow): MỌI clip nạp ảnh 1 + ảnh 2 làm
     nguyên liệu, chỉ khác prompt bối cảnh.
  3) Shuffle theo t_window + fade + gắn nhạc → final/final.mp4.

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
    *,
    allow_rebuild: bool = False,
) -> dict:
    from .prompt_workflow import prepare
    saved = prepare(project_id, project_name, idea, style_key,
                    log=lambda msg: progress_cb(msg, 1.0) if progress_cb else None,
                    allow_rebuild=allow_rebuild)
    return saved["prompts"]


def step_prepare_prompts(project_id: int, progress_cb, project_name=None,
                         idea=None, style_key=None) -> dict:
    prompts = _resolve_prompts(project_id, project_name, idea, progress_cb, style_key)
    progress_cb("Đã chuẩn bị và kiểm tra bộ prompt; chưa tạo ảnh/video.", 100)
    return {"count": len(prompts)}


def _validate_saved_workflow(project_id: int, project_name: str | None) -> dict:
    from .prompt_workflow import ensure_english_manifest, validate_manifest
    # Hồ sơ cũ còn tiếng Việt → tự dịch sang tiếng Anh trước khi dùng.
    saved = ensure_english_manifest(project_id, project_name)
    if saved is None:
        raise RuntimeError("Chưa có bộ prompt đã lưu. Hãy chuẩn bị prompt trước khi tạo media.")
    validate_manifest(project_id, saved)
    return saved


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
    # Chỉ số ảnh/clip đang có (để UI vẽ gallery + nút "Tạo lại" từng cái).
    image_indices: list[int] = []
    if img_dir.exists():
        for p in img_dir.glob("*.png"):
            try:
                image_indices.append(int(p.stem))
            except ValueError:
                pass
    clip_indices: list[int] = []
    if clip_dir.exists():
        for p in clip_dir.glob("clip_*.mp4"):
            try:
                clip_indices.append(int(p.stem.split("_")[1]))
            except (ValueError, IndexError):
                pass
    image_indices.sort()
    clip_indices.sort()
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
        "image_indices": image_indices,
        "clips": clips,
        "clip_count": len(clips),
        "clip_indices": clip_indices,
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
                                        progress_cb, style_key,
                                        allow_rebuild=(mode == "restart"))
    # Ảnh 0 giữ SẠCH (không chữ). Tiêu đề chỉ overlay ở bước ghép video
    # (step_assemble) lên clip intro + thumbnail.png — tránh chữ nướng sẵn bị
    # Veo tạo lại thành bóng ma/chồng chéo với overlay ghép.
    return generate_images(project_id, topic, config.PARAMS, progress_cb,
                           resume=(mode == "resume"), project_name=project_name,
                           prompts_override=prompts_override, style=style_key)


def _clip_generator():
    """Hàm tạo clip theo máy đang chọn — cùng chữ ký generate_clips."""
    engine = config.clip_engine()
    if engine == "flow":
        from .flow_driver import generate_clips
        return generate_clips
    if engine == "muse":
        from .muse_video import generate_clips_muse
        return generate_clips_muse
    from .gemini_video import generate_clips_gemini
    return generate_clips_gemini


def clip_engine_label() -> str:
    return config.CLIP_ENGINE_LABELS[config.clip_engine()]


def step_clips(project_id: int,
               progress_cb: Callable[[str, float], None],
               mode: str = "restart",
               project_name: str | None = None,
               style_key: str | None = None) -> list[str]:
    # mode="resume": giữ clip đã tạo, chỉ tạo tiếp clip còn thiếu.
    # style_key: "2d"/"3d" → gợi ý phong cách gắn vào prompt chuyển động.
    # Máy tạo clip: Muse.ai (mặc định) / Flow / Gemini Video (config.clip_engine()).
    generate_clips = _clip_generator()
    _validate_saved_workflow(project_id, project_name)
    return generate_clips(project_id, config.PARAMS, progress_cb,
                          resume=(mode == "resume"), project_name=project_name,
                          style=config.normalize_style(style_key))


def run_manual_clips(project_id: int,
                     progress_cb: Callable[[str, float], None],
                     project_name: str | None = None,
                     style_key: str | None = None,
                     only: Optional[list[int]] = None,
                     assemble: bool = True) -> dict:
    """"Tạo video thủ công" (bán tự động): tool mở Flow + đưa nguyên liệu, người
    dùng dán prompt + bấm Tạo, tool tải + đặt tên clip_NN.mp4. Đủ clip và có
    nhạc → ghép video (+ đăng nháp nếu project bật auto_upload)."""
    from .flow_manual import manual_clips
    _validate_saved_workflow(project_id, project_name)
    manual_clips(project_id, progress_cb, project_name=project_name,
                 style=config.normalize_style(style_key), only=only)
    total = config.PARAMS.total_clips
    clip_dir = config.clips_dir(project_id, project_name)
    missing = [k for k in range(total)
               if not (clip_dir / f"clip_{k:02d}.mp4").exists()]
    if missing:
        progress_cb(f"Còn thiếu {len(missing)} clip ({', '.join(f'{k:02d}' for k in missing)}) "
                    f"— chưa ghép video.", 100.0)
        return {"clips_missing": missing}
    if not assemble or only:
        progress_cb(f"Đã đủ {total} clip — bấm 'Ghép video' khi sẵn sàng.", 100.0)
        return {"clips_missing": []}
    if not find_latest_audio(project_id, project_name):
        progress_cb(f"Đã đủ {total} clip nhưng chưa có nhạc nền — mix audio rồi "
                    f"bấm 'Ghép video'.", 100.0)
        return {"clips_missing": [], "assembled": False}
    progress_cb("Đủ clip — ghép video + gắn nhạc…", 96.0)
    result = step_assemble(project_id,
                           lambda m, p: progress_cb(m, 96.0 + p * 0.04),
                           project_name=project_name)
    progress_cb("Hoàn thành", 100.0)
    _maybe_auto_upload(project_id, progress_cb, project_name)
    return result if isinstance(result, dict) else {"assembled": True}


def _load_saved_prompts(project_id: int,
                        project_name: str | None) -> Optional[dict]:
    """Đọc THẲNG bộ prompt ảnh đã lưu (images/prompts.json → 'prompts') để tạo
    lại đúng 1 ảnh mà KHÔNG lệ thuộc điều kiện cache phong cách. Trả None nếu
    chưa có (→ driver dùng DEFAULT_PROMPTS)."""
    prompts_path = config.images_dir(project_id, project_name) / "prompts.json"
    if not prompts_path.exists():
        return None
    try:
        saved = json.loads(prompts_path.read_text(encoding="utf-8"))
        pr = saved.get("prompts")
        if isinstance(pr, dict) and pr:
            return {str(k): v for k, v in pr.items()}
    except Exception:
        pass
    return None


def regen_image(project_id: int,
                progress_cb: Callable[[str, float], None],
                index: int,
                project_name: str | None = None,
                style_key: str | None = None) -> list[str]:
    """Tạo lại ĐÚNG 1 ảnh (ghi đè), GIỮ NGUYÊN mọi ảnh khác. Dùng lại prompt đã
    lưu để nhân vật/bối cảnh vẫn khớp cả bộ."""
    from .gemini_driver import generate_images
    style_key = config.normalize_style(style_key)
    prompts_override = _validate_saved_workflow(project_id, project_name)["prompts"]
    return generate_images(
        project_id, (project_name or "Healing"), config.PARAMS, progress_cb,
        project_name=project_name, prompts_override=prompts_override,
        style=style_key, only=[int(index)])


def regen_clip(project_id: int,
               progress_cb: Callable[[str, float], None],
               index: int,
               project_name: str | None = None,
               style_key: str | None = None) -> list[str]:
    """Tạo lại ĐÚNG 1 clip (ghi đè), GIỮ NGUYÊN mọi clip khác. Nguyên liệu mỗi
    clip là cố định (ảnh 1 + ảnh 2) nên không cần kế hoạch cũ."""
    generate_clips = _clip_generator()
    _validate_saved_workflow(project_id, project_name)
    return generate_clips(
        project_id, config.PARAMS, progress_cb, project_name=project_name,
        style=config.normalize_style(style_key), only=[int(index)])


def regen_motions(project_id: int,
                  progress_cb: Callable[[str, float], None],
                  project_name: str | None = None,
                  style_key: str | None = None) -> dict:
    """Sinh LẠI CHỈ bộ prompt cảnh (motions) từ HAI bảng nhân vật đã khoá và ghi
    đè 'motions' trong images/prompts.json. KHÔNG tạo lại ảnh/clip — lần tạo clip
    sau sẽ dùng bộ cảnh mới. Trả {"count": N} khi thành công."""
    from backend.core_bridge import get_api_config_from_db
    from backend.database import DB_PATH
    from .prompt_gen import generate_scene_prompts

    def _log(m: str):
        if progress_cb:
            progress_cb(m, 1.0)

    prompts_path = config.images_dir(project_id, project_name) / "prompts.json"
    if not prompts_path.exists():
        raise RuntimeError(
            "Chưa có bộ prompt (images/prompts.json). Hãy tạo ảnh từ 'Ý tưởng' "
            "một lần để sinh prompt, rồi mới sinh lại bộ cảnh.")
    from .prompt_workflow import ensure_english_manifest
    try:
        # tự cắt 40 → N cảnh + dịch hồ sơ cũ sang tiếng Anh
        saved = ensure_english_manifest(project_id, project_name, extend=False)
    except Exception as e:
        raise RuntimeError(f"Không đọc được prompts.json ({e}).")
    prompts = saved.get("prompts")
    if not isinstance(prompts, dict) or not prompts:
        raise RuntimeError("prompts.json không có 'prompts' hợp lệ.")
    locks = saved.get("continuity") or {}
    character_sheet = (locks.get("character_sheet") or "").strip()
    pet_sheet = (locks.get("pet_sheet") or "").strip()
    if len(character_sheet) < 20 or len(pet_sheet) < 20:
        raise RuntimeError(
            "prompts.json thiếu khoá continuity (character_sheet / pet_sheet). "
            "Hãy chuẩn bị lại bộ prompt bằng 'Làm lại từ đầu'.")
    idea = (saved.get("idea") or "").strip()
    style_key = config.normalize_style(style_key or saved.get("style_key"))
    scenes = config.PARAMS.total_clips   # sinh lại ĐỦ số cảnh theo cấu hình hiện tại

    _log(f"Sinh lại {scenes} prompt cảnh từ hai bảng nhân vật đã khoá…")
    motions = generate_scene_prompts(
        character_sheet=character_sheet, pet_sheet=pet_sheet, idea=idea,
        style=config.style_info(style_key)["brief"],
        api_config=get_api_config_from_db(str(DB_PATH)),
        scene_count=scenes, log=_log,
        instrument=(saved.get("context") or {}).get("instrument", "")
        or _project_instrument(project_id))
    if not motions or len(motions) != scenes:
        raise RuntimeError(
            "AI chưa sinh lại đủ bộ prompt cảnh (kiểm tra API key/model ở ⚙️ "
            "Settings rồi thử lại).")

    from .prompt_workflow import (validate_manifest, save_manifest,
                                  SCENE_SUFFIX)
    saved["motions"] = {k: v + SCENE_SUFFIX for k, v in motions.items()}
    if "scene_count" in saved and saved["scene_count"] != scenes:
        from .prompt_workflow import _resign
        _resign(dict(saved), saved, scenes)
        saved["scene_count"] = scenes
    saved["prompt_hashes"] = validate_manifest(project_id, saved,
                                               len(prompts), scenes)
    try:
        save_manifest(prompts_path, saved)
    except Exception as e:
        raise RuntimeError(f"Không lưu được bộ cảnh mới ({e}).")
    _log(f"Đã cập nhật {len(motions)} prompt cảnh — tạo clip lần sau sẽ dùng.")
    return {"count": len(motions)}


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
            f"Chưa đủ {P.total_clips} clip để ghép; còn thiếu: "
            f"{', '.join(missing)}.")
    clips = [str(p) for p in expected]

    dur = probe_duration(audio)

    # clip_00 là intro và chỉ xuất hiện đúng MỘT lần trong toàn video. Các clip
    # còn lại (mỗi clip một bối cảnh riêng) được trộn ngẫu nhiên thành cycle rồi
    # loop; mỗi clip xuất hiện đúng một lần/cycle. Cycle đủ nhiều clip phân biệt
    # nên cả trong cycle lẫn qua ranh giới loop đều thỏa T+10.
    import random
    rest = clips[1:]
    random.Random(seed).shuffle(rest)

    # Intro = clip_00 (dựng lại cảnh ảnh bìa từ ảnh 0 + hai bảng nhân vật) → chồng
    # tiêu đề lên bằng font thật, khớp chữ với final/thumbnail.png.
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
        render_thumbnail(project_id, project_name, subtitle,
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


def render_thumbnail(project_id: int, project_name: str | None,
                     subtitle: str | None = None, log=None) -> Optional[str]:
    """Ảnh 0 SẠCH (không chữ) + tiêu đề = tên project (font thật) →
    final/thumbnail.png. Trả đường dẫn, hoặc None nếu chưa có ảnh 0."""
    import shutil
    from .thumbnail import render_title
    title = (project_name or "Healing").strip()
    src0 = config.images_dir(project_id, project_name) / "0.png"
    if not title or not src0.exists():
        return None
    thumb = config.final_dir(project_id, project_name) / "thumbnail.png"
    thumb.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src0, thumb)
    render_title(thumb, title,
                 _thumbnail_keywords() if subtitle is None else subtitle,
                 log=log)
    return str(thumb)


def retitle_video(project_id: int,
                  progress_cb: Callable[[str, float], None],
                  project_name: str | None = None,
                  style_key: str | None = None,
                  regen_cover: bool = False) -> dict:
    """Sau khi ĐỔI TÊN project: cập nhật chữ tiêu đề trên thumbnail + intro.
    regen_cover=True: tạo lại ảnh bìa (ảnh 0, Gemini) + clip intro 00 (Flow)
    trước — prompt đã khoá giữ nguyên nên nhân vật/bối cảnh vẫn khớp cả bộ.
    Rồi vẽ lại final/thumbnail.png và (nếu đủ clip + nhạc) ghép lại final.mp4
    để chữ ở intro là tên mới. KHÔNG tự đăng YouTube (bấm «Đăng nháp» sau)."""
    def scaled(lo, hi):
        return lambda m, p: progress_cb(m, lo + (p / 100.0) * (hi - lo))

    out: dict = {"regen_cover": bool(regen_cover)}
    lo = 1.0
    if regen_cover:
        progress_cb("Tạo lại ảnh bìa (ảnh 0) bằng Gemini…", 1.0)
        regen_image(project_id, scaled(1.0, 35.0), 0, project_name, style_key)
        progress_cb(f"Tạo lại clip intro 00 ({clip_engine_label()})…", 35.0)
        regen_clip(project_id, scaled(35.0, 70.0), 0, project_name, style_key)
        lo = 70.0
    progress_cb("Vẽ lại chữ tiêu đề lên thumbnail…", lo)
    out["thumbnail"] = render_thumbnail(
        project_id, project_name, log=lambda m: progress_cb(m, lo))
    clip_dir = config.clips_dir(project_id, project_name)
    have_clips = all((clip_dir / f"clip_{i:02d}.mp4").exists()
                     for i in range(config.PARAMS.total_clips))
    audio = find_latest_audio(project_id, project_name)
    if have_clips and audio:
        progress_cb("Ghép lại video để intro mang tên mới…", lo + 2)
        out["final"] = step_assemble(project_id, scaled(lo + 2, 100.0),
                                     audio_path=audio, project_name=project_name)
        progress_cb("Xong — thumbnail + intro đã mang tên mới. Bấm «Đăng nháp "
                    "YouTube» nếu muốn cập nhật bản nháp.", 100.0)
    else:
        progress_cb("Xong — đã cập nhật thumbnail. Chưa đủ clip/nhạc nên chưa "
                    "ghép video (chữ intro sẽ đúng tên mới khi ghép).", 100.0)
    return out


# ── Pipeline đầy đủ ─────────────────────────────────────────────

def run_full_video(project_id: int,
                   progress_cb: Callable[[str, float], None],
                   topic: str,
                   audio_path: Optional[str] = None,
                   seed: Optional[int] = None,
                   project_name: str | None = None,
                   idea: str | None = None,
                   style_key: str | None = None,
                   mode: str = "restart") -> dict:
    """Chạy trọn: ảnh → clip → ghép. progress_cb(msg, percent 0..100).
    mode="resume": giữ ảnh/clip đã có, chỉ tạo phần còn thiếu (dùng khi chạy lại)."""
    def scaled(lo, hi):
        return lambda m, p: progress_cb(m, lo + (p / 100.0) * (hi - lo))

    style_key = config.normalize_style(style_key)
    mode = "resume" if mode == "resume" else "restart"
    progress_cb("Bắt đầu — tạo ảnh", 1.0)
    step_images(project_id, scaled(1.0, 30.0), topic, mode=mode,
                project_name=project_name, idea=idea, style_key=style_key)

    progress_cb(f"Tạo clip video ({clip_engine_label()})", 30.0)
    step_clips(project_id, scaled(30.0, 80.0), mode=mode,
               project_name=project_name, style_key=style_key)

    progress_cb("Ghép video + gắn nhạc", 80.0)
    result = step_assemble(project_id, scaled(80.0, 100.0),
                           audio_path=audio_path, seed=seed,
                           project_name=project_name)
    progress_cb("Hoàn thành", 100.0)
    _maybe_auto_upload(project_id, progress_cb, project_name)
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
    progress_cb(f"Tạo clip video từ ảnh đã có ({clip_engine_label()})", 1.0)
    step_clips(project_id, scaled(1.0, 80.0), mode=mode,
               project_name=project_name, style_key=style_key)

    progress_cb("Ghép video + gắn nhạc", 80.0)
    result = step_assemble(project_id, scaled(80.0, 100.0),
                           audio_path=audio_path, seed=seed,
                           project_name=project_name)
    progress_cb("Hoàn thành", 100.0)
    _maybe_auto_upload(project_id, progress_cb, project_name)
    return result


# ── Đăng nháp YouTube qua GPMLogin ──────────────────────────────

def _db():
    """Kết nối SQLite app.db (worker thread) — trả sqlite3.Connection."""
    import sqlite3
    from backend.database import DB_PATH
    con = sqlite3.connect(str(DB_PATH))
    con.row_factory = sqlite3.Row
    return con


def _project_instrument(project_id: int) -> str:
    """Nhạc cụ người dùng chọn cho project ('' nếu chưa chọn / lỗi đọc DB)."""
    con = _db()
    try:
        row = con.execute("SELECT instrument FROM project WHERE id=?",
                          (project_id,)).fetchone()
    except Exception:
        return ""
    finally:
        con.close()
    return (row["instrument"] or "") if row else ""


def _lookup_channel_mapping(instrument: str, music_style: str) -> dict | None:
    """Tra ChannelMapping theo (instrument, music_style), so khớp không phân biệt
    hoa/thường & khoảng trắng thừa. Trả dict hoặc None."""
    ins = (instrument or "").strip().lower()
    sty = (music_style or "").strip().lower()
    con = _db()
    try:
        row = con.execute(
            "SELECT * FROM channelmapping "
            "WHERE lower(trim(instrument))=? AND lower(trim(music_style))=? "
            "ORDER BY id DESC LIMIT 1",
            (ins, sty),
        ).fetchone()
        if not row:
            # 'Piano' (danh mục) ↔ 'Lofi Piano' (ánh xạ): cùng nhạc cụ + cùng thể loại.
            from .music_spec import same_instrument
            row = next((r for r in con.execute(
                "SELECT * FROM channelmapping ORDER BY id DESC").fetchall()
                if (r["music_style"] or "").strip().lower() == sty
                and same_instrument(r["instrument"] or "", ins)), None)
    except Exception:
        return None
    finally:
        con.close()
    return dict(row) if row else None


def _read_gpm_settings_db() -> tuple[str, str | None]:
    """Đọc gpm_api_base_url + gpm_exe_path từ appsettings (id=1)."""
    con = _db()
    try:
        row = con.execute(
            "SELECT gpm_api_base_url, gpm_exe_path FROM appsettings WHERE id=1"
        ).fetchone()
    except Exception:
        return ("", None)
    finally:
        con.close()
    if not row:
        return ("", None)
    return (row["gpm_api_base_url"] or "", row["gpm_exe_path"])


def _read_project_fields(project_id: int) -> dict:
    con = _db()
    try:
        row = con.execute(
            "SELECT name, instrument, music_style, auto_upload "
            "FROM project WHERE id=?", (project_id,)
        ).fetchone()
    finally:
        con.close()
    return dict(row) if row else {}


def _past_titles(profile_id: str, limit: int = 20) -> list[str]:
    con = _db()
    try:
        rows = con.execute(
            "SELECT title FROM youtubeupload WHERE profile_id=? "
            "ORDER BY id DESC LIMIT ?", (profile_id, limit)
        ).fetchall()
    except Exception:
        return []
    finally:
        con.close()
    return [r["title"] for r in rows if r["title"]]


def _record_upload(project_id: int, mapping: dict, meta: dict,
                   video_path: str, status: str,
                   error: str | None = None) -> None:
    from datetime import datetime, timezone
    con = _db()
    try:
        con.execute(
            "INSERT INTO youtubeupload "
            "(project_id, profile_id, channel_name, title, description, "
            " hashtags, video_path, status, error_message, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (project_id, mapping.get("gpm_profile_id", ""),
             mapping.get("channel_name", ""), meta.get("title", ""),
             meta.get("description", ""), meta.get("hashtags", ""),
             video_path, status, error,
             datetime.now(timezone.utc).isoformat()),
        )
        con.commit()
    except Exception:
        pass
    finally:
        con.close()


def step_upload_youtube(project_id: int,
                        progress_cb: Callable[[str, float], None],
                        project_name: str | None = None,
                        instrument: str | None = None,
                        music_style: str | None = None) -> dict:
    """Đăng NHÁP video final.mp4 lên YouTube qua profile GPMLogin khớp
    (instrument, music_style). AI viết title/description từ thumbnail. KHÔNG
    publish — chỉ lưu bản nháp."""
    from backend.core_bridge import get_api_config_from_db
    from backend.database import DB_PATH
    from . import gpm_client as gpmc
    from . import youtube_meta, youtube_driver

    # Lấy instrument/music_style: ưu tiên tham số, fallback đọc từ project.
    fields = _read_project_fields(project_id)
    instrument = (instrument if instrument is not None else fields.get("instrument")) or ""
    music_style = (music_style if music_style is not None else fields.get("music_style")) or ""
    project_name = project_name or fields.get("name")

    progress_cb("Chuẩn bị đăng nháp YouTube…", 2.0)
    if not instrument.strip() or not music_style.strip():
        raise RuntimeError(
            "Chưa nhập 'nhạc cụ' và 'phong cách nhạc' cho project — cần để chọn "
            "đúng kênh (profile GPMLogin).")

    mapping = _lookup_channel_mapping(instrument, music_style)
    if not mapping:
        raise RuntimeError(
            f"Chưa cấu hình kênh cho nhạc cụ '{instrument}' + phong cách "
            f"'{music_style}'. Vào phần Cài đặt → YouTube/GPMLogin để thêm dòng "
            f"ánh xạ (instrument + music_style → profile).")

    profile_id = mapping.get("gpm_profile_id", "")
    if not profile_id:
        raise RuntimeError("Dòng ánh xạ thiếu profile GPMLogin.")

    # File final + thumbnail
    out_dir = config.final_dir(project_id, project_name)
    final = out_dir / "final.mp4"
    thumb = out_dir / "thumbnail.png"
    if not final.exists():
        raise RuntimeError("Chưa có final.mp4 — hãy render video trước khi đăng.")

    # Track lẻ + khung thời gian của bản mix dùng làm nhạc nền → đưa vào mô tả
    # dưới dạng chapters (tên track do bước Mix tự đặt).
    meta_warnings: list[str] = []
    audio = find_latest_audio(project_id, project_name)
    tracklist = youtube_meta.load_tracklist(audio)
    if not tracklist:
        meta_warnings.append(
            "Không có dữ liệu track lẻ cho nhạc nền "
            f"({Path(audio).name if audio else 'không rõ file'}) — mô tả thiếu "
            "tracklist. Chỉ bản mix render từ tab Audio mới có mốc thời gian.")
    else:
        # Bản mix mới hơn video đã render ⇒ mốc thời gian sẽ LỆCH. Thà thiếu
        # tracklist còn hơn ghi sai giờ.
        try:
            gap = abs(probe_duration(str(final)) - probe_duration(audio))
        except Exception:
            gap = 0.0
        if gap > 5.0:
            tracklist = []
            meta_warnings.append(
                f"Bỏ tracklist: nhạc nền mới nhất ({Path(audio).name}) lệch "
                f"{gap:.0f} giây so với final.mp4 — mốc thời gian sẽ sai. "
                "Hãy render lại video từ đúng bản mix.")

    # Metadata (AI vision + bộ nhớ chống trùng)
    progress_cb("AI viết tiêu đề & mô tả từ thumbnail…", 10.0)
    api_config = get_api_config_from_db(str(DB_PATH))
    meta = youtube_meta.generate_metadata(
        tracklist=tracklist,
        thumbnail_path=str(thumb),
        channel_name=mapping.get("channel_name", ""),
        instrument=instrument, music_style=music_style,
        default_hashtags=mapping.get("default_hashtags", ""),
        past_titles=_past_titles(profile_id),
        api_config=api_config,
        log=lambda m: progress_cb(m, 12.0),
    )

    # Bật GPMLogin profile → CDP
    progress_cb("Bật GPMLogin & mở kênh YouTube…", 22.0)
    base_url, exe_path = _read_gpm_settings_db()
    client = gpmc.GPMClient(base_url or None)
    try:
        client.ensure_app_running(exe_path)   # best-effort (không raise nếu thiếu exe)
    except gpmc.GPMError as e:
        progress_cb(f"(Bỏ qua tự mở app: {e})", 22.0)

    def _do_upload(cdp: str) -> dict:
        progress_cb("Điều khiển YouTube Studio (lưu nháp)…", 26.0)
        return youtube_driver.upload_draft(
            cdp_endpoint=cdp,
            video_path=str(final),
            title=meta["title"],
            description=meta["description"],
            hashtags=meta["hashtags"],
            language=mapping.get("language", "en-US"),
            # Có file thumbnail → driver BẤM chọn hình thu nhỏ và nạp ảnh này.
            thumbnail_path=str(thumb) if thumb.exists() else None,
            progress_cb=lambda m, p: progress_cb(m, 26.0 + (p / 100.0) * 70.0),
            log=lambda m: progress_cb(m, 30.0),
            # Tài khoản có nhiều kênh → dừng nếu Studio đang mở kênh khác.
            expected_channel=mapping.get("channel_name", ""),
        )

    try:
        result = _upload_with_gpm_retry(client, profile_id, _do_upload,
                                        progress_cb)
    except Exception as e:
        _record_upload(project_id, mapping, meta, str(final),
                       status="failed", error=f"{type(e).__name__}: {e}")
        raise

    _record_upload(project_id, mapping, meta, str(final), status="draft")
    warns = meta_warnings + (result.get("warnings") or [])
    msg = "Đã lưu bản nháp trên YouTube."
    if warns:
        msg += " Lưu ý: " + " | ".join(warns)
    progress_cb(msg, 100.0)
    return {"status": "draft", "title": meta["title"],
            "channel_name": mapping.get("channel_name", ""),
            "warnings": warns}


def _upload_with_gpm_retry(client, profile_id: str,
                           do_upload: Callable[[str], dict],
                           progress_cb: Callable[[str, float], None],
                           attempts: int = 3, pause_sec: float = 15.0,
                           sleep: Callable[[float], None] | None = None) -> dict:
    """Bật profile GPMLogin → do_upload(cdp). Nếu KHÔNG NỐI ĐƯỢC CDP (trình duyệt
    của profile chết ngay sau khi bật, cổng debug không mở — «WebSocket error:
    connect ECONNREFUSED») thì đóng profile, nghỉ rồi bật lại (cổng MỚI) và thử
    lại. Lỗi SAU khi đã nối (trong YouTube Studio) KHÔNG thử lại — tránh tải
    trùng video. Luôn đóng profile khi xong."""
    import time as _time
    from .youtube_driver import CDPConnectError
    sleep = sleep or _time.sleep
    last: Exception | None = None
    for i in range(1, attempts + 1):
        if i > 1:
            progress_cb(f"Trình duyệt GPMLogin không phản hồi — đóng profile, "
                        f"bật lại (lần {i}/{attempts})…", 22.0)
        cdp = client.start_profile(profile_id)
        try:
            return do_upload(cdp)
        except CDPConnectError as e:
            last = e
        except Exception as e:
            # Cửa sổ trình duyệt GPM bị ĐÓNG giữa chừng (tay người / GPMLogin
            # tắt) → không tự thử lại (có thể người dùng chủ ý đóng).
            if "has been closed" in str(e) or type(e).__name__ == "TargetClosedError":
                raise RuntimeError(
                    "Trình duyệt GPMLogin của kênh bị ĐÓNG giữa lúc đang tải video "
                    "lên YouTube — đừng đóng cửa sổ GPM (hoặc tắt GPMLogin) khi tool "
                    "đang đăng nháp. Bấm «Đăng nháp YouTube» để chạy lại (nếu Studio còn "
                    f"bản nháp «final» dở dang thì xoá nó sau khi bản mới xong). ({e})") from e
            raise
        finally:
            client.close_profile(profile_id)
        if i < attempts:
            sleep(pause_sec)     # GPMLogin giải phóng profile chậm sau khi stop
    raise RuntimeError(
        f"{last} — đã đóng/bật lại profile {attempts} lần vẫn không nối được. "
        f"Hãy mở GPMLogin, bấm «Mở» profile này 1 lần xem trình duyệt có lên "
        f"không (lỗi proxy/nhân trình duyệt/profile hỏng), đóng nó lại rồi bấm "
        f"«Đăng nháp YouTube» lần nữa. Video đã dựng xong, không mất gì.")


def _maybe_auto_upload(project_id: int,
                       progress_cb: Callable[[str, float], None],
                       project_name: str | None) -> None:
    """Nếu project bật auto_upload → tự đăng nháp sau khi render xong. KHÔNG làm
    hỏng job render nếu upload lỗi (chỉ báo qua progress)."""
    fields = _read_project_fields(project_id)
    if not fields.get("auto_upload"):
        return
    try:
        progress_cb("Tự động đăng nháp lên YouTube…", 100.0)
        step_upload_youtube(project_id, progress_cb, project_name,
                            fields.get("instrument"), fields.get("music_style"))
    except Exception as e:
        progress_cb(f"Auto-upload YouTube lỗi (video ĐÃ render xong, bỏ qua "
                    f"đăng nháp): {type(e).__name__}: {e}", 100.0)
