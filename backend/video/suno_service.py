"""
video/suno_service.py — Orchestrator STEP 0 (tạo 15 bài nhạc nền từ Suno).

Điều phối các phase (mục I của spec) trên state BỀN VỮNG (SunoBatch/SunoCandidate
trong DB) để chống trùng + resume + pause/cancel. Gọi SunoDriver để thao tác UI,
suno_verify để xác thực WAV, và HÀM IMPORT THẬT của app (tracks._persist +
_existing_paths, giống nút "Add file") để đưa track vào project.

Chạy trong worker thread của video_job_manager (kind="suno") — 1 luồng, nên đã
được serialize; lock trong DB là chốt phụ chống 2 tiến trình.

Chữ ký hàm step tuân quy ước pipeline: (project_id, progress_cb, ...extra).

AN TOÀN:
  • dry_run=True → KHÔNG bấm Create, KHÔNG tải file, KHÔNG tiêu credit.
  • Live chỉ khi router gọi với dry_run=False (người dùng bấm Start / lệnh live).
  • DoD: 15 selected = 15 song_id khác nhau, 15 WAV hợp lệ, manifest đầy đủ,
    import đúng 1 lần. KHÔNG coi 15 lần Create / 15 card / 15 filename / 15
    download-đã-bắt-đầu là hoàn thành.
"""
from __future__ import annotations

import json
import shutil
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Optional

from sqlmodel import Session, select

from backend.database import engine, DB_PATH
from backend.models import Project, SunoBatch, SunoCandidate, Track
from backend.video import config as vconfig
from backend.video.browser_base import BrowserSession
from backend.video.suno_driver import (
    SunoDriver, SunoLoginRequired, SunoUIChanged, SunoSubmissionUncertain,
    SunoBudgetError, SunoError, NewSong,
)
from backend.video import suno_verify

_LOG = Callable[[str], None]


# ── Phase constants (khớp models.SunoBatch.phase) ─────────────────
PREFLIGHT = "PREFLIGHT"
GENERATING = "GENERATING"
DOWNLOADING = "DOWNLOADING"
VALIDATING = "VALIDATING"
READY_FOR_IMPORT = "READY_FOR_IMPORT"
IMPORTED = "IMPORTED"
DOWNSTREAM_RUNNING = "DOWNSTREAM_RUNNING"
COMPLETED = "COMPLETED"
# Blocking / non-terminal:
WAITING_FOR_LOGIN = "WAITING_FOR_LOGIN"
WAITING_FOR_HUMAN = "WAITING_FOR_HUMAN"
SUBMISSION_UNCERTAIN = "SUBMISSION_UNCERTAIN"
INSUFFICIENT_GENERATION_CREDITS = "INSUFFICIENT_GENERATION_CREDITS"
INSUFFICIENT_DOWNLOAD_ALLOWANCE = "INSUFFICIENT_DOWNLOAD_ALLOWANCE"
UI_CHANGED = "UI_CHANGED"
FAILED = "FAILED"
PAUSED = "PAUSED"
CANCELLED = "CANCELLED"

_TERMINAL = {COMPLETED, CANCELLED}
_ASSUMED_SONGS_PER_REQUEST = 2   # paired_outputs — đo lại ở request thật đầu tiên

# Phân biệt Pause vs Cancel: cả hai đều dùng job_manager.cancel() (ném
# JobCancelled). project_id nằm trong set này ⇒ Cancel (terminal CANCELLED);
# ngược lại ⇒ Pause (PAUSED, resume được).
_cancel_requested: set[int] = set()


