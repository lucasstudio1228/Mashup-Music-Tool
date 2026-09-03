"""T+3 shuffle: chọn track sao cho không lặp trong vòng 3 vị trí."""
import random

from audio_loader import Track

RECENT_WINDOW_SIZE = 3
MIN_EFFECTIVE_SECONDS = 1.0


def effective_duration(track: Track, crossfade_sec: float) -> float:
    """Thời gian track thực sự đóng góp vào mix (2 track overlap crossfade_sec)."""
    return max(track.duration_seconds - crossfade_sec, MIN_EFFECTIVE_SECONDS)


def _pick(available: list[Track], remaining: float, crossfade_sec: float) -> Track:
    """
    Bình thường: random thuần.
    Khi remaining đủ nhỏ để một track duy nhất kết thúc mix: chọn track khít
    nhất (effective >= remaining, nhỏ nhất) để mix không vượt target quá xa.
    """
    fitting = [t for t in available
               if effective_duration(t, crossfade_sec) >= remaining]
    if not fitting or len(fitting) == len(available):
        return random.choice(available)
    best = min(effective_duration(t, crossfade_sec) for t in fitting)
    closest = [t for t in fitting
               if effective_duration(t, crossfade_sec) == best]
    return random.choice(closest)


def generate_playlist(
    tracks: list[Track],
    target_seconds: float,
    crossfade_sec: float,
) -> list[Track]:
    """
    T+3 rule: track X không được xuất hiện lại cho đến khi
    ít nhất 3 track khác đã được chọn SAU nó.
    Chạy cho đến khi tích lũy đủ target_seconds.
    """
    playlist: list[Track] = []
    # recent_window: tối đa 3 phần tử, track trong đây không được chọn
    recent_window: list[Track] = []
    accumulated = 0.0

    while accumulated < target_seconds:
        available = [t for t in tracks if t not in recent_window]
        if not available:          # safety (không xảy ra với 15+ tracks)
            available = list(tracks)

        chosen = _pick(available, target_seconds - accumulated, crossfade_sec)
        playlist.append(chosen)

        recent_window.append(chosen)
        if len(recent_window) > RECENT_WINDOW_SIZE:
            recent_window.pop(0)

        # Thời gian hiệu dụng: trừ crossfade vì 2 track overlap
        accumulated += effective_duration(chosen, crossfade_sec)

    return playlist
