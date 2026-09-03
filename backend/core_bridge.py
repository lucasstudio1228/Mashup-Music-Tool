"""
core_bridge.py – Adapter giữa backend và existing core modules.
Tất cả gọi mixer đi qua đây để cô lập việc quản lý import path.
KHÔNG sửa core modules — chỉ wrap chúng.
"""
import os
import sys
from pathlib import Path

# Add meditation_mixer root vào path để import được core modules ở parent dir.
_ROOT = Path(__file__).parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import numpy as np  # noqa: E402
import soundfile as sf  # noqa: E402

from api_config import APIConfig  # noqa: E402
from audio_loader import (SUPPORTED_EXTENSIONS, Track, load_library)  # noqa: E402
from crossfade_engine import render_mix  # noqa: E402
from metadata_writer import write_metadata  # noqa: E402
from playlist_generator import generate_playlist  # noqa: E402
from track_namer import generate_names  # noqa: E402


def scan_folder(folder_path: str, recursive: bool = False) -> tuple[list[Track], list[str]]:
    """
    Scan 1 folder → (tracks hợp lệ, danh sách filename lỗi).
    Không dùng load_library trực tiếp vì nó raise khi < 15 track;
    ở tầng scan ta muốn lấy tất cả những gì đọc được rồi để router quyết định.
    """
    folder = Path(folder_path)
    pattern = "**/*" if recursive else "*"
    candidates = [p for p in folder.glob(pattern)
                  if p.is_file() and p.suffix.lower() in SUPPORTED_EXTENSIONS]

    tracks: list[Track] = []
    error_files: list[str] = []
    for path in sorted(candidates):
        loaded = _probe_single(str(path))
        if loaded is None:
            error_files.append(path.name)
        else:
            tracks.append(loaded)
    return tracks, error_files


def probe_file(file_path: str) -> Track | None:
    """Đọc metadata 1 file đơn lẻ. None nếu lỗi."""
    return _probe_single(file_path)


def _probe_single(file_path: str) -> Track | None:
    """Tận dụng load_library (đọc đúng metadata) trên đúng 1 file."""
    try:
        tracks = _load_ignore_minimum([file_path])
        return tracks[0] if tracks else None
    except Exception:
        return None


def _load_ignore_minimum(paths: list[str]) -> list[Track]:
    """
    load_library raise ValueError khi < 15 track. Ở tầng scan/probe ta chỉ cần
    metadata nên tạm nâng ngưỡng lên 0 quanh lời gọi (không sửa file core).
    """
    import audio_loader
    original = audio_loader.MIN_TRACKS
    audio_loader.MIN_TRACKS = 0
    try:
        return load_library(paths)
    finally:
        audio_loader.MIN_TRACKS = original


def get_api_config_from_db(db_path: str) -> APIConfig:
    """
    Đọc AppSettings từ SQLite trực tiếp (không qua FastAPI session)
    vì hàm này chạy trong ThreadPoolExecutor thread.
    """
    import sqlite3
    try:
        conn = sqlite3.connect(db_path)
        try:
            cur = conn.execute(
                "SELECT api_key, api_base_url, api_model "
                "FROM appsettings WHERE id=1"
            )
            row = cur.fetchone()
        finally:
            conn.close()
    except sqlite3.OperationalError:
        # Bảng chưa tồn tại — fallback về env vars.
        return APIConfig()

    if not row:
        # Fallback về env vars nếu chưa có settings trong DB.
        return APIConfig()

    api_key, base_url, model = row
    cfg = APIConfig()
    # DB override env, nhưng env là fallback nếu DB trống.
    cfg.api_key = api_key or cfg.api_key
    cfg.base_url = base_url or cfg.base_url
    cfg.model = model or cfg.model
    return cfg


