import { useEffect, useRef, useState } from "react";
import { useOpenFinal, videoDownloadUrl, videoFinalUrl } from "../../api/video";
import { apiErrorText } from "../../api/youtube";

/** Trình phát final.mp4 ngay trong tool — mở là tự phát. Esc / bấm nền để đóng. */
export function FinalVideoPlayer({ projectId, title, bust, onClose }: {
  projectId: number; title?: string; bust?: string | number | null; onClose: () => void;
}) {
  const ref = useRef<HTMLVideoElement>(null);
  const openFinal = useOpenFinal(projectId);
  const [err, setErr] = useState("");

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  // Một số trình duyệt chặn tự phát có tiếng → thử phát; bị chặn thì phát tắt tiếng.
  useEffect(() => {
    const v = ref.current;
    if (!v) return;
    v.play().catch(() => { v.muted = true; v.play().catch(() => {}); });
  }, []);

  function openExternal() {
    ref.current?.pause();
    openFinal.mutate(undefined, { onError: (e) => setErr(apiErrorText(e)) });
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/80 p-4"
      onClick={onClose}
    >
      <div
        className="w-full max-w-5xl space-y-3 rounded-xl border border-white/10 bg-surface p-4"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between gap-3">
          <h3 className="truncate text-sm font-semibold text-white">
            ▶ Video final{title ? ` — ${title}` : ""}
          </h3>
          <button
            onClick={onClose}
            className="rounded-lg px-2 py-1 text-sm text-gray-400 hover:bg-white/10 hover:text-white"
            title="Đóng (Esc)"
          >
            ✕
          </button>
        </div>
        <video
          ref={ref}
          src={videoFinalUrl(projectId, bust ?? undefined)}
          controls
          autoPlay
          playsInline
          className="max-h-[75vh] w-full rounded-lg bg-black"
          onError={() => setErr("Không phát được final.mp4 trong tool — thử «Mở bằng trình phát của máy».")}
        />
        <div className="flex flex-wrap items-center gap-2">
          <button
            onClick={openExternal}
            disabled={openFinal.isPending}
            className="rounded-lg border border-white/15 px-3 py-1.5 text-xs font-medium text-gray-200 hover:bg-white/5 disabled:opacity-40"
          >
            🖥️ Mở bằng trình phát của máy
          </button>
          <a
            href={videoDownloadUrl(projectId)}
            download
            className="rounded-lg border border-white/15 px-3 py-1.5 text-xs font-medium text-gray-200 hover:bg-white/5"
          >
            ⬇ Tải final.mp4
          </a>
          {openFinal.isSuccess && !err && (
            <span className="text-xs text-green-400">✓ Đã mở bằng trình phát của máy</span>
          )}
          {err && <span className="text-xs text-red-400">❌ {err}</span>}
        </div>
      </div>
    </div>
  );
}