def request_cancel(project_id: int) -> None:
    _cancel_requested.add(project_id)


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ══════════════════════════════════════════════════════════════════
# Cấu hình batch
# ══════════════════════════════════════════════════════════════════
def resolve_batch_config(overrides: Optional[dict] = None) -> dict:
    """Ghép SunoConfig mặc định + preset (styles/exclusions nguyên văn) +
    override từ UI. Trả dict phẳng để lưu snapshot config_json."""
    base = vconfig.SUNO
    ov = dict(overrides or {})
    preset_key = ov.get("preset", base.preset)
    preset = vconfig.SUNO_PRESETS.get(preset_key)
    if preset is None:
        raise ValueError(f"Preset không tồn tại: {preset_key!r}. "
                         f"Có: {list(vconfig.SUNO_PRESETS)}")

    # Styles/Exclusions: ưu tiên override (AI viết từ ý tưởng, hoặc người dùng tự
    # sửa) → nếu không có thì dùng nguyên văn preset. LUÔN chốt an toàn "không lời"
    # (đảm bảo Exclude styles chứa đủ từ khoá loại bỏ giọng hát).
    styles = (ov.get("styles") or "").strip() or preset["styles"]
    exclusions = (ov.get("exclusions") or "").strip() or preset["exclusions"]
    from backend.video.suno_prompt import _ensure_instrumental
    styles, exclusions = _ensure_instrumental(styles, exclusions)
    # allow_bass/allow_drums=False → đảm bảo drums/bass nằm trong Exclusions
    # (Suno không có switch stem lúc tạo). Preset đã có sẵn; đây là chốt an toàn.
    cfg = {
        "source": ov.get("source", base.source),
        "preset": preset_key,
        "preset_label": preset["label"],
        "suno_idea": (ov.get("suno_idea") or "").strip(),
        "styles": styles,
        "styles_supplied": bool((ov.get("styles") or "").strip()) and
                           (ov.get("styles") or "").strip() not in
                           {p["styles"].strip() for p in vconfig.SUNO_PRESETS.values()},
        "exclusions": exclusions,
        "target_tracks": int(ov.get("target_tracks", base.target_tracks)),
        "preferred_model": ov.get("preferred_model", base.preferred_model),
        "allow_model_fallback": bool(ov.get("allow_model_fallback",
                                            base.allow_model_fallback)),
        "instrumental": bool(ov.get("instrumental", base.instrumental)),
        "allow_bass": bool(ov.get("allow_bass", base.allow_bass)),
        "allow_drums": bool(ov.get("allow_drums", base.allow_drums)),
        "generation_strategy": ov.get("generation_strategy",
                                      base.generation_strategy),
        "max_mode": bool(ov.get("max_mode", base.max_mode)),
        "preferred_duration_seconds_min": int(ov.get(
            "preferred_duration_seconds_min", base.preferred_duration_seconds_min)),
        "preferred_duration_seconds_max": int(ov.get(
            "preferred_duration_seconds_max", base.preferred_duration_seconds_max)),
        "minimum_duration_seconds": int(ov.get(
            "minimum_duration_seconds", base.minimum_duration_seconds)),
        "max_create_actions": int(ov.get("max_create_actions",
                                         base.max_create_actions)),
        "max_generation_credits": int(ov.get("max_generation_credits",
                                            base.max_generation_credits)),
        "max_new_song_downloads": int(ov.get("max_new_song_downloads",
                                            base.max_new_song_downloads)),
        "auto_continue_workflow": bool(ov.get("auto_continue_workflow",
                                             base.auto_continue_workflow)),
    }
    return cfg


