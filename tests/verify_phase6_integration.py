"""verify_phase6_integration.py – End-to-end integration test."""
import sys, json, tempfile, soundfile as sf
from pathlib import Path
from click.testing import CliRunner
sys.path.insert(0, str(Path(__file__).parent.parent))
from tests.helpers import create_test_library
from main import main

PASS = "  ✅ PASS"
FAIL = "  ❌ FAIL"

def run():
    results = []
    runner = CliRunner()

    with tempfile.TemporaryDirectory() as tmpdir:
        input_dir = Path(tmpdir) / "input"
        input_dir.mkdir()
        create_test_library(16, str(input_dir))
        out_wav = Path(tmpdir) / "output" / "mix.wav"
        out_wav.parent.mkdir()

        result = runner.invoke(main, [
            '--input', str(input_dir),
            '--duration', '5',       # 5 phút để test nhanh
            '--output', str(out_wav),
            '--crossfade', '5',
            '--sample-rate', '48000',
            '--bit-depth', '24',
        ])

        results.append(("CLI exit code = 0", result.exit_code == 0))
        if result.exit_code != 0:
            print(f"  CLI output: {result.output}")
            print(f"  Exception: {result.exception}")

        results.append(("WAV file tồn tại", out_wav.exists()))

        if out_wav.exists():
            info = sf.info(str(out_wav))
            results.append(("WAV subtype = PCM_24", info.subtype == 'PCM_24'))
            results.append(("WAV sample rate = 48000", info.samplerate == 48000))
            results.append(("WAV là stereo", info.channels == 2))
            ok_dur = abs(info.duration - 300) <= 30   # 5 min ± 30s
            results.append((f"Duration gần 5 phút: {info.duration:.0f}s", ok_dur))

        stem = out_wav.stem
        parent = out_wav.parent
        results.append((".cue file tạo ra", (parent / f"{stem}.cue").exists()))
        results.append(("_tracklist.txt tạo ra", (parent / f"{stem}_tracklist.txt").exists()))
        results.append(("_info.json tạo ra", (parent / f"{stem}_info.json").exists()))

        json_path = parent / f"{stem}_info.json"
        if json_path.exists():
            data = json.loads(json_path.read_text())
            results.append(("JSON tracks > 0", len(data.get("tracks", [])) > 0))
            results.append(("JSON audio.sample_rate = 48000",
                             data.get("audio", {}).get("sample_rate") == 48000))

    print("\n=== PHASE 6 INTEGRATION VERIFICATION ===")
    all_pass = True
    for name, passed in results:
        print(f"{PASS if passed else FAIL} {name}")
        if not passed:
            all_pass = False
    print(f"\n{'✅ ALL PHASES PASSED – Tool sẵn sàng dùng thực tế' if all_pass else '❌ Integration FAILED – xem lỗi phía trên'}")
    sys.exit(0 if all_pass else 1)

if __name__ == "__main__":
    run()
