"""verify_phase1_stems.py – Test stems backend endpoints."""
import sys, requests, tempfile, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
try:
    import console
except ImportError:
    pass

BASE = "http://localhost:8000"
PASS = "  ✅ PASS"
FAIL = "  ❌ FAIL"

def run():
    results = []
    from tests.helpers import create_test_library

    # Setup project + track
    pid = requests.post(f"{BASE}/api/projects",
                        json={"name":"StemTest"}).json()["id"]
    with tempfile.TemporaryDirectory() as tmp:
        paths = create_test_library(1, tmp)
        r     = requests.post(
            f"{BASE}/api/projects/{pid}/tracks/add-file",
            json={"file_path": paths[0]}
        )
        results.append(("Add track OK", r.status_code == 200))
        tid = r.json()["id"]

    # GET stems — not_started
    r = requests.get(f"{BASE}/api/tracks/{tid}/stems")
    results.append(("GET stems 200", r.status_code == 200))
    results.append(("Status = not_started",
                    r.json().get("status") == "not_started"))
    results.append(("Volumes present",
                    "volumes" in r.json()))
    results.append(("api_key không lộ",
                    "api_key" not in str(r.json())))

    # PATCH volumes — phải 404 khi chưa analyze
    r = requests.patch(f"{BASE}/api/tracks/{tid}/stems/volumes",
                       json={"vol_drums": 0.5})
    results.append(("PATCH volumes 404 khi chưa analyze",
                    r.status_code == 404))

    # POST analyze (submit, không đợi finish)
    r = requests.post(f"{BASE}/api/tracks/{tid}/stems/analyze")
    results.append(("POST analyze 200", r.status_code == 200))
    results.append(("Response có stem_id", "stem_id" in r.json()))

    # Project stems status
    r = requests.get(f"{BASE}/api/projects/{pid}/stems/status")
    results.append(("GET project stems status 200", r.status_code == 200))
    results.append(("total >= 1", r.json().get("total", 0) >= 1))

    # Loudness service test
    try:
        import numpy as np
        from backend.loudness_service import (
            measure_lufs, compute_lufs_gain,
            apply_gain_safe, normalize_stems_to_target
        )
        sr    = 44100
        audio = (np.random.randn(sr * 5, 2) * 0.3).astype(np.float32)
        lufs  = measure_lufs(audio, sr)
        results.append(("measure_lufs trả float",
                        isinstance(lufs, float)))
        gain  = compute_lufs_gain(lufs, -16.0)
        results.append(("compute_lufs_gain > 0", gain > 0))
        safe  = apply_gain_safe(audio, gain)
        peak  = float(np.max(np.abs(safe)))
        results.append((f"apply_gain_safe peak < 1.0 ({peak:.4f})",
                        peak < 1.0))
        stems = {"other": audio, "bass": audio * 0.3,
                 "drums": audio * 0.1, "vocals": audio * 0.05}
        normed, g = normalize_stems_to_target(stems, audio, sr, -16.0)
        results.append(("normalize_stems cùng gain cho tất cả", g > 0))
        results.append(("normalize_stems không thay đổi số stems",
                        len(normed) == 4))
    except Exception as e:
        results.append((f"Loudness service error: {e}", False))

    # Cleanup
    requests.delete(f"{BASE}/api/projects/{pid}")

    print("\n=== PHASE 1 STEMS VERIFY ===")
    all_pass = True
    for name, passed in results:
        print(f"{PASS if passed else FAIL} {name}")
        if not passed:
            all_pass = False
    print(f"\n{'✅ Phase 1 PASSED' if all_pass else '❌ FAILED'}")
    sys.exit(0 if all_pass else 1)

if __name__ == "__main__":
    run()