def prepare_batch_prompts(project: Project, cfg: dict, log=None) -> dict:
    """Freeze a project-specific, per-Create music plan before opening Suno."""
    from .prompt_catalog import project_brief, claim_prompts, prompt_hash
    from .prompt_workflow import project_context
    from .suno_prompt import generate_suno_styles, compose_project_styles, build_suno_prompt_plan
    context = project_context(project.id, project.name)
    context["music_idea"] = cfg.get("suno_idea") or context["music_idea"]
    if not cfg.get("prompt_plan"):
        # A fresh music brief must not inherit a previous batch's incompatible
        # instrument/arrangement when the user changed the idea or base Styles.
        context["music_styles"] = cfg["styles"] if cfg.get("styles_supplied") else ""
    brief = project_brief(project.id, project.name, json.dumps(context, ensure_ascii=False))
    if not cfg.get("prompt_plan"):
        styles = cfg["styles"]
        if not cfg.get("styles_supplied"):
            from backend.core_bridge import get_api_config_from_db
            from backend.database import DB_PATH
            result = generate_suno_styles(json.dumps(context, ensure_ascii=False),
                         get_api_config_from_db(str(DB_PATH)), log=log,
                         project_title=project.name, creative_brief=brief["music"])
            if not result:
                raise ValueError("Chưa viết được Styles từ hồ sơ project. Kiểm tra API hoặc "
                                 "nhập Styles đã duyệt; không chạy preset thay thế ý tưởng.")
            styles, cfg["exclusions"] = result["styles"], result["exclusions"]
        cfg["styles"] = compose_project_styles(styles, brief["music"])
        cfg["prompt_plan"] = build_suno_prompt_plan(cfg["styles"], cfg["exclusions"],
                                                   int(cfg["max_create_actions"]))
        cfg["creative_brief"] = brief
        cfg["creative_context"] = context
        cfg["prompt_workflow_version"] = 1
    plan = cfg["prompt_plan"]
    if len(plan) != int(cfg["max_create_actions"]):
        raise ValueError("Prompt plan không khớp ngân sách Create đã lưu.")
    for i, item in enumerate(plan):
        if (item.get("request") != i + 1 or not isinstance(item.get("styles"), str)
                or not 1 <= len(item["styles"]) <= 1000 or not item.get("exclusions")):
            raise ValueError(f"Prompt Suno #{i + 1} không hợp lệ hoặc vượt 1000 ký tự.")
    if len({prompt_hash(item["styles"]) for item in plan}) != len(plan):
        raise ValueError("Prompt plan Suno có lượt Create trùng nguyên văn.")
    cfg["prompt_hashes"] = claim_prompts(project.id, "suno",
                                         {str(i): p["styles"] for i, p in enumerate(plan)})
    return cfg


# ══════════════════════════════════════════════════════════════════
# Truy vấn / cập nhật batch (state bền vững)
# ══════════════════════════════════════════════════════════════════
def get_active_batch(session: Session, project_id: int) -> Optional[SunoBatch]:
    """Batch chưa terminal gần nhất của project (để resume/hiển thị)."""
    rows = session.exec(
        select(SunoBatch).where(SunoBatch.project_id == project_id)
        .order_by(SunoBatch.created_at.desc())).all()
    for b in rows:
        if b.phase not in _TERMINAL:
            return b
    return rows[0] if rows else None


def batch_status(project_id: int) -> Optional[dict]:
    """Trạng thái batch Suno gần nhất (chưa terminal ưu tiên) — cho endpoint status.
    None nếu project chưa có batch nào."""
    with Session(engine) as session:
        batch = get_active_batch(session, project_id)
        if batch is None:
            return None
        return _summary(session, batch, dry_run=False, credits=None)


def mark_cancelled(project_id: int) -> Optional[dict]:
    """Đánh dấu batch đang dở là CANCELLED khi KHÔNG có job đang chạy (batch đang
    ở trạng thái blocking/paused). Nếu job đang chạy, router gọi request_cancel +
    job_manager.cancel thay vì hàm này."""
    with Session(engine) as session:
        batch = get_active_batch(session, project_id)
        if batch is None or batch.phase in _TERMINAL:
            return None
        _touch(session, batch, CANCELLED, "Đã huỷ batch Suno.", None)
        _release_lock(session, batch)
        return _summary(session, batch, dry_run=False, credits=None)


def _touch(session: Session, batch: SunoBatch, phase: Optional[str] = None,
           message: Optional[str] = None, error: Optional[str] = None) -> None:
    if phase is not None:
        batch.phase = phase
    if message is not None:
        batch.message = message
    if error is not None:
        batch.error_message = error
    batch.updated_at = _now()
    session.add(batch)
    session.commit()
    session.refresh(batch)


