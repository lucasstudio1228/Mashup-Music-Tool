import { useState } from "react";
import {
  useVideoStatus, useGenImages, useGenClips, useAssemble, useRunAll,
  videoDownloadUrl,
} from "../../api/video";

function fmtDur(sec?: number | null): string {
  if (!sec || sec <= 0) return "—";
  const s = Math.round(sec);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const ss = s % 60;
  return h > 0
    ? `${h}h ${m}m ${ss}s`
    : `${m}m ${ss.toString().padStart(2, "0")}s`;
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="rounded-xl border border-white/5 bg-surface p-4">
      <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-gray-400">
        {title}
      </h2>
      {children}
    </div>
  );
}

function Stat({ label, value, ok }: { label: string; value: string; ok?: boolean }) {
  return (
    <div className="rounded-lg border border-white/5 bg-background px-3 py-2">
      <div className="text-[11px] uppercase tracking-wide text-gray-500">{label}</div>
      <div className={`text-sm font-semibold ${ok ? "text-green-400" : "text-gray-200"}`}>
        {value}
      </div>
    </div>
  );
}

export default function VideoPanel({ projectId }: { projectId: number }) {
  const { data: st, isLoading } = useVideoStatus(projectId);
  const genImages = useGenImages(projectId);
  const genClips  = useGenClips(projectId);
  const assemble  = useAssemble(projectId);
  const runAll    = useRunAll(projectId);
  const [idea, setIdea] = useState("");

  if (isLoading || !st) {
    return <p className="text-gray-400">Đang tải trạng thái video…</p>;
  }

  const p = st.params;
  const job = st.job;
  const busy = job?.status === "running" || job?.status === "pending";
  const imagesDone = st.image_count >= p.image_count;
  const clipsDone  = st.clip_count >= p.total_clips;
  const hasAudio   = !!st.audio_path;

  const btn = "rounded-lg px-4 py-2.5 text-sm font-medium transition-colors disabled:opacity-40 disabled:cursor-not-allowed";
  const primary = `${btn} bg-accent text-white hover:bg-accent-hover`;
  const ghost = `${btn} bg-surface-2 text-gray-200 hover:bg-white/10`;

  const kindLabel: Record<string, string> = {
    images: "Đang tạo ảnh", clips: "Đang tạo clip",
    assemble: "Đang ghép video", full: "Đang chạy toàn bộ",
  };

  return (
    <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
      {/* Cột trái: trạng thái + tiến độ */}
      <div className="space-y-4">
        <Section title="Trạng thái media">
          <div className="grid grid-cols-2 gap-2">
            <Stat label="Ảnh" value={`${st.image_count}/${p.image_count}`} ok={imagesDone} />
            <Stat label="Clip" value={`${st.clip_count}/${p.total_clips}`} ok={st.clip_count >= p.total_clips} />
            <Stat label="Nhạc (audio)" value={hasAudio ? fmtDur(st.audio_duration) : "Chưa có"} ok={hasAudio} />
            <Stat label="Video final" value={st.final_exists ? "Đã có" : "Chưa"} ok={st.final_exists} />
          </div>
          {hasAudio && (
            <p className="mt-2 text-xs text-gray-500">
              Clip 00 chỉ phát một lần ở đầu; clip 01–{p.total_clips - 1} chạy cycle random T+{p.t_window} để phủ hết {fmtDur(st.audio_duration)} nhạc.
            </p>
          )}
          {!hasAudio && (
            <p className="mt-2 text-xs text-yellow-300">
              ⚠️ Chưa có bản mix audio. Hãy render 1 bản ở tab <b>Audio</b> trước khi ghép video.
            </p>
          )}
        </Section>

        <Section title="Tiến độ">
          {job ? (
            <div className="space-y-2">
              <div className="flex justify-between text-xs text-gray-400">
                <span>
                  {busy ? (kindLabel[job.kind] ?? "Đang chạy")
                        : job.status === "completed" ? "Hoàn thành"
                        : job.status === "failed" ? "Lỗi" : "—"}
                </span>
                <span className="tabular-nums">{Math.round(job.percent)}%</span>
              </div>
              <div className="h-2 w-full overflow-hidden rounded bg-background">
                <div
                  className={`h-full transition-all ${
                    job.status === "failed" ? "bg-red-500" : "bg-accent"
                  }`}
                  style={{ width: `${Math.min(job.percent, 100)}%` }}
                />
              </div>
              {job.message && (
                <p className="text-xs text-gray-500 break-words">{job.message}</p>
              )}
              {job.status === "failed" && job.error && (
                <p className="rounded bg-red-950/30 p-2 text-xs text-red-400 break-words">
                  {job.error.slice(0, 300)}
                </p>
              )}
            </div>
          ) : (
            <p className="text-xs text-gray-500">Chưa chạy tác vụ nào.</p>
          )}
        </Section>

        {st.final_exists && (
          <Section title="Video hoàn chỉnh">
            <a
              href={videoDownloadUrl(projectId)}
              className={`${primary} inline-block`}
              download
            >
              ⬇ Tải video final.mp4
            </a>
          </Section>
        )}
      </div>

      {/* Cột phải: thao tác */}
      <div className="space-y-4">
        <Section title="Thông số">
          <div className="flex flex-wrap gap-2 text-xs">
            {[
              `Model: ${p.model}`,
              `Tỉ lệ ${p.aspect_ratio}`,
              `${p.clip_seconds}s/clip`,
              `${p.image_count} ảnh → ${p.total_clips} clip`,
              `Shuffle T+${p.t_window}`,
              `Fade ${p.blend_seconds}s · slow ${p.slow_speed}x`,
            ].map((t) => (
              <span key={t} className="rounded-full border border-white/10 bg-background px-2.5 py-1 text-gray-300">
                {t}
              </span>
            ))}
          </div>
        </Section>

        <Section title="Ý tưởng của bạn">
          <textarea
            value={idea}
            onChange={(e) => setIdea(e.target.value)}
            disabled={busy}
            rows={4}
            placeholder="Ví dụ: Một chú cáo nhỏ đi lang thang trong rừng mùa thu, dừng lại uống trà bên suối lúc hoàng hôn…"
            className="w-full resize-y rounded-lg border border-white/10 bg-background px-3 py-2 text-sm text-gray-200 placeholder:text-gray-600 focus:border-accent focus:outline-none disabled:opacity-50"
          />
          <p className="mt-2 text-xs text-gray-500">
            AI sẽ bám theo ý tưởng để viết prompt cho từng cảnh — mặc định tỉ lệ
            <b> 16:9</b>, phong cách <b>hoạt hình</b>, nhân vật <b>đồng bộ xuyên
            suốt</b> như một bộ phim ngắn chill/relaxing. Ảnh 0 là thumbnail có
            tiêu đề; các ảnh sau không chữ.
          </p>
          <p className="mt-1 text-xs text-gray-600">
            Để trống → dùng bộ prompt mặc định. Cần cấu hình API key ở
            ⚙️ Settings để AI viết prompt.
          </p>
        </Section>

        <Section title="Các bước">
          <div className="space-y-2">
            <button className={`${primary} w-full`} disabled={busy || !idea.trim()}
                    onClick={() => genImages.mutate({ idea: idea.trim(), mode: "restart" })}>
              ✨ Tạo {p.image_count} ảnh mới theo ý tưởng (AI viết prompt)
            </button>
            <button className={`${ghost} w-full`} disabled={busy}
                    onClick={() => genImages.mutate({ mode: "resume" })}>
              1️⃣ Tạo tiếp đến đủ {p.image_count} ảnh liên kết — ảnh 0 là thumbnail
            </button>
            <button className={`${ghost} w-full`} disabled={busy || !imagesDone}
                    onClick={() => genClips.mutate({ mode: "resume" })}>
              2️⃣ Tạo tiếp đến đủ {p.total_clips} clip 1:1 (Flow · {p.model})
            </button>
            <button className={`${ghost} w-full`} disabled={busy || !clipsDone || !hasAudio}
                    onClick={() => assemble.mutate(undefined)}>
              3️⃣ Ghép + gắn nhạc (clip 00 một lần · T+{p.t_window} · fade {p.blend_seconds}s)
            </button>
            <div className="pt-1">
              <button className={`${primary} w-full`} disabled={busy}
                      onClick={() => runAll.mutate(idea.trim() ? { idea: idea.trim() } : undefined)}>
                ▶ Chạy tất cả (ảnh → clip → ghép){idea.trim() ? " — theo ý tưởng" : ""}
              </button>
            </div>
          </div>
        </Section>

        <Section title="Lưu ý">
          <ul className="list-disc space-y-1 pl-4 text-xs text-gray-500">
            <li>Lần đầu, cửa sổ trình duyệt sẽ mở ra để bạn <b>tự đăng nhập Gemini & Flow</b> (session được lưu lại).</li>
            <li>Automation Gemini/Flow là best-effort — nếu báo lỗi "không tìm thấy selector", cập nhật trong <code className="text-gray-400">backend/video/config.py</code>.</li>
            <li>Chỉ chạy 1 tác vụ video tại một thời điểm.</li>
          </ul>
        </Section>
      </div>
    </div>
  );
}
