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

from analyzer import find_transition_points  # noqa: E402
from api_config import APIConfig  # noqa: E402
from audio_loader import (SUPPORTED_EXTENSIONS, Track, load_library)  # noqa: E402
from crossfade_engine import render_mix  # noqa: E402
from metadata_writer import write_metadata  # noqa: E402
from playlist_generator import generate_playlist  # noqa: E402
from track_namer import generate_names  # noqa: E402

# Sàn sample rate: không upsample giả xuống dưới mức này, cũng không ép lên cao
# hơn nguồn. 44100 = sr gốc của stem Demucs.
MIN_SAMPLE_RATE = 44100


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


def resolve_target_sample_rate(track_sample_rates: list[int]) -> int:
    """
    Auto sample rate = max native trong library (sàn 44100), KHÔNG upsample giả.
    Dùng khi người dùng chọn 'Auto (gốc cao nhất)'.
    """
    return max(list(track_sample_rates) + [MIN_SAMPLE_RATE])


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
    source_path: str | None = None,
    normalize: bool = True,
) -> np.ndarray:
    """
    Load audio cho 1 track:
      Path A (stems ready): mix 4 stems với volume settings → đã LUFS normalized
                            + đã khử noise/bleed thông minh theo volume
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
                   ts.vol_drums, ts.vol_vocals,
                   ts.den_other, ts.den_bass,
                   ts.den_drums, ts.den_vocals
            FROM track t
            LEFT JOIN trackstem ts ON ts.track_id = t.id
            WHERE t.filepath = ?
        """, (filepath,)).fetchone()
    finally:
        conn.close()

    if row and row[2] == "completed":
        (track_id, project_id, _, vo, vb, vd, vv,
         do, db_, dd, dv) = row
        from backend.stem_service import load_mixed_stems
        mixed = load_mixed_stems(
            project_id=project_id,
            track_id=track_id,
            vol_other=vo,  vol_bass=vb,
            vol_drums=vd,  vol_vocals=vv,
            den_other=do,  den_bass=db_,
            den_drums=dd,  den_vocals=dv,
            target_sr=target_sr,
        )
        if mixed is not None:
            # Stems đã được LUFS normalized + smart-clean trong stem_service
            return mixed
        # Nếu load_mixed_stems trả None (file bị xóa) → fallback

    # ── Fallback: load file gốc (hoặc bản master của nó) + LUFS normalize ──
    # source_path = bản _master (mastering_service); normalize=False khi bước
    # cân bằng âm lượng (loudness_balance) lo độ to sau.
    audio, sr = sf.read(source_path or filepath, dtype="float32")
    if audio.ndim == 1:
        audio = np.column_stack([audio, audio])
    if sr != target_sr:
        audio = librosa.resample(
            audio.T, orig_sr=sr, target_sr=target_sr,
            res_type="kaiser_best"
        ).T
    # Normalize về -16 LUFS để đồng bộ với stems
    if normalize:
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


