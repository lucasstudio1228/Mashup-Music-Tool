"""
batch_service.py — Orchestrator sản xuất HÀNG LOẠT N sản phẩm YouTube.

Ý tưởng: tái dùng NGUYÊN chuỗi một-chạm của từng project (Suno STEP 0 → Mix →
Video → đăng nháp YouTube) và chạy TUẦN TỰ cho lần lượt N project. Giữ nguyên
nhạc cụ + style ảnh + thể loại cho cả lô; AI sinh tên/ý tưởng/mô tả/prompt khác
nhau cho từng project (backend/video/batch_ideas.py).

RÀNG BUỘC AN TOÀN (bám sát):
  • Chạy tuần tự tuyệt đối — 1 VideoJobManager toàn cục (max_workers=1) + Suno
    account lock. Không bao giờ submit song song.
  • "Chuỗi project i xong" KHÔNG chỉ là "job Suno xong": start_mix_for_project chỉ
    *submit* mix rồi trả ngay, và mix→video bàn giao qua 2 manager khác nhau
    (job_manager cho mix, video_job_manager cho video). Vì job mix được đánh dấu
    completed TRƯỚC khi on_success submit video (job_manager._run), có một khe hở
    ngắn cả hai manager cùng rảnh. Do đó điều kiện "xong" = Suno phase terminal
    (COMPLETED/CANCELLED) VÀ cả hai manager rảnh, GIỮ ỔN ĐỊNH qua nhiều lần poll
    liên tiếp (debounce) để không nhảy sớm ngay tại khe bàn giao.
  • Gặp trạng thái blocking của Suno (login/credit/UI đổi/...) → DỪNG lô an toàn,
    báo rõ project nào ở phase nào; không kéo các project còn lại vào lỗi.
  • Không tự mua credit, không publish (YouTube chỉ lưu nháp qua luồng hiện có).
"""
from __future__ import annotations

import json
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from sqlmodel import Session, select

from backend.database import engine
from backend.models import BatchRun, Project
from backend.job_manager import job_manager               # mix manager
from backend.video.job_manager import video_job_manager   # suno + video manager
from backend.video import suno_service

# Phase Suno chặn lô — cần người xử lý rồi mới resume được.
_BLOCKING = {
    suno_service.WAITING_FOR_LOGIN,
    suno_service.WAITING_FOR_HUMAN,
    suno_service.SUBMISSION_UNCERTAIN,
    suno_service.INSUFFICIENT_GENERATION_CREDITS,
    suno_service.INSUFFICIENT_DOWNLOAD_ALLOWANCE,
    suno_service.UI_CHANGED,
    suno_service.FAILED,
    suno_service.PAUSED,
}
_TERMINAL_OK = {suno_service.COMPLETED}


def _suno_done(st: Optional[dict]) -> bool:
    """Khâu Suno của project đã xong? COMPLETED, HOẶC đã nhập đủ số bài mục tiêu
    mà phase còn kẹt ở bước trung gian (vd DOWNLOADING do khâu rà soát tạo lại
    bài ghi đè phase) — lúc đó chạy lại Suno là đốt credit vô ích và lô từng chờ
    COMPLETED tới trần 12 giờ. Gọi khi chuỗi đã RẢNH (không còn job Suno chạy)."""
    st = st or {}
    phase = st.get("phase")
    if phase in _TERMINAL_OK:
        return True
    if phase in _BLOCKING or phase in (None, suno_service.CANCELLED):
        return False
    imported = int(st.get("imported_tracks") or 0)
    target = int(st.get("target_tracks") or 0)
    return target > 0 and imported >= target

# Chặn CỨNG: cần con người (đăng nhập / nạp credit / tự tay pause) — thử lại vô
# ích và còn đốt thêm credit ⇒ dừng cả lô, báo rõ.
_HARD_STOP = {
    suno_service.WAITING_FOR_LOGIN,
    suno_service.INSUFFICIENT_GENERATION_CREDITS,
    suno_service.INSUFFICIENT_DOWNLOAD_ALLOWANCE,
    suno_service.PAUSED,
}
# Số lần TỰ resume 1 sản phẩm trước khi bỏ qua nó và chạy sản phẩm kế.
_MAX_RETRY_PER_PRODUCT = 2

