"""verify_phase3.py – Kiểm tra crossfade engine."""
import sys, tempfile, numpy as np, soundfile as sf
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
from tests.helpers import create_test_library
from audio_loader import load_library
from playlist_generator import generate_playlist
from crossfade_engine import render_mix, make_crossfade_curves

PASS = "  ✅ PASS"
FAIL = "  ❌ FAIL"

def run():
    results = []
    with tempfile.TemporaryDirectory() as tmpdir:
        # Test 1: Equal-power curves tổng = 1.0 tại mọi điểm
        fo, fi = make_crossfade_curves(44100)
        power_sum = fo**2 + fi**2
        ok = np.allclose(power_sum, 1.0, atol=1e-6)
        results.append(("Equal-power curve: sum of squares = 1.0", ok))

        # Test 2: Render 3 phút từ 16 test tracks
        paths = create_test_library(16, tmpdir)
        tracks = load_library(paths)
        playlist = generate_playlist(tracks, target_seconds=180, crossfade_sec=10)
        out = Path(tmpdir) / "test_mix.wav"
        timestamps = render_mix(playlist, str(out), crossfade_sec=10,
                                target_sr=48000, bit_depth=24)
        results.append(("render_mix tạo file output", out.exists()))

        # Test 3: Output là WAV PCM_24
        info = sf.info(str(out))
        results.append(("Output subtype = PCM_24", info.subtype == 'PCM_24'))

        # Test 4: Duration trong khoảng target ± 30 giây
        ok = abs(info.duration - 180) <= 30
        results.append((f"Duration gần target 180s (±30s): got {info.duration:.1f}s", ok))

        # Test 5: Không có clipping (peak < 0.99)
        audio, sr = sf.read(str(out), dtype='float32')
        peak = float(np.max(np.abs(audio)))
        results.append((f"Không clipping (peak={peak:.4f} < 0.99)", peak < 0.99))

        # Test 6: Output là stereo
        results.append(("Output là stereo (2 channels)",
                         info.channels == 2))

        # Test 7: timestamps được trả về đúng số lượng
        results.append(("Timestamps count == playlist length",
                         len(timestamps) == len(playlist)))

        # Test 8: Sample rate đúng
        results.append(("Output sample rate = 48000", info.samplerate == 48000))

    print("\n=== PHASE 3 VERIFICATION ===")
    all_pass = True
    for name, passed in results:
        print(f"{PASS if passed else FAIL} {name}")
        if not passed:
            all_pass = False
    print(f"\n{'✅ Phase 3 PASSED' if all_pass else '❌ Phase 3 FAILED – fix trước khi tiếp tục'}")
    sys.exit(0 if all_pass else 1)

if __name__ == "__main__":
    run()
