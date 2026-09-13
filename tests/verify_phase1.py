"""verify_phase1.py – Tự động test audio_loader và analyzer."""
import sys, tempfile, numpy as np, soundfile as sf
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
from tests.helpers import create_test_library, generate_test_wav
from audio_loader import load_library, Track
from analyzer import find_transition_points

PASS = "  ✅ PASS"
FAIL = "  ❌ FAIL"

def run():
    results = []

    # Test 1: Load 16 valid WAV files
    with tempfile.TemporaryDirectory() as tmpdir:
        paths = create_test_library(16, tmpdir)
        tracks = load_library(paths)
        ok = (len(tracks) == 16 and all(isinstance(t, Track) for t in tracks)
              and all(t.duration_seconds > 0 for t in tracks))
        results.append(("Load 16 valid WAV files", ok))

    # Test 2: Reject < 15 files
    with tempfile.TemporaryDirectory() as tmpdir:
        paths = create_test_library(10, tmpdir)
        try:
            load_library(paths)
            results.append(("Reject < 15 files", False))
        except ValueError:
            results.append(("Reject < 15 files", True))

    # Test 3: Skip corrupt file, still load nếu đủ 15
    with tempfile.TemporaryDirectory() as tmpdir:
        paths = create_test_library(16, tmpdir)
        Path(paths[0]).write_bytes(b"CORRUPTED_DATA")
        tracks = load_library(paths)   # phải warn, không raise
        results.append(("Skip corrupt, warn only", len(tracks) == 15))

    # Test 4: Analyzer finds valid transition points
    with tempfile.TemporaryDirectory() as tmpdir:
        p = Path(tmpdir) / "test.wav"
        generate_test_wav(str(p), duration_sec=60)
        audio, sr = sf.read(str(p), dtype='float32')
        tail, head = find_transition_points(audio, sr, search_window_sec=15)
        total = len(audio)
        ok = (tail > int(sr * 45) and         # tail nằm trong 15s cuối
              head < int(sr * 15) and          # head nằm trong 15s đầu
              0 < tail < total and
              0 < head < total)
        results.append(("Analyzer transition points valid", ok))

    # Test 5: APIConfig reads env variables
    import os
    os.environ["OPENAI_API_KEY"] = "test-key-123"
    os.environ["OPENAI_BASE_URL"] = "https://custom.endpoint.com"
    os.environ["OPENAI_MODEL"] = "gpt-4o-mini"
    from api_config import APIConfig
    cfg = APIConfig()
    ok = (cfg.api_key == "test-key-123" and
          cfg.base_url == "https://custom.endpoint.com" and
          cfg.model == "gpt-4o-mini" and
          cfg.is_configured() is True)
    results.append(("APIConfig reads env vars", ok))
    del os.environ["OPENAI_API_KEY"]
    cfg2 = APIConfig()
    results.append(("APIConfig.is_configured() False when no key", not cfg2.is_configured()))

    # Report
    print("\n=== PHASE 1 VERIFICATION ===")
    all_pass = True
    for name, passed in results:
        print(f"{PASS if passed else FAIL} {name}")
        if not passed:
            all_pass = False
    print(f"\n{'✅ Phase 1 PASSED – tiếp tục Phase 2' if all_pass else '❌ Phase 1 FAILED – fix lỗi trước khi tiếp tục'}")
    sys.exit(0 if all_pass else 1)

if __name__ == "__main__":
    run()
