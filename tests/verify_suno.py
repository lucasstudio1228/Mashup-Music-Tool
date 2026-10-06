"""
verify_suno.py — Kiểm thử STEP 0 (Suno) KHÔNG tiêu credit, KHÔNG mở trình duyệt.

Chạy:  python tests/verify_suno.py   (cần ffmpeg/ffprobe trên PATH)

Bao phủ 3 tầng có thể test offline (mục K của spec — test không được tiêu credit,
không bấm Create/Download):
  1. suno_verify.verify_wav — sinh file WAV thật bằng ffmpeg rồi kiểm:
     WAV hợp lệ PASS; MP3-đổi-đuôi-.wav TRƯỢT; quá ngắn TRƯỢT; im lặng TRƯỢT;
     trùng SHA-256 TRƯỢT.
  2. suno_service.resolve_batch_config — merge defaults + preset (Styles/Exclusions
     nguyên văn) + override.
  3. SunoDriver ở chế độ DRY-RUN — click_create() là no-op (không tiêu credit),
     download_wav() bị chặn (raise); snapshot_song_ids() parse UUID đúng;
     _dur_to_seconds parse 'mm:ss'.
"""
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
import console  # noqa: F401 - UTF-8 output trên Windows console

from backend.video import suno_verify
from backend.video import suno_service
from backend.video.suno_driver import (
    SunoDriver, SunoError, _dur_to_seconds,
)

PASS = "  ✅ PASS"
FAIL = "  ❌ FAIL"


def _print(title, results):
    print(f"\n=== {title} ===")
    ok = True
    for name, passed, *extra in results:
        print(f"{PASS if passed else FAIL} {name}"
              + (f"  ({extra[0]})" if extra else ""))
        if not passed:
            ok = False
    return ok


# ── ffmpeg helpers (sinh file thật) ────────────────────────────────
def _run(cmd) -> int:
    return subprocess.run(cmd, capture_output=True, text=True).returncode


def _make_sine_wav(path: Path, seconds: float, freq: int = 220) -> bool:
    return _run(["ffmpeg", "-y", "-f", "lavfi",
                 "-i", f"sine=frequency={freq}:duration={seconds}",
                 "-c:a", "pcm_s16le", str(path)]) == 0


def _make_silent_wav(path: Path, seconds: float) -> bool:
    return _run(["ffmpeg", "-y", "-f", "lavfi",
                 "-i", f"anullsrc=r=44100:cl=stereo",
                 "-t", str(seconds), "-c:a", "pcm_s16le", str(path)]) == 0


def _make_mp3(path: Path, seconds: float) -> bool:
    # Thử libmp3lame trước, fallback mã hoá mp3 mặc định.
    if _run(["ffmpeg", "-y", "-f", "lavfi",
             "-i", f"sine=frequency=330:duration={seconds}",
             "-c:a", "libmp3lame", "-q:a", "5", str(path)]) == 0:
        return True
    return _run(["ffmpeg", "-y", "-f", "lavfi",
                 "-i", f"sine=frequency=330:duration={seconds}",
                 "-c:a", "mp3", str(path)]) == 0