def _load_track_audio_with_stems(
    filepath: str,
    target_sr: int,
    db_path: str,
) -> np.ndarray:
    """
    Load audio cho 1 track:
      Path A (stems ready): mix 4 stems với volume settings → đã LUFS normalized
      Path B (fallback):    load file gốc → apply LUFS normalization

    Không bao giờ raise → luôn trả về audio array hợp lệ.
    """
    import sqlite3
    import librosa
    from backend.loudness_service import normalize_audio

    # ── Kiểm tra stems có sẵn không ──
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute("""
            SELECT t.id, t.project_id,
                   ts.status,
                   ts.vol_other, ts.vol_bass,
                   ts.vol_drums, ts.vol_vocals
            FROM track t
            LEFT JOIN trackstem ts ON ts.track_id = t.id
            WHERE t.filepath = ?
        """, (filepath,)).fetchone()
    finally:
        conn.close()

    if row and row[2] == "completed":
        track_id, project_id, _, vo, vb, vd, vv = row
        from backend.stem_service import load_mixed_stems
        mixed = load_mixed_stems(
            project_id=project_id,
            track_id=track_id,
            vol_other=vo,  vol_bass=vb,
            vol_drums=vd,  vol_vocals=vv,
            target_sr=target_sr,
        )
        if mixed is not None:
            # Stems đã được LUFS normalized trong stem_service
            return mixed
        # Nếu load_mixed_stems trả None (file bị xóa) → fallback

    # ── Fallback: load file gốc + LUFS normalize ──
    audio, sr = sf.read(filepath, dtype="float32")
    if audio.ndim == 1:
        audio = np.column_stack([audio, audio])
    if sr != target_sr:
        audio = librosa.resample(
            audio.T, orig_sr=sr, target_sr=target_sr,
            res_type="kaiser_best"
        ).T
    # Normalize về -16 LUFS để đồng bộ với stems
    audio = normalize_audio(audio, target_sr)
    return audio


def _get_track_stem_status(filepath: str, db_path: str) -> str:
    """Trả về stem status của track ('completed', 'pending', ...)."""
    import sqlite3
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute("""
            SELECT ts.status FROM track t
            LEFT JOIN trackstem ts ON ts.track_id = t.id
            WHERE t.filepath = ?
        """, (filepath,)).fetchone()
        return row[0] if row and row[0] else "not_started"
    finally:
        conn.close()


def run_mix_job(
    mix_id: int,
    progress_cb,           # callback(step, step_name, percent, message="")
    track_paths: list[str],
    output_dir: str,
    duration_minutes: float,
    crossfade_seconds: float,
    sample_rate: int,
    bit_depth: int,
    db_path: str,          # đọc APIConfig từ DB trong worker thread
) -> dict:
    """
    Chạy full mix pipeline trong ThreadPoolExecutor worker.
    Return metadata dict để router cập nhật Mix record.
    """
    api_config = get_api_config_from_db(db_path)
    os.makedirs(output_dir, exist_ok=True)

    # Step 1/6 – Load & validate tracks
    progress_cb(1, "Loading tracks", 5.0)
    tracks = load_library(track_paths)

    # Step 2/6 – Generate playlist
    progress_cb(2, "Generating playlist", 15.0)
    target_seconds = duration_minutes * 60
    playlist = generate_playlist(tracks, target_seconds, crossfade_seconds)

    # Step 3/6 – Name tracks
    progress_cb(3, "Naming tracks", 22.0)
    names = generate_names(len(playlist), api_config)

    # Step 4/6 – Pre-load audio (stems nếu có, fallback nếu không)
    progress_cb(4, "Loading stems & mixing", 28.0)
    preloaded: dict[str, np.ndarray] = {}
    for i, track in enumerate(playlist):
        pct = 28.0 + (i / max(len(playlist), 1)) * 30.0
        stem_status = _get_track_stem_status(track.path, db_path)
        msg = (f"[{i+1}/{len(playlist)}] "
               f"{'[STEMS] ' if stem_status == 'completed' else '[ORIGINAL] '}"
               f"{Path(track.path).name}")
        progress_cb(4, msg, pct)
        preloaded[track.path] = _load_track_audio_with_stems(
            track.path, sample_rate, db_path
        )

    # Step 5/6 – Render mix (dùng preloaded audio)
    output_wav = os.path.join(output_dir, "mix.wav")

    def render_progress(written, total):
        # render_mix gọi (written_samples, total_samples). Map → overall 58-88%.
        pct = (written / total) if total else 0.0
        progress_cb(5, "Rendering audio", 58.0 + pct * 30.0)

    timestamps = render_mix(
        playlist=playlist,
        output_path=output_wav,
        crossfade_sec=crossfade_seconds,
        target_sr=sample_rate,
        bit_depth=bit_depth,
        target_seconds=target_seconds,
        progress_callback=render_progress,
        preloaded_audio=preloaded,    # ← key mới
    )

    for i, ts in enumerate(timestamps):
        ts["name"] = names[i] if i < len(names) else f"Track {i + 1}"

    # Step 6/6 – Write metadata
    progress_cb(6, "Writing metadata", 92.0)
    info = sf.info(output_wav)
    write_metadata(
        timestamps=timestamps,
        output_wav_path=output_wav,
        total_duration_sec=info.duration,
        sample_rate=sample_rate,
        bit_depth=bit_depth,
        crossfade_sec=crossfade_seconds,
        channels=info.channels,
    )

    # Done
    progress_cb(6, "Completed", 100.0)

    return {
        "total_duration_seconds": info.duration,
        "track_count": len(playlist),
        "output_dir": output_dir,
    }