# Lỗi Video ở mức TÀI KHOẢN (Flow/Gemini gắn cờ chống tự động hoá, hết hạn mức):
# sản phẩm nào cũng sẽ hỏng y hệt ⇒ không "bỏ qua sang sản phẩm kế" (mỗi sản phẩm
# kế lại đốt 15 credit Suno mà vẫn không ra video) — thử lại tại chỗ rồi DỪNG lô.
_ACCOUNT_BLOCK_MARKERS = ("hoạt động bất thường", "unusual activity",
                          "flowblocked", "google flow từ chối",
                          # Gemini Video hết lượt/giới hạn (gemini_video.GeminiVideoLimit)
                          "gemini báo hết lượt", "geminivideolimit",
                          # Muse.ai (muse_video): hết lượt / kẹt kết nối / mất đăng nhập
                          "muse báo hết lượt", "muse chưa sẵn sàng",
                          "muse.ai đang ở trang đăng nhập", "không mở được profile gpm")

# Poll & debounce
_POLL_SEC = 5.0
_IDLE_STREAK_REQUIRED = 3      # số lần poll liên tiếp thấy cả 2 manager rảnh
# Trần an toàn 1 sản phẩm (tránh kẹt vô hạn). Flow chạy chậm kiểu người (40 clip,
# 4 clip/phiên, nghỉ 20–30' giữa phiên) + upload 5 GB ≈ 6–8 giờ ⇒ để 12 giờ.
_PER_PRODUCT_TIMEOUT_SEC = 12 * 3600
_SUBMIT_GRACE_SEC = 30         # chờ job vừa submit vào worker rồi mới chấm "rảnh"

# Cờ huỷ theo batch_key (thread orchestrator đọc mỗi vòng).
_cancel_flags: dict[str, threading.Event] = {}
_threads: dict[str, threading.Thread] = {}
# Khoá chống bấm «Tạo» 2 lần khi AI đang nghĩ ý tưởng (call LLM > 30s).
_create_lock = threading.Lock()


def _active_batch_key() -> Optional[str]:
    """batch_key của lô có thread orchestrator còn sống (tối đa 1 lô chạy)."""
    return next((k for k, t in _threads.items() if t.is_alive()), None)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _touch(session: Session, run: BatchRun, **kw) -> None:
    for k, v in kw.items():
        setattr(run, k, v)
    run.updated_at = _now()
    session.add(run)
    session.commit()
    session.refresh(run)


def get_batch(batch_key: str) -> Optional[dict]:
    """Trạng thái lô + danh sách project (id/name/phase Suno) cho UI."""
    with Session(engine) as session:
        run = session.exec(
            select(BatchRun).where(BatchRun.batch_key == batch_key)).first()
        if run is None:
            return None
        projects = session.exec(
            select(Project).where(Project.batch_id == batch_key)
            .order_by(Project.id)).all()
        items = []
        for p in projects:
            batch = suno_service.batch_status(p.id)
            items.append({
                "project_id": p.id,
                "name": p.name,
                "suno_phase": (batch or {}).get("phase"),
                "suno_message": (batch or {}).get("message"),
            })
        out = _run_dict(run, items)
        # DB ghi "running" nhưng thread orchestrator đã chết (server khởi động
        # lại giữa chừng) → báo "interrupted" để UI hiện nút Tiếp tục lô.
        t = _threads.get(batch_key)
        if out["status"] == "running" and (t is None or not t.is_alive()):
            out["status"] = "interrupted"
        return out


def _run_dict(run: BatchRun, items: list[dict]) -> dict:
    return {
        "batch_key": run.batch_key,
        "total": run.total,
        "completed": run.completed,
        "failed": run.failed,
        "status": run.status,
        "current_index": run.current_index,
        "current_project_id": run.current_project_id,
        "message": run.message,
        "params": json.loads(run.params_json or "{}"),
        "created_at": run.created_at,
        "updated_at": run.updated_at,
        "projects": items,
    }


