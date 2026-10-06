import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { api, API_BASE } from "./client";
import type { VideoStatus } from "../types";

const base = (pid: number) => `/api/projects/${pid}/video`;

export function useVideoStatus(projectId: number) {
  return useQuery<VideoStatus>({
    queryKey: ["video", projectId],
    queryFn: async () => (await api.get(`${base(projectId)}/status`)).data,
    // Poll khi có job đang chạy để cập nhật % + message
    refetchInterval: (q) => {
      const s = q.state.data?.job?.status;
      return (s === "pending" || s === "running" || s === "paused") ? 1500 : false;
    },
  });
}

export type VideoStyleOption = {
  key: string;
  icon: string;
  label: string;
  desc: string;
};

export function useVideoStyles(projectId: number) {
  return useQuery<{ default: string; styles: VideoStyleOption[] }>({
    queryKey: ["video-styles"],
    queryFn: async () => (await api.get(`${base(projectId)}/styles`)).data,
    staleTime: Infinity,        // danh sách phong cách gần như tĩnh
  });
}

export type ThumbnailFontOption = { key: string; label: string; note: string };

// Font tiêu đề thumbnail + intro — lựa chọn CHUNG cho mọi project.
export function useThumbnailFonts(projectId: number) {
  return useQuery<{ selected: string; default: string; fonts: ThumbnailFontOption[] }>({
    queryKey: ["thumbnail-fonts"],
    queryFn: async () => (await api.get(`${base(projectId)}/thumbnail-fonts`)).data,
  });
}

export function useSetThumbnailFont(projectId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (key: string) => api.put(`${base(projectId)}/thumbnail-fonts`, { key }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["thumbnail-fonts"] }),
  });
}

// Máy tạo clip (chung mọi project): flow | gemini | muse.
export function useSetClipEngine(projectId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (engine: string) => api.put(`${base(projectId)}/clip-engine`, { engine }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["video"] }),
  });
}

// Ảnh mẫu: tên project viết bằng font `key` trên ảnh bìa của chính project.
export function thumbnailFontPreviewUrl(projectId: number, key: string,
                                        bust?: string | number): string {
  const q = bust != null ? `?t=${encodeURIComponent(String(bust))}` : "";
  return `${API_BASE}${base(projectId)}/thumbnail-fonts/${encodeURIComponent(key)}/preview${q}`;
}

function useVideoAction(projectId: number, path: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body?: any) =>
      api.post(`${base(projectId)}/${path}`, body ?? {}),
    onSuccess: () =>
      qc.invalidateQueries({ queryKey: ["video", projectId] }),
  });
}

export function useCancelVideo(projectId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => api.post(`${base(projectId)}/cancel`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["video", projectId] }),
  });
}

export function usePauseVideo(projectId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => api.post(`${base(projectId)}/pause`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["video", projectId] }),
  });
}

export function useResumeVideo(projectId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => api.post(`${base(projectId)}/resume`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["video", projectId] }),
  });
}

export const useUploadYoutube = (pid: number) => useVideoAction(pid, "upload-youtube");

// Mở cửa sổ Flow/Gemini bằng profile của tool (theo dõi/đăng nhập/kiểm tra).
export function useOpenVideoBrowser(projectId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (site: "flow" | "gemini") =>
      api.post(`${base(projectId)}/open-browser`, null, { params: { site } }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["video", projectId] }),
  });
}
export const useCloseVideoBrowser = (pid: number) => useVideoAction(pid, "close-browser");

export type YoutubeHistoryItem = {
  id: number;
  channel_name: string;
  title: string;
  hashtags: string;
  status: string;
  error_message: string | null;
  created_at: string | null;
};

export function useYoutubeHistory(projectId: number) {
  return useQuery<YoutubeHistoryItem[]>({
    queryKey: ["youtube-history", projectId],
    queryFn: async () =>
      (await api.get(`${base(projectId)}/youtube-history`)).data,
    // Cập nhật lịch sử khi có job upload đang chạy.
    refetchInterval: (q) => (q.state.data ? false : false),
  });
}