def _load_mastered_balanced(
    paths: list[str],
    sample_rate: int,
    db_path: str,
    progress_cb,
    pct_from: float = 8.0,
    pct_to: float = 50.0,
    reuse: dict | None = None,
) -> tuple[dict[str, np.ndarray], dict]:
    """Master + cân bằng âm lượng như tool «Cover nhạc» (2026-11-17):

      1. Master từng bài bằng ffmpeg (EQ, mono bass, nén nhẹ, loudnorm 2 pass)
         → bộ đệm <thư mục bài>/mastered/ (mastering_service). Bài đã có stems
         (Demucs) giữ đường stems, không master.
      2. Đọc bản master (lỗi → file gốc), ĐO «đoạn to» mọi bài, dời gain để các
         bài to đều nhau, trung vị = LUFS của preset; limiter nhìn trước chặn
         đỉnh -1 dBFS (loudness_balance).

    Trả (audio đã xử lý theo đường dẫn GỐC, khối «loudness» cho mix_info.json).
    Tắt trong data/master_config.json: "enabled" (master) / "balance_enabled".

    reuse = khối «loudness» của bản mix CŨ (vá mix): bài còn giữ dùng LẠI đúng
    gain cũ (đoạn trước bài thay giống hệt), bài mới nhận gain để «đoạn to»
    bằng mức chung của bản cũ.
    """
    from backend import loudness_balance as lb
    from backend import mastering_service as ms
    from backend.loudness_service import normalize_audio

    cfg = ms.load_config()
    ms.ensure_config_file()
    target = ms.target_lufs(cfg)
    uniq = list(dict.fromkeys(paths))
    n = max(len(uniq), 1)
    span = pct_to - pct_from
    stems = {p: _get_track_stem_status(p, db_path) == "completed" for p in uniq}

    masters: dict[str, dict] = {}
    to_master = [p for p in uniq if not stems[p]]
    if cfg.get("enabled", True) and to_master:
        progress_cb(2, f"Master âm thanh 0/{len(to_master)}", pct_from)
        labels = {"cached": "đã có", "done": "xong", "failed": "lỗi → dùng file gốc"}

        def on_master(done, total, res):
            state = labels.get(res.get("status"), res.get("status"))
            progress_cb(2, f"Master âm thanh {done}/{total}: {res.get('file')} ({state})",
                        pct_from + span * 0.5 * done / total)
        masters = ms.master_many(to_master, cfg, on_master)

    balance = bool(cfg.get("balance_enabled", True))
    load_from = pct_from + span * 0.5 if masters else pct_from
    load_span = pct_to - load_from
    audio: dict[str, np.ndarray] = {}
    rows: list[dict] = []
    for i, p in enumerate(uniq):
        m = masters.get(p) or {}
        src = m.get("path") if m.get("status") in ("done", "cached") else None
        kind = "stems" if stems[p] else ("master" if src else "original")
        progress_cb(2, f"[{i + 1}/{len(uniq)}] [{kind.upper()}] {Path(p).name}",
                    load_from + load_span * (0.6 if balance else 1.0) * i / n)
        audio[p] = _load_track_audio_with_stems(p, sample_rate, db_path,
                                                source_path=src, normalize=not balance)
        if not balance and src:
            # Bản master đã ở LUFS của preset → chuẩn theo đúng mức đó.
            audio[p] = normalize_audio(audio[p], sample_rate, target)
        rows.append({"audio_file": Path(p).name, "source": kind,
                     "master": ({k: m.get(k) for k in ("status", "before", "after", "error")}
                                if m else None)})
    info: dict = {"mastered": sum(r["source"] == "master" for r in rows),
                  "master_preset": cfg.get("preset"), "target_lufs": target,
                  "balanced": balance, "songs": rows}
    if not balance:
        return audio, info

    stats = []
    for i, p in enumerate(uniq):
        progress_cb(2, f"Đo âm lượng [{i + 1}/{len(uniq)}] {Path(p).name}",
                    load_from + load_span * (0.6 + 0.2 * i / n))
        stats.append(lb.analyze(audio[p], sample_rate))
    plan = lb.plan(stats, target)
    old_gain = {r.get("audio_file"): r.get("gain_db")
                for r in (reuse or {}).get("songs") or [] if r.get("gain_db") is not None}
    levels = [r["after"]["loud"] for r in (reuse or {}).get("songs") or []
              if (r.get("after") or {}).get("loud") is not None]
    if old_gain and levels:
        level = float(np.median(levels))
        for i, p in enumerate(uniq):
            name = Path(p).name
            if name in old_gain:
                plan[i]["gain_db"] = float(old_gain[name])
            elif stats[i].get("loud") is not None:
                plan[i]["gain_db"] = round(max(lb.MAX_CUT_DB, min(
                    lb.MAX_BOOST_DB, level - stats[i]["loud"])), 2)
        info["reused_gains"] = True
    for i, (p, st, pl) in enumerate(zip(uniq, stats, plan)):
        progress_cb(2, f"Cân bằng âm lượng [{i + 1}/{len(uniq)}] {Path(p).name}",
                    load_from + load_span * (0.8 + 0.2 * i / n))
        audio[p], limited = lb.apply(audio[p], sample_rate, pl["gain_db"])
        rows[i].update(before=st, after=lb.analyze(audio[p], sample_rate),
                       gain_db=pl["gain_db"], limited_db=limited, flag=pl["flag"])
    info.update(ceiling_db=lb.CEILING_DB,
                spread_before=_spread([st.get("loud") for st in stats]),
                spread_after=_spread([r["after"].get("loud") for r in rows]))
    return audio, info