def create_batch(count: int, instrument: str, music_style: str, video_style: str,
                 theme: str = "", *, target_tracks: Optional[int] = None,
                 max_create_actions: Optional[int] = None,
                 channel_name: str = "",
                 mix_duration_minutes: float = 120.0) -> dict:
    """
    Sinh N ý tưởng phân biệt (1 call LLM, KHÔNG tốn credit Suno) → tạo N Project
    cùng batch_id → tạo BatchRun → khởi động thread chạy tuần tự.

    Ném RuntimeError nếu đầu vào không hợp lệ hoặc AI không sinh đủ ý tưởng
    (không tạo project rác).
    """
    from backend.database import DB_PATH
    from backend.core_bridge import get_api_config_from_db
    from backend.video import batch_ideas
    from backend.video import config as vconfig

    count = int(count)
    if not 1 <= count <= 50:
        raise RuntimeError("Số lượng sản phẩm phải từ 1 đến 50.")
    instrument = (instrument or "").strip()
    music_style = (music_style or "").strip()
    # Dùng chính bộ phong cách của video/config (10 key) — không tự chế danh sách
    # riêng, tránh gửi key lạ rồi bị normalize_style âm thầm đổi về "2d".
    style_key = vconfig.normalize_style(video_style)
    if not instrument:
        raise RuntimeError("Thiếu loại nhạc cụ cho lô sản xuất.")
    if not music_style:
        raise RuntimeError("Thiếu thể loại nhạc cho lô sản xuất.")

    tgt = int(target_tracks) if target_tracks else int(vconfig.SUNO.target_tracks)
    mca = (int(max_create_actions) if max_create_actions
           else int(vconfig.SUNO.max_create_actions))

    # Chỉ 1 lô chạy một lúc (worker Suno/Video tuần tự) — chặn cả lần bấm lại khi
    # trình duyệt đã báo timeout nhưng server vẫn đang tạo lô trước đó.
    active = _active_batch_key()
    if active:
        raise RuntimeError("Đang có 1 lô sản xuất chạy — đợi lô đó xong hoặc huỷ "
                           "rồi mới tạo lô mới (tránh tạo trùng).")
    if not _create_lock.acquire(blocking=False):
        raise RuntimeError("Đang tạo 1 lô khác (AI đang nghĩ ý tưởng) — đợi ít phút, "
                           "lô sẽ hiện ở thanh tiến độ.")
    try:
        api_config = get_api_config_from_db(str(DB_PATH))
        ideas = batch_ideas.generate_batch_ideas(
            count, instrument, style_key, theme, api_config, music_style=music_style)
        return _create_batch_rows(ideas, count, instrument, music_style, style_key,
                                  theme, channel_name, tgt, mca, mix_duration_minutes)
    finally:
        _create_lock.release()


def _create_batch_rows(ideas, count, instrument, music_style, style_key, theme,
                       channel_name, tgt, mca, mix_duration_minutes) -> dict:
    batch_key = uuid.uuid4().hex
    params = {
        "count": count, "instrument": instrument, "music_style": music_style,
        "video_style": style_key, "theme": theme, "channel_name": channel_name,
        "target_tracks": tgt, "max_create_actions": mca,
        "mix_duration_minutes": float(mix_duration_minutes),
    }

    with Session(engine) as session:
        created_ids = []
        for it in ideas:
            p = Project(
                name=it["project_name"],
                description=it["description"],
                video_idea=it["video_idea"],
                suno_idea=it["suno_idea"],
                video_style=style_key,
                instrument=instrument,
                music_style=music_style,
                music_source="suno",
                auto_video=True,
                auto_upload=True,
                batch_id=batch_key,
                mix_duration_minutes=float(mix_duration_minutes),
            )
            session.add(p)
            session.commit()
            session.refresh(p)
            created_ids.append(p.id)

        run = BatchRun(
            batch_key=batch_key, total=count, status="running",
            current_index=0, current_project_id=created_ids[0],
            params_json=json.dumps(params, ensure_ascii=False),
            message="Đã tạo N project — bắt đầu chạy tuần tự.",
        )
        session.add(run)
        session.commit()
        session.refresh(run)

    _cancel_flags[batch_key] = threading.Event()
    t = threading.Thread(target=_run_batch, args=(batch_key,),
                         name=f"batch-{batch_key[:8]}", daemon=True)
    _threads[batch_key] = t
    t.start()

    return get_batch(batch_key)


