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

    # Mặc định (không override) → preset relaxing_flute, Styles/Exclusions nguyên văn.
    cfg = suno_service.resolve_batch_config(None)
    results.append(("resolve_batch_config(None) chạy được", isinstance(cfg, dict)))
    results.append(("preset mặc định = relaxing_flute",
                    cfg["preset"] == "relaxing_flute", cfg.get("preset")))
    results.append(("Styles nguyên văn khớp preset",
                    cfg["styles"] == presets["relaxing_flute"]["styles"]))
    results.append(("Exclusions nguyên văn khớp preset",
                    cfg["exclusions"] == presets["relaxing_flute"]["exclusions"]))
    results.append(("target_tracks mặc định = 15", cfg["target_tracks"] == 15))
    results.append(("preferred_model mặc định = v6", cfg["preferred_model"] == "v6"))
    results.append(("instrumental = True", cfg["instrumental"] is True))
    results.append(("allow_model_fallback = False", cfg["allow_model_fallback"] is False))

    # Override: đổi preset + target + model.
    cfg2 = suno_service.resolve_batch_config({
        "preset": "lofi_ambient", "target_tracks": 8, "preferred_model": "v5",
        "max_generation_credits": 42,
    })
    results.append(("override preset = lofi_ambient",
                    cfg2["preset"] == "lofi_ambient"))
    results.append(("override Styles theo lofi_ambient",
                    cfg2["styles"] == presets["lofi_ambient"]["styles"]))
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


def main():
    all_ok = True
    all_ok &= test_verify()
    all_ok &= test_resolve_config()
    all_ok &= test_driver_dryrun_guards()
    print(f"\n{'✅ SUNO ALL PASSED' if all_ok else '❌ SUNO FAILED'}")
    sys.exit(0 if all_ok else 1)


if __name__ == "__main__":
    main()
