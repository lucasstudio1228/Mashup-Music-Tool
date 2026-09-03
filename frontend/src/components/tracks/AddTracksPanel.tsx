import { useState } from 'react';
import { useScanFolder, useDeleteAllTracks } from '../../api/tracks';
import type { ScanResult } from '../../types';

export default function AddTracksPanel({ projectId }: { projectId: number }) {
  const [folder, setFolder] = useState('');
  const [result, setResult] = useState<ScanResult | null>(null);
  const scan = useScanFolder(projectId);
  const deleteAll = useDeleteAllTracks(projectId);

  function runScan() {
    if (!folder.trim()) return;
    scan.mutate(folder.trim(), {
      onSuccess: (r) => setResult(r),
    });
  }

  const inputCls =
    'flex-1 rounded-lg border border-white/10 bg-background px-3 py-2 text-sm text-white outline-none focus:border-accent';

  return (
    <div className="space-y-2">
      <div className="flex gap-2">
        <input
          className={inputCls}
          value={folder}
          onChange={(e) => setFolder(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && runScan()}
          placeholder="D:/Music/Meditation"
        />
        <button
          onClick={runScan}
          disabled={scan.isPending || !folder.trim()}
          className="rounded-lg bg-accent px-4 py-2 text-sm font-medium text-white hover:bg-accent-hover disabled:opacity-50"
        >
          {scan.isPending ? 'Scanning…' : 'Scan'}
        </button>
      </div>

      <button
        onClick={() => deleteAll.mutate()}
        disabled={deleteAll.isPending}
        className="text-xs text-gray-400 hover:text-red-400"
      >
        Delete all tracks
      </button>

      {scan.isError && (
        <p className="text-xs text-red-400">
          {(scan.error as any)?.response?.data?.detail ?? 'Scan failed'}
        </p>
      )}

      {result && (
        <div className="rounded-lg bg-background/50 p-2 text-xs text-gray-300">
          <span className="text-green-400">{result.added} added</span>
          {result.skipped_duplicates > 0 && (
            <span>, {result.skipped_duplicates} duplicates</span>
          )}
          {result.skipped_errors > 0 && (
            <span className="text-red-400">
              , {result.skipped_errors} errors
            </span>
          )}
          {result.warning_lossy && (
            <div className="mt-1 text-yellow-300">
              ⚠️ Contains MP3 (lossy) sources.
            </div>
          )}
        </div>
      )}
    </div>
  );
}
