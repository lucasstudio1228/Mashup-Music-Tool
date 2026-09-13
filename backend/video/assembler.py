"""
video/assembler.py — Nối các clip (đã shuffle) thành 1 video dài, cắt đúng
duration audio và gắn nhạc mix vào. Xuất MP4 (H.264 + AAC) chuẩn YouTube.

Clip do Veo tạo có sẵn tiếng — ta BỎ tiếng clip, dùng nhạc từ bản mix audio.
"""
from __future__ import annotations
import json
import math
import subprocess
from pathlib import Path
from typing import Callable, Optional


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True)


def probe_duration(path: str) -> float:
    """Đọc duration (giây) bằng ffprobe."""
    r = _run([
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "json", path,
    ])
    if r.returncode != 0:
        raise RuntimeError(f"ffprobe lỗi: {r.stderr.strip()[:200]}")
    return float(json.loads(r.stdout)["format"]["duration"])


def _write_concat_list(sequence: list[str], list_path: Path) -> None:
    """File cho ffmpeg concat demuxer. Escape dấu ' trong path."""
    lines = []
    for p in sequence:
        safe = str(Path(p).resolve()).replace("'", r"'\''")
        lines.append(f"file '{safe}'")
    list_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _concat_video(list_path: Path, tmp_video: Path,
                  reencode: bool) -> None:
    """Nối clip (chỉ hình, bỏ tiếng). Thử -c copy trước, fallback re-encode."""
    base = ["ffmpeg", "-y", "-f", "concat", "-safe", "0",
            "-i", str(list_path), "-an"]
    if not reencode:
        r = _run(base + ["-c:v", "copy", str(tmp_video)])
        if r.returncode == 0:
            return
        # copy thất bại (clip không đồng nhất) → re-encode
    r = _run(base + [
        "-c:v", "libx264", "-preset", "medium", "-crf", "18",
        "-pix_fmt", "yuv420p", str(tmp_video),
    ])
    if r.returncode != 0:
        raise RuntimeError(f"ffmpeg concat lỗi: {r.stderr.strip()[-300:]}")


def _mux_audio(tmp_video: Path, audio_path: str, out_path: Path,
               target_seconds: float, reencode_video: bool) -> None:
    """Gắn nhạc mix; cắt theo duration audio; xuất MP4 cuối."""
    vcodec = ["-c:v", "libx264", "-preset", "medium", "-crf", "18",
              "-pix_fmt", "yuv420p"] if reencode_video else ["-c:v", "copy"]
    cmd = [
        "ffmpeg", "-y",
        "-i", str(tmp_video),
        "-i", str(audio_path),
        "-map", "0:v:0", "-map", "1:a:0",
        *vcodec,
        "-c:a", "aac", "-b:a", "320k",
        "-t", f"{target_seconds:.3f}",
        "-shortest", "-movflags", "+faststart",
        str(out_path),
    ]
    r = _run(cmd)
    if r.returncode != 0:
        raise RuntimeError(f"ffmpeg mux lỗi: {r.stderr.strip()[-300:]}")


def _mux_visual_list(list_path: Path, audio_path: str, out_path: Path,
                     target_seconds: float) -> None:
    """Mux concat-list hình với audio; danh sách dài nằm trong file, không ở CLI."""
    cmd = [
        "ffmpeg", "-y",
        "-f", "concat", "-safe", "0", "-i", str(list_path),
        "-i", str(audio_path),
        "-map", "0:v:0", "-map", "1:a:0",
        "-c:v", "copy",
        "-c:a", "aac", "-b:a", "320k",
        "-t", f"{target_seconds:.3f}",
        "-shortest", "-movflags", "+faststart",
        str(out_path),
    ]
    r = _run(cmd)
    if r.returncode != 0:
        raise RuntimeError(f"ffmpeg mux visual list lỗi: {r.stderr.strip()[-400:]}")