def _acquire_lock(session: Session, batch: SunoBatch,
                  ttl_sec: int = 3600) -> str:
    """Chốt để 1 batch không bị 2 worker chạy song song. Trả lock_token."""
    now = _now()
    if batch.lock_token and batch.lock_expires_at:
        exp = batch.lock_expires_at
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
        if exp > now:
            raise SunoError(
                "Batch Suno này đang được xử lý bởi tiến trình khác (đang khoá). "
                "Chờ nó xong hoặc thử lại sau.")
    token = uuid.uuid4().hex
    batch.lock_token = token
    batch.lock_expires_at = now + timedelta(seconds=ttl_sec)
    session.add(batch)
    session.commit()
    return token


def _release_lock(session: Session, batch: SunoBatch) -> None:
    batch.lock_token = None
    batch.lock_expires_at = None
    session.add(batch)
    session.commit()


# ══════════════════════════════════════════════════════════════════
# Import handoff — DÙNG hàm import thật của app (tracks._persist)
# ══════════════════════════════════════════════════════════════════
def _import_validated_tracks(session: Session, batch: SunoBatch,
                             log: _LOG) -> int:
    """Chỉ chạy khi CẢ batch đã sẵn sàng (đủ 15 WAV hợp lệ). Copy WAV từ staging
    vào thư mục media của project rồi đăng ký Track (dedup theo path đã resolve)
    — giống nút Add file. Idempotent: candidate đã imported thì bỏ qua."""
    from backend.routers.tracks import _persist, _existing_paths
    from backend import core_bridge

    project = session.get(Project, batch.project_id)
    dest_dir = (vconfig.project_dir(batch.project_id, project.name if project else None)
                / "suno_tracks")
    dest_dir.mkdir(parents=True, exist_ok=True)

    cands = session.exec(
        select(SunoCandidate).where(
            SunoCandidate.batch_id == batch.id,
            SunoCandidate.selected == True,           # noqa: E712
            SunoCandidate.validation_status == "valid",
        )).all()

    existing = _existing_paths(session, batch.project_id)
    imported = 0
    for c in cands:
        if c.imported:
            continue
        if not c.wav_path or not Path(c.wav_path).exists():
            log(f"Bỏ qua import {c.song_id}: thiếu file WAV đã xác thực.")
            continue
        # Copy vào media project (giữ nguyên bản gốc trong staging).
        final_path = dest_dir / Path(c.wav_path).name
        if str(final_path.resolve()) not in existing:
            if not final_path.exists():
                shutil.copy2(c.wav_path, final_path)
            core_track = core_bridge.probe_file(str(final_path))
            if core_track is None:
                log(f"Không đọc được WAV khi import: {final_path.name}")
                continue
            track = _persist(session, batch.project_id, core_track)
            session.commit()
            session.refresh(track)
            c.track_id = track.id
            existing.add(str(final_path.resolve()))
        c.imported = True
        session.add(c)
        imported += 1
    batch.imported_tracks = session.exec(
        select(SunoCandidate).where(
            SunoCandidate.batch_id == batch.id,
            SunoCandidate.imported == True)).all().__len__()   # noqa: E712
    session.commit()
    log(f"Đã import {imported} track mới (tổng imported={batch.imported_tracks}).")
    return imported


