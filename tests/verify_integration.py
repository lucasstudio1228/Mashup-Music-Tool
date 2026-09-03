"""
verify_integration.py – End-to-end test toàn stack.
Yêu cầu: backend đang chạy (python run.py HOẶC uvicorn backend.main:app --port 8000)
Chạy: python tests/verify_integration.py
"""
import shutil
import sys
import tempfile
import time
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent.parent))
import console  # noqa: F401 - UTF-8 output trên Windows
from tests.helpers import create_test_library

BASE = "http://localhost:8000"


def test_full_flow():
    results = []

    r = requests.post(f"{BASE}/api/projects", json={"name": "Integration Test"})
    assert r.status_code == 200, r.text
    pid = r.json()["id"]

    tmpdir = tempfile.mkdtemp()
    try:
        create_test_library(16, tmpdir)
        r = requests.post(f"{BASE}/api/projects/{pid}/tracks/scan",
                          json={"folder_path": tmpdir})
        assert r.status_code == 200, r.text
        results.append(("Scan added 16 tracks", r.json()["added"] == 16))

        r = requests.post(f"{BASE}/api/projects/{pid}/mixes", json={
            "duration_minutes": 5, "crossfade_seconds": 5,
            "sample_rate": 48000, "bit_depth": 24})
        assert r.status_code == 200, r.text
        mix_id = r.json()["id"]

        status = "pending"
        for _ in range(300):
            status = requests.get(f"{BASE}/api/mixes/{mix_id}").json()["status"]
            if status in ("completed", "failed"):
                break
            time.sleep(1)
        results.append((f"Mix completed (status={status})", status == "completed"))

        if status == "completed":
            for ftype in ("wav", "cue", "tracklist", "json"):
                rd = requests.get(f"{BASE}/api/mixes/{mix_id}/download/{ftype}",
                                  stream=True)
                results.append((f"{ftype.upper()} download 200",
                                rd.status_code == 200))
                rd.close()
            mix = requests.get(f"{BASE}/api/mixes/{mix_id}").json()
            dur = mix.get("total_duration_seconds") or 0
            results.append((f"Duration ~5min ({dur:.0f}s)", abs(dur - 300) <= 30))
    finally:
        requests.delete(f"{BASE}/api/projects/{pid}")
        shutil.rmtree(tmpdir, ignore_errors=True)

    print("\n=== INTEGRATION VERIFY ===")
    all_pass = True
    for name, passed in results:
        print(f"  {'✅' if passed else '❌'} {name}")
        if not passed:
            all_pass = False
    print(f"\n{'✅ Integration PASSED' if all_pass else '❌ FAILED'}")
    sys.exit(0 if all_pass else 1)


if __name__ == "__main__":
    try:
        requests.get(f"{BASE}/api/health", timeout=3)
    except Exception:
        print("❌ Backend chưa chạy. Khởi động: python run.py")
        sys.exit(2)
    test_full_flow()
