"""MeditationMixer – ghép nhạc thiền thành 1 file WAV lossless."""
import sys
from pathlib import Path

import click
import soundfile as sf
from rich.console import Console
from rich.progress import (BarColumn, Progress, TextColumn, TimeRemainingColumn)

import console as _console  # noqa: F401 - bật UTF-8 output khi import
from api_config import APIConfig
from audio_loader import determine_target_sample_rate, load_library
from crossfade_engine import render_mix
from exporter import check_disk_space, format_duration, wav_size_on_disk
from metadata_writer import write_metadata
from playlist_generator import generate_playlist
from track_namer import generate_names_with_source

MIN_SAMPLE_RATE = 48000
MIN_TRACKS = 15
TOTAL_STEPS = 6


def _step(ui: Console, index: int, label: str) -> None:
    ui.print(f"[bold cyan][{index}/{TOTAL_STEPS}][/] {label:<28}", end="")


def _done(ui: Console, detail: str) -> None:
    ui.print(f"{detail} ✅")


def _usable_tracks(tracks, crossfade_sec, ui):
    """Track ngắn hơn crossfade + 5s không đủ chỗ để fade → loại khỏi library."""
    minimum = crossfade_sec + 5.0
    usable = [t for t in tracks if t.duration_seconds >= minimum]
    for track in tracks:
        if track not in usable:
            ui.print(f"  [yellow]⚠️  Bỏ qua '{track.name}' "
                     f"({track.duration_seconds:.0f}s < {minimum:.0f}s cần cho "
                     f"crossfade {crossfade_sec:.0f}s)[/]")
    if len(usable) < MIN_TRACKS:
        raise ValueError(
            f"Chỉ còn {len(usable)} track đủ dài cho crossfade {crossfade_sec:.0f}s "
            f"(cần {MIN_TRACKS}). Giảm --crossfade hoặc thêm track dài hơn."
        )
    return usable


@click.command()
@click.option('--input', 'input_path', required=True,
              help='Folder chứa nhạc (.wav/.flac/.mp3)')
@click.option('--duration', required=True, type=float, help='Phút')
@click.option('--output', required=True, help='Đường dẫn file WAV output')
@click.option('--crossfade', default=15, type=float, help='Giây, default 15')
@click.option('--sample-rate', default=0, type=int,
              help='0 = auto (max của inputs, sàn 48000)')
@click.option('--bit-depth', default='24', type=click.Choice(['24', '32']),
              help='24 = PCM_24, 32 = float')
@click.option('--api-key', default=None, envvar='OPENAI_API_KEY')
@click.option('--api-base-url', default=None, envvar='OPENAI_BASE_URL')
@click.option('--api-model', default=None, envvar='OPENAI_MODEL')
def main(input_path, duration, output, crossfade, sample_rate, bit_depth,
         api_key, api_base_url, api_model):
    ui = Console()
    bit_depth = int(bit_depth)
    target_seconds = duration * 60.0

    ui.print("\n[bold]🎵 MeditationMixer[/]")
    ui.print("─" * 42)

    if duration <= 0:
        ui.print("[red]❌ --duration phải > 0[/]")
        sys.exit(1)
    if crossfade <= 0:
        ui.print("[red]❌ --crossfade phải > 0[/]")
        sys.exit(1)

    try:
        # [1/6] Load
        _step(ui, 1, "Loading files...")
        tracks = load_library(input_path)
        _done(ui, f"{len(tracks)}/{len(tracks)}")

        # [2/6] Validate + chốt chất lượng output
        _step(ui, 2, "Analyzing audio...")
        tracks = _usable_tracks(tracks, crossfade, ui)
        if sample_rate <= 0:
            target_sr = determine_target_sample_rate(tracks)
        else:
            target_sr = sample_rate
            if target_sr < MIN_SAMPLE_RATE:
                ui.print(f"  [yellow]⚠️  --sample-rate {target_sr} thấp hơn sàn "
                         f"{MIN_SAMPLE_RATE}, dùng {MIN_SAMPLE_RATE}[/]")
                target_sr = MIN_SAMPLE_RATE
        _done(ui, f"{len(tracks)} tracks")

        # [3/6] Playlist
        _step(ui, 3, "Generating playlist...")
        playlist = generate_playlist(tracks, target_seconds, crossfade)
        _done(ui, f"{len(playlist)} tracks / {int(target_seconds // 60)} min")

        # [4/6] Đặt tên
        _step(ui, 4, "Naming tracks...")
        config = APIConfig.from_cli(api_key, api_base_url, api_model)
        names, source = generate_names_with_source(len(playlist), config)
        _done(ui, f"{len(names)} names ({source})")

        # [5/6] Render
        out_path = Path(output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        check_disk_space(str(out_path), target_seconds, target_sr, 2, bit_depth)

        ui.print(f"[bold cyan][5/{TOTAL_STEPS}][/] Rendering mix...")
        with Progress(TextColumn("      "), BarColumn(bar_width=30),
                      TextColumn("{task.percentage:>3.0f}%"),
                      TimeRemainingColumn(), console=ui) as progress:
            task = progress.add_task("mix", total=1000)

            def on_progress(written, total):
                if total:
                    progress.update(task, completed=min(written / total * 1000, 1000))

            timestamps = render_mix(
                playlist, str(out_path), crossfade_sec=crossfade,
                target_sr=target_sr, bit_depth=bit_depth,
                target_seconds=target_seconds, progress_callback=on_progress,
            )
            progress.update(task, completed=1000)

        for stamp, name in zip(timestamps, names):
            stamp["name"] = name

        # [6/6] Metadata
        _step(ui, 6, "Writing metadata...")
        info = sf.info(str(out_path))
        files = write_metadata(
            timestamps=timestamps, output_wav_path=str(out_path),
            total_duration_sec=info.duration, sample_rate=target_sr,
            bit_depth=bit_depth, crossfade_sec=crossfade, channels=info.channels,
        )
        _done(ui, "")

        depth_label = "32-bit float" if bit_depth == 32 else "24-bit"
        ui.print("\n[bold]Output files:[/]")
        ui.print(f"  {out_path}  {wav_size_on_disk(str(out_path))}  "
                 f"({target_sr} Hz / {depth_label} / Stereo)")
        for key in ("cue", "tracklist", "json"):
            ui.print(f"  {files[key]}")
        ui.print(f"\n[bold green]✅ Done — {format_duration(info.duration)}[/]\n")

    except (ValueError, RuntimeError, OSError) as exc:
        ui.print(f"\n[red]❌ {exc}[/]")
        sys.exit(1)


if __name__ == "__main__":
    main()