# ══════════════════════════════════════════════════════════════════
# Điểm vào chính — chạy/resume batch
# ══════════════════════════════════════════════════════════════════
def run_suno_step0(project_id: int, progress_cb: Callable,
                   *, dry_run: bool = True,
                   overrides: Optional[dict] = None,
                   resume: bool = False) -> dict:
    """
    Entry cho video_job_manager.submit(project_id, "suno", run_suno_step0, ...).

    dry_run=True: PREFLIGHT + điền form + đọc credits/plan, KHÔNG Create/tải.
    dry_run=False: chạy đầy đủ tới IMPORTED (+ auto_continue nếu bật).
    resume=True: tiếp tục batch chưa terminal thay vì tạo mới.
    """
    # job_manager cb là cb(message, percent). Giữ percent hiện tại để log() không
    # tụt thanh tiến trình về 0. report()/log() để JobCancelled truyền lên (pause).
    _pct = {"v": 0.0}

    def report(msg: str, percent: Optional[float] = None) -> None:
        if percent is not None:
            _pct["v"] = float(percent)
        progress_cb(msg, _pct["v"])

    def log(msg: str) -> None:
        report(msg)

    _cancel_requested.discard(project_id)   # bắt đầu lượt chạy mới

    with Session(engine) as session:
        project = session.get(Project, project_id)
        if project is None:
            raise ValueError(f"Project {project_id} không tồn tại.")

        # ── Lấy/khởi tạo batch ──
        batch: Optional[SunoBatch] = None
        if resume:
            batch = get_active_batch(session, project_id)
            if batch is None or batch.phase in _TERMINAL:
                raise SunoError("Không có batch Suno nào đang dở để resume.")
        if batch is None:
            cfg = resolve_batch_config(overrides)
            cfg = prepare_batch_prompts(project, cfg, log)
            staging = vconfig.SUNO_STAGING_ROOT / f"project_{project_id}" / uuid.uuid4().hex[:8]
            staging.mkdir(parents=True, exist_ok=True)
            batch = SunoBatch(
                project_id=project_id,
                idempotency_key=f"suno:{project_id}:{int(time.time())}",
                phase=PREFLIGHT,
                preset=cfg["preset"], model=cfg["preferred_model"],
                target_tracks=cfg["target_tracks"],
                config_json=json.dumps(cfg, ensure_ascii=False),
                staging_dir=str(staging),
            )
            session.add(batch)
            session.commit()
            session.refresh(batch)
        cfg = json.loads(batch.config_json or "{}")

        if not cfg.get("prompt_plan") and batch.create_actions_used:
            raise ValueError("Batch cũ đã tạo nhạc nhưng chưa có prompt plan. Không đổi bản sắc "
                             "giữa batch; hoàn tất/kiểm tra batch cũ trước khi chạy project mới.")
        cfg = prepare_batch_prompts(project, cfg, log)
        batch.config_json = json.dumps(cfg, ensure_ascii=False)
        session.add(batch)
        session.commit()

        token = _acquire_lock(session, batch)
        try:
            return _drive_batch(session, batch, cfg, dry_run, report, log)
        except SunoLoginRequired as e:
            _touch(session, batch, WAITING_FOR_LOGIN, str(e), str(e))
            raise
        except SunoSubmissionUncertain as e:
            _touch(session, batch, SUBMISSION_UNCERTAIN, str(e), str(e))
            raise
        except SunoUIChanged as e:
            _touch(session, batch, UI_CHANGED, str(e), str(e))
            raise
        except SunoBudgetError as e:
            _touch(session, batch, INSUFFICIENT_GENERATION_CREDITS, str(e), str(e))
            raise
        except Exception as e:
            # JobCancelled (pause/cancel) hoặc lỗi khác — giữ state resume được.
            if type(e).__name__ == "JobCancelled":
                if project_id in _cancel_requested:
                    _touch(session, batch, CANCELLED, "Đã huỷ batch Suno.", None)
                else:
                    _touch(session, batch, PAUSED,
                           "Đã tạm dừng — resume để tiếp tục.", None)
            else:
                _touch(session, batch, FAILED, f"Lỗi: {e}", str(e))
            raise
        finally:
            _cancel_requested.discard(project_id)
            _release_lock(session, batch)