def assemble_video_blend(
    clip_sequence: list[str],
    audio_path: str,
    output_path: str,
    target_seconds: Optional[float] = None,
    blend_seconds: float = 1.0,
    slow_speed: float = 0.7,
    width: int = 1920,
    height: int = 1080,
    fps: int = 30,
    progress_cb: Optional[Callable[[str, float], None]] = None,
) -> dict:
    """
    Phát clip đầu đúng một lần làm intro. Các clip còn lại tạo thành visual
    cycle khép kín bằng `xfade` + làm chậm, rồi lặp đến đúng độ dài audio.

    - Mỗi clip: setpts=PTS/slow_speed (chậm lại) + chuẩn hoá về width×height@fps.
    - Nối bằng xfade fade dài `blend_seconds` giữa 2 clip liên tiếp → chuyển mượt.
    """
    if not clip_sequence:
        raise ValueError("clip_sequence rỗng.")
    if not Path(audio_path).exists():
        raise FileNotFoundError(f"Không thấy audio: {audio_path}")

    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    if target_seconds is None:
        target_seconds = probe_duration(audio_path)

    def _p(msg: str, pct: float):
        if progress_cb:
            progress_cb(msg, pct)

    n = len(clip_sequence)
    if n < 2:
        raise ValueError("Cần ít nhất 2 clip: 1 intro và 1 clip cho cycle.")
    D = max(0.1, float(blend_seconds))
    _p(f"Đo độ dài {n} clip", 10.0)
    intro_clip = clip_sequence[0]
    cycle_clips = clip_sequence[1:]
    intro_dur = probe_duration(intro_clip) / slow_speed
    cycle_durs = [probe_duration(c) / slow_speed for c in cycle_clips]

    norm = (f"setpts=PTS/{slow_speed},"
            f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,"
            f"fps={fps},format=yuv420p,settb=AVTB")
    # 1) Intro: clip_00 -> fade vào clip đầu cycle. Cắt đúng lúc clip cycle
    # đạt source-time D; visual cycle bên dưới cũng bắt đầu tại frame đó.
    intro_script = out.parent / "_intro_filter.txt"
    intro_video = out.parent / "_visual_intro.mp4"
    intro_parts = [f"[0:v]{norm}[v0]", f"[1:v]{norm}[v1]"]
    intro_parts.append(
        f"[v0][v1]xfade=transition=fade:duration={D}:"
        f"offset={max(0.0, intro_dur - D):.3f}[ix]")
    intro_parts.append(
        f"[ix]trim=start=0:end={intro_dur:.3f},setpts=PTS-STARTPTS[vout]")
    intro_script.write_text(";".join(intro_parts) + "\n", encoding="utf-8")
    _p("Tạo intro từ clip 00 (chỉ một lần)", 20.0)
    r = _run([
        "ffmpeg", "-y", "-i", intro_clip, "-i", cycle_clips[0],
        "-filter_complex_script", str(intro_script),
        "-map", "[vout]", "-an",
        "-c:v", "libx264", "-preset", "medium", "-crf", "18",
        "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(intro_video),
    ])
    if r.returncode != 0:
        raise RuntimeError(f"ffmpeg tạo intro lỗi: {r.stderr.strip()[-500:]}")

    # 2) Cycle chỉ gồm clip 01..N. Nối thêm clip đầu cycle ở cuối để fade
    # khép kín. clip_00 tuyệt đối không có mặt trong cycle được lặp.
    cycle_inputs = [*cycle_clips, cycle_clips[0]]
    cycle_input_durs = [*cycle_durs, cycle_durs[0]]
    inputs: list[str] = []
    for c in cycle_inputs:
        inputs += ["-i", c]
    m = len(cycle_clips)
    parts = [f"[{i}:v]{norm}[v{i}]" for i in range(m + 1)]
    prev = "v0"
    running = cycle_input_durs[0]
    for k in range(1, m + 1):
        offset = max(0.0, running - D)
        outlbl = f"x{k}"
        parts.append(
            f"[{prev}][v{k}]xfade=transition=fade:"
            f"duration={D}:offset={offset:.3f}[{outlbl}]")
        prev = outlbl
        running = running + cycle_input_durs[k] - D

    # Cắt từ D đến đúng điểm clip đầu vừa fade vào xong. Frame đầu/cuối cùng
    # thuộc cùng thời điểm của clip đầu, vì vậy khi loop không có hard cut.
    cycle_seconds = max(sum(cycle_durs) - m * D, 0.1)
    parts.append(
        f"[{prev}]trim=start={D:.3f}:end={D + cycle_seconds:.3f},"
        "setpts=PTS-STARTPTS[vout]")

    filter_script = out.parent / "_cycle_filter.txt"
    cycle_video = out.parent / "_visual_cycle.mp4"
    filter_script.write_text(";".join(parts) + "\n", encoding="utf-8")

    _p("Tạo visual cycle T+5 + fade (không có clip 00)", 45.0)
    cmd = [
        "ffmpeg", "-y",
        *inputs,
        "-filter_complex_script", str(filter_script),
        "-map", "[vout]", "-an",
        "-c:v", "libx264", "-preset", "medium", "-crf", "18",
        "-pix_fmt", "yuv420p",
        "-movflags", "+faststart",
        str(cycle_video),
    ]
    r = _run(cmd)
    if r.returncode != 0:
        raise RuntimeError(f"ffmpeg tạo visual cycle lỗi: {r.stderr.strip()[-500:]}")

    # 3) Danh sách hình: intro đúng một lần, sau đó chỉ lặp cycle 01..N.
    repeats = max(1, math.ceil(max(target_seconds - intro_dur, 0.0)
                               / cycle_seconds) + 1)
    visual_list = out.parent / "_visual_list.txt"
    _write_concat_list([str(intro_video)] + [str(cycle_video)] * repeats,
                       visual_list)
    _p("Lặp cycle 01..N + gắn nhạc", 75.0)
    _mux_visual_list(visual_list, audio_path, out, target_seconds)

    for temp in (intro_script, intro_video, filter_script, cycle_video,
                 visual_list):
        try:
            temp.unlink()
        except OSError:
            pass

    dur = probe_duration(str(out))
    _p("Hoàn thành", 100.0)
    return {"output": str(out), "duration": dur, "clip_count": n}


