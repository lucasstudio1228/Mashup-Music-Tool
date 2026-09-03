"""
stem_service.py
Wrapper cho Demucs htdemucs_ft.
Chạy trong ThreadPoolExecutor — không có asyncio context.
"""
import json, sqlite3, numpy as np, soundfile as sf
from pathlib import Path
from datetime import datetime, timezone
from typing import Callable, Optional

STEMS_ROOT = Path(__file__).parent.parent / "stems"
DB_PATH    = Path(__file__).parent.parent / "data" / "app.db"
STEM_NAMES = ["other", "bass", "drums", "vocals"]

# ─── DB helpers (sqlite3 thuần — không dùng SQLModel) ───────────

def _db_conn():
    return sqlite3.connect(str(DB_PATH))

def _db_set_status(stem_id: int, status: str,
                   error: str | None = None,
                   gain: float | None = None):
    now  = datetime.now(timezone.utc).isoformat()
    conn = _db_conn()
    try:
        if status == "running":
            conn.execute(
                "UPDATE trackstem SET status=?, started_at=? WHERE id=?",
                (status, now, stem_id))
        elif status in ("completed", "failed"):
            conn.execute(
                """UPDATE trackstem
                   SET status=?, completed_at=?, error_message=?,
                       lufs_gain_applied=?
                   WHERE id=?""",
                (status, now, error, gain, stem_id))
        else:
            conn.execute("UPDATE trackstem SET status=? WHERE id=?",
                         (status, stem_id))
        conn.commit()
    finally:
        conn.close()

def _db_save_waveform(stem_id: int, waveform_json: str):
    conn = _db_conn()
    try:
        conn.execute("UPDATE trackstem SET waveform_data=? WHERE id=?",
                     (waveform_json, stem_id))
        conn.commit()
    finally:
        conn.close()

def _stem_record_still_exists(stem_id: int) -> bool:
    """Re-check DB trước khi write — tránh orphan files khi project bị xóa."""
    conn = _db_conn()
    try:
        row = conn.execute(
            "SELECT id FROM trackstem WHERE id=?", (stem_id,)
        ).fetchone()
        return row is not None
    finally:
        conn.close()

# ─── Waveform thumbnail ──────────────────────────────────────────

