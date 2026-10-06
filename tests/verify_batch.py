"""
verify_batch.py — Kiểm thử orchestrator sản xuất HÀNG LOẠT (backend/batch_service.py)
KHÔNG tiêu credit Suno, KHÔNG mở trình duyệt, KHÔNG gọi OpenAI.

Chạy:  python tests/verify_batch.py

Thay `batch_ideas.generate_batch_ideas` và `suno_service.run_suno_step0` bằng fake
để kiểm phần DỄ SAI NHẤT của tính năng — điều phối tuần tự qua 2 job manager:

  1. create_batch tạo N Project cùng batch_id, music_source='suno', auto_video +
     auto_upload BẬT, instrument/music_style/video_style giữ nguyên cả lô; + BatchRun.
  2. Chạy TUẦN TỰ đúng thứ tự, và chỉ chuyển project i→i+1 sau khi chuỗi i kết thúc
     (Suno phase terminal VÀ cả video_job_manager lẫn job_manager đều rảnh, debounce).
  3. Huỷ lô giữa chừng → dừng đúng, không chạy nốt các project còn lại.
  4. Suno rơi vào phase blocking (vd WAITING_FOR_LOGIN) → DỪNG lô an toàn ở đúng
     project đó, không kéo các project sau vào lỗi.

Mọi row test (Project / BatchRun / SunoBatch) đều được dọn sạch sau khi chạy.
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
import console  # noqa: F401 - UTF-8 output trên Windows console

from sqlmodel import Session, select

from backend import batch_service
from backend.database import engine
from backend.models import BatchRun, Project, SunoBatch, YoutubeUpload
from backend.video import batch_ideas, config as vconfig, suno_service

PASS = "  ✅ PASS"
FAIL = "  ❌ FAIL"

# Poll nhanh cho test (production: 5s).
batch_service._POLL_SEC = 0.2
batch_service._IDLE_STREAK_REQUIRED = 3
batch_service._SUBMIT_GRACE_SEC = 2


def _print(title, results):
    print(f"\n=== {title} ===")
    ok = True
    for name, passed, *extra in results:
        print(f"{PASS if passed else FAIL} {name}" +
              (f" — {extra[0]}" if extra and not passed else ""))
        ok &= bool(passed)
    return ok


def _fake_generate(prefix, seen=None):
    """Fake generator; `seen` (dict) nhận lại đúng đầu vào AI được truyền."""
    def gen(count, instrument, video_style, theme, api_config, log=None,
            music_style=""):
        if seen is not None:
            seen.update({"count": count, "instrument": instrument,
                         "video_style": video_style, "theme": theme,
                         "music_style": music_style})
        return [{"project_name": f"{prefix} {i}", "video_idea": f"scene {i}",
                 "suno_idea": f"flute {i}", "description": f"desc {i}"}
                for i in range(1, count + 1)]
    return gen


def _mark_batch(project_id, phase, message=""):
    with Session(engine) as s:
        s.add(SunoBatch(project_id=project_id,
                        idempotency_key=f"verify:{project_id}:{time.time()}",
                        phase=phase, message=message))
        s.commit()


def _fake_artifacts(project_id):
    """Giả lập THÀNH PHẨM của cả chuỗi: final.mp4 + bản nháp YouTube.

    `batch_service._verify_product` chỉ chấm "xong" khi có đủ 2 thứ này, nên fake
    chỉ mark Suno COMPLETED là chưa đủ (sẽ bị coi là lỗi và chạy lại).
    """
    with Session(engine) as s:
        proj = s.get(Project, project_id)
        name = proj.name if proj else ""
    d = vconfig.final_dir(project_id, name)
    d.mkdir(parents=True, exist_ok=True)
    (d / "final.mp4").write_bytes(b"fake")
    with Session(engine) as s:
        s.add(YoutubeUpload(project_id=project_id, profile_id="verify-fake",
                            channel_name="Test", title=f"fake {project_id}",
                            video_path=str(d / "final.mp4"), status="draft"))
        s.commit()


def _wait_terminal(key, timeout=40.0):
    start = time.monotonic()
    while time.monotonic() - start < timeout:
        st = batch_service.get_batch(key)
        if st["status"] in ("completed", "cancelled", "blocked", "failed"):
            return st
        time.sleep(0.2)
    return batch_service.get_batch(key)


def _cleanup(key):
    import shutil
    with Session(engine) as s:
        projs = s.exec(select(Project).where(Project.batch_id == key)).all()
        pids = [p.id for p in projs]
        for p in projs:
            try:
                shutil.rmtree(vconfig.project_dir(p.id, p.name),
                              ignore_errors=True)
            except Exception:
                pass
        if pids:
            for b in s.exec(select(SunoBatch)
                            .where(SunoBatch.project_id.in_(pids))).all():
                s.delete(b)
            for u in s.exec(select(YoutubeUpload)
                            .where(YoutubeUpload.project_id.in_(pids))).all():
                s.delete(u)
        for p in s.exec(select(Project).where(Project.batch_id == key)).all():
            s.delete(p)
        for r in s.exec(select(BatchRun).where(BatchRun.batch_key == key)).all():
            s.delete(r)
        s.commit()


def test_sequential_completion():
    """Tạo lô + chạy tuần tự hết 3 sản phẩm."""
    order = []

    def fake_run(project_id, progress_cb, dry_run=False, overrides=None, resume=False):
        order.append(project_id)
        progress_cb("fake suno", 10.0)
        time.sleep(1.0)
        _mark_batch(project_id, suno_service.COMPLETED, "fake completed")
        _fake_artifacts(project_id)
        return {"phase": "COMPLETED"}

    batch_ideas.generate_batch_ideas = _fake_generate("VERIFY seq")
    suno_service.run_suno_step0 = fake_run

    res = batch_service.create_batch(
        count=3, instrument="bamboo flute", music_style="ambient",
        video_style="2d", theme="", channel_name="Test")
    key = res["batch_key"]
    results = []
    try:
        with Session(engine) as s:
            projs = s.exec(select(Project).where(Project.batch_id == key)
                           .order_by(Project.id)).all()
            pids = [p.id for p in projs]
            results.append(("Tạo đúng 3 project cùng batch_id", len(projs) == 3,
                            f"got {len(projs)}"))
            results.append(("music_source='suno' cho cả lô",
                            all(p.music_source == "suno" for p in projs)))
            results.append(("auto_video + auto_upload BẬT cả lô",
                            all(p.auto_video and p.auto_upload for p in projs)))
            results.append(("Giữ nguyên nhạc cụ / phong cách / style ảnh",
                            all(p.instrument == "bamboo flute" and
                                p.music_style == "ambient" and
                                p.video_style == "2d" for p in projs)))
            results.append(("Tên + ý tưởng KHÁC nhau giữa các project",
                            len({p.name for p in projs}) == 3 and
                            len({p.video_idea for p in projs}) == 3))

        st = _wait_terminal(key)
        results.append(("Lô kết thúc status='completed'",
                        st["status"] == "completed", st["status"]))
        results.append(("Đếm completed = 3", st["completed"] == 3, st["completed"]))
        results.append(("Chạy TUẦN TỰ đúng thứ tự project", order == pids,
                        f"{order} != {pids}"))
    finally:
        _cleanup(key)
    return _print("BATCH — tạo lô + chạy tuần tự tới hết", results)


def test_cancel_midway():
    """Huỷ giữa chừng → dừng, không chạy nốt lô."""
    def fake_run(project_id, progress_cb, dry_run=False, overrides=None, resume=False):
        for i in range(20):
            progress_cb(f"fake {i}", i * 5.0)
            time.sleep(0.3)
        _mark_batch(project_id, suno_service.COMPLETED)
        return {"phase": "COMPLETED"}

    batch_ideas.generate_batch_ideas = _fake_generate("VERIFY cancel")
    suno_service.run_suno_step0 = fake_run

    res = batch_service.create_batch(count=3, instrument="bamboo flute",
                                     music_style="ambient", video_style="2d")
    key = res["batch_key"]
    results = []
    try:
        time.sleep(1.0)                  # project 1 đang chạy
        batch_service.cancel_batch(key)
        st = _wait_terminal(key)
        results.append(("Huỷ lô → status='cancelled'",
                        st["status"] == "cancelled", st["status"]))
        results.append(("Không chạy hết lô sau khi huỷ",
                        st["completed"] < 3, st["completed"]))
    finally:
        _cleanup(key)
    return _print("BATCH — huỷ giữa chừng", results)


def test_blocking_stops_batch():
    """Suno blocking ở project 1 → dừng lô, project 2/3 KHÔNG chạy."""
    first = {"pid": None}
    ran = []

    def fake_run(project_id, progress_cb, dry_run=False, overrides=None, resume=False):
        ran.append(project_id)
        progress_cb("fake", 10.0)
        time.sleep(0.5)
        if project_id == first["pid"]:
            _mark_batch(project_id, suno_service.WAITING_FOR_LOGIN,
                        "cần đăng nhập Suno")
            raise RuntimeError("WAITING_FOR_LOGIN (mô phỏng)")
        _mark_batch(project_id, suno_service.COMPLETED)
        return {"phase": "COMPLETED"}

    batch_ideas.generate_batch_ideas = _fake_generate("VERIFY block")
    suno_service.run_suno_step0 = fake_run

    res = batch_service.create_batch(count=3, instrument="bamboo flute",
                                     music_style="ambient", video_style="2d")
    key = res["batch_key"]
    results = []
    try:
        with Session(engine) as s:
            pids = [p.id for p in s.exec(select(Project)
                                         .where(Project.batch_id == key)
                                         .order_by(Project.id)).all()]
        first["pid"] = pids[0]
        st = _wait_terminal(key)
        results.append(("Phase blocking → status='blocked'",
                        st["status"] == "blocked", st["status"]))
        results.append(("Đếm failed = 1, completed = 0",
                        st["failed"] == 1 and st["completed"] == 0,
                        f"failed={st['failed']} completed={st['completed']}"))
        results.append(("Nêu rõ project + phase trong message",
                        "WAITING_FOR_LOGIN" in st["message"], st["message"][:60]))
        results.append(("KHÔNG kéo project 2/3 vào chạy", ran == [pids[0]],
                        f"ran={ran}"))
    finally:
        _cleanup(key)
    return _print("BATCH — dừng an toàn khi Suno blocking", results)


def test_retry_then_skip():
    """Sản phẩm 1 chạy xong Suno nhưng THIẾU thành phẩm (final.mp4 / nháp YouTube)
    → tự chạy lại tối đa _MAX_RETRY_PER_PRODUCT lần rồi BỎ QUA, vẫn chạy tiếp
    sản phẩm 2 (khác với blocking thật sự: mới dừng cả lô)."""
    first = {"pid": None}
    ran = []

    def fake_run(project_id, progress_cb, dry_run=False, overrides=None, resume=False):
        ran.append(project_id)
        progress_cb("fake", 10.0)
        time.sleep(0.5)
        _mark_batch(project_id, suno_service.COMPLETED)
        if project_id != first["pid"]:          # chỉ sản phẩm sau mới ra thành phẩm
            _fake_artifacts(project_id)
        return {"phase": "COMPLETED"}

    batch_ideas.generate_batch_ideas = _fake_generate("VERIFY retry")
    suno_service.run_suno_step0 = fake_run

    res = batch_service.create_batch(count=2, instrument="bamboo flute",
                                     music_style="ambient", video_style="2d")
    key = res["batch_key"]
    results = []
    try:
        with Session(engine) as s:
            pids = [p.id for p in s.exec(select(Project)
                                         .where(Project.batch_id == key)
                                         .order_by(Project.id)).all()]
        first["pid"] = pids[0]
        st = _wait_terminal(key, timeout=120.0)
        tries_1 = ran.count(pids[0])
        results.append((f"Chạy lại sản phẩm lỗi đúng "
                        f"{batch_service._MAX_RETRY_PER_PRODUCT + 1} lượt",
                        tries_1 == batch_service._MAX_RETRY_PER_PRODUCT + 1,
                        f"{tries_1} lượt"))
        results.append(("Hết lượt thì BỎ QUA, vẫn chạy sản phẩm 2",
                        pids[1] in ran, f"ran={ran}"))
        results.append(("Thiếu thành phẩm KHÔNG bị tính là xong",
                        st["completed"] == 1 and st["failed"] == 1,
                        f"completed={st['completed']} failed={st['failed']}"))
        results.append(("Lô kết thúc, báo rõ số sản phẩm lỗi",
                        st["status"] in ("completed", "failed")
                        and "lỗi" in (st["message"] or ""),
                        f"{st['status']} — {(st['message'] or '')[:70]}"))
    finally:
        _cleanup(key)
    return _print("BATCH — tự chạy lại rồi bỏ qua sản phẩm hỏng", results)


def test_flow_block_stops_batch():
    """Flow gắn cờ «hoạt động bất thường» (chặn mức TÀI KHOẢN) → thử lại tại chỗ
    rồi DỪNG lô, KHÔNG bỏ qua sang sản phẩm kế (sẽ đốt thêm credit Suno vô ích)."""
    ran = []

    def fake_run(project_id, progress_cb, dry_run=False, overrides=None, resume=False):
        ran.append(project_id)
        progress_cb("fake", 10.0)
        time.sleep(0.5)
        _mark_batch(project_id, suno_service.COMPLETED)
        raise RuntimeError("Google Flow từ chối tạo clip: “Chúng tôi nhận thấy có "
                           "hoạt động bất thường nào đó.” (mô phỏng)")

    batch_ideas.generate_batch_ideas = _fake_generate("VERIFY flowblock")
    suno_service.run_suno_step0 = fake_run

    res = batch_service.create_batch(count=2, instrument="bamboo flute",
                                     music_style="ambient", video_style="2d")
    key = res["batch_key"]
    results = []
    try:
        with Session(engine) as s:
            pids = [p.id for p in s.exec(select(Project)
                                         .where(Project.batch_id == key)
                                         .order_by(Project.id)).all()]
        st = _wait_terminal(key, timeout=120.0)
        results.append(("Flow chặn tài khoản → status='blocked'",
                        st["status"] == "blocked", st["status"]))
        results.append(("Có thử lại tại chỗ trước khi dừng",
                        ran.count(pids[0]) == batch_service._MAX_RETRY_PER_PRODUCT + 1,
                        f"{ran.count(pids[0])} lượt"))
        results.append(("KHÔNG chạy sang sản phẩm 2", pids[1] not in ran,
                        f"ran={ran}"))
        results.append(("Message nêu rõ Flow chặn tài khoản",
                        "Flow/Google chặn" in (st["message"] or ""),
                        (st["message"] or "")[:80]))
    finally:
        _cleanup(key)
    return _print("BATCH — Flow chặn tài khoản thì dừng lô", results)


def test_music_inputs_feed_prompts():
    """Nhạc cụ + thể loại nhạc + style ảnh người dùng nhập phải tới được AI sinh
    ý tưởng VÀ được lưu vào project (đầu vào của mọi prompt Suno/Gemini/Flow)."""
    seen: dict = {}

    def fake_run(project_id, progress_cb, dry_run=False, overrides=None, resume=False):
        _mark_batch(project_id, suno_service.COMPLETED)
        _fake_artifacts(project_id)
        return {"phase": "COMPLETED"}

    batch_ideas.generate_batch_ideas = _fake_generate("VERIFY inputs", seen)
    suno_service.run_suno_step0 = fake_run

    results = []
    # Thiếu thể loại nhạc → chặn ngay, không tạo project rác.
    try:
        batch_service.create_batch(count=1, instrument="Guzheng", music_style="",
                                   video_style="2d")
        results.append(("Thiếu thể loại nhạc → RuntimeError", False, "không ném"))
    except RuntimeError:
        results.append(("Thiếu thể loại nhạc → RuntimeError", True))

    # 'real' là key phong cách THẬT trong config.STYLES — không được âm thầm về '2d'.
    res = batch_service.create_batch(count=1, instrument="Guzheng",
                                     music_style="Chinese Zen Music",
                                     video_style="real", theme="đêm mưa")
    key = res["batch_key"]
    try:
        results.append(("Nhạc cụ tới AI sinh ý tưởng",
                        seen.get("instrument") == "Guzheng", str(seen.get("instrument"))))
        results.append(("Thể loại nhạc tới AI sinh ý tưởng",
                        seen.get("music_style") == "Chinese Zen Music",
                        str(seen.get("music_style"))))
        results.append(("Chủ đề tới AI sinh ý tưởng",
                        seen.get("theme") == "đêm mưa", str(seen.get("theme"))))
        results.append(("Style ảnh 'real' KHÔNG bị hạ về '2d'",
                        seen.get("video_style") == "real", str(seen.get("video_style"))))
        _wait_terminal(key)
        with Session(engine) as s:
            proj = s.exec(select(Project).where(Project.batch_id == key)).first()
            results.append(("Project lưu đúng nhạc cụ + thể loại + style ảnh",
                            proj.instrument == "Guzheng" and
                            proj.music_style == "Chinese Zen Music" and
                            proj.video_style == "real",
                            f"{proj.instrument}/{proj.music_style}/{proj.video_style}"))
            # project_context() là nguồn duy nhất nuôi prompt Suno/Gemini/Flow.
            from backend.video.prompt_workflow import project_context
            ctx = project_context(proj.id, proj.name)
            results.append(("project_context() mang nhạc cụ sang tầng prompt",
                            ctx["instrument"] == "Guzheng", str(ctx["instrument"])))
            results.append(("project_context() mang thể loại sang tầng prompt",
                            ctx["purpose"] == "Chinese Zen Music", str(ctx["purpose"])))
    finally:
        _cleanup(key)
    return _print("BATCH — nhạc cụ/thể loại nhạc chảy vào prompt", results)


def main():
    # Giữ lại bản gốc để không rò rỉ monkeypatch sang test khác cùng tiến trình.
    orig_gen = batch_ideas.generate_batch_ideas
    orig_run = suno_service.run_suno_step0
    try:
        all_ok = True
        all_ok &= test_sequential_completion()
        all_ok &= test_cancel_midway()
        all_ok &= test_blocking_stops_batch()
        all_ok &= test_retry_then_skip()
        all_ok &= test_flow_block_stops_batch()
        all_ok &= test_music_inputs_feed_prompts()
    finally:
        batch_ideas.generate_batch_ideas = orig_gen
        suno_service.run_suno_step0 = orig_run
    print(f"\n{'✅ BATCH ALL PASSED' if all_ok else '❌ BATCH FAILED'}")
    sys.exit(0 if all_ok else 1)


if __name__ == "__main__":
    main()
