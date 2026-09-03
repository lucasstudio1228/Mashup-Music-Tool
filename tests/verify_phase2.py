"""verify_phase2.py – Kiểm tra T+3 rule và playlist logic."""
import sys, random, tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
from tests.helpers import create_test_library
from audio_loader import load_library
from playlist_generator import generate_playlist

PASS = "  ✅ PASS"
FAIL = "  ❌ FAIL"

def check_t3_rule(playlist) -> bool:
    """Kiểm tra: không track nào lặp lại trong vòng 3 vị trí."""
    for i in range(3, len(playlist)):
        recent = {playlist[i-1], playlist[i-2], playlist[i-3]}
        if playlist[i] in recent:
            return False
    return True

def run():
    results = []
    with tempfile.TemporaryDirectory() as tmpdir:
        paths = create_test_library(16, tmpdir)
        tracks = load_library(paths)

        # Test 1: T+3 rule đúng, chạy 50 lần
        all_valid = True
        for _ in range(50):
            pl = generate_playlist(tracks, target_seconds=600, crossfade_sec=15)
            if not check_t3_rule(pl):
                all_valid = False
                break
        results.append(("T+3 rule đúng qua 50 lần random", all_valid))

        # Test 2: Thời gian tích lũy đủ target
        target = 3600.0   # 60 phút
        pl = generate_playlist(tracks, target_seconds=target, crossfade_sec=15)
        total_eff = sum(max(t.duration_seconds - 15, 1.0) for t in pl)
        results.append(("Tổng duration >= target", total_eff >= target))

        # Test 3: Playlist có > 0 tracks
        results.append(("Playlist không rỗng", len(pl) > 0))

        # Test 4: Không có 2 track GIỐNG NHAU liên tiếp
        no_adjacent = all(pl[i] is not pl[i+1] for i in range(len(pl)-1))
        results.append(("Không có 2 track liên tiếp giống nhau", no_adjacent))

        # Test 5: playlist ngắn nhất (duration nhỏ)
        pl_short = generate_playlist(tracks, target_seconds=120, crossfade_sec=15)
        results.append(("Playlist ngắn hoạt động đúng", len(pl_short) > 0 and check_t3_rule(pl_short)))

    print("\n=== PHASE 2 VERIFICATION ===")
    all_pass = True
    for name, passed in results:
        print(f"{PASS if passed else FAIL} {name}")
        if not passed:
            all_pass = False
    print(f"\n{'✅ Phase 2 PASSED' if all_pass else '❌ Phase 2 FAILED – fix trước khi tiếp tục'}")
    sys.exit(0 if all_pass else 1)

if __name__ == "__main__":
    run()
