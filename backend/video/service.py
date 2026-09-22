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
        # Bộ prompt lớn (vd 41 shot) thỉnh thoảng model bỏ sót / không đủ
        # 'motions' trong 1 lượt → nếu để trống, flow_driver rơi về chuyển động
        # camera GENERIC (mất anti-hallucination: khói/đầu-ngược/méo tay). Sinh
        # BỔ SUNG motion bằng call riêng (nhỏ & ổn định hơn) từ chính prompt.
        if len(motions) < P.image_count:
            try:
                from .prompt_gen import generate_motions_for_prompts
                _log(f"Motion {len(motions)}/{P.image_count} — sinh bổ sung riêng…")
                m2 = generate_motions_for_prompts(
                    prompts=prompts, idea=idea,
                    style=config.style_info(style_key)["brief"],
                    api_config=get_api_config_from_db(str(DB_PATH)),
                    log=lambda m: _log(m))
                if m2 and len(m2) == P.image_count:
                    motions = m2
            except Exception:
                pass
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
    prompts_override = _load_saved_prompts(project_id, project_name)
    return generate_images(
        project_id, (project_name or "Healing"), config.PARAMS, progress_cb,
        project_name=project_name, prompts_override=prompts_override,
        style=style_key, only=[int(index)])


def regen_clip(project_id: int,
               progress_cb: Callable[[str, float], None],
               index: int,
               project_name: str | None = None,
               style_key: str | None = None) -> list[str]:
    """Tạo lại ĐÚNG 1 clip (ghi đè), GIỮ NGUYÊN mọi clip khác. Cần _plan.json
    hợp lệ (flow_driver sẽ báo lỗi rõ nếu thiếu) để giữ ánh xạ ảnh↔clip 1:1."""
    from .flow_driver import generate_clips
    return generate_clips(
        project_id, config.PARAMS, progress_cb, project_name=project_name,
        style=config.normalize_style(style_key), only=[int(index)])


def regen_motions(project_id: int,
                  progress_cb: Callable[[str, float], None],
                  project_name: str | None = None,
                  style_key: str | None = None) -> dict:
    """Sinh LẠI CHỈ bộ prompt chuyển động (motions) từ prompt ảnh đã lưu và ghi
    đè 'motions' trong images/prompts.json. KHÔNG tạo lại ảnh/clip — lần tạo clip
    sau sẽ dùng motion mới. Trả {"count": N} khi thành công."""
    from backend.core_bridge import get_api_config_from_db
    from backend.database import DB_PATH
    from .prompt_gen import generate_motions_for_prompts

    def _log(m: str):
        if progress_cb:
            progress_cb(m, 1.0)

    prompts_path = config.images_dir(project_id, project_name) / "prompts.json"
    if not prompts_path.exists():
        raise RuntimeError(
            "Chưa có bộ prompt (images/prompts.json). Hãy tạo ảnh từ 'Ý tưởng' "
            "một lần để sinh prompt, rồi mới sinh lại motion.")
    try:
        saved = json.loads(prompts_path.read_text(encoding="utf-8"))
    except Exception as e:
        raise RuntimeError(f"Không đọc được prompts.json ({e}).")
    prompts = saved.get("prompts")
    if not isinstance(prompts, dict) or not prompts:
        raise RuntimeError("prompts.json không có 'prompts' hợp lệ.")
    idea = (saved.get("idea") or "").strip()
    style_key = config.normalize_style(style_key or saved.get("style_key"))

    _log(f"Sinh lại motion cho {len(prompts)} prompt…")
    motions = generate_motions_for_prompts(
        prompts={str(k): v for k, v in prompts.items()},
        idea=idea,
        style=config.style_info(style_key)["brief"],
        api_config=get_api_config_from_db(str(DB_PATH)),
        log=_log)
    if not motions or len(motions) != len(prompts):
        raise RuntimeError(
            "AI chưa sinh lại đủ bộ motion (kiểm tra API key/model ở ⚙️ "
            "Settings rồi thử lại).")

    saved["motions"] = motions
    try:
        prompts_path.write_text(
            json.dumps(saved, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        raise RuntimeError(f"Không lưu được motion mới ({e}).")
    _log(f"Đã cập nhật {len(motions)} prompt chuyển động — tạo clip lần sau sẽ dùng.")
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
    progress_cb("Tạo clip video từ ảnh đã có (Flow/Veo)", 1.0)
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
    final = final_dir(project_id, project_name) / "final.mp4"
    thumb = final_dir(project_id, project_name) / "thumbnail.png"
    if not final.exists():
        raise RuntimeError("Chưa có final.mp4 — hãy render video trước khi đăng.")

    # Metadata (AI vision + bộ nhớ chống trùng)
    progress_cb("AI viết tiêu đề & mô tả từ thumbnail…", 10.0)
    api_config = get_api_config_from_db(str(DB_PATH))
    meta = youtube_meta.generate_metadata(
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
    cdp = client.start_profile(profile_id)

    try:
        progress_cb("Điều khiển YouTube Studio (lưu nháp)…", 26.0)
        result = youtube_driver.upload_draft(
            cdp_endpoint=cdp,
            video_path=str(final),
            title=meta["title"],
            description=meta["description"],
            hashtags=meta["hashtags"],
            language=mapping.get("language", "en-US"),
            progress_cb=lambda m, p: progress_cb(m, 26.0 + (p / 100.0) * 70.0),
            log=lambda m: progress_cb(m, 30.0),
        )
    except Exception as e:
        _record_upload(project_id, mapping, meta, str(final),
                       status="failed", error=f"{type(e).__name__}: {e}")
        raise
    finally:
        client.close_profile(profile_id)

    _record_upload(project_id, mapping, meta, str(final), status="draft")
    warns = result.get("warnings") or []
    msg = "Đã lưu bản nháp trên YouTube."
    if warns:
        msg += " Lưu ý: " + " | ".join(warns)
    progress_cb(msg, 100.0)
    return {"status": "draft", "title": meta["title"],
            "channel_name": mapping.get("channel_name", ""),
            "warnings": warns}


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
