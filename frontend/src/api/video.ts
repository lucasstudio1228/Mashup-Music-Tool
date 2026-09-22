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
      return (s === "pending" || s === "running") ? 1500 : false;
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

export const useUploadYoutube = (pid: number) => useVideoAction(pid, "upload-youtube");

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
    prompts?: Record<string, string>;
    motions?: Record<string, string>;
    continuity?: { character_sheet?: string; scene_sheet?: string };
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
