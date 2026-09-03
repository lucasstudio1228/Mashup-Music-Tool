"""
verify_phase0_stems.py
Kiểm tra: demucs, pyloudnorm, CUDA, model htdemucs_ft.
"""
import sys, tempfile, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
try:
    import console
except ImportError:
    pass

PASS = "  ✅ PASS"
FAIL = "  ❌ FAIL"

def run():
    results = []

    # ── Test 1: Import demucs ──
    try:
        import demucs.api
        results.append(("demucs import OK", True))
    except ImportError as e:
        results.append((f"demucs import FAILED: {e}", False))
        sys.exit(1)

    # ── Test 2: Import pyloudnorm ──
    try:
        import pyloudnorm as pyln
        results.append(("pyloudnorm import OK", True))
    except ImportError as e:
        results.append((f"pyloudnorm import FAILED: {e}", False))
        sys.exit(1)

    # ── Test 3: CUDA availability ──
    import torch
    cuda_ok = torch.cuda.is_available()
    if cuda_ok:
        gpu_name = torch.cuda.get_device_name(0)
        vram_gb  = torch.cuda.get_device_properties(0).total_memory / 1e9
        results.append((f"CUDA OK: {gpu_name} ({vram_gb:.1f} GB VRAM)", True))
    else:
        results.append(("CUDA NOT available — sẽ dùng CPU (chậm hơn)", True))
        # Không fail — chỉ warn

    # ── Test 4: Demucs separation với htdemucs_ft ──
    from tests.helpers import generate_test_wav
    with tempfile.TemporaryDirectory() as tmpdir:
        src     = Path(tmpdir) / "test.wav"
        out_dir = Path(tmpdir) / "stems"
        generate_test_wav(str(src), duration_sec=8.0,
                          sample_rate=44100, channels=2)

        print("\n  Đang load model htdemucs_ft (lần đầu ~300MB)...")
        t0 = time.time()
        try:
            separator = demucs.api.Separator(
                model="htdemucs_ft",
                device="cuda" if cuda_ok else "cpu",
            )
            origin, separated = separator.separate_audio_file(str(src))
            elapsed = time.time() - t0

            results.append((f"htdemucs_ft separation OK ({elapsed:.1f}s)", True))

            # Kiểm tra 4 stems đúng tên
            expected = {"drums", "bass", "vocals", "other"}
            got      = set(separated.keys())
            results.append((f"Stems: {sorted(got)}", expected == got))

            # Kiểm tra tensor dtype (phải là float32)
            sample_tensor = list(separated.values())[0]
            results.append(("Tensor dtype float32",
                             str(sample_tensor.dtype) == "torch.float32"))

            # Ước tính tốc độ
            rate = elapsed / 8.0
            print(f"\n  ⚡ Tốc độ thực tế trên {'GPU' if cuda_ok else 'CPU'}:")
            print(f"     1 track 5 phút  ≈ {rate*300/60:.1f} phút")
            print(f"     15 tracks 5 phút ≈ {rate*300*15/60:.0f} phút")

        except Exception as e:
            results.append((f"Demucs separation failed: {e}", False))

    # ── Test 5: pyloudnorm LUFS measurement ──
    import numpy as np, soundfile as sf
    with tempfile.TemporaryDirectory() as tmpdir:
        p = Path(tmpdir) / "test.wav"
        generate_test_wav(str(p), duration_sec=10.0, amplitude=0.5)
        audio, sr = sf.read(str(p), dtype="float32")
        meter     = pyln.Meter(sr)
        loudness  = meter.integrated_loudness(audio)
        ok        = -70.0 < loudness < 0.0   # LUFS phải trong khoảng hợp lệ
        results.append((f"pyloudnorm đo LUFS OK: {loudness:.1f} LUFS", ok))

        # Test normalize
        normalized = pyln.normalize.loudness(audio, loudness, -16.0)
        meter2     = pyln.Meter(sr)
        new_loud   = meter2.integrated_loudness(normalized)
        ok2        = abs(new_loud - (-16.0)) < 0.5  # sai số < 0.5 LUFS
        results.append((f"Normalize về -16 LUFS: {new_loud:.1f} LUFS", ok2))

    # ── Report ──
    print("\n=== PHASE 0 VERIFY ===")
    all_pass = True
    for name, passed in results:
        print(f"{PASS if passed else FAIL} {name}")
        if not passed:
            all_pass = False
    print(f"\n{'✅ Phase 0 PASSED' if all_pass else '❌ FAILED – fix trước khi tiếp tục'}")
    sys.exit(0 if all_pass else 1)

if __name__ == "__main__":
    run()
