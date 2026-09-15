import { useMixes, useCancelMix } from '../../api/mixes';
import { useVideoStatus, useCancelVideo } from '../../api/video';
import { useWakeLock } from '../../hooks/useWakeLock';

const VIDEO_KIND_LABEL: Record<string, string> = {
  images: 'Đang tạo ảnh',
  clips: 'Đang tạo clip',
  assemble: 'Đang ghép video',
  full: 'Đang dựng toàn bộ video',
  rebuild: 'Đang dựng lại video từ ảnh',
};

/**
 * Thanh trạng thái toàn cục (cố định đáy màn hình): hiển thị quy trình ĐANG
 * CHẠY (render nhạc HOẶC dựng video) kèm hoạt động + % hoàn thành + nút Huỷ.
 * Đồng thời giữ màn hình sáng (chống khoá) trong lúc có job chạy.
 * Không có job → không hiển thị gì.
 */
export default function WorkflowStatusBar({
  projectId,
  onReopenMix,
}: {
  projectId: number;
  /** Mở lại modal chi tiết của mix đang chạy (nếu người dùng đã ẩn nó). */
  onReopenMix?: (mixId: number) => void;
}) {
  const { data: videoSt } = useVideoStatus(projectId);
  const { data: mixes } = useMixes(projectId);
  const cancelVideo = useCancelVideo(projectId);
  const cancelMix = useCancelMix(projectId);

  const vjob = videoSt?.job;
  const videoActive = vjob?.status === 'running' || vjob?.status === 'pending';

  const activeMix = mixes?.find(
    (m) => m.status === 'running' || m.status === 'pending',
  );
  const mixActive = Boolean(activeMix);

  const active = videoActive || mixActive;
  useWakeLock(active);

  if (!active) return null;

  // Ưu tiên hiển thị job video nếu cả hai cùng chạy (hiếm); nếu không thì mix.
  const isVideo = videoActive;
  const label = isVideo
    ? (VIDEO_KIND_LABEL[vjob!.kind] ?? 'Đang chạy video')
    : 'Đang render nhạc';
  const percent = isVideo
    ? vjob!.percent
    : (activeMix!.progress_percent ?? 0);
  const message = isVideo
    ? (vjob!.message || '')
    : (activeMix!.progress_step_name
        ? `Bước ${activeMix!.progress_step ?? 0}/6 · ${activeMix!.progress_step_name}`
        : '');
  const cancelling = isVideo ? cancelVideo.isPending : cancelMix.isPending;

  const onCancel = () => {
    if (!confirm('Huỷ quy trình đang chạy? Phần đã tạo sẽ được giữ lại.')) return;
    if (isVideo) cancelVideo.mutate();
    else if (activeMix) cancelMix.mutate(activeMix.id);
  };

  return (
    <div className="fixed bottom-0 left-0 right-0 z-40 border-t border-white/10 bg-surface/95 px-4 py-2.5 backdrop-blur">
      <div className="mx-auto flex max-w-5xl items-center gap-4">
        <span className="shrink-0 animate-pulse text-lg" aria-hidden>
          {isVideo ? '🎬' : '🎵'}
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex items-center justify-between gap-3 text-xs">
            <span className="truncate font-medium text-gray-200">
              {label}
              {message && (
                <span className="ml-2 font-normal text-gray-500">— {message}</span>
              )}
            </span>
            <span className="shrink-0 tabular-nums text-gray-400">
              {Math.round(percent)}%
            </span>
          </div>
          <div className="mt-1.5 h-1.5 w-full overflow-hidden rounded-full bg-background">
            <div
              className="h-full bg-accent transition-all"
              style={{ width: `${Math.min(Math.max(percent, 2), 100)}%` }}
            />
          </div>
        </div>
        {!isVideo && activeMix && onReopenMix && (
          <button
            onClick={() => onReopenMix(activeMix.id)}
            className="shrink-0 rounded-lg border border-white/15 bg-white/5 px-3 py-1.5 text-xs font-medium text-gray-200 transition-colors hover:bg-white/10"
            title="Mở lại bảng chi tiết"
          >
            ⤢ Mở
          </button>
        )}
        <button
          onClick={onCancel}
          disabled={cancelling}
          className="shrink-0 rounded-lg border border-red-500/40 bg-red-950/30 px-3 py-1.5 text-xs font-medium text-red-300 transition-colors hover:bg-red-950/60 disabled:opacity-50"
        >
          {cancelling ? 'Đang huỷ…' : '✕ Huỷ'}
        </button>
      </div>
    </div>
  );
}