def test_verify():
    results = []
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        results.append(("ffmpeg/ffprobe có trên PATH", False,
                        "thiếu ffmpeg/ffprobe — không thể sinh file thật"))
        return _print("SUNO_VERIFY (file WAV thật)", results)

    tmp = Path(tempfile.mkdtemp(prefix="suno_verify_"))
    try:
        # 1) WAV hợp lệ (130s > 120s tối thiểu, có tín hiệu)
        valid = tmp / "valid.wav"
        _make_sine_wav(valid, 130)
        r = suno_verify.verify_wav(valid, minimum_duration_seconds=120)
        results.append(("WAV hợp lệ → valid=True", r.valid, r.reason))
        results.append(("  container nhận diện WAV",
                        suno_verify._is_wav_container(r.format_name)))
        results.append(("  codec PCM",
                        suno_verify._is_pcm_codec(r.codec_name), r.codec_name))
        results.append(("  có sha256", bool(r.sha256)))

        # 2) MP3 đổi đuôi .wav → PHẢI trượt (quy tắc cứng)
        mp3 = tmp / "song.mp3"
        if _make_mp3(mp3, 130):
            fake_wav = tmp / "fake_wav_is_mp3.wav"
            shutil.copyfile(mp3, fake_wav)
            r = suno_verify.verify_wav(fake_wav, minimum_duration_seconds=120)
            results.append(("MP3 đổi đuôi .wav → valid=False", not r.valid, r.reason))
            results.append(("  format KHÔNG phải wav container",
                            not suno_verify._is_wav_container(r.format_name),
                            str(r.format_name)))
        else:
            results.append(("Sinh được MP3 để test đổi đuôi", False,
                            "ffmpeg không mã hoá được mp3"))

        # 3) Quá ngắn (5s < 120s) → trượt
        short = tmp / "short.wav"
        _make_sine_wav(short, 5)
        r = suno_verify.verify_wav(short, minimum_duration_seconds=120)
        results.append(("WAV quá ngắn → valid=False", not r.valid, r.reason))

        # 4) Im lặng (130s nhưng anullsrc) → trượt
        silent = tmp / "silent.wav"
        _make_silent_wav(silent, 130)
        r = suno_verify.verify_wav(silent, minimum_duration_seconds=120)
        results.append(("WAV im lặng → valid=False", not r.valid, r.reason))
        results.append(("  is_silent=True", r.is_silent is True, str(r.is_silent)))

        # 5) Trùng SHA-256 với known_hashes → trượt
        good_hash = suno_verify.sha256_file(valid)
        r = suno_verify.verify_wav(valid, minimum_duration_seconds=120,
                                   known_hashes=[good_hash])
        results.append(("WAV trùng SHA-256 → valid=False & is_duplicate",
                        (not r.valid) and r.is_duplicate, r.reason))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    return _print("SUNO_VERIFY (file WAV thật)", results)


def test_resolve_config():
    results = []
    presets = suno_service.vconfig.SUNO_PRESETS

    # Mặc định đi theo config hiện tại, không hard-code preset đã bị bỏ.
    default_preset = suno_service.vconfig.SUNO.preset
    cfg = suno_service.resolve_batch_config(None)
    results.append(("resolve_batch_config(None) chạy được", isinstance(cfg, dict)))
    results.append(("preset mặc định khớp config",
                    cfg["preset"] == default_preset, cfg.get("preset")))
    results.append(("Styles nguyên văn khớp preset",
                    cfg["styles"] == presets[default_preset]["styles"]))
    results.append(("Exclusions nguyên văn khớp preset",
                    all(token.strip() in cfg["exclusions"] for token in presets[default_preset]["exclusions"].split(","))))
    results.append(("target_tracks mặc định = 15", cfg["target_tracks"] == 15))
    results.append(("preferred_model mặc định = v6", cfg["preferred_model"] == "v6"))
    results.append(("instrumental = True", cfg["instrumental"] is True))
    results.append(("allow_model_fallback = False", cfg["allow_model_fallback"] is False))

    # Override: đổi preset + target + model.
    cfg2 = suno_service.resolve_batch_config({
        "preset": "lofi", "target_tracks": 8, "preferred_model": "v5",
        "max_generation_credits": 42,
    })
    results.append(("override preset = lofi", cfg2["preset"] == "lofi"))
    results.append(("override Styles theo lofi",
                    cfg2["styles"] == presets["lofi"]["styles"]))
    results.append(("override target_tracks = 8", cfg2["target_tracks"] == 8))
    results.append(("override preferred_model = v5", cfg2["preferred_model"] == "v5"))
    results.append(("override max_generation_credits = 42",
                    cfg2["max_generation_credits"] == 42))

    return _print("RESOLVE_BATCH_CONFIG (merge + preset nguyên văn)", results)


# ── FakePage: chỉ đủ cho các phương thức offline của driver ─────────
class _FakePage:
    def __init__(self, hrefs):
        self._hrefs = hrefs
        self.clicks = 0

    def eval_on_selector_all(self, selector, js):
        # driver dùng để lấy href a[href*='/song/']
        return self._hrefs


