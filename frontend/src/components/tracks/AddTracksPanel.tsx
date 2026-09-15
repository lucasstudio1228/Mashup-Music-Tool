import { useRef, useState } from 'react';
import {
  useScanFolder,
  useUploadTrack,
  useDeleteAllTracks,
} from '../../api/tracks';
import type { ScanResult, Track } from '../../types';

interface Props {
  projectId: number;
  onGoToVideo?: () => void;   // chuyển sang tab Video sau khi upload
}

export default function AddTracksPanel({ projectId, onGoToVideo }: Props) {
  const [folder, setFolder] = useState('');
  const [result, setResult] = useState<ScanResult | null>(null);
  const [uploaded, setUploaded] = useState<Track | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const scan = useScanFolder(projectId);
  const upload = useUploadTrack(projectId);
  const deleteAll = useDeleteAllTracks(projectId);

  function runScan() {
    if (!folder.trim()) return;
    scan.mutate(folder.trim(), { onSuccess: (r) => setResult(r) });
  }

  function onPickFile(e: React.ChangeEvent<HTMLInputElement>) {
    const f = e.target.files?.[0];
    e.target.value = ''; // cho phép chọn lại cùng 1 file
    if (!f) return;
    setUploaded(null);
    upload.mutate(f, { onSuccess: (t) => setUploaded(t) });
  }

  const inputCls =
    'flex-1 rounded-lg border border-white/10 bg-background px-3 py-2 text-sm text-white outline-none focus:border-accent';

  return (
    <div className="space-y-2">
      {/* Quét cả thư mục (nhiều track để mix) */}
      <p className="text-xs text-gray-400">Scan folder (nhiều track để mix)</p>
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
            <span className="text-red-400">, {result.skipped_errors} errors</span>
          )}
          {result.warning_lossy && (
            <div className="mt-1 text-yellow-300">
              ⚠️ Contains MP3 (lossy) sources.
            </div>
          )}
        </div>
      )}

      {/* Upload 1 track dài từ máy → dùng trực tiếp làm nhạc nền video */}
      <div className="border-t border-white/5 pt-3">
        <p className="text-xs text-gray-400">
          Upload track dài → dùng trực tiếp làm nhạc nền video (không cần mix)
        </p>
        <div className="flex items-center gap-2 mt-1">
          <input
            ref={fileInputRef}
            type="file"
            accept="audio/*,.wav,.flac,.mp3,.m4a,.aac,.ogg,.opus,.wma"
            onChange={onPickFile}
            className="hidden"
          />
          <button
            onClick={() => fileInputRef.current?.click()}
            disabled={upload.isPending}
            className="rounded-lg bg-accent px-4 py-2 text-sm font-medium text-white hover:bg-accent-hover disabled:opacity-50"
          >
            {upload.isPending ? '⏳ Uploading…' : '⬆️ Upload track'}
          </button>
          {upload.isPending && (
            <span className="text-xs text-gray-400">
              File dài có thể mất một lúc…
            </span>
          )}
        </div>

        {upload.isError && (
          <p className="text-xs text-red-400 mt-1">
            {(upload.error as any)?.response?.data?.detail ?? 'Upload failed'}
          </p>
        )}

        {uploaded && (
          <div className="rounded-lg bg-green-500/10 border border-green-500/30 p-2 mt-2 text-xs">
            <div className="text-green-300">
              ✅ Uploaded: {uploaded.filename}
              <span className="text-green-400/70">
                {' '}
                ({Math.round(uploaded.duration_seconds / 60)} min
                {uploaded.is_lossy ? ' · lossy' : ''})
              </span>
            </div>
            <div className="text-gray-400 mt-1">
              Sẽ được dùng làm nhạc nền khi tạo video.
            </div>
            {onGoToVideo && (
              <button
                onClick={onGoToVideo}
                className="mt-2 rounded-lg bg-accent px-3 py-1.5 text-xs font-medium text-white hover:bg-accent-hover"
              >
                🎬 Sang phần Video để tạo video →
              </button>
            )}
          </div>
        )}
      </div>

      <button
        onClick={() => deleteAll.mutate()}
        disabled={deleteAll.isPending}
        className="text-xs text-gray-400 hover:text-red-400 pt-1"
      >
        Delete all tracks
      </button>
    </div>
  );
}