def cancel_batch(batch_key: str) -> Optional[dict]:
    """Yêu cầu dừng lô: set cờ + huỷ job hiện tại (Suno/Video) của project đang chạy.
    Mix đang render (job_manager, khoá theo mix_id) để chạy nốt — không kéo dài."""
    ev = _cancel_flags.get(batch_key)
    if ev is not None:
        ev.set()
    with Session(engine) as session:
        run = session.exec(
            select(BatchRun).where(BatchRun.batch_key == batch_key)).first()
        if run is None:
            return None
        pid = run.current_project_id
    if pid is not None:
        suno_service.request_cancel(pid)
        video_job_manager.cancel(pid)   # huỷ job suno HOẶC video của project này
    return get_batch(batch_key)


def resume_batch(batch_key: str) -> Optional[dict]:
    """Chạy TIẾP lô đã dừng (blocked / cancelled / completed còn sản phẩm lỗi /
    server khởi động lại giữa chừng). Sản phẩm đã có final.mp4 + nháp YouTube
    được bỏ qua; sản phẩm dở dang nối lại đúng bước còn thiếu (Suno resume batch
    cũ, không tạo batch Suno mới)."""
    t = _threads.get(batch_key)
    if t is not None and t.is_alive():
        raise ValueError("Lô này đang chạy — không cần tiếp tục.")
    if _chain_busy():
        raise ValueError("Đang có job Suno/Mix/Video chạy — đợi xong (hoặc huỷ) "
                         "rồi mới tiếp tục lô.")
    with Session(engine) as session:
        run = session.exec(
            select(BatchRun).where(BatchRun.batch_key == batch_key)).first()
        if run is None:
            return None
        _touch(session, run, status="running", completed=0, failed=0,
               message="Tiếp tục lô — kiểm tra sản phẩm đã xong, nối lại chỗ dừng.")

    _cancel_flags[batch_key] = threading.Event()
    t = threading.Thread(target=_run_batch, args=(batch_key,),
                         kwargs={"resume": True},
                         name=f"batch-{batch_key[:8]}", daemon=True)
    _threads[batch_key] = t
    t.start()
    return get_batch(batch_key)


