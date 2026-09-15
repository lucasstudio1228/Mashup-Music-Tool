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

export const useGenImages = (pid: number) => useVideoAction(pid, "images");
export const useGenClips  = (pid: number) => useVideoAction(pid, "clips");
export const useAssemble  = (pid: number) => useVideoAction(pid, "assemble");
export const useRunAll    = (pid: number) => useVideoAction(pid, "run-all");
export const useRebuild   = (pid: number) => useVideoAction(pid, "rebuild");

export function videoDownloadUrl(projectId: number): string {
  return `${API_BASE}${base(projectId)}/download`;
}

// URL ảnh thumbnail (kèm chữ). Thêm cache-buster để sau mỗi lần ghép lại, ảnh
// mới hiển thị thay vì bản cache cũ của trình duyệt.
export function videoThumbnailUrl(projectId: number, bust?: string | number): string {
  const q = bust != null ? `?t=${encodeURIComponent(String(bust))}` : "";
  return `${API_BASE}${base(projectId)}/thumbnail${q}`;
}