def _drive_batch(session: Session, batch: SunoBatch, cfg: dict,
                 dry_run: bool, report: Callable, log: _LOG) -> dict:
    """Thân điều phối. Mỗi phase lưu state trước khi sang phase kế → resume an toàn."""
    target = int(cfg.get("target_tracks", 15))
    staging = Path(batch.staging_dir) if batch.staging_dir else \
        (vconfig.SUNO_STAGING_ROOT / f"project_{batch.project_id}")
    staging.mkdir(parents=True, exist_ok=True)

    with BrowserSession(profile_dir=vconfig.SUNO_PROFILE_DIR) as bs:
        page = bs.new_page()
        driver = SunoDriver(page, dry_run=dry_run,
                            selectors=vconfig.get_selectors("suno"), log=log)

        # ── PREFLIGHT ──
        report("Mở Suno, kiểm tra đăng nhập…", 5)
        _touch(session, batch, PREFLIGHT, "Preflight: mở Suno…")
        driver.open_create_page()
        credits = driver.read_credits()
        if credits is not None:
            log(f"Credits còn lại: {credits}.")
            if not dry_run and credits <= 0:
                raise SunoBudgetError(
                    "Hết credit Suno — không tự mua thêm. Nạp credit rồi resume.")

        # ── Cấu hình form (an toàn cho dry-run) ──
        report("Cấu hình Advanced / model / styles…", 12)
        driver.select_advanced_mode()
        driver.select_model(cfg.get("preferred_model", "v6"),
                            allow_fallback=cfg.get("allow_model_fallback", False))
        if cfg.get("instrumental", True):
            driver.assert_instrumental()
        driver.fill_styles(cfg["styles"])
        driver.open_more_options()
        driver.fill_exclusions(cfg["exclusions"])
        dur = cfg.get("preferred_duration_seconds_min") or None
        driver.set_duration(dur)
        driver.set_max_mode(cfg.get("max_mode", False))

        if dry_run:
            report("DRY-RUN xong: đã điền form & đọc trạng thái, KHÔNG tạo/tải.",
                   100)
            _touch(session, batch, PREFLIGHT,
                   f"DRY-RUN OK (credits={credits}). Chưa tiêu credit.")
            return _summary(session, batch, dry_run=True, credits=credits)

        # ══════════ LIVE ══════════
        # ── GENERATING ──
        _generate_until_target(session, batch, cfg, driver, target,
                               report, log)

        # ── DOWNLOADING + VALIDATING ──
        _download_and_validate(session, batch, cfg, driver, target,
                               staging, report, log)

        # Đủ 15 hợp lệ chưa?
        valid = _count(session, batch, validation_status="valid", selected=True)
        if valid < target:
            _touch(session, batch, WAITING_FOR_HUMAN,
                   f"Mới có {valid}/{target} WAV hợp lệ — cần thêm/kiểm tra thủ công.")
            return _summary(session, batch, dry_run=False, credits=credits)

        # ── READY_FOR_IMPORT → IMPORTED (publish cả batch 1 lần) ──
        report("Đủ 15 WAV hợp lệ — import vào project…", 90)
        _touch(session, batch, READY_FOR_IMPORT, "Sẵn sàng import.")
        _import_validated_tracks(session, batch, log)
        _touch(session, batch, IMPORTED, "Đã import 15 track vào project.")

    # ── auto-continue workflow (ngoài browser session) ──
    # 15 WAV đã import → tự động chạy Mix (Audio) trên chính project này, và ép
    # render Video sau khi mix xong (Suno → Mix → Video, không cần bật auto_video
    # riêng). Mix chạy ở job_manager (worker riêng), Video ở video_job_manager —
    # job Suno này kết thúc ngay sau khối này nên worker video sẽ rảnh cho bước
    # dựng video. Bọc try/except để lỗi downstream không làm hỏng STEP 0 đã xong.
    if cfg.get("auto_continue_workflow", True):
        _touch(session, batch, DOWNSTREAM_RUNNING,
               "Tự chạy tiếp: Mix 15 track → render Video…")
        try:
            from backend.routers.mixes import start_mix_for_project
            mix_id = start_mix_for_project(batch.project_id, force_video=True)
            log(f"Đã bắt đầu Mix #{mix_id}. Video sẽ tự render sau khi mix xong.")
        except Exception as e:      # noqa: BLE001 — downstream là bước phụ
            log(f"Không tự chạy tiếp workflow: {e}. Bạn có thể chạy Mix thủ công.")
    _touch(session, batch, COMPLETED, "Hoàn tất STEP 0.")
    return _summary(session, batch, dry_run=False, credits=None)