# ══════════════════════════════════════════════════════════════════
# Vòng lặp tuần tự (chạy trong daemon thread)
# ══════════════════════════════════════════════════════════════════
def _run_batch(batch_key: str, resume: bool = False) -> None:
    ev = _cancel_flags.get(batch_key) or threading.Event()

    with Session(engine) as session:
        projects = session.exec(
            select(Project).where(Project.batch_id == batch_key)
            .order_by(Project.id)).all()
        run = session.exec(
            select(BatchRun).where(BatchRun.batch_key == batch_key)).first()
        if run is None or not projects:
            return
        params = json.loads(run.params_json or "{}")
        project_ids = [p.id for p in projects]

    overrides = {
        "target_tracks": params.get("target_tracks", 15),
        "max_create_actions": params.get("max_create_actions", 15),
        "max_new_song_downloads": params.get("target_tracks", 15),
    }

    for idx, pid in enumerate(project_ids):
        if ev.is_set():
            _finish(batch_key, "cancelled", f"Đã huỷ lô ở sản phẩm {idx + 1}.")
            return

        name = f"#{pid}"
        suno_idea = ""
        with Session(engine) as session:
            run = session.exec(
                select(BatchRun).where(BatchRun.batch_key == batch_key)).first()
            proj = session.get(Project, pid)
            if proj is not None:
                name = proj.name
                suno_idea = proj.suno_idea or ""
                # Chốt lại cấu hình an toàn cho từng project (phòng khi bị đổi).
                proj.music_source = "suno"
                proj.auto_video = True
                proj.auto_upload = True
                session.add(proj)
                session.commit()
            _touch(session, run, current_index=idx, current_project_id=pid,
                   message=f"Sản phẩm {idx + 1}/{len(project_ids)}: «{name}» — bắt đầu Suno.")

        if resume and _verify_product(pid)[0]:
            with Session(engine) as session:
                run = session.exec(
                    select(BatchRun).where(BatchRun.batch_key == batch_key)).first()
                _touch(session, run, completed=run.completed + 1,
                       message=f"Sản phẩm {idx + 1}/{len(project_ids)} «{name}» đã xong từ trước.")
            continue

        # ── Đợi worker rảnh rồi submit Suno (LIVE) cho project i ──
        if not _wait_until_idle(ev, timeout_sec=600):
            _finish(batch_key, "blocked",
                    f"Sản phẩm {idx + 1}: worker bận quá lâu trước khi bắt đầu.")
            return
        if ev.is_set():
            _finish(batch_key, "cancelled", f"Đã huỷ lô ở sản phẩm {idx + 1}.")
            return

        ov = dict(overrides)
        ov["suno_idea"] = suno_idea

        # ── Chạy + TỰ THỬ LẠI sản phẩm i (resume, không tạo batch Suno mới) ──
        attempt = 0
        outcome, detail, phase = "blocked", "chưa chạy", None
        acct: Optional[str] = None
        while True:
            # Khi tiếp tục lô: nối lại batch Suno dở ngay lần đầu (_submit_stage
            # tự bỏ qua resume nếu batch đã terminal / chưa có).
            stage = _submit_stage(pid, ov, resume=(resume or attempt > 0))
            if stage is None:
                outcome, detail = "blocked", "không submit được bước tiếp theo"
                break

            outcome, detail, phase = _await_product_chain(batch_key, pid, ev)

            if outcome == "done":
                ok, why = _verify_product(pid)
                if ok:
                    break
                detail = why
                outcome = "blocked"     # thiếu final.mp4 / nháp YouTube → thử lại
                acct = _account_block(pid)
                if acct:
                    # Chặn mức tài khoản: chạy lại chỉ làm cờ bám chặt hơn →
                    # DỪNG ngay, không thử lại (người dùng xử lý rồi resume lô).
                    detail = f"Máy tạo video (Muse/Flow/Gemini) chặn/giới hạn ở mức tài khoản — {acct[:200]}"
                    break
            if outcome == "cancelled":
                break
            if phase in _HARD_STOP:
                break                   # cần người — không thử lại
            attempt += 1
            if attempt > _MAX_RETRY_PER_PRODUCT:
                break
            with Session(engine) as session:
                run = session.exec(
                    select(BatchRun).where(BatchRun.batch_key == batch_key)).first()
                _touch(session, run,
                       message=(f"Sản phẩm {idx + 1}/{len(project_ids)} «{name}» lỗi "
                                f"({detail}) — tự chạy lại lần {attempt}/"
                                f"{_MAX_RETRY_PER_PRODUCT}."))
            if not _wait_until_idle(ev, timeout_sec=900) or ev.is_set():
                outcome, detail = "cancelled", "huỷ khi chờ chạy lại"
                break

        with Session(engine) as session:
            run = session.exec(
                select(BatchRun).where(BatchRun.batch_key == batch_key)).first()
            if outcome == "done":
                _touch(session, run, completed=run.completed + 1,
                       message=f"Sản phẩm {idx + 1}/{len(project_ids)} xong: {detail}")
            elif outcome == "cancelled":
                _touch(session, run, message=f"Đã huỷ ở sản phẩm {idx + 1}.")
                _finish(batch_key, "cancelled", f"Đã huỷ lô ở sản phẩm {idx + 1}.")
                return
            elif phase in _HARD_STOP or acct:
                # Đăng nhập/credit/pause/Flow chặn tài khoản: sản phẩm kế cũng hỏng
                # y hệt (lại còn đốt credit Suno) ⇒ dừng lô, chờ người xử lý rồi resume.
                _touch(session, run, failed=run.failed + 1,
                       message=(f"DỪNG lô ở sản phẩm {idx + 1}/{len(project_ids)} "
                                f"«{name}»: {detail}"))
                _finish(batch_key, "blocked",
                        f"Sản phẩm {idx + 1} «{name}» bị chặn: {detail}")
                return
            else:
                # Lỗi riêng của sản phẩm này → bỏ qua, chạy tiếp sản phẩm sau.
                _touch(session, run, failed=run.failed + 1,
                       message=(f"Bỏ qua sản phẩm {idx + 1}/{len(project_ids)} "
                                f"«{name}» sau {_MAX_RETRY_PER_PRODUCT} lần thử: "
                                f"{detail}"))

    with Session(engine) as session:
        run = session.exec(
            select(BatchRun).where(BatchRun.batch_key == batch_key)).first()
        n_ok = run.completed if run else 0
        n_bad = run.failed if run else 0
    _finish(batch_key, "completed",
            f"Hoàn tất lô: {n_ok}/{len(project_ids)} sản phẩm xong"
            + (f", {n_bad} sản phẩm lỗi (xem từng project)." if n_bad else "."))


