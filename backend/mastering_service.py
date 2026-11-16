"""
mastering_service.py — MASTER từng bài (ffmpeg) trước khi Mix.

Port chuỗi master của tool «Cover nhạc» (backend/cover/mastering.py) — 2026-11-17
người dùng yêu cầu: «trong quá trình mix, sử dụng công cụ master audio và cân
bằng lại volume giữa các bài để tạo sự đồng bộ như ở tool Cover nhạc».

Chuỗi lọc (giống Cover):
  stereo → highpass 30 Hz → EQ cắt đục 300 Hz → EQ cắt chói 3.5 kHz → high-shelf
  10 kHz → bass < 120 Hz về mono → compressor nhẹ → loudnorm 2 pass (tuyến tính,
  true-peak -1 dBTP).

Khác Cover:
  • Preset mặc định «dynamic» (-16 LUFS, nén 1.5:1) — hợp nhạc thiền/healing,
    trùng mức -16 LUFS tool vẫn dùng. Đổi trong data/master_config.json.
  • Bản master là BỘ ĐỆM: <thư mục bài>/mastered/<tên>_master.wav, giữ đúng
    sample rate gốc (không đổi 48 kHz → không resample 2 lần), WAV 24-bit.
    File gốc KHÔNG bao giờ bị sửa. Bản master cũ tự làm lại khi file gốc mới hơn
    hoặc đổi thông số (chữ ký cấu hình lưu trong <tên>_master.json).
  • Lỗi master (thiếu ffmpeg, file hỏng…) KHÔNG làm hỏng Mix — bài đó dùng file
    gốc như cũ (vẫn được cân bằng âm lượng).
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Callable

from backend.database import DATA_DIR

CONFIG_FILE = DATA_DIR / "master_config.json"
MASTER_DIR = "mastered"
SUFFIX = "_master"
LOG_NAME = "_master.log"

DEFAULT_CONFIG: dict = {
    "enabled": True,                 # master từng bài trước khi Mix
    "balance_enabled": True,         # cân bằng «đoạn to» giữa các bài + limiter
    "ffmpeg_path": "ffmpeg",         # "ffmpeg" = tìm trong PATH, hoặc đường dẫn đầy đủ
    "preset": "dynamic",             # natural | dynamic | loud
    "max_parallel": 3,
    "highpass_hz": 30,
    "eq_mud": {"freq_hz": 300, "width_q": 1.0, "gain_db": -1.5},
    "eq_harsh": {"freq_hz": 3500, "width_q": 1.5, "gain_db": -1.5},
    "high_shelf": {"freq_hz": 10000, "gain_db": -1.5},
    "mono_bass": {"enabled": True, "split_hz": 120},
    "compressor": {"threshold_db": -18, "attack_ms": 20, "release_ms": 200},
    "loudnorm": {"true_peak_db": -1.0, "lra": 11},
    "true_peak_margin_db": 0.3,
    "presets": {
        "natural": {"lufs": -14, "ratio": 2, "note": "Cân bằng, chuẩn YouTube"},
        "dynamic": {"lufs": -16, "ratio": 1.5, "note": "Giữ dynamic, ít nén (thiền/healing)"},
        "loud": {"lufs": -10, "ratio": 3, "note": "To hơn, TP vẫn -1"},
    },
}

# Khoá thông số ảnh hưởng tới âm thanh bản master → đổi là master lại.
_SIGNATURE_KEYS = ("highpass_hz", "eq_mud", "eq_harsh", "high_shelf", "mono_bass",
                   "compressor", "loudnorm", "true_peak_margin_db")

Log = Callable[[str], None]
_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


class MasterError(RuntimeError):
    pass


# ── config ────────────────────────────────────────────────────────────────
def _merge(base: dict, extra: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (extra or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def load_config(path: Path | None = None) -> dict:
    """Mặc định + ghi đè từ data/master_config.json (file hỏng → mặc định)."""
    path = path or CONFIG_FILE
    try:
        if path.is_file():
            return _merge(DEFAULT_CONFIG, json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError) as e:
        print(f"[master] master_config.json lỗi ({e}) — dùng mặc định.", flush=True)
    return copy.deepcopy(DEFAULT_CONFIG)


def ensure_config_file(path: Path | None = None) -> Path:
    """Tạo file cấu hình mặc định cho người dùng sửa (không ghi đè file đã có)."""
    path = path or CONFIG_FILE
    try:
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(DEFAULT_CONFIG, indent=2, ensure_ascii=False),
                            encoding="utf-8")
    except OSError:
        pass
    return path


def preset_values(cfg: dict, name: str | None = None) -> tuple[str, dict]:
    presets = cfg.get("presets") or DEFAULT_CONFIG["presets"]
    name = (name or cfg.get("preset") or "dynamic").strip().lower()
    if name not in presets:
        raise MasterError(f"Preset «{name}» không có — chọn: {', '.join(presets)}.")
    return name, presets[name]


def target_lufs(cfg: dict | None = None, fallback: float = -16.0) -> float:
    """Độ to mục tiêu của Mix = LUFS của preset đang chọn."""
    cfg = cfg or load_config()
    try:
        return float(preset_values(cfg)[1]["lufs"])
    except (MasterError, KeyError, TypeError, ValueError):
        return fallback


def signature(cfg: dict, preset_name: str, preset: dict) -> str:
    raw = json.dumps({"preset": preset_name, "lufs": preset.get("lufs"),
                      "ratio": preset.get("ratio"),
                      **{k: cfg.get(k) for k in _SIGNATURE_KEYS}}, sort_keys=True)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


# ── ffmpeg ────────────────────────────────────────────────────────────────
def _run_kwargs() -> dict:
    kw: dict = {"capture_output": True, "text": True, "encoding": "utf-8",
                "errors": "replace"}
    if os.name == "nt":
        kw["creationflags"] = subprocess.CREATE_NO_WINDOW
    return kw


def resolve_ffmpeg(cfg: dict) -> str:
    raw = str(cfg.get("ffmpeg_path") or "ffmpeg").strip().strip('"')
    if Path(raw).is_file():
        return str(Path(raw))
    found = shutil.which(raw)
    if not found:
        raise MasterError(f"Không tìm thấy ffmpeg («{raw}») — Mix dùng file gốc.")
    return found


def _num(v) -> str:
    return f"{float(v):g}"


def build_filter(cfg: dict, preset: dict, loudnorm: str) -> str:
    """filter_complex: [0:a] → … → [out]. ``loudnorm`` = tham số sau «loudnorm=»."""
    mud, harsh, shelf = cfg["eq_mud"], cfg["eq_harsh"], cfg["high_shelf"]
    comp = cfg["compressor"]
    pre = ",".join([
        "aformat=channel_layouts=stereo",
        f"highpass=f={_num(cfg['highpass_hz'])}",
        f"equalizer=f={_num(mud['freq_hz'])}:t=q:w={_num(mud['width_q'])}:g={_num(mud['gain_db'])}",
        f"equalizer=f={_num(harsh['freq_hz'])}:t=q:w={_num(harsh['width_q'])}"
        f":g={_num(harsh['gain_db'])}",
        f"highshelf=f={_num(shelf['freq_hz'])}:g={_num(shelf['gain_db'])}",
    ])
    post = (f"acompressor=threshold={_num(comp['threshold_db'])}dB:ratio={_num(preset['ratio'])}"
            f":attack={_num(comp['attack_ms'])}:release={_num(comp['release_ms'])}"
            f",loudnorm={loudnorm}")
    mono = cfg.get("mono_bass") or {}
    if not mono.get("enabled", True):
        return f"[0:a]{pre},{post}[out]"
    return (f"[0:a]{pre},acrossover=split={_num(mono.get('split_hz', 120))}[lo][hi];"
            "[lo]pan=stereo|c0=0.5*c0+0.5*c1|c1=0.5*c0+0.5*c1[lom];"
            f"[lom][hi]amix=inputs=2:normalize=0,{post}[out]")


def _loudnorm_args(cfg: dict, preset: dict, measured: dict | None = None,
                   tp_margin: float = 0.0) -> str:
    ln = cfg["loudnorm"]
    tp = float(ln["true_peak_db"]) - abs(float(tp_margin or 0))
    base = (f"I={_num(preset['lufs'])}:TP={_num(tp)}:LRA={_num(ln['lra'])}"
            ":print_format=json")
    if measured:
        base += (f":measured_I={measured['input_i']}:measured_TP={measured['input_tp']}"
                 f":measured_LRA={measured['input_lra']}"
                 f":measured_thresh={measured['input_thresh']}"
                 f":offset={measured['target_offset']}:linear=true")
    return base


def parse_loudnorm_json(stderr: str) -> dict:
    """Lấy khối JSON cuối cùng mà loudnorm in ra stderr."""
    blocks = re.findall(r"\{[^{}]*\"input_i\"[^{}]*\}", stderr or "", re.S)
    if not blocks:
        raise MasterError("ffmpeg không in số đo loudnorm (JSON).")
    data = json.loads(blocks[-1])
    for k in ("input_i", "input_tp", "input_lra", "input_thresh", "target_offset"):
        if not math.isfinite(float(data[k])):
            raise MasterError(f"file im lặng / không đo được loudness ({k}={data[k]}).")
    return data


def _ffmpeg(exe: str, args: list[str], timeout: float = 1800) -> subprocess.CompletedProcess:
    return subprocess.run([exe, "-hide_banner", "-nostdin", *args], timeout=timeout,
                          **_run_kwargs())


def _sample_rate(path: Path) -> int | None:
    try:
        import soundfile as sf
        return int(sf.info(str(path)).samplerate)
    except Exception:                                   # noqa: BLE001
        return None


# ── bộ đệm bản master ─────────────────────────────────────────────────────
def master_path(src: Path) -> Path:
    src = Path(src)
    return src.parent / MASTER_DIR / f"{src.stem}{SUFFIX}.wav"


def _info_path(src: Path) -> Path:
    src = Path(src)
    return src.parent / MASTER_DIR / f"{src.stem}{SUFFIX}.json"


def read_info(src: Path) -> dict:
    try:
        data = json.loads(_info_path(src).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_info(src: Path, data: dict) -> None:
    path = _info_path(src)
    try:
        path.parent.mkdir(exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)
    except OSError:
        pass


def _write_log(src: Path, text: str) -> None:
    try:
        path = Path(src).parent / MASTER_DIR / LOG_NAME
        path.parent.mkdir(exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {text}\n")
    except OSError:
        pass


def fresh_master(src: Path, cfg: dict | None = None) -> Path | None:
    """Bản master còn dùng được: có dữ liệu, KHÔNG cũ hơn file gốc, cùng chữ ký
    cấu hình. Không thì None (→ phải master lại)."""
    src = Path(src)
    dest = master_path(src)
    try:
        if not (dest.is_file() and dest.stat().st_size > 44):
            return None
        if dest.stat().st_mtime < src.stat().st_mtime:
            return None
    except OSError:
        return None
    cfg = cfg or load_config()
    try:
        name, preset = preset_values(cfg)
    except MasterError:
        return None
    if read_info(src).get("signature") != signature(cfg, name, preset):
        return None
    return dest


def _lock_for(src: Path) -> threading.Lock:
    key = os.path.normcase(str(Path(src).resolve()))
    with _locks_guard:
        return _locks.setdefault(key, threading.Lock())


# ── master 1 file ─────────────────────────────────────────────────────────
def master_file(src: Path, cfg: dict | None = None, force: bool = False) -> dict:
    """Master 1 file → {"status": done|cached|failed, "path", "before", "after", ...}.
    KHÔNG raise: lỗi → status failed + "error" (Mix dùng file gốc)."""
    src = Path(src)
    cfg = cfg or load_config()
    with _lock_for(src):
        return _master_file(src, cfg, force)


def _master_file(src: Path, cfg: dict, force: bool) -> dict:
    t0 = time.monotonic()
    out: dict = {"file": src.name, "status": "failed", "path": None}
    try:
        name, preset = preset_values(cfg)
        out["preset"] = name
        sig = signature(cfg, name, preset)
        if not force:
            cached = fresh_master(src, cfg)
            if cached is not None:
                info = read_info(src)
                out.update(status="cached", path=str(cached), before=info.get("before"),
                           after=info.get("after"))
                return out
        if not src.is_file():
            raise MasterError("không thấy file gốc.")
        exe = resolve_ffmpeg(cfg)
        sr = _sample_rate(src) or 48000

        # Pass 1: đo cả chuỗi (đã EQ/nén) để loudnorm pass 2 chỉnh tuyến tính chính xác.
        res = _ffmpeg(exe, ["-i", str(src), "-filter_complex",
                            build_filter(cfg, preset, _loudnorm_args(cfg, preset)),
                            "-map", "[out]", "-f", "null", "-"])
        if res.returncode != 0:
            raise MasterError(f"pass 1 lỗi:\n{res.stderr}")
        measured = parse_loudnorm_json(res.stderr)

        # Pass 2: ghi file tạm rồi đổi tên → không bao giờ để bản master dở.
        dest = master_path(src)
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_name(f".{dest.stem}.tmp.wav")
        tmp.unlink(missing_ok=True)
        margin = abs(float(cfg.get("true_peak_margin_db", 0.3)))
        graph = build_filter(cfg, preset, _loudnorm_args(cfg, preset, measured, margin))
        try:
            res = _ffmpeg(exe, ["-y", "-i", str(src), "-filter_complex", graph,
                                "-map", "[out]", "-ar", str(sr), "-c:a", "pcm_s24le",
                                "-f", "wav", str(tmp)])
            if res.returncode != 0 or not tmp.is_file() or tmp.stat().st_size <= 44:
                raise MasterError(f"pass 2 lỗi:\n{res.stderr}")
            loud = parse_loudnorm_json(res.stderr)
            tmp.replace(dest)
        finally:
            tmp.unlink(missing_ok=True)
        before = {"lufs": round(float(measured["input_i"]), 2),
                  "tp": round(float(measured["input_tp"]), 2)}
        after = {"lufs": round(float(loud["output_i"]), 2),
                 "tp": round(float(loud["output_tp"]), 2)}
        secs = round(time.monotonic() - t0, 1)
        out.update(status="done", path=str(dest), before=before, after=after)
        _write_info(src, {"preset": name, "signature": sig, "before": before,
                          "after": after, "seconds": secs, "sample_rate": sr,
                          "at": f"{datetime.now():%Y-%m-%d %H:%M}"})
        _write_log(src, f"✓ {src.name} | preset {name} ({preset['lufs']} LUFS) | "
                        f"LUFS {before['lufs']:.1f} → {after['lufs']:.1f} | "
                        f"TP {before['tp']:.1f} → {after['tp']:.1f} dBTP | {secs}s")
    except Exception as e:  # noqa: BLE001 — master lỗi KHÔNG được làm hỏng Mix
        detail = str(e)
        out["error"] = detail.splitlines()[0][:300] if detail else type(e).__name__
        if src.parent.is_dir():
            _write_log(src, f"LỖI {src.name}\n{detail[-2000:]}")
    out["seconds"] = round(time.monotonic() - t0, 1)
    return out


def master_many(paths: list[str], cfg: dict | None = None,
                progress: Callable[[int, int, dict], None] | None = None) -> dict[str, dict]:
    """Master song song (max_parallel) các file chưa có bản master hợp lệ.
    Trả {đường dẫn gốc: kết quả master_file}. progress(i_xong, tổng, kết_quả)."""
    cfg = cfg or load_config()
    uniq = list(dict.fromkeys(str(p) for p in paths))
    results: dict[str, dict] = {}
    if not uniq:
        return results
    workers = max(1, min(int(cfg.get("max_parallel", 3) or 1), len(uniq)))
    done = 0
    ex = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="master")
    try:
        futures = {ex.submit(master_file, Path(p), cfg): p for p in uniq}
        for fut in futures:
            p = futures[fut]
            res = fut.result()
            results[p] = res
            done += 1
            if progress:
                progress(done, len(uniq), res)   # có thể raise (huỷ job)
    finally:
        # Huỷ job giữa chừng → bỏ các bài chưa bắt đầu (bài đang chạy làm nốt).
        ex.shutdown(wait=True, cancel_futures=True)
    return results