def _generate_until_target(session, batch, cfg, driver: SunoDriver, target,
                           report, log) -> None:
    _touch(session, batch, GENERATING, "Đang tạo bài trên Suno…")
    max_creates = int(cfg.get("max_create_actions", 15))
    songs_per_req = _ASSUMED_SONGS_PER_REQUEST
    # Mỗi Create chỉ lấy 1 bài (2 bài na ná) → đếm theo bài ĐÃ CHỌN, không đếm
    # tổng candidate. Cần đủ `target` lượt Create khác nhau mới đủ `target` bài.
    while _count(session, batch, selected=True) < target:
        if batch.create_actions_used >= max_creates:
            raise SunoBudgetError(
                f"Chạm trần max_create_actions={max_creates} nhưng mới chọn được "
                f"{_count(session, batch, selected=True)}/{target} bài. "
                f"Dừng để không tốn thêm.")
        credits = driver.read_credits()
        if credits is not None and credits <= 0:
            raise SunoBudgetError("Hết credit giữa chừng — dừng, resume sau khi nạp.")

        before = driver.snapshot_song_ids()
        item = cfg["prompt_plan"][batch.create_actions_used]
        driver.fill_styles(item["styles"])
        driver.fill_exclusions(item["exclusions"])
        # LƯU submit-intent TRƯỚC khi Create (chống re-click khi timeout).
        batch.create_actions_used += 1
        _touch(session, batch, GENERATING,
               f"Create #{batch.create_actions_used} "
               f"(đã chọn {_count(session, batch, selected=True)}/{target})…")

        driver.click_create()
        new_songs = driver.wait_for_new_songs(
            before, expected=songs_per_req, timeout_sec=300)
        # Đo số bài/req thật ở lần đầu.
        if batch.create_actions_used == 1 and new_songs:
            songs_per_req = max(1, len(new_songs))
            log(f"Đo được: 1 request trả {songs_per_req} bài.")

        req_id = f"req{batch.create_actions_used}"
        for ns in new_songs:
            _record_candidate(session, batch, ns, req_id)
        _mark_selection(session, batch, target)
        report(f"Đã chọn {_count(session, batch, selected=True)}/{target} bài "
               f"(qua {batch.create_actions_used} lượt Create).",
               min(60, 20 + _count(session, batch, selected=True) * 3))


def _download_and_validate(session, batch, cfg, driver: SunoDriver, target,
                           staging: Path, report, log) -> None:
    _touch(session, batch, DOWNLOADING, "Tải WAV các bài đã chọn…")
    minimum = float(cfg.get("minimum_duration_seconds", 120))
    max_dl = int(cfg.get("max_new_song_downloads", 15))

    selected = session.exec(
        select(SunoCandidate).where(
            SunoCandidate.batch_id == batch.id,
            SunoCandidate.selected == True)).all()      # noqa: E712
    known_hashes = {c.sha256 for c in
                    session.exec(select(SunoCandidate).where(
                        SunoCandidate.sha256 != None)).all() if c.sha256}  # noqa: E711

    downloaded = _count(session, batch, download_status="downloaded")
    for c in selected:
        if c.validation_status == "valid" and c.wav_path and Path(c.wav_path).exists():
            continue   # resume: đã xong bài này
        if downloaded >= max_dl:
            raise SunoError(
                f"Chạm trần max_new_song_downloads={max_dl}. Dừng tải để an toàn.")

        safe = "".join(ch for ch in (c.title or c.song_id) if ch.isalnum() or ch in " _-")[:60].strip()
        dest = staging / f"{safe or c.song_id}_{c.song_id[:8]}.wav"
        c.download_status = "downloading"
        session.add(c); session.commit()
        try:
            path = driver.download_wav(c.song_id, dest, timeout_sec=240)
            downloaded += 1
        except Exception as e:
            c.download_status = "failed"
            c.download_error = str(e)
            session.add(c); session.commit()
            log(f"Tải WAV lỗi ({c.song_id}): {e}")
            continue

        # Xác thực kỹ thuật (KHÔNG tiêu credit) — WAV thật, đủ dài, không im lặng.
        res = suno_verify.verify_wav(path, minimum_duration_seconds=minimum,
                                     known_hashes=known_hashes)
        c.wav_path = path
        c.download_status = "downloaded"
        c.sha256 = res.sha256
        c.verified_duration_seconds = res.duration_seconds
        c.verified_sample_rate = res.sample_rate
        c.verified_channels = res.channels
        c.content_qc_status = "not_verified"   # không có analyzer → không bịa
        if res.valid:
            c.validation_status = "valid"
            c.validation_error = None
            if res.sha256:
                known_hashes.add(res.sha256)
        else:
            c.validation_status = "invalid"
            c.validation_error = res.reason
            log(f"WAV không hợp lệ ({c.song_id}): {res.reason}")
        session.add(c); session.commit()
        _sync_counters(session, batch)
        report(f"Tải+kiểm {downloaded} WAV "
               f"(hợp lệ {_count(session, batch, validation_status='valid')}).",
               min(88, 62 + downloaded * 2))

    _touch(session, batch, VALIDATING, "Đã tải/kiểm tra xong đợt này.")