def _project_paths(pid: int) -> tuple[Path, str]:
    """(thư mục final, tên project)."""
    from backend.video import config as vconfig
    with Session(engine) as session:
        proj = session.get(Project, pid)
        name = proj.name if proj else ""
    return vconfig.final_dir(pid, name), name


def _has_youtube_draft(pid: int) -> bool:
    from backend.models import YoutubeUpload
    with Session(engine) as session:
        rows = session.exec(
            select(YoutubeUpload).where(YoutubeUpload.project_id == pid)).all()
    return any((r.status or "") == "draft" for r in rows)


def _chain_busy() -> bool:
    """Còn bước nào của chuỗi đang chạy: worker video/Suno, mix, hoặc khâu rà
    soát → tự tạo lại Suno → vá mix (giữa các job, cả 2 manager có lúc rảnh)."""
    from backend.video import qc_autofix
    return (video_job_manager.is_busy() or job_manager.is_busy()
            or qc_autofix.is_active())


def _account_block(pid: int) -> Optional[str]:
    """Lỗi của job Video gần nhất nếu đó là chặn ở mức tài khoản, ngược lại None."""
    job = video_job_manager.get(pid)
    err = (getattr(job, "error", None) or "") if job else ""
    if job is None or job.status != "failed" or not err:
        return None
    low = err.lower()
    return err if any(m in low for m in _ACCOUNT_BLOCK_MARKERS) else None


def _verify_product(pid: int) -> tuple[bool, str]:
    """Sản phẩm CHỈ được tính là xong khi có final.mp4 VÀ đã lưu nháp YouTube —
    không tin mỗi 'job đã chạy xong' (job video có thể hỏng mà Suno vẫn
    COMPLETED, lúc đó cả 2 manager đều rảnh và dễ bị chấm nhầm là hoàn tất)."""
    final = _project_paths(pid)[0] / "final.mp4"
    if not final.exists():
        return (False, "thiếu final.mp4")
    if not _has_youtube_draft(pid):
        return (False, "chưa có bản nháp YouTube")
    return (True, "final.mp4 + bản nháp YouTube đã có")


