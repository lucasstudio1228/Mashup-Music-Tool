"""verify_phase5.py – Kiểm tra metadata output."""
import sys, json, tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
from metadata_writer import write_metadata, seconds_to_cue_time

PASS = "  ✅ PASS"
FAIL = "  ❌ FAIL"

def run():
    results = []

    # Test 1: seconds_to_cue_time
    cases = [(0, "00:00:00"), (60, "01:00:00"), (323.5, "05:23:37"), (3661.0, "61:01:00")]
    for secs, expected in cases:
        got = seconds_to_cue_time(secs)
        results.append((f"CUE time {secs}s → {expected}", got == expected))

    # Test 2: write_metadata tạo 3 files
    with tempfile.TemporaryDirectory() as tmpdir:
        out_wav = Path(tmpdir) / "mix.wav"
        out_wav.touch()   # file giả

        mock_timestamps = [
            {"name": "Morning Stillness", "source_file": "a.wav",
             "start_seconds": 0.0, "end_seconds": 323.5},
            {"name": "Deep River Flow",   "source_file": "b.wav",
             "start_seconds": 308.5, "end_seconds": 650.0},
        ]
        write_metadata(
            timestamps=mock_timestamps,
            output_wav_path=str(out_wav),
            total_duration_sec=650.0,
            sample_rate=96000, bit_depth=24, crossfade_sec=15
        )
        cue  = Path(tmpdir) / "mix.cue"
        txt  = Path(tmpdir) / "mix_tracklist.txt"
        js   = Path(tmpdir) / "mix_info.json"

        results.append((".cue file tồn tại", cue.exists()))
        results.append(("_tracklist.txt tồn tại", txt.exists()))
        results.append(("_info.json tồn tại", js.exists()))

        # Test 3: JSON valid và đúng schema
        data = json.loads(js.read_text())
        results.append(("JSON có key 'tracks'", "tracks" in data))
        results.append(("JSON có key 'audio'", "audio" in data))
        results.append(("JSON tracks count = 2", len(data["tracks"]) == 2))
        results.append(("JSON duration đúng", data["audio"]["total_duration_seconds"] == 650.0))

        # Test 4: CUE file chứa đúng track titles
        cue_text = cue.read_text()
        results.append(("CUE chứa track 1 title", "Morning Stillness" in cue_text))
        results.append(("CUE chứa INDEX 01 00:00:00", "INDEX 01 00:00:00" in cue_text))

        # Test 5: tracklist.txt chứa timestamps dạng đọc được
        txt_content = txt.read_text()
        results.append(("Tracklist chứa 00:00", "00:00" in txt_content))

    print("\n=== PHASE 5 VERIFICATION ===")
    all_pass = True
    for name, passed in results:
        print(f"{PASS if passed else FAIL} {name}")
        if not passed:
            all_pass = False
    print(f"\n{'✅ Phase 5 PASSED' if all_pass else '❌ Phase 5 FAILED – fix trước khi tiếp tục'}")
    sys.exit(0 if all_pass else 1)

if __name__ == "__main__":
    run()