def test_driver_dryrun_guards():
    results = []

    uid1 = "11111111-2222-3333-4444-555555555555"
    uid2 = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    page = _FakePage([f"/song/{uid1}", f"/song/{uid2}", "/other/link"])
    drv = SunoDriver(page, dry_run=True)

    # click_create() ở DRY-RUN: no-op, không raise, không cần page selector.
    try:
        drv.click_create()
        results.append(("DRY-RUN click_create() = no-op (không tiêu credit)", True))
    except Exception as e:
        results.append(("DRY-RUN click_create() = no-op", False, repr(e)))

    # download_wav() ở DRY-RUN: PHẢI bị chặn (raise SunoError).
    blocked = False
    with tempfile.TemporaryDirectory() as d:
        try:
            drv.download_wav(uid1, Path(d) / "x.wav")
        except SunoError:
            blocked = True
        except Exception as e:
            results.append(("DRY-RUN download_wav() bị chặn", False,
                            f"raise sai loại: {e!r}"))
    if blocked:
        results.append(("DRY-RUN download_wav() bị chặn (raise SunoError)", True))

    # snapshot_song_ids() parse đúng UUID từ href.
    ids = drv.snapshot_song_ids()
    results.append(("snapshot_song_ids() parse 2 UUID",
                    ids == {uid1, uid2}, str(sorted(ids))))

    # _dur_to_seconds parse mm:ss và hh:mm:ss.
    results.append(("_dur_to_seconds('3:40') = 220", _dur_to_seconds("3:40") == 220))
    results.append(("_dur_to_seconds('1:02:03') = 3723",
                    _dur_to_seconds("1:02:03") == 3723))
    results.append(("_dur_to_seconds('abc') = None", _dur_to_seconds("abc") is None))

    return _print("SUNO_DRIVER (chốt an toàn DRY-RUN)", results)


class _FakeDlDriver:
    """Driver giả cho pha tải: sập trình duyệt `crash` lần đầu, sau đó ghi WAV thật."""
    def __init__(self, src_dir: Path, crash: int = 0):
        self.src_dir, self.crash, self.calls = src_dir, crash, 0

    def download_wav(self, song_id, dest, timeout_sec=240):
        self.calls += 1
        if self.crash > 0:
            self.crash -= 1
            raise RuntimeError(
                "Page.wait_for_timeout: Target page, context or browser has been closed")
        freq = 200 + sum(ord(ch) for ch in song_id) % 500
        _make_sine_wav(Path(dest), 130, freq=freq)
        return str(dest)

    def close_extra_tabs(self):
        pass


def test_download_browser_reopen():
    """Trình duyệt sập giữa pha tải → mở lại phiên, KHÔNG đánh hỏng bài/đốt spare."""
    from sqlmodel import SQLModel, Session, create_engine
    from backend.models import SunoBatch, SunoCandidate
    results = []
    if not shutil.which("ffmpeg"):
        results.append(("ffmpeg có trên PATH", False))
        return _print("SUNO_DOWNLOAD (mở lại trình duyệt khi sập)", results)

    cfg = {"download_min_song_age_sec": 0, "download_gap_sec": (0, 0),
           "download_retry_wait_sec": 0, "max_browser_reopens": 2,
           "minimum_duration_seconds": 120}
    tmp = Path(tempfile.mkdtemp(prefix="suno_dl_"))
    try:
        def _setup():
            eng = create_engine(f"sqlite:///{tmp / 'db.sqlite'}")
            SQLModel.metadata.drop_all(eng)
            SQLModel.metadata.create_all(eng)
            s = Session(eng)
            b = SunoBatch(project_id=1, idempotency_key="t", phase="GENERATING",
                          target_tracks=2, staging_dir=str(tmp))
            s.add(b); s.commit(); s.refresh(b)
            for i, sid in enumerate(["11111111-0000-0000-0000-000000000001",
                                     "22222222-0000-0000-0000-000000000002"]):
                s.add(SunoCandidate(batch_id=b.id, project_id=1, song_id=sid,
                                    request_id=f"req{i}", selected=True))
            s.commit()
            return s, b

        # 1) Sập 1 lần → mở lại 1 lần → cả 2 bài hợp lệ, không bài nào 'failed'.
        s, b = _setup()
        dead = _FakeDlDriver(tmp, crash=1)
        fresh = _FakeDlDriver(tmp)
        reopened = []
        suno_service._download_and_validate(
            s, b, cfg, dead, 2, tmp, lambda *a: None, lambda m: None,
            reopen=lambda: (reopened.append(1), fresh)[1])
        cands = s.exec(suno_service.select(SunoCandidate)).all()
        results.append(("Sập 1 lần → mở lại đúng 1 lần", len(reopened) == 1,
                        str(len(reopened))))
        results.append(("Cả 2 bài có WAV hợp lệ sau khi mở lại",
                        all(c.validation_status == "valid" for c in cands),
                        str([c.validation_status for c in cands])))
        results.append(("Không bài nào bị đánh 'failed' vì trình duyệt sập",
                        not any(c.download_status == "failed" for c in cands)))
        s.close()

        # 2) Sập liên tục → dừng bằng SunoError sau max_browser_reopens, không đốt bài.
        s, b = _setup()
        always = _FakeDlDriver(tmp, crash=99)
        stopped = False
        try:
            suno_service._download_and_validate(
                s, b, cfg, always, 2, tmp, lambda *a: None, lambda m: None,
                reopen=lambda: always)
        except SunoError:
            stopped = True
        cands = s.exec(suno_service.select(SunoCandidate)).all()
        results.append(("Sập liên tục → dừng (SunoError) thay vì đốt cả lô", stopped))
        results.append(("  không bài nào bị đánh 'failed'",
                        not any(c.download_status == "failed" for c in cands)))
        results.append(("  số lần thử = 1 + max_browser_reopens", always.calls == 3,
                        str(always.calls)))
        s.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return _print("SUNO_DOWNLOAD (mở lại trình duyệt khi sập)", results)


