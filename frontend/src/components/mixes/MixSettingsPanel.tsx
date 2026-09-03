import { useState } from 'react';
import type { MixCreate } from '../../types';

interface Props {
  trackCount: number;
  disabled?: boolean;
  onStart: (settings: MixCreate) => void;
}

const SAMPLE_RATES = [48000, 88200, 96000, 176400, 192000];
const MIN_TRACKS = 15;

export default function MixSettingsPanel({ trackCount, disabled, onStart }: Props) {
  const [duration, setDuration] = useState(60);
  const [crossfade, setCrossfade] = useState(15);
  const [sampleRate, setSampleRate] = useState(96000);
  const [bitDepth, setBitDepth] = useState<24 | 32>(24);

  const enoughTracks = trackCount >= MIN_TRACKS;

  const inputCls =
    'w-24 rounded-lg border border-white/10 bg-background px-2 py-1 text-sm text-white outline-none focus:border-accent';

  return (
    <div className="space-y-3">
      <Row label="Duration">
        <input
          type="number"
          min={1}
          className={inputCls}
          value={duration}
          onChange={(e) => setDuration(Number(e.target.value))}
        />
        <span className="text-xs text-gray-400">min</span>
      </Row>

      <Row label="Crossfade">
        <input
          type="number"
          min={1}
          className={inputCls}
          value={crossfade}
          onChange={(e) => setCrossfade(Number(e.target.value))}
        />
        <span className="text-xs text-gray-400">sec</span>
      </Row>

      <Row label="Sample Rate">
        <select
          className={inputCls + ' w-32'}
          value={sampleRate}
          onChange={(e) => setSampleRate(Number(e.target.value))}
        >
          {SAMPLE_RATES.map((sr) => (
            <option key={sr} value={sr}>
              {sr} Hz
            </option>
          ))}
        </select>
      </Row>

      <Row label="Bit Depth">
        <div className="flex gap-3 text-sm text-gray-200">
          {[24, 32].map((bd) => (
            <label key={bd} className="flex items-center gap-1">
              <input
                type="radio"
                checked={bitDepth === bd}
                onChange={() => setBitDepth(bd as 24 | 32)}
              />
              {bd === 32 ? '32 (float)' : '24'}
            </label>
          ))}
        </div>
      </Row>

      {!enoughTracks && (
        <div className="rounded-lg bg-yellow-500/10 px-3 py-2 text-xs text-yellow-300">
          ⚠️ Minimum {MIN_TRACKS} tracks needed ({trackCount} now).
        </div>
      )}

      <button
        disabled={disabled || !enoughTracks}
        onClick={() =>
          onStart({
            duration_minutes: duration,
            crossfade_seconds: crossfade,
            sample_rate: sampleRate,
            bit_depth: bitDepth,
          })
        }
        className="w-full rounded-lg bg-accent px-4 py-2.5 text-sm font-medium text-white hover:bg-accent-hover disabled:opacity-50"
      >
        ▶ Start Mix
      </button>
    </div>
  );
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex items-center justify-between">
      <span className="text-sm text-gray-300">{label}</span>
      <div className="flex items-center gap-2">{children}</div>
    </div>
  );
}
