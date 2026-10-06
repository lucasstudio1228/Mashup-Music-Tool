"""Prepare, freeze and validate image/motion prompts before paid media generation."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import tempfile

from . import config, prompt_catalog

# v2: 3 ảnh (bìa + 2 bảng phân tích nhân vật) → N clip dùng chung 2 bảng đó làm
# nguyên liệu. Bộ prompt v1 (41 ảnh ↔ 41 clip 1:1) không tương thích.
WORKFLOW_VERSION = 2

# Ràng buộc bám thực tế gắn vào CUỐI mọi prompt cảnh: nguyên liệu là BẢNG THIẾT
# KẾ chứ không phải khung hình đầu clip, nên KHÔNG khoá máy quay như quy trình
# cũ — chỉ cấm dựng lại chính bảng thiết kế và cấm cắt cảnh.
SCENE_SUFFIX = (
    " One single continuous shot, no cuts. Do not reproduce the reference "
    "sheets: no side-by-side views, no labels, no text, no colour-swatch strip, "
    "no flat studio backdrop, no duplicated character. Keep the character and "
    "creature identity unchanged.")


def project_context(project_id: int, title: str = "", idea: str | None = None) -> dict:
    from sqlmodel import Session, select
    from backend.database import engine
    from backend.models import Project, SunoBatch
    with Session(engine) as session:
        project = session.get(Project, project_id)
        if project is None:
            raise ValueError(f"Project {project_id} không tồn tại.")
        batch = session.exec(select(SunoBatch).where(SunoBatch.project_id == project_id)
                             .order_by(SunoBatch.created_at.desc())).first()
        music_styles = ""
        if batch:
            try:
                music_styles = json.loads(batch.config_json or "{}").get("styles", "")
            except (ValueError, AttributeError):
                pass
        return {
            "title": title or project.name,
            "visual_idea": (idea if idea is not None else project.video_idea) or "",
            "music_idea": project.suno_idea or "",
            "instrument": project.instrument or "",
            "purpose": project.music_style or "chill, relaxing, meditation",
            "description": project.description or "",
            "music_styles": music_styles,
        }


def validate_set(values, count: int, label: str) -> dict[str, str]:
    expected = {str(i) for i in range(count)}
    if (not isinstance(values, dict) or set(values) != expected
            or any(not isinstance(v, str) or not v.strip() for v in values.values())):
        raise ValueError(f"{label} phải đủ {count} mục không trống, đúng chỉ số 0–{count - 1}. "
                         "Hãy chuẩn bị lại bộ prompt; không dùng cảnh/chuyển động mặc định thay thế.")
    return {k: v.strip() for k, v in values.items()}


def save_manifest(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    name = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=".prompts-", suffix=".tmp", delete=False) as fh:
            name = fh.name
            json.dump(data, fh, ensure_ascii=False, indent=2)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(name, path)
    finally:
        if name and Path(name).exists():
            Path(name).unlink()


def read_manifest(project_id: int, project_name: str | None) -> dict | None:
    path = config.images_dir(project_id, project_name) / "prompts.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (ValueError, OSError) as exc:
        raise ValueError("Không đọc được prompts.json; giữ nguyên media và sửa bộ prompt trước.") from exc
    if not isinstance(data, dict):
        raise ValueError("prompts.json không phải hồ sơ hợp lệ.")
    trimmed = fit_scene_count(data)
    if trimmed is not data:
        # Giữ bản đủ cảnh cũ (vd 40) cạnh file — cắt về N không làm mất dữ liệu.
        backup = path.with_name(f"prompts.{trimmed['trimmed_from_scene_count']}scenes.bak.json")
        if not backup.exists():
            try:
                backup.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            except OSError:
                pass
        save_manifest(path, trimmed)
    return trimmed


_ENGLISH_FIELDS = ("prompts", "motions", "continuity", "idea", "creative_brief", "title")


def _to_english_keep_locks(text: str, lock_map: dict[str, str], api_config, log) -> str:
    """Dịch 1 prompt nhưng giữ khối khoá nhân vật GIỐNG HỆT nhau ở mọi prompt:
    tách chuỗi theo các khối khoá (bản gốc) → thay bằng bản dịch dùng chung của
    khoá, chỉ dịch phần còn lại (preamble / bối cảnh). Nhờ vậy validate_manifest
    ('khoá phải nằm nguyên văn trong mọi prompt') vẫn đạt sau khi dịch."""
    from .translate import needs_translation, to_english
    keys = sorted((k for k in lock_map if k), key=len, reverse=True)
    parts = re.split("(" + "|".join(map(re.escape, keys)) + ")", text) if keys else [text]
    out = []
    for part in parts:
        if part in lock_map:
            out.append(lock_map[part])
        elif needs_translation(part):
            core = part.strip()
            lead = part[:len(part) - len(part.lstrip())]
            trail = part[len(part.rstrip()):]
            out.append(lead + to_english(core, api_config, log) + trail)
        else:
            out.append(part)
    return "".join(out)


def english_manifest(project_id: int, saved: dict, api_config=None, log=None) -> dict:
    """Hồ sơ cũ (trước quy tắc 2026-09-26) còn tiếng Việt → trả BẢN SAO đã dịch
    100% sang tiếng Anh (Flow/Gemini hiểu tiếng Anh tốt nhất). Đã là tiếng Anh →
    trả chính `saved`. Không dịch được → TranslationError (caller dừng, không
    gửi prompt lẫn tiếng Việt)."""
    from .translate import TranslationError, non_english_fields, to_english
    view = {k: saved.get(k) for k in _ENGLISH_FIELDS if saved.get(k) is not None}
    if not non_english_fields(view):
        return saved
    out = dict(saved)
    locks = dict(saved.get("continuity") or {})
    lock_map: dict[str, str] = {}
    for key in ("character_sheet", "pet_sheet"):
        v = locks.get(key)
        if isinstance(v, str) and v.strip():
            en = to_english(v.strip(), api_config, log)
            lock_map[v.strip()] = en
            locks[key] = en
    for key, v in list(locks.items()):
        if key not in ("character_sheet", "pet_sheet"):
            locks[key] = to_english(v, api_config, log)
    if saved.get("continuity") is not None:
        out["continuity"] = locks
    for field in ("prompts", "motions"):
        values = saved.get(field)
        if isinstance(values, dict):
            out[field] = {k: (_to_english_keep_locks(v, lock_map, api_config, log)
                              if isinstance(v, str) else v) for k, v in values.items()}
    for field in ("idea", "creative_brief", "title"):
        if saved.get(field) is not None:
            out[field] = to_english(saved[field], api_config, log)
    left = non_english_fields({k: out.get(k) for k in _ENGLISH_FIELDS if out.get(k) is not None})
    if left:
        raise TranslationError("Dịch hồ sơ prompt sang tiếng Anh chưa trọn: "
                               + ", ".join(left[:8]))
    try:
        out["prompt_hashes"] = validate_manifest(project_id, out)
    except ValueError:
        pass
    out["translated_to_english"] = True
    return out


def ensure_english_manifest(project_id: int, project_name: str | None,
                            api_config=None, log=None, *, extend: bool = True) -> dict | None:
    """read_manifest + tự dịch hồ sơ cũ sang tiếng Anh (lưu lại, giữ bản gốc ở
    prompts.vi.bak.json). Lỗi dịch → RuntimeError, KHÔNG trả hồ sơ tiếng Việt.
    extend=True: hồ sơ ít cảnh hơn cấu hình (20 → 40) được AI viết bổ sung."""
    from .translate import TranslationError
    saved = read_manifest(project_id, project_name)
    if saved is None:
        return None
    try:
        out = english_manifest(project_id, saved, api_config, log)
    except TranslationError as exc:
        raise RuntimeError(f"Hồ sơ prompt cũ còn tiếng Việt và chưa dịch được sang "
                           f"tiếng Anh: {exc}") from exc
    if out is not saved:
        path = config.images_dir(project_id, project_name) / "prompts.json"
        backup = path.with_name("prompts.vi.bak.json")
        if not backup.exists():
            try:
                backup.write_text(json.dumps(saved, ensure_ascii=False, indent=2), encoding="utf-8")
            except OSError:
                pass
        save_manifest(path, out)
        if log:
            log("Đã chuyển hồ sơ prompt cũ sang 100% tiếng Anh (bản gốc: prompts.vi.bak.json).")
    if extend:
        grown = extend_scene_count(project_id, out, api_config=api_config, log=log)
        if grown is not out:
            save_manifest(config.images_dir(project_id, project_name) / "prompts.json", grown)
            if log:
                log(f"Đã bổ sung prompt cảnh: {grown['extended_from_scene_count']} → "
                    f"{len(grown['motions'])}.")
            out = grown
    return out


_SIGNATURE_KEYS = ("context", "style", "count", "scene_count", "style_version", "workflow_version")


def fit_scene_count(saved: dict, target: int | None = None) -> dict:
    """Hồ sơ lưu NHIỀU cảnh hơn cấu hình hiện tại (vd 40 → 20 clip/video): giữ
    nguyên hồ sơ nhân vật + ảnh, chỉ cắt motions về 0..N-1 và cập nhật chữ ký để
    project đang dở chạy tiếp mà không phải 'Làm lại từ đầu'. Trả CHÍNH `saved`
    nếu không cần cắt, ngược lại trả bản sao đã cắt."""
    target = target or config.PARAMS.total_clips
    motions = saved.get("motions")
    if not isinstance(motions, dict) or len(motions) <= target:
        return saved
    if not all(str(i) in motions for i in range(target)):
        return saved
    out = dict(saved)
    out["motions"] = {str(i): motions[str(i)] for i in range(target)}
    if "scene_count" in saved:
        out["scene_count"] = target
    _resign(saved, out, target)
    hashes = saved.get("prompt_hashes")
    if isinstance(hashes, dict) and isinstance(hashes.get("flow"), dict):
        out["prompt_hashes"] = {**hashes, "flow": {k: v for k, v in hashes["flow"].items()
                                                   if k in out["motions"]}}
    out["trimmed_from_scene_count"] = int(saved.get("scene_count") or len(motions))
    return out


def _resign(saved: dict, out: dict, target: int) -> None:
    """Cập nhật input_signature theo scene_count mới — chỉ khi chữ ký cũ khớp."""
    old_sig = saved.get("input_signature")
    if old_sig and all(k in saved for k in _SIGNATURE_KEYS):
        before = {k: saved[k] for k in _SIGNATURE_KEYS}
        if prompt_catalog.prompt_hash(json.dumps(before, ensure_ascii=False, sort_keys=True)) == old_sig:
            after = {**before, "scene_count": target}
            out["input_signature"] = prompt_catalog.prompt_hash(
                json.dumps(after, ensure_ascii=False, sort_keys=True))


def extend_scene_count(project_id: int, saved: dict, target: int | None = None,
                       api_config=None, log=None, _generate=None) -> dict:
    """Hồ sơ lưu ÍT cảnh hơn cấu hình hiện tại (vd 20 → 40 clip/video): giữ
    nguyên hồ sơ nhân vật, ảnh và các cảnh đã có; AI chỉ viết THÊM cảnh
    N..target-1 từ hai bảng nhân vật đã khoá (tránh bối cảnh cũ). Trả CHÍNH
    `saved` nếu không cần bổ sung, ngược lại trả bản sao đã bổ sung + kiểm tra."""
    target = target or config.PARAMS.total_clips
    motions = saved.get("motions")
    if (not isinstance(motions, dict) or len(motions) >= target
            or saved.get("workflow_version") != WORKFLOW_VERSION):
        return saved
    have = len(motions)
    if not all(str(i) in motions for i in range(have)):
        return saved
    locks = saved.get("continuity") or {}
    character_sheet = (locks.get("character_sheet") or "").strip()
    pet_sheet = (locks.get("pet_sheet") or "").strip()
    if len(character_sheet) < 20 or len(pet_sheet) < 20:
        raise ValueError("Hồ sơ thiếu khoá continuity — không bổ sung cảnh được; "
                         "dùng Làm lại từ đầu.")
    need = target - have
    if log:
        log(f"Nâng {have} → {target} clip/video: AI viết thêm {need} prompt cảnh "
            "từ hai bảng nhân vật đã khoá (giữ nguyên ảnh + cảnh cũ)…")
    if _generate is None:
        from .prompt_gen import generate_scene_prompts as _generate
        if api_config is None:
            from backend.core_bridge import get_api_config_from_db
            from backend.database import DB_PATH
            api_config = get_api_config_from_db(str(DB_PATH))
    context = saved.get("context")
    idea = json.dumps(context, ensure_ascii=False) if context else (saved.get("idea") or "")
    avoid = list(locks.get("settings") or [])
    style = config.style_info(config.normalize_style(saved.get("style_key") or saved.get("style")))["brief"]
    extra = _generate(character_sheet=character_sheet, pet_sheet=pet_sheet, idea=idea,
                      style=style, api_config=api_config, scene_count=need, log=log,
                      avoid=avoid or None)
    extra = validate_set(extra, need, "Prompt cảnh bổ sung") if extra else None
    if not extra:
        raise RuntimeError(f"AI chưa viết thêm được {need} prompt cảnh (kiểm tra API "
                           "key/model ở ⚙️ Settings rồi thử lại).")
    new = dict(motions)
    for j in range(need):
        new[str(have + j)] = extra[str(j)] + SCENE_SUFFIX
    out = dict(saved)
    out["motions"] = new
    if "scene_count" in saved:
        out["scene_count"] = target
    _resign(saved, out, target)
    out.pop("trimmed_from_scene_count", None)
    out["extended_from_scene_count"] = have
    out["prompt_hashes"] = validate_manifest(
        project_id, out, len(saved.get("prompts") or {}) or None, target)
    return out


def validate_manifest(project_id: int, saved: dict, count: int | None = None,
                      scene_count: int | None = None) -> dict:
    count = count or config.PARAMS.image_count
    scene_count = scene_count or config.PARAMS.total_clips
    prompts = validate_set(saved.get("prompts"), count, "Prompt ảnh")
    motions = validate_set(saved.get("motions"), scene_count, "Prompt cảnh (clip)")
    if saved.get("workflow_version") == WORKFLOW_VERSION:
        for label, values, n in (("ảnh", prompts, count), ("cảnh", motions, scene_count)):
            if len({prompt_catalog.prompt_hash(v) for v in values.values()}) != n:
                raise ValueError(f"Bộ prompt {label} lặp nguyên văn giữa các shot; cần biến thể theo từng {label}.")
        # Khoá continuity: character_sheet nằm trong ảnh 0 (bìa) + ảnh 1 (bảng
        # nhân vật chính); pet_sheet nằm trong ảnh 0 + ảnh 2 (bảng linh thú).
        # CẢ HAI phải nằm trong MỌI prompt cảnh — đó là thứ giữ N clip cùng
        # một nhân vật.
        locks = saved.get("continuity", {})
        owners = {"character_sheet": (config.IMG_THUMBNAIL, config.IMG_MAIN_SHEET),
                  "pet_sheet": (config.IMG_THUMBNAIL, config.IMG_PET_SHEET)}
        for key, idxs in owners.items():
            lock = locks.get(key, "")
            if not isinstance(lock, str) or len(lock.strip()) < 20:
                raise ValueError(f"Thiếu khoá continuity {key}.")
            for i in idxs:
                if lock not in prompts[str(i)]:
                    raise ValueError(f"Prompt ảnh {i} đã mất khối khoá {key}.")
            if any(lock not in value for value in motions.values()):
                raise ValueError(f"Một số prompt cảnh đã mất khối khoá {key}.")
    hashes = {
        "gemini": prompt_catalog.claim_prompts(project_id, "gemini", prompts),
        "flow": prompt_catalog.claim_prompts(project_id, "flow", motions),
    }
    return hashes


def prepare(project_id: int, project_name: str | None = None, idea: str | None = None,
            style_key: str | None = None, log=None, *, allow_rebuild: bool = False) -> dict:
    from backend.core_bridge import get_api_config_from_db
    from backend.database import DB_PATH
    from .prompt_gen import generate_prompts, generate_scene_prompts

    log = log or (lambda _: None)
    context = project_context(project_id, project_name or "", idea)
    style_key = config.normalize_style(style_key)
    count = config.PARAMS.image_count          # 3 ảnh: bìa + 2 bảng nhân vật
    scenes = config.PARAMS.total_clips         # N clip, N bối cảnh khác nhau
    inputs = {"context": context, "style": style_key, "count": count,
              "scene_count": scenes,
              "style_version": config.STYLE_VERSION, "workflow_version": WORKFLOW_VERSION}
    signature = prompt_catalog.prompt_hash(json.dumps(inputs, ensure_ascii=False, sort_keys=True))
    img_dir = config.images_dir(project_id, project_name)
    saved = ensure_english_manifest(project_id, project_name, log=log)
    if saved and saved.get("input_signature") == signature:
        try:
            validate_manifest(project_id, saved, count, scenes)
        except ValueError:
            if not allow_rebuild:
                raise
            log("Bộ đã lưu không còn hợp lệ; viết lại đầy đủ trước khi thay ảnh.")
        else:
            log("Dùng lại hồ sơ và prompt đã khoá của project; không gọi AI lại.")
            return saved
    if not allow_rebuild and any(img_dir.glob("*.png")):
        # Historical manifests can resume unchanged. Never replace prompts while
        # keeping existing images produced from a different identity/layout.
        if saved and not saved.get("workflow_version"):
            same_idea = not context["visual_idea"] or saved.get("idea", "").strip() == context["visual_idea"].strip()
            same_style = config.normalize_style(saved.get("style_key")) == style_key
            if same_idea and same_style:
                validate_manifest(project_id, saved, count, scenes)
                log("Tiếp tục bộ prompt cũ, giữ nguyên ảnh và nhân vật đã tạo.")
                return saved
        raise ValueError("Hồ sơ/số lượng/phong cách đã đổi nhưng project có ảnh. "
                         "Dùng Làm lại từ đầu khi muốn thay cả bộ; không trộn prompt mới với ảnh cũ.")

    brief = prompt_catalog.project_brief(project_id, context["title"],
                                         json.dumps(context, ensure_ascii=False))
    api_config = get_api_config_from_db(str(DB_PATH))
    locks = {}
    effective_idea = json.dumps(context, ensure_ascii=False)
    log(f"Chuẩn bị hồ sơ chung → {count} ảnh (bìa + 2 bảng phân tích nhân vật) "
        f"và {scenes} prompt cảnh; kiểm tra continuity và chống trùng…")
    result = generate_prompts(
        idea=effective_idea, title=context["title"], keywords=context["purpose"],
        scene_count=scenes, aspect_ratio=config.PARAMS.aspect_ratio,
        style=config.style_info(style_key)["brief"], api_config=api_config, log=log,
        creative_brief=brief["visual"] + "\nMusic: " + brief["music"], metadata_out=locks,
        instrument=context["instrument"])
    if not result:
        raise RuntimeError("AI chưa tạo được hồ sơ/prompt đầy đủ. Kiểm tra cấu hình API rồi thử lại; "
                           "Tool chưa mở Gemini/Flow và không thay bằng prompt chung.")
    prompts, motions = result
    prompts = validate_set(prompts, count, "Prompt ảnh")
    try:
        motions = validate_set(motions, scenes, "Prompt cảnh (clip)")
    except ValueError:
        log("Prompt cảnh chưa đủ; viết bổ sung từ hai bảng nhân vật đã khoá.")
        motions = generate_scene_prompts(
            character_sheet=locks.get("character_sheet", ""),
            pet_sheet=locks.get("pet_sheet", ""), idea=effective_idea,
            style=config.style_info(style_key)["brief"], api_config=api_config,
            scene_count=scenes, log=log, avoid=locks.get("settings") or None,
            instrument=context["instrument"])
        motions = validate_set(motions, scenes, "Prompt cảnh (clip)")
    motions = {key: value + SCENE_SUFFIX for key, value in motions.items()}
    saved = {**inputs, "input_signature": signature, "creative_brief": brief,
             "continuity": locks, "title": context["title"], "idea": context["visual_idea"],
             "style_key": style_key, "prompts": prompts, "motions": motions,
             "review_note": "Structural checks passed; visual/music quality still requires output review."}
    saved["prompt_hashes"] = validate_manifest(project_id, saved, count, scenes)
    save_manifest(img_dir / "prompts.json", saved)
    log(f"Đã lưu bộ prompt đầy đủ: {count} ảnh + {scenes} cảnh — mọi clip dùng "
        f"chung hai bảng nhân vật làm nguyên liệu, chỉ khác bối cảnh.")
    return saved