def _submit_stage(pid: int, ov: dict, *, resume: bool) -> Optional[str]:
    """Submit ĐÚNG bước còn thiếu của project và trả tên bước.

    Chạy lại không phải lúc nào cũng là chạy lại Suno: nếu Suno đã COMPLETED mà
    hỏng ở khâu sau thì phải nối lại từ Mix / Video / Upload, nếu không sẽ đốt
    thêm credit Suno vô ích (hoặc resume lỗi vì batch đã terminal).
    """
    from backend.video import service as video_service

    st = suno_service.batch_status(pid) or {}
    phase = st.get("phase")
    imported = int(st.get("imported_tracks") or 0)

    if not (_suno_done(st) and imported > 0):
        can_resume = resume and phase not in (None, suno_service.COMPLETED,
                                              suno_service.CANCELLED)
        video_job_manager.submit(pid, "suno", suno_service.run_suno_step0,
                                 dry_run=False, overrides=ov, resume=can_resume)
        return "suno"

    final_dir, name = _project_paths(pid)
    if not (final_dir / "final.mp4").exists():
        # Có mix chưa? Chưa thì chạy Mix (mix xong tự kéo sang Video).
        from backend.models import Mix
        with Session(engine) as session:
            proj = session.get(Project, pid)
            mixes = session.exec(select(Mix).where(Mix.project_id == pid)).all()
            idea = (proj.video_idea if proj else "") or ""
            style = (proj.video_style if proj else None)
        done_mix = [m for m in mixes
                    if (m.status or "") == "completed" and m.output_dir
                    and (Path(m.output_dir) / "mix.wav").exists()]
        if not done_mix:
            from backend.routers.mixes import start_mix_for_project
            start_mix_for_project(pid, force_video=True)
            return "mix"
        audio = str(Path(done_mix[-1].output_dir) / "mix.wav")
        # Chạy lại Video luôn ở chế độ resume: GIỮ ảnh + clip đã tạo (restart từng
        # xoá sạch 28 clip của lần trước chỉ vì bước sau lỗi).
        video_job_manager.submit(pid, "full", video_service.run_full_video,
                                 (name or "meditation"), audio, None, name,
                                 (idea or None), style, mode="resume")
        return "video"

    if not _has_youtube_draft(pid):
        with Session(engine) as session:
            proj = session.get(Project, pid)
            instrument = (proj.instrument if proj else "") or ""
            style_music = (proj.music_style if proj else "") or ""
        video_job_manager.submit(pid, "upload", video_service.step_upload_youtube,
                                 name, instrument, style_music)
        return "upload"
    return "done"


def _await_product_chain(batch_key: str, pid: int,
                         ev: threading.Event) -> tuple[str, str, Optional[str]]:
    """
    Trả (outcome, detail, suno_phase):
      • "done"      — Suno COMPLETED và cả 2 manager rảnh ổn định (chuỗi xong).
      • "blocked"   — Suno rơi vào phase blocking (login/credit/UI/...).
      • "cancelled" — người dùng huỷ lô.
    """
    start = time.monotonic()
    idle_streak = 0

    # Job vừa submit cần vài giây mới vào worker — đừng chấm "rảnh" ngay.
    grace = time.monotonic() + _SUBMIT_GRACE_SEC
    while time.monotonic() < grace and not ev.is_set():
        if _chain_busy():
            break
        time.sleep(min(1.0, _POLL_SEC))

    while True:
        if ev.is_set():
            return ("cancelled", "huỷ theo yêu cầu", None)

        batch = suno_service.batch_status(pid)
        phase = (batch or {}).get("phase")

        if time.monotonic() - start > _PER_PRODUCT_TIMEOUT_SEC:
            return ("blocked", "quá thời gian tối đa cho 1 sản phẩm — dừng an toàn.",
                    phase)

        both_idle = not _chain_busy()

        # Phase trong DB có thể còn là phase CŨ của lượt trước (vd WAITING_FOR_HUMAN)
        # trong lúc job resume vừa submit đang chạy → chỉ chốt khi worker đã rảnh.
        if phase in _BLOCKING and both_idle:
            msg = (batch or {}).get("message") or phase
            return ("blocked", f"Suno ở phase {phase} — {msg}", phase)
        if phase == suno_service.CANCELLED and both_idle:
            return ("cancelled", "batch Suno đã bị huỷ", phase)

        if both_idle and _suno_done(batch):
            idle_streak += 1
            if idle_streak >= _IDLE_STREAK_REQUIRED:
                return ("done",
                        "Suno + Mix + Video đã hoàn tất (đăng nháp nếu cấu hình đủ).",
                        phase)
        else:
            idle_streak = 0

        time.sleep(_POLL_SEC)


def _wait_until_idle(ev: threading.Event, timeout_sec: float) -> bool:
    """Đợi tới khi CẢ 2 manager rảnh (để submit an toàn). False nếu quá hạn/huỷ."""
    start = time.monotonic()
    while time.monotonic() - start < timeout_sec:
        if ev.is_set():
            return False
        if not _chain_busy():
            return True
        time.sleep(_POLL_SEC)
    return False


def _finish(batch_key: str, status: str, message: str) -> None:
    with Session(engine) as session:
        run = session.exec(
            select(BatchRun).where(BatchRun.batch_key == batch_key)).first()
        if run is not None:
            _touch(session, run, status=status, current_project_id=None,
                   message=message)