export const useGenImages = (pid: number) => useVideoAction(pid, "images");
export const usePreparePrompts = (pid: number) => useVideoAction(pid, "prompts/prepare");
export function usePromptManifest(pid: number) {
  return useQuery<{ manifest: null | {
    workflow_version?: number;
    scene_count?: number;
    // prompts: 3 ảnh (0 bìa, 1 nhân vật chính, 2 linh thú).
    prompts?: Record<string, string>;
    // motions: prompt cảnh, khoá theo CHỈ SỐ CLIP (0..total_clips-1).
    motions?: Record<string, string>;
    continuity?: {
      character_sheet?: string; pet_sheet?: string; pet_name?: string;
      scene_sheet?: string;
    };
    creative_brief?: { music?: string; visual?: string };
  } }>({
    queryKey: ["prompt-manifest", pid],
    queryFn: async () => (await api.get(`${base(pid)}/prompts`)).data,
  });
}
export const useGenClips  = (pid: number) => useVideoAction(pid, "clips");
export const useAssemble  = (pid: number) => useVideoAction(pid, "assemble");
export const useRunAll    = (pid: number) => useVideoAction(pid, "run-all");
export const useRebuild   = (pid: number) => useVideoAction(pid, "rebuild");
export const useRegenMotions = (pid: number) => useVideoAction(pid, "motions/regenerate");

// Tạo lại 1 ảnh / 1 clip lẻ theo chỉ số — giữ nguyên các cái khác.
export function useRegenImage(projectId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (index: number) =>
      api.post(`${base(projectId)}/images/${index}/regenerate`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["video", projectId] }),
  });
}

export function useRegenClip(projectId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (index: number) =>
      api.post(`${base(projectId)}/clips/${index}/regenerate`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["video", projectId] }),
  });
}

// URL xem trước ảnh/clip lẻ (kèm cache-buster để sau khi tạo lại thấy bản mới).
export function videoImageUrl(projectId: number, index: number, bust?: string | number): string {
  const q = bust != null ? `?t=${encodeURIComponent(String(bust))}` : "";
  return `${API_BASE}${base(projectId)}/image/${index}${q}`;
}

export function videoClipUrl(projectId: number, index: number, bust?: string | number): string {
  const q = bust != null ? `?t=${encodeURIComponent(String(bust))}` : "";
  return `${API_BASE}${base(projectId)}/clip/${index}${q}`;
}

export function videoDownloadUrl(projectId: number): string {
  return `${API_BASE}${base(projectId)}/download`;
}

// URL ảnh thumbnail (kèm chữ). Thêm cache-buster để sau mỗi lần ghép lại, ảnh
// mới hiển thị thay vì bản cache cũ của trình duyệt.
export function videoThumbnailUrl(projectId: number, bust?: string | number): string {
  const q = bust != null ? `?t=${encodeURIComponent(String(bust))}` : "";
  return `${API_BASE}${base(projectId)}/thumbnail${q}`;
}

// ── "Tạo video thủ công" (bán tự động) ───────────────────────────
// Tool mở Flow + đưa 2 bảng nhân vật vào ô soạn; người dùng dán prompt + bấm
// Tạo; tool tải về + đặt tên clip_NN.mp4.
export type ManualClipItem = {
  index: number;
  name: string;
  ingredients: number[];
  prompt: string;        // đúng chữ tool sẽ gửi Flow (đã bọc phong cách + phủ định)
  done: boolean;
};

export type ManualState = {
  active: boolean;
  phase: "opening" | "preparing" | "waiting_user" | "downloading" | "done" | "error" | "stopped" | string;
  current: number | null;
  prompt: string;
  ingredients: number[];
  blocked: string | null;
  total: number;
  done: number[];
  todo: number[];
  message: string;
  updated_at?: number;
};

export function useManualClips(projectId: number, poll: boolean) {
  return useQuery<{ total: number; clips: ManualClipItem[]; state: ManualState | null }>({
    queryKey: ["manual-clips", projectId],
    queryFn: async () => (await api.get(`${base(projectId)}/clips/manual`)).data,
    refetchInterval: poll ? 2000 : false,
    retry: false,
  });
}

export function useStartManualClips(projectId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body?: { only?: number[]; assemble?: boolean }) =>
      api.post(`${base(projectId)}/clips/manual`, body ?? {}),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["video", projectId] });
      qc.invalidateQueries({ queryKey: ["manual-clips", projectId] });
    },
  });
}

export function useImportClip(projectId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ index, file }: { index: number; file: File }) => {
      const fd = new FormData();
      fd.append("file", file);
      return api.post(`${base(projectId)}/clips/${index}/import`, fd, {
        headers: { "Content-Type": "multipart/form-data" },
      });
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["video", projectId] });
      qc.invalidateQueries({ queryKey: ["manual-clips", projectId] });
    },
  });
}