def _spread(values) -> float | None:
    """Chênh «đoạn to» giữa bài to nhất và nhỏ nhất (LU)."""
    vals = [v for v in values if v is not None]
    return round(max(vals) - min(vals), 2) if vals else None


def _write_loudness_info(files: dict, loudness: dict) -> None:
    """Ghi khối «loudness» (master + cân bằng) vào mix_info.json."""
    import json
    try:
        p = Path(files["json"])
        data = json.loads(p.read_text(encoding="utf-8"))
        data["loudness"] = loudness
        p.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n",
                     encoding="utf-8")
    except (OSError, ValueError, KeyError, TypeError):
        pass


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

    sample_rate <= 0 → auto: max native của library (sàn 44100), không upsample giả.
    bit_depth mặc định 32 (float, không nén) để giữ headroom tối đa.
    """
    api_config = get_api_config_from_db(db_path)
    os.makedirs(output_dir, exist_ok=True)

    # Step 1/6 – Load & validate tracks
    progress_cb(1, "Loading tracks", 5.0)
    tracks = load_library(track_paths)

    # Auto sample rate nếu chưa chỉ định (hoặc <= 0).
    if not sample_rate or sample_rate <= 0:
        sample_rate = resolve_target_sample_rate([t.sample_rate for t in tracks])

    # Step 2/6 – Pre-load audio (stems nếu có, fallback nếu không). Nạp TRƯỚC khi
    # xếp playlist để đo độ dài THỰC mỗi bài đóng góp vào mix (render bỏ dead-air
    # đầu/đuôi + overlap crossfade) — ước lượng theo duration file làm mix hụt
    # so với yêu cầu (vd đặt 60' ra 58'35").
    # Master + cân bằng âm lượng giữa các bài (như tool Cover nhạc).
    progress_cb(2, "Loading stems", 8.0)
    preloaded, loudness = _load_mastered_balanced(
        [t.path for t in tracks], sample_rate, db_path, progress_cb)
    effective: dict[str, float] = {}
    for path, audio in preloaded.items():
        tail, head = find_transition_points(audio, sample_rate, crossfade_seconds)
        effective[path] = (tail - head) / sample_rate - crossfade_seconds

    # Step 3/6 – Generate playlist (shuffle theo vòng + T+5)
    progress_cb(3, "Generating playlist", 52.0)
    target_seconds = duration_minutes * 60
    playlist = generate_playlist(tracks, target_seconds, crossfade_seconds,
                                 effective=effective)

    # Step 4/6 – Name tracks
    progress_cb(4, "Naming tracks", 55.0)
    names = generate_names(len(playlist), api_config)

    # Step 5/6 – Render mix (dùng preloaded audio)
    output_wav = os.path.join(output_dir, "mix.wav")

    def render_progress(written, total):
        # render_mix gọi (written_samples, total_samples). Map → overall 58-88%.
        pct = (written / total) if total else 0.0
        progress_cb(5, "Rendering audio", 58.0 + pct * 30.0)

    # Duration là SOFT target: playlist đã dừng thêm bài ngay khi vượt target,
    # nên KHÔNG cắt cứng — để bài nhạc cuối cùng phát trọn vẹn (hard_cut=False).
    timestamps = render_mix(
        playlist=playlist,
        output_path=output_wav,
        crossfade_sec=crossfade_seconds,
        target_sr=sample_rate,
        bit_depth=bit_depth,
        progress_callback=render_progress,
        preloaded_audio=preloaded,    # ← key mới
    )

    for i, ts in enumerate(timestamps):
        ts["name"] = names[i] if i < len(names) else f"Track {i + 1}"

    # Step 6/6 – Write metadata
    progress_cb(6, "Writing metadata", 92.0)
    info = sf.info(output_wav)
    files = write_metadata(
        timestamps=timestamps,
        output_wav_path=output_wav,
        total_duration_sec=info.duration,
        sample_rate=sample_rate,
        bit_depth=bit_depth,
        crossfade_sec=crossfade_seconds,
        channels=info.channels,
    )
    _write_loudness_info(files, loudness)

    # Done
    progress_cb(6, "Completed", 100.0)

    return {
        "total_duration_seconds": info.duration,
        "track_count": len(playlist),
        "output_dir": output_dir,
        "sample_rate": sample_rate,
        "bit_depth": bit_depth,
    }


def run_patch_mix_job(
    mix_id: int,
    progress_cb,           # callback(step, step_name, percent, message="")
    sequence: list[str],   # đường dẫn file theo ĐÚNG thứ tự bản mix cũ
    names: list[str],      # tên từng đoạn của bản mix cũ (giữ nguyên)
    output_dir: str,
    crossfade_seconds: float,
    sample_rate: int,
    bit_depth: int,
    db_path: str,
    target_seconds: float,
    prev_loudness: dict | None = None,
) -> dict:
    """
    "Vá mix": render lại theo ĐÚNG playlist + tên của bản mix cũ, chỉ khác ở các
    bài đã được thay (sequence đã trỏ sang file mới). KHÔNG shuffle lại, KHÔNG
    đặt tên lại ⇒ mọi đoạn trước lần xuất hiện đầu tiên của bài thay giống hệt
    bản cũ; đoạn sau chỉ dịch đi đúng phần chênh độ dài.
    Cắt cứng tại target_seconds (= độ dài bản cũ, có fade-out) để video đã dựng
    vẫn khớp — chỉ cần thay track âm thanh trong final.mp4.
    """
    os.makedirs(output_dir, exist_ok=True)
    progress_cb(1, "Loading tracks", 5.0)
    seq = [str(Path(p)) for p in sequence]
    uniq = list(dict.fromkeys(seq))
    lib = {t.path: t for t in _load_ignore_minimum(uniq)}
    missing = [p for p in uniq if p not in lib]
    if missing:
        raise ValueError("Không đọc được: " + ", ".join(Path(p).name for p in missing))

    progress_cb(2, "Loading stems", 8.0)
    if prev_loudness and prev_loudness.get("balanced"):
        preloaded, loudness = _load_mastered_balanced(
            uniq, sample_rate, db_path, progress_cb, reuse=prev_loudness)
    else:
        # Bản cũ làm trước khi có master/cân bằng → xử lý ĐÚNG như cũ để đoạn
        # trước bài thay giống hệt (chỉ chuẩn -16 LUFS từng bài).
        preloaded, loudness = {}, {"balanced": False, "legacy": True}
        for i, p in enumerate(uniq):
            progress_cb(2, f"[{i+1}/{len(uniq)}] {Path(p).name}",
                        8.0 + (i / max(len(uniq), 1)) * 42.0)
            preloaded[p] = _load_track_audio_with_stems(p, sample_rate, db_path)

    output_wav = os.path.join(output_dir, "mix.wav")

    def render_progress(written, total):
        pct = (written / total) if total else 0.0
        progress_cb(5, "Rendering audio", 55.0 + min(pct, 1.0) * 33.0)

    timestamps = render_mix(
        playlist=[lib[p] for p in seq],
        output_path=output_wav,
        crossfade_sec=crossfade_seconds,
        target_sr=sample_rate,
        bit_depth=bit_depth,
        target_seconds=target_seconds,
        hard_cut=True,
        progress_callback=render_progress,
        preloaded_audio=preloaded,
    )
    for i, ts in enumerate(timestamps):
        ts["name"] = names[i] if i < len(names) and names[i] else f"Track {i + 1}"

    progress_cb(6, "Writing metadata", 92.0)
    info = sf.info(output_wav)
    files = write_metadata(
        timestamps=timestamps, output_wav_path=output_wav,
        total_duration_sec=info.duration, sample_rate=sample_rate,
        bit_depth=bit_depth, crossfade_sec=crossfade_seconds,
        channels=info.channels,
    )
    _write_loudness_info(files, loudness)
    progress_cb(6, "Completed", 100.0)
    return {
        "total_duration_seconds": info.duration,
        "track_count": len(timestamps),
        "output_dir": output_dir,
        "sample_rate": sample_rate,
        "bit_depth": bit_depth,
    }
