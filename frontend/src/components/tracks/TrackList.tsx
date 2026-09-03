import type { Track } from '../../types';

interface Props {
  tracks: Track[];
  onDelete: (trackId: number) => void;
}

function fmtDuration(sec: number): string {
  const m = Math.floor(sec / 60);
  const s = Math.round(sec % 60);
  return `${m}:${s.toString().padStart(2, '0')}`;
}

const MIN_TRACKS = 15;

export default function TrackList({ tracks, onDelete }: Props) {
  const needed = MIN_TRACKS - tracks.length;
  return (
    <div>
      {needed > 0 && (
        <div className="mb-3 rounded-lg bg-yellow-500/10 px-3 py-2 text-xs text-yellow-300">
          ⚠️ Need at least {needed} more track{needed > 1 ? 's' : ''} (minimum{' '}
          {MIN_TRACKS}).
        </div>
      )}
      {tracks.length === 0 ? (
        <p className="text-sm text-gray-500">No tracks yet. Scan a folder below.</p>
      ) : (
        <ul className="space-y-1">
          {tracks.map((t) => (
            <li
              key={t.id}
              className="group flex items-center gap-3 rounded-lg px-2 py-1.5 text-sm hover:bg-surface-2"
            >
              <span title={t.is_lossy ? 'Lossy (MP3)' : t.format}>
                {t.is_lossy ? '⚠️' : '✅'}
              </span>
              <span className="flex-1 truncate text-gray-200" title={t.filename}>
                {t.filename}
              </span>
              <span className="text-xs text-gray-500">
                {(t.sample_rate / 1000).toFixed(1)}k
              </span>
              <span className="w-12 text-right text-gray-400">
                {fmtDuration(t.duration_seconds)}
              </span>
              <button
                onClick={() => onDelete(t.id)}
                className="opacity-0 transition group-hover:opacity-100"
                title="Remove track"
              >
                ✕
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