# ── helpers manifest ──────────────────────────────────────────────
def _record_candidate(session, batch, ns: NewSong, req_id: str) -> SunoCandidate:
    existing = session.exec(
        select(SunoCandidate).where(
            SunoCandidate.batch_id == batch.id,
            SunoCandidate.song_id == ns.song_id)).first()
    if existing:
        return existing
    c = SunoCandidate(
        batch_id=batch.id, project_id=batch.project_id,
        song_id=ns.song_id, request_id=req_id,
        title=ns.title or "", duration_seconds=ns.duration_seconds,
    )
    session.add(c)
    session.commit()
    session.refresh(c)
    _sync_counters(session, batch)
    return c


def _mark_selection(session, batch, target: int) -> None:
    """Mỗi lượt Create (request) CHỈ chọn 1 bài — 2 bài cùng 1 Create thường na
    ná nhau nên chỉ lấy bài đầu, bài còn lại là spare (không tải/import). Do đó
    cần đủ `target` lượt Create để có `target` bài khác nhau."""
    cands = session.exec(
        select(SunoCandidate).where(SunoCandidate.batch_id == batch.id)
        .order_by(SunoCandidate.created_at)).all()
    seen_requests: set[str] = set()
    selected_count = 0
    for c in cands:
        # Chọn bài ĐẦU của mỗi request, tối đa `target` bài.
        want = (selected_count < target and c.request_id not in seen_requests)
        if want:
            seen_requests.add(c.request_id)
            selected_count += 1
        if c.selected != want or c.is_spare == want:
            c.selected = want
            c.is_spare = not want
            session.add(c)
    session.commit()
    _sync_counters(session, batch)


def _count(session, batch, **filters) -> int:
    stmt = select(SunoCandidate).where(SunoCandidate.batch_id == batch.id)
    for k, v in filters.items():
        stmt = stmt.where(getattr(SunoCandidate, k) == v)
    return len(session.exec(stmt).all())


def _sync_counters(session, batch) -> None:
    batch.generated_candidates = _count(session, batch)
    batch.selected_tracks = _count(session, batch, selected=True)
    batch.downloaded_tracks = _count(session, batch, download_status="downloaded")
    batch.validated_tracks = _count(session, batch, validation_status="valid")
    batch.updated_at = _now()
    session.add(batch)
    session.commit()


def _summary(session, batch, *, dry_run: bool, credits: Optional[int]) -> dict:
    _sync_counters(session, batch)
    return {
        "batch_id": batch.id,
        "project_id": batch.project_id,
        "phase": batch.phase,
        "message": batch.message,
        "dry_run": dry_run,
        "credits_remaining": credits,
        "preset": batch.preset,
        "model": batch.model,
        "target_tracks": batch.target_tracks,
        "generated_candidates": batch.generated_candidates,
        "selected_tracks": batch.selected_tracks,
        "downloaded_tracks": batch.downloaded_tracks,
        "validated_tracks": batch.validated_tracks,
        "imported_tracks": batch.imported_tracks,
        "create_actions_used": batch.create_actions_used,
        "staging_dir": batch.staging_dir,
    }