def _rms_thumbnail(audio: np.ndarray, n_points: int = 200) -> list[float]:
    """
    Tính 200 điểm RMS để vẽ waveform mini.
    Input: (samples, channels) hoặc (samples,)
    """
    mono  = audio.mean(axis=1) if audio.ndim == 2 else audio
    chunk = max(1, len(mono) // n_points)
    pts   = []
    for i in range(n_points):
        seg = mono[i*chunk : (i+1)*chunk]
        pts.append(float(np.sqrt(np.mean(seg**2))) if len(seg) else 0.0)
    mx = max(pts) or 1.0
    return [v / mx for v in pts]

# ─── Spectral noise gate (giảm bleed sau Demucs) ────────────────

# Cường độ khử noise theo từng stem. prop_decrease tối đa 0.7 —
# cao hơn dễ làm méo nhạc chính. drums/vocals bleed nhiều nhất.
_DENOISE_CONFIG = {
    "other":  {"prop_decrease": 0.6, "n_fft": 2048},
    "vocals": {"prop_decrease": 0.7, "n_fft": 2048},
    "bass":   {"prop_decrease": 0.5, "n_fft": 2048},
    "drums":  {"prop_decrease": 0.7, "n_fft": 2048},
}

def _denoise_stem(audio: np.ndarray, sr: int, stem_name: str) -> np.ndarray:
    """
    Spectral noise gate (stationary) để giảm bleed artifacts.

    - Xử lý từng channel riêng (stereo), giữ float32 xuyên suốt.
    - Fallback: nếu noisereduce lỗi/chậm → trả nguyên audio gốc,
      log warning, KHÔNG block pipeline (constraint 3).
    """
    try:
        import noisereduce as nr
    except Exception as e:
        print(f"  ⚠️  noisereduce không khả dụng ({e}) — bỏ qua denoise {stem_name}")
        return audio

    cfg = _DENOISE_CONFIG.get(stem_name, {"prop_decrease": 0.6, "n_fft": 2048})

    try:
        result     = np.zeros_like(audio, dtype=np.float32)
        n_channels = audio.shape[1] if audio.ndim == 2 else 1

        for ch in range(n_channels):
            channel = audio[:, ch] if audio.ndim == 2 else audio
            cleaned = nr.reduce_noise(
                y=channel.astype(np.float32),
                sr=sr,
                stationary=True,
                prop_decrease=cfg["prop_decrease"],
                n_fft=cfg["n_fft"],
                n_jobs=1,          # tránh multiprocessing conflict trong thread
            ).astype(np.float32)

            if audio.ndim == 2:
                result[:, ch] = cleaned
            else:
                result = cleaned

        return result
    except Exception as e:
        print(f"  ⚠️  denoise {stem_name} lỗi ({type(e).__name__}: {e}) — dùng stem gốc")
        return audio

# ─── CUDA check ─────────────────────────────────────────────────

def _get_device() -> str:
    try:
        import torch
        if torch.cuda.is_available():
            name = torch.cuda.get_device_name(0)
            vram = torch.cuda.get_device_properties(0).total_memory / 1e9
            print(f"  🎮 GPU: {name} ({vram:.1f} GB VRAM) — CUDA enabled")
            return "cuda"
    except ImportError:
        pass
    print("  ⚠️  CUDA không khả dụng — dùng CPU")
    return "cpu"

# ─── Main separation function ────────────────────────────────────

def separate_track(
    stem_id:    int,
    track_id:   int,
    project_id: int,
    audio_path: str,
    progress_cb: Optional[Callable[[str], None]] = None,
) -> None:
    """
    Chạy Demucs htdemucs_ft cho 1 track.
    Gọi từ StemJobManager trong ThreadPoolExecutor.

    Pipeline:
      1. Load track gốc → đo LUFS
      2. Demucs separation → 4 stem tensors (float32)
      3. Convert tensors → numpy arrays
      4. Apply LUFS normalization (cùng gain cho tất cả stems)
      5. Lưu mỗi stem thành WAV float32
      6. Tính waveform thumbnails
      7. Lưu kết quả vào DB
    """
    import demucs.api
    import torchaudio
    import torch
    from backend.loudness_service import (
        normalize_stems_to_target, TARGET_LUFS
    )

    _db_set_status(stem_id, "running")

    try:
        # out_dir tạo lazy SAU guard check (Bước 6) — tránh orphan directory
        # nếu project bị xóa trong khi job đang chạy.
        out_dir = STEMS_ROOT / str(project_id) / str(track_id)

        # ── Bước 1: Load track gốc để đo LUFS ──
        if progress_cb: progress_cb("Đang load audio...")
        original, orig_sr = sf.read(audio_path, dtype="float32")
        if original.ndim == 1:
            original = np.column_stack([original, original])

        # ── Bước 2: Khởi tạo Demucs ──
        if progress_cb: progress_cb("Đang load model htdemucs_ft...")
        device = _get_device()
        separator = demucs.api.Separator(
            model="htdemucs_ft",
            device=device,
            # Dùng shifts=1 để chất lượng tốt hơn (ít artifact hơn)
            # shifts=0 nhanh hơn nhưng có thể nghe tiếng "metallic"
            shifts=1,
            overlap=0.25,     # overlap giữa các chunk — tránh seam artifact
        )

        # ── Bước 3: Separation ──
        if progress_cb: progress_cb(
            f"Đang tách stems {'(GPU)' if device=='cuda' else '(CPU)'}...")
        origin_tensor, separated = separator.separate_audio_file(audio_path)

        # ── Bước 4: Convert tensor → numpy float32 ──
        # Demucs output: tensor shape (channels, samples)
        stem_arrays: dict[str, np.ndarray] = {}
        for name, tensor in separated.items():
            if name not in STEM_NAMES:
                continue
            # Chuyển về (samples, channels) — chuẩn soundfile
            arr = tensor.cpu().numpy().T.astype(np.float32)
            # Đảm bảo stereo
            if arr.ndim == 1:
                arr = np.column_stack([arr, arr])
            stem_arrays[name] = arr

        # ── Bước 4.5: Denoise stems (spectral gate — giảm bleed) ──
        # Chạy TRƯỚC LUFS normalize, trên sr gốc của stem (= separator.samplerate).
        if progress_cb: progress_cb("Đang khử noise...")
        stem_sr = separator.samplerate
        for name in STEM_NAMES:
            if name in stem_arrays:
                stem_arrays[name] = _denoise_stem(
                    stem_arrays[name], stem_sr, name
                )

        # ── Bước 5: LUFS Normalization ──
        if progress_cb: progress_cb("Đang normalize LUFS...")

        # Resample original về sr của separator nếu cần (thường là 44100)
        target_sr = separator.samplerate
        if orig_sr != target_sr:
            import librosa
            original = librosa.resample(
                original.T, orig_sr=orig_sr, target_sr=target_sr,
                res_type="kaiser_best"
            ).T

        # Apply CÙNG gain cho tất cả stems (đo từ track gốc)
        normalized_stems, gain_applied = normalize_stems_to_target(
            stems=stem_arrays,
            original_audio=original,
            sr=target_sr,
            target_lufs=TARGET_LUFS,
        )

        # ── Existence guard: tránh orphan files nếu project/track bị xóa
        #    trong khi job đang chạy (demucs mất ~40 phút) ──
        if not _stem_record_still_exists(stem_id):
            # Project/track đã bị xóa trong khi job đang chạy
            # Dọn sạch out_dir nếu đã tạo, không write gì cả
            import shutil
            if out_dir.exists():
                shutil.rmtree(out_dir, ignore_errors=True)
            return   # Không raise, không set status — row đã không còn

        # Lazy mkdir: chỉ tạo directory khi record còn tồn tại (sau guard)
        out_dir.mkdir(parents=True, exist_ok=True)

        # ── Bước 6: Lưu WAV float32 (KHÔNG nén) ──
        if progress_cb: progress_cb("Đang lưu stem files...")
        for name in STEM_NAMES:
            arr      = normalized_stems.get(name)
            if arr is None:
                continue
            wav_path = out_dir / f"{name}.wav"
            sf.write(str(wav_path), arr, target_sr, subtype="FLOAT")
            # FLOAT = 32-bit float PCM — không mất chất lượng

        # ── Bước 7: Waveform thumbnails ──
        if progress_cb: progress_cb("Đang tính waveform...")
        waveform = {
            name: _rms_thumbnail(normalized_stems[name])
            for name in STEM_NAMES
            if name in normalized_stems
        }
        _db_save_waveform(stem_id, json.dumps(waveform))
        _db_set_status(stem_id, "completed", gain=float(gain_applied))

        if progress_cb: progress_cb("Hoàn thành!")

    except Exception as e:
        import traceback
        err_msg = f"{type(e).__name__}: {e}\n{traceback.format_exc()[-500:]}"
        _db_set_status(stem_id, "failed", error=err_msg)
        raise

# ─── Mix stems back → 1 audio array ─────────────────────────────

def load_mixed_stems(
    project_id: int,
    track_id:   int,
    vol_other:  float,
    vol_bass:   float,
    vol_drums:  float,
    vol_vocals: float,
    target_sr:  int,
) -> Optional[np.ndarray]:
    """
    Load 4 stem WAV files, apply volume, mix thành 1 stereo array.
    Return None nếu bất kỳ stem nào thiếu.

    Volume range 0.0–2.0:
      0.0 = mute hoàn toàn
      1.0 = giữ nguyên (sau LUFS normalize)
      2.0 = boost gấp đôi

    Sau khi mix: soft limiter để tránh méo tiếng.
    """
    import librosa

    stem_dir = STEMS_ROOT / str(project_id) / str(track_id)
    volumes  = {
        "other":  vol_other,
        "bass":   vol_bass,
        "drums":  vol_drums,
        "vocals": vol_vocals,
    }

    # Kiểm tra tất cả 4 stems tồn tại
    for name in STEM_NAMES:
        if not (stem_dir / f"{name}.wav").exists():
            return None

    mixed: Optional[np.ndarray] = None

    for name in STEM_NAMES:
        arr, sr = sf.read(str(stem_dir / f"{name}.wav"), dtype="float32")

        # Resample về target_sr nếu khác
        if sr != target_sr:
            arr = librosa.resample(
                arr.T, orig_sr=sr, target_sr=target_sr,
                res_type="kaiser_best"
            ).T

        # Đảm bảo stereo
        if arr.ndim == 1:
            arr = np.column_stack([arr, arr])
        elif arr.shape[1] == 1:
            arr = np.column_stack([arr[:, 0], arr[:, 0]])

        # Apply volume
        arr = arr * volumes[name]

        # Accumulate
        if mixed is None:
            mixed = arr
        else:
            n     = min(len(mixed), len(arr))
            mixed = mixed[:n] + arr[:n]

    if mixed is None:
        return None

    # Soft limiter sau khi mix (KHÔNG hard-clip)
    peak = np.max(np.abs(mixed))
    if peak > 0.99:
        mixed = mixed * (0.99 / peak)

    return mixed