class _FakeCreateDriver(_FakeDlDriver):
    """Driver giả cho Create thay thế: bài có id bắt đầu 'dead' ra WAV 20 s
    (lỗi kiểu Suno trả bài vài giây), bài khác 130 s. Đếm số lần bấm Create."""
    def __init__(self, src_dir: Path, new_prefixes=("aaaa", "bbbb")):
        super().__init__(src_dir)
        self.creates, self.new_prefixes, self.styles = 0, list(new_prefixes), []

    def download_wav(self, song_id, dest, timeout_sec=240):
        self.calls += 1
        freq = 200 + sum(ord(ch) for ch in song_id) % 500
        _make_sine_wav(Path(dest), 20 if song_id.startswith("dead") else 130, freq=freq)
        return str(dest)

    def read_credits(self): return 100
    def open_create_page(self): pass
    def select_advanced_mode(self): pass
    def select_model(self, *a, **k): pass
    def assert_instrumental(self): pass
    def open_more_options(self): pass
    def fill_exclusions(self, v): pass
    def ensure_duration_auto(self): pass
    def set_max_mode(self, v): pass
    def snapshot_song_ids(self): return set()
    def fill_styles(self, v): self.styles.append(v)
    def click_create(self): self.creates += 1

    def wait_for_new_songs(self, before, expected=2, timeout_sec=300):
        from backend.video.suno_driver import NewSong
        p = self.new_prefixes.pop(0)
        return [NewSong(song_id=f"{p}{i}{self.creates:03d}-0000-0000-0000-000000000000")
                for i in range(2)]


