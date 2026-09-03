"""
verify_backend.py – Test toàn bộ backend endpoints qua HTTP.
Yêu cầu: server đang chạy ở http://localhost:8000
  uvicorn backend.main:app --port 8000
Rồi: python tests/verify_backend.py
"""
import sys
import tempfile
import time
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent.parent))
import console  # noqa: F401 - bật UTF-8 output trên Windows console
from tests.helpers import create_test_library

BASE = "http://localhost:8000"
PASS = "  ✅ PASS"
FAIL = "  ❌ FAIL"


def _print(title, results):
    print(f"\n=== {title} ===")
    ok = True
    for name, passed in results:
        print(f"{PASS if passed else FAIL} {name}")
        if not passed:
            ok = False
    return ok


def run_phase1():
    results = []
    r = requests.get(f"{BASE}/api/health")
    results.append(("Health endpoint", r.status_code == 200 and
                    r.json().get("status") == "ok"))

    r = requests.post(f"{BASE}/api/projects", json={
        "name": "Test Project", "description": "Created by verify script"})
    results.append(("Create project", r.status_code == 200))
    pid = r.json().get("id")
    results.append(("Project has id", isinstance(pid, int)))

    r = requests.get(f"{BASE}/api/projects")
    results.append(("List projects", r.status_code == 200 and len(r.json()) >= 1))

    r = requests.get(f"{BASE}/api/projects/{pid}")
    results.append(("Get project", r.status_code == 200))
    results.append(("Detail has tracks[] & mixes[]",
                    "tracks" in r.json() and "mixes" in r.json()))

    r = requests.patch(f"{BASE}/api/projects/{pid}", json={"description": "Updated"})
    results.append(("Update project", r.status_code == 200))
    results.append(("Description updated", r.json().get("description") == "Updated"))

    r = requests.delete(f"{BASE}/api/projects/{pid}")
    results.append(("Delete project", r.status_code == 200))

    r = requests.get(f"{BASE}/api/projects/{pid}")
    results.append(("Project 404 after delete", r.status_code == 404))

    return _print("PHASE 1 BACKEND VERIFY", results)


def run_phase2():
    results = []
    pid = requests.post(f"{BASE}/api/projects",
                        json={"name": "Tracks Test"}).json()["id"]

    with tempfile.TemporaryDirectory() as tmpdir:
        create_test_library(16, tmpdir)
        r = requests.post(f"{BASE}/api/projects/{pid}/tracks/scan",
                          json={"folder_path": tmpdir})
        results.append(("Scan valid folder 200", r.status_code == 200))
        scan = r.json()
        results.append(("Scan added 16", scan.get("added") == 16))

        # scan lại → toàn bộ là duplicate
        r2 = requests.post(f"{BASE}/api/projects/{pid}/tracks/scan",
                           json={"folder_path": tmpdir})
        results.append(("Re-scan skips duplicates",
                        r2.json().get("skipped_duplicates") == 16 and
                        r2.json().get("added") == 0))

        r = requests.get(f"{BASE}/api/projects/{pid}/tracks")
        results.append(("List tracks = 16", len(r.json()) == 16))

        # project response phản ánh track_count
        pr = requests.get(f"{BASE}/api/projects/{pid}").json()
        results.append(("Project track_count = 16", pr.get("track_count") == 16))

        # delete 1 track
        tid = r.json()[0]["id"]
        rd = requests.delete(f"{BASE}/api/projects/{pid}/tracks/{tid}")
        results.append(("Delete one track", rd.status_code == 200))
        results.append(("Count = 15 after delete",
                        len(requests.get(f"{BASE}/api/projects/{pid}/tracks").json()) == 15))

    r = requests.post(f"{BASE}/api/projects/{pid}/tracks/scan",
                      json={"folder_path": "Z:/does/not/exist/xyz"})
    results.append(("Scan missing folder 404", r.status_code == 404))

    rda = requests.delete(f"{BASE}/api/projects/{pid}/tracks")
    results.append(("Delete all tracks", rda.status_code == 200 and
                    rda.json().get("deleted") == 15))

    requests.delete(f"{BASE}/api/projects/{pid}")
    return _print("PHASE 2 TRACKS VERIFY", results)


def run_phase3():
    results = []
    pid = requests.post(f"{BASE}/api/projects",
                        json={"name": "Mix Test"}).json()["id"]

    # < 15 track → 400
    with tempfile.TemporaryDirectory() as small:
        create_test_library(10, small)
        requests.post(f"{BASE}/api/projects/{pid}/tracks/scan",
                      json={"folder_path": small})
        r = requests.post(f"{BASE}/api/projects/{pid}/mixes",
                          json={"duration_minutes": 1, "crossfade_seconds": 5,
                                "sample_rate": 48000, "bit_depth": 24})
        results.append(("Create mix < 15 tracks → 400", r.status_code == 400))
        requests.delete(f"{BASE}/api/projects/{pid}/tracks")

    tmpdir = tempfile.mkdtemp()
    create_test_library(16, tmpdir)
    requests.post(f"{BASE}/api/projects/{pid}/tracks/scan",
                  json={"folder_path": tmpdir})

    r = requests.post(f"{BASE}/api/projects/{pid}/mixes",
                      json={"duration_minutes": 2, "crossfade_seconds": 5,
                            "sample_rate": 48000, "bit_depth": 24})
    results.append(("Create mix 200", r.status_code == 200))
    mix_id = r.json()["id"]
    results.append(("Mix status pending/running",
                    r.json()["status"] in ("pending", "running")))

    # SSE stream: đọc vài event
    got_progress = False
    got_done = False
    try:
        with requests.get(f"{BASE}/api/mixes/{mix_id}/progress",
                          stream=True, timeout=180) as sse:
            for raw in sse.iter_lines(decode_unicode=True):
                if raw and raw.startswith("event:"):
                    etype = raw.split(":", 1)[1].strip()
                    if etype == "progress":
                        got_progress = True
                    if etype == "done":
                        got_done = True
                        break
    except Exception as exc:
        print(f"  SSE error: {exc}")
    results.append(("SSE received progress event", got_progress))
    results.append(("SSE received done event", got_done))

    # poll đến completed
    status = "pending"
    for _ in range(180):
        status = requests.get(f"{BASE}/api/mixes/{mix_id}").json()["status"]
        if status in ("completed", "failed"):
            break
        time.sleep(1)
    results.append((f"Mix completed (status={status})", status == "completed"))

    if status == "completed":
        for ftype in ("wav", "cue", "tracklist", "json"):
            rd = requests.get(f"{BASE}/api/mixes/{mix_id}/download/{ftype}",
                              stream=True)
            results.append((f"Download {ftype} 200", rd.status_code == 200))
            rd.close()

    rdel = requests.delete(f"{BASE}/api/mixes/{mix_id}")
    results.append(("Delete completed mix", rdel.status_code == 200))

    requests.delete(f"{BASE}/api/projects/{pid}")
    import shutil
    shutil.rmtree(tmpdir, ignore_errors=True)
    return _print("PHASE 3 MIXES VERIFY", results)


def main():
    try:
        requests.get(f"{BASE}/api/health", timeout=3)
    except Exception:
        print("❌ Server chưa chạy. Khởi động: "
              "uvicorn backend.main:app --port 8000")
        sys.exit(2)

    all_ok = True
    all_ok &= run_phase1()
    all_ok &= run_phase2()
    all_ok &= run_phase3()

    print(f"\n{'✅ BACKEND ALL PASSED' if all_ok else '❌ BACKEND FAILED'}")
    sys.exit(0 if all_ok else 1)


if __name__ == "__main__":
    main()
