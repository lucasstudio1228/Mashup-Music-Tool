"""Prepare, freeze and validate image/motion prompts before paid media generation."""
from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile

from . import config, prompt_catalog

WORKFLOW_VERSION = 1


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
    return data


def validate_manifest(project_id: int, saved: dict, count: int | None = None) -> dict:
    count = count or config.PARAMS.image_count
    prompts = validate_set(saved.get("prompts"), count, "Prompt ảnh")
    motions = validate_set(saved.get("motions"), count, "Prompt chuyển động")
    if saved.get("workflow_version") == WORKFLOW_VERSION:
        for label, values in (("ảnh", prompts), ("motion", motions)):
            if len({prompt_catalog.prompt_hash(v) for v in values.values()}) != count:
                raise ValueError(f"Bộ prompt {label} lặp nguyên văn giữa các shot; cần biến thể theo từng ảnh.")
        locks = saved.get("continuity", {})
        for key in ("character_sheet", "scene_sheet"):
            lock = locks.get(key, "")
            if not isinstance(lock, str) or len(lock.strip()) < 20:
                raise ValueError(f"Thiếu khoá continuity {key}.")
            if any(lock not in value for value in prompts.values()):
                raise ValueError(f"Một số prompt đã mất khối khoá {key}.")
    hashes = {
        "gemini": prompt_catalog.claim_prompts(project_id, "gemini", prompts),
        "flow": prompt_catalog.claim_prompts(project_id, "flow", motions),
    }
    return hashes


def prepare(project_id: int, project_name: str | None = None, idea: str | None = None,
            style_key: str | None = None, log=None, *, allow_rebuild: bool = False) -> dict:
    from backend.core_bridge import get_api_config_from_db
    from backend.database import DB_PATH
    from .prompt_gen import generate_prompts, generate_motions_for_prompts

    log = log or (lambda _: None)
    context = project_context(project_id, project_name or "", idea)
    style_key = config.normalize_style(style_key)
    count = config.PARAMS.image_count
    inputs = {"context": context, "style": style_key, "count": count,
              "style_version": config.STYLE_VERSION, "workflow_version": WORKFLOW_VERSION}
    signature = prompt_catalog.prompt_hash(json.dumps(inputs, ensure_ascii=False, sort_keys=True))
    img_dir = config.images_dir(project_id, project_name)
    saved = read_manifest(project_id, project_name)
    if saved and saved.get("input_signature") == signature:
        try:
            validate_manifest(project_id, saved, count)
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
                validate_manifest(project_id, saved, count)
                log("Tiếp tục bộ prompt cũ, giữ nguyên ảnh và nhân vật đã tạo.")
                return saved
        raise ValueError("Hồ sơ/số lượng/phong cách đã đổi nhưng project có ảnh. "
                         "Dùng Làm lại từ đầu khi muốn thay cả bộ; không trộn prompt mới với ảnh cũ.")

    brief = prompt_catalog.project_brief(project_id, context["title"],
                                         json.dumps(context, ensure_ascii=False))
    api_config = get_api_config_from_db(str(DB_PATH))
    locks = {}
    effective_idea = json.dumps(context, ensure_ascii=False)
    log(f"Chuẩn bị hồ sơ chung → {count} ảnh và {count} motion; kiểm tra continuity và chống trùng…")
    result = generate_prompts(
        idea=effective_idea, title=context["title"], keywords=context["purpose"],
        image_count=count, aspect_ratio=config.PARAMS.aspect_ratio,
        style=config.style_info(style_key)["brief"], api_config=api_config, log=log,
        creative_brief=brief["visual"] + "\nMusic: " + brief["music"], metadata_out=locks)
    if not result:
        raise RuntimeError("AI chưa tạo được hồ sơ/prompt đầy đủ. Kiểm tra cấu hình API rồi thử lại; "
                           "Tool chưa mở Gemini/Flow và không thay bằng prompt chung.")
    prompts, motions = result
    prompts = validate_set(prompts, count, "Prompt ảnh")
    try:
        motions = validate_set(motions, count, "Prompt chuyển động")
    except ValueError:
        log("Motion chưa đủ; viết bổ sung từ chính bộ prompt ảnh đã khoá.")
        motions = generate_motions_for_prompts(prompts=prompts, idea=effective_idea,
                    style=config.style_info(style_key)["brief"], api_config=api_config, log=log)
        motions = validate_set(motions, count, "Prompt chuyển động")
    # No contradictory motion signatures: these are grounded constraints only.
    motions = {key: value + " Locked camera; preserve exact framing; animate only details "
               "visible in the supplied image. No fade-in or fade-out, no new objects."
               for key, value in motions.items()}
    saved = {**inputs, "input_signature": signature, "creative_brief": brief,
             "continuity": locks, "title": context["title"], "idea": context["visual_idea"],
             "style_key": style_key, "prompts": prompts, "motions": motions,
             "review_note": "Structural checks passed; visual/music quality still requires output review."}
    saved["prompt_hashes"] = validate_manifest(project_id, saved, count)
    save_manifest(img_dir / "prompts.json", saved)
    log("Đã lưu bộ prompt đầy đủ; clip 0 mở đầu, các cảnh còn lại cùng bố cục và đảo thứ tự được.")
    return saved
