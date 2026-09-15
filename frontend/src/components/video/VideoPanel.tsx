import { useEffect, useState } from "react";
import {
  useVideoStatus, useGenImages, useGenClips, useAssemble, useRunAll, useRebuild,
  videoDownloadUrl, videoThumbnailUrl,
} from "../../api/video";
import { useProject, useUpdateProject } from "../../api/projects";

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
  const { data: project } = useProject(projectId);
  const updateProject = useUpdateProject(projectId);
  const genImages = useGenImages(projectId);
  const genClips  = useGenClips(projectId);
  const assemble  = useAssemble(projectId);
  const runAll    = useRunAll(projectId);
  const rebuild   = useRebuild(projectId);
  const [idea, setIdea] = useState("");
  // Nạp ý tưởng đã lưu của project vào ô nhập (khi tải xong / đổi project).
  const savedIdea = project?.video_idea ?? "";
  useEffect(() => { setIdea(savedIdea); }, [savedIdea, projectId]);
  const autoVideo = project?.auto_video ?? false;
  const videoStyle = project?.video_style ?? "2d";
  const ideaDirty = idea.trim() !== savedIdea.trim();

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
  const warn = `${btn} border border-amber-500/30 bg-amber-950/20 text-amber-300 hover:bg-amber-950/40`;

  const kindLabel: Record<string, string> = {
    images: "Đang tạo ảnh", clips: "Đang tạo clip",
    assemble: "Đang ghép video", full: "Đang chạy toàn bộ",
    rebuild: "Đang tạo lại video từ ảnh",
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

        {(st.final_exists || st.thumbnail_path) && (
          <Section title="Video hoàn chỉnh">
            {st.thumbnail_path && (
              <div className="mb-3">
                <img
                  src={videoThumbnailUrl(projectId, st.thumbnail_path ?? "")}
                  alt="Thumbnail (kèm chữ)"
                  className="w-full rounded-lg border border-white/10"
                />
                <a
                  href={videoThumbnailUrl(projectId, st.thumbnail_path ?? "")}
                  className={`${ghost} mt-2 inline-block`}
                  download
                >
                  ⬇ Tải thumbnail (kèm chữ)
                </a>
              </div>
            )}
            {st.final_exists && (
              <a
                href={videoDownloadUrl(projectId)}
                className={`${primary} inline-block`}
                download
              >
                ⬇ Tải video final.mp4
              </a>
            )}
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

        <Section title="Phong cách hình ảnh & video">
          <div className="grid grid-cols-2 gap-2">
            {([
              { key: "2d", title: "2D", desc: "Tranh vẽ / anime / cartoon" },
              { key: "3d", title: "3D", desc: "Pixar / Disney CGI" },
            ] as const).map((s) => {
              const active = videoStyle === s.key;
              return (
                <button
                  key={s.key}
                  disabled={busy || updateProject.isPending}
                  onClick={() => updateProject.mutate({ video_style: s.key })}
                  className={`rounded-lg border px-3 py-2.5 text-left transition-colors disabled:opacity-40 disabled:cursor-not-allowed ${
                    active
                      ? "border-accent bg-accent/15 text-white"
                      : "border-white/10 bg-background text-gray-300 hover:bg-white/5"
                  }`}
                >
                  <div className="flex items-center gap-1.5 text-sm font-semibold">
                    <span>{active ? "●" : "○"}</span>
                    {s.title === "2D" ? "🎨 2D" : "🧊 3D"}
                  </div>
                  <div className="mt-0.5 text-[11px] text-gray-400">{s.desc}</div>
                </button>
              );
            })}
          </div>
          <p className="mt-2 text-xs text-gray-500">
            Lựa chọn này ép thẳng vào prompt tạo <b>ảnh</b> (Gemini) và <b>video</b> (Flow/Veo).
            Đổi phong cách rồi bấm <b>Tạo lại từ đầu</b> để áp dụng cho toàn bộ.
          </p>
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
            <b> 16:9</b>, phong cách <b>{videoStyle === "3d" ? "hoạt hình 3D (Pixar/Disney)" : "hoạt hình 2D (tranh vẽ/anime)"}</b>,
            nhân vật <b>đồng bộ xuyên suốt</b> như một bộ phim ngắn chill/relaxing.
            Các ảnh không chứa chữ; tiêu đề được ghép vào đầu video và xuất thumbnail riêng khi ghép.
          </p>
          <p className="mt-1 text-xs text-gray-600">
            Để trống → dùng bộ prompt mặc định. Cần cấu hình API key ở
            ⚙️ Settings để AI viết prompt.
          </p>

          <div className="mt-3 flex items-center gap-2">
            <button
              className={`${btn} bg-surface-2 text-gray-200 hover:bg-white/10`}
              disabled={updateProject.isPending || !ideaDirty}
              onClick={() => updateProject.mutate({ video_idea: idea.trim() })}
            >
              💾 Lưu ý tưởng
            </button>
            {updateProject.isSuccess && !ideaDirty && !updateProject.isPending && (
              <span className="text-xs text-green-400">Đã lưu ✓</span>
            )}
            {ideaDirty && (
              <span className="text-xs text-yellow-300">Có thay đổi chưa lưu</span>
            )}
          </div>
          <p className="mt-1 text-xs text-gray-600">
            Ý tưởng được lưu theo project — dùng cho cả nút tạo ảnh/clip và khi
            tự động dựng video sau mix.
          </p>

          <label className="mt-3 flex cursor-pointer items-start gap-2 rounded-lg border border-white/5 bg-background px-3 py-2">
            <input
              type="checkbox"
              className="mt-0.5 h-4 w-4 accent-accent"
              checked={autoVideo}
              disabled={updateProject.isPending}
              onChange={(e) => updateProject.mutate({ auto_video: e.target.checked })}
            />
            <span className="text-xs text-gray-300">
              <b>Tự động dựng video sau khi mix Audio xong</b>
              <br />
              <span className="text-gray-500">
                Khi render mix hoàn tất, hệ thống tự chạy ảnh → clip → ghép và
                xuất <code className="text-gray-400">final.mp4</code> vào thư mục
                tên project (dùng ý tưởng đã lưu ở trên).
              </span>
            </span>
          </label>
        </Section>

        <Section title="Tạo video (cách nhanh)">
          <p className="mb-3 text-xs text-gray-500">
            Đa số trường hợp chỉ cần bấm <b>Tạo toàn bộ video</b>. Nếu đã có bộ
            ảnh ưng ý và chỉ muốn dựng lại phần video, dùng nút bên dưới — giữ
            nguyên ảnh, chỉ làm lại clip rồi ghép.
          </p>
          <div className="space-y-2">
            <button className={`${primary} w-full`} disabled={busy}
                    onClick={() => runAll.mutate(idea.trim() ? { idea: idea.trim() } : undefined)}>
              ▶ Tạo toàn bộ video (ảnh → clip → ghép){idea.trim() ? " — theo ý tưởng" : ""}
            </button>
            <button className={`${ghost} w-full`} disabled={busy || !imagesDone || !hasAudio}
                    onClick={() => {
                      if (confirm(`Giữ nguyên ${p.image_count} ảnh đã có, tạo lại toàn bộ clip rồi ghép video?`)) {
                        rebuild.mutate({ mode: "restart" });
                      }
                    }}>
              🎬 Tạo lại video từ ảnh đã có (giữ ảnh · clip → ghép)
            </button>
            {!hasAudio && (
              <p className="text-xs text-yellow-300">
                ⚠️ Cần có bản mix Audio trước khi dựng/ghép video.
              </p>
            )}
          </div>
        </Section>

        <Section title="Chạy từng bước">
          <p className="mb-3 text-xs text-gray-500">
            Dùng khi muốn kiểm soát từng phần hoặc tạo tiếp phần còn dở.
            <b> Tạo tiếp</b> = giữ cái đã có, chỉ làm phần còn thiếu.
            <b> Làm lại</b> = xoá hết phần đó rồi tạo mới.
          </p>
          <div className="space-y-4">
            {/* Bước 1 — Ảnh */}
            <div>
              <div className="mb-1.5 text-xs font-semibold text-gray-300">
                ① Ảnh — {st.image_count}/{p.image_count} (không chứa chữ)
              </div>
              <div className="grid grid-cols-2 gap-2">
                <button className={`${ghost} w-full`} disabled={busy}
                        onClick={() => genImages.mutate({ mode: "resume" })}>
                  Tạo tiếp ảnh thiếu
                </button>
                <button className={`${warn} w-full`} disabled={busy}
                        onClick={() => {
                          if (confirm(`Xoá toàn bộ ảnh cũ và tạo lại ${p.image_count} ảnh từ đầu theo ý tưởng?`)) {
                            genImages.mutate({ idea: idea.trim() || undefined, mode: "restart" });
                          }
                        }}>
                  Làm lại từ đầu
                </button>
              </div>
            </div>
            {/* Bước 2 — Clip */}
            <div>
              <div className="mb-1.5 text-xs font-semibold text-gray-300">
                ② Clip — {st.clip_count}/{p.total_clips} (Flow · {p.model})
              </div>
              <div className="grid grid-cols-2 gap-2">
                <button className={`${ghost} w-full`} disabled={busy || !imagesDone}
                        onClick={() => genClips.mutate({ mode: "resume" })}>
                  Tạo tiếp clip thiếu
                </button>
                <button className={`${warn} w-full`} disabled={busy || !imagesDone}
                        onClick={() => {
                          if (confirm(`Xoá toàn bộ clip cũ và tạo lại ${p.total_clips} clip từ ảnh đã có?`)) {
                            genClips.mutate({ mode: "restart" });
                          }
                        }}>
                  Làm lại từ đầu
                </button>
              </div>
            </div>
            {/* Bước 3 — Ghép nhạc */}
            <div>
              <div className="mb-1.5 text-xs font-semibold text-gray-300">
                ③ Ghép video + gắn nhạc
              </div>
              <button className={`${ghost} w-full`} disabled={busy || !clipsDone || !hasAudio}
                      onClick={() => assemble.mutate(undefined)}>
                Ghép video (clip 00 một lần · T+{p.t_window} · fade {p.blend_seconds}s)
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