def assemble_video(
    clip_sequence: list[str],
    audio_path: str,
    output_path: str,
    target_seconds: Optional[float] = None,
    reencode_concat: bool = False,
    progress_cb: Optional[Callable[[str, float], None]] = None,
) -> dict:
    """
    clip_sequence : danh sách đường dẫn clip đã shuffle (đủ phủ audio).
    audio_path    : file nhạc (mix.wav).
    output_path   : file MP4 cuối.
    target_seconds: độ dài mong muốn (mặc định = duration audio).

    Return: {output, duration, clip_count}
    """
    if not clip_sequence:
        raise ValueError("clip_sequence rỗng.")
    if not Path(audio_path).exists():
        raise FileNotFoundError(f"Không thấy audio: {audio_path}")

    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    if target_seconds is None:
        target_seconds = probe_duration(audio_path)

    def _p(msg: str, pct: float):
        if progress_cb:
            progress_cb(msg, pct)

    work = out.parent
    list_path = work / "_concat_list.txt"
    tmp_video = work / "_concat_video.mp4"

    _p("Chuẩn bị danh sách clip", 10.0)
    _write_concat_list(clip_sequence, list_path)

    _p("Nối clip (ffmpeg)", 40.0)
    _concat_video(list_path, tmp_video, reencode=reencode_concat)

    _p("Gắn nhạc & xuất MP4", 80.0)
    # Nếu concat đã copy được thì video giữ codec clip; mux copy video luôn.
    _mux_audio(tmp_video, audio_path, out, target_seconds,
               reencode_video=False)

    _p("Dọn tạm", 95.0)
    for f in (list_path, tmp_video):
        try:
            f.unlink()
        except OSError:
            pass

    dur = probe_duration(str(out))
    _p("Hoàn thành", 100.0)
    return {
        "output": str(out),
        "duration": dur,
        "clip_count": len(clip_sequence),
    }