def test_replace_dead_create():
    """Cả 2 bài của 1 lượt Create lỗi (vài giây) → loại lượt đó, Create thay thế
    bằng đúng prompt của bài đó; vẫn chỉ lấy 1/2; có trần lượt thay thế."""
    from sqlmodel import SQLModel, Session, create_engine
    from backend.models import SunoBatch, SunoCandidate
    title = "SUNO_REPLACE (cả 2 bài lỗi → Create bài thay thế)"
    results = []
    if not shutil.which("ffmpeg"):
        results.append(("ffmpeg có trên PATH", False))
        return _print(title, results)
    plan = [{"styles": f"STYLE-{i + 1}", "exclusions": "vocals"} for i in range(2)]
    base_cfg = {"download_min_song_age_sec": 0, "download_gap_sec": (0, 0),
                "download_retry_wait_sec": 0, "download_passes": 1,
                "minimum_duration_seconds": 120, "max_create_actions": 2,
                "max_new_song_downloads": 2, "prompt_plan": plan,
                "styles": "x", "exclusions": "vocals"}
    tmp = Path(tempfile.mkdtemp(prefix="suno_rep_"))
    try:
        def _setup(ids):
            eng = create_engine(f"sqlite:///{tmp / 'db.sqlite'}")
            SQLModel.metadata.drop_all(eng)
            SQLModel.metadata.create_all(eng)
            s = Session(eng)
            b = SunoBatch(project_id=1, idempotency_key="t", phase="GENERATING",
                          target_tracks=2, staging_dir=str(tmp), create_actions_used=2)
            s.add(b); s.commit(); s.refresh(b)
            for req, sid, sel in ids:
                s.add(SunoCandidate(batch_id=b.id, project_id=1, song_id=sid,
                                    request_id=req, selected=sel, is_spare=not sel))
            s.commit()
            return s, b

        ids = [("req1", "11111111-0000-0000-0000-000000000001", True),
               ("req1", "11111111-0000-0000-0000-000000000002", False),
               ("req2", "dead0001-0000-0000-0000-000000000001", True),
               ("req2", "dead0002-0000-0000-0000-000000000002", False)]

        # 1) req2 ra 2 bài 20 s → loại req2, Create lại prompt #2, lấy 1 bài mới.
        s, b = _setup(ids)
        drv = _FakeCreateDriver(tmp)
        suno_service._download_and_validate(
            s, b, dict(base_cfg, max_replacement_creates=3), drv, 2, tmp,
            lambda *a: None, lambda m: None)
        cands = s.exec(suno_service.select(SunoCandidate)).all()
        by = {c.song_id[:5]: c for c in cands}
        sel = [c for c in cands if c.selected]
        results.append(("Bấm Create thay thế đúng 1 lần", drv.creates == 1, str(drv.creates)))
        results.append(("  dùng lại đúng prompt của bài #2", drv.styles == ["STYLE-2"],
                        str(drv.styles)))
        results.append(("  lượt req2 bị loại (discarded, không chọn)",
                        all(c.download_status == "discarded" and not c.selected
                            for c in cands if c.request_id == "req2")))
        results.append(("  vẫn đúng 2 bài chọn, mỗi lượt Create 1 bài, đều hợp lệ",
                        len(sel) == 2 and len({c.request_id for c in sel}) == 2
                        and all(c.validation_status == "valid" for c in sel),
                        str([(c.request_id, c.validation_status) for c in sel])))
        results.append(("  bài thay thế mang request 'req3:p2'",
                        by["aaaa0"].request_id == "req3:p2", by["aaaa0"].request_id))
        results.append(("  bài thứ 2 của lượt thay thế chỉ là dự phòng (không tải)",
                        by["aaaa0"].selected and not by["aaaa1"].selected
                        and by["aaaa1"].download_status != "downloaded"))
        # _mark_selection chạy lại (resume) không được hoàn tác lựa chọn/loại bỏ.
        suno_service._mark_selection(s, b, 2)
        sel2 = sorted(c.song_id for c in
                      s.exec(suno_service.select(SunoCandidate)).all() if c.selected)
        results.append(("  _mark_selection chạy lại giữ nguyên lựa chọn",
                        sel2 == sorted(c.song_id for c in sel)))
        s.close()

        # 2) Bài thay thế cũng lỗi liên tục → dừng ở trần max_replacement_creates.
        s, b = _setup(ids)
        drv = _FakeCreateDriver(tmp, new_prefixes=("dead", "dead", "dead", "dead"))
        logs = []
        suno_service._download_and_validate(
            s, b, dict(base_cfg, max_replacement_creates=2), drv, 2, tmp,
            lambda *a: None, logs.append)
        results.append(("Lỗi liên tục → dừng đúng trần 2 lượt thay thế",
                        drv.creates == 2, str(drv.creates)))
        results.append(("  có log báo cần kiểm tra thủ công",
                        any("hết 2 lượt Create thay thế" in m for m in logs)))
        s.close()

        # 3) Chỉ 1 bài lỗi, bài kia hợp lệ → KHÔNG Create thêm (không tốn credit).
        s, b = _setup([ids[0], ids[1],
                       ("req2", "dead0001-0000-0000-0000-000000000001", True),
                       ("req2", "22222222-0000-0000-0000-000000000002", False)])
        drv = _FakeCreateDriver(tmp)
        suno_service._download_and_validate(
            s, b, dict(base_cfg, max_replacement_creates=3), drv, 2, tmp,
            lambda *a: None, lambda m: None)
        sel = [c for c in s.exec(suno_service.select(SunoCandidate)).all() if c.selected]
        results.append(("1 bài lỗi → đôn bài còn lại, không Create thêm",
                        drv.creates == 0 and any(c.song_id.startswith("2222") for c in sel),
                        f"creates={drv.creates}"))
        s.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return _print(title, results)


def main():
    all_ok = True
    all_ok &= test_verify()
    all_ok &= test_resolve_config()
    all_ok &= test_driver_dryrun_guards()
    all_ok &= test_download_browser_reopen()
    all_ok &= test_replace_dead_create()
    print(f"\n{'✅ SUNO ALL PASSED' if all_ok else '❌ SUNO FAILED'}")
    sys.exit(0 if all_ok else 1)


if __name__ == "__main__":
    main()
