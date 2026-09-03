import {
  useQuery, useMutation, useQueryClient
} from "@tanstack/react-query";
import { api, API_BASE } from "./client";
import type { TrackStemInfo, StemVolumes, ProjectStemStatus } from "../types";

/**
 * Backend dùng key `vol_*` (VolumeUpdate/BatchVolumeUpdate); frontend dùng
 * StemVolumes {other,bass,drums,vocals}. Map trước khi gửi.
 */
function toVolPayload(v: Partial<StemVolumes>): Record<string, number> {
  const out: Record<string, number> = {};
  if (v.other  !== undefined) out.vol_other  = v.other;
  if (v.bass   !== undefined) out.vol_bass   = v.bass;
  if (v.drums  !== undefined) out.vol_drums  = v.drums;
  if (v.vocals !== undefined) out.vol_vocals = v.vocals;
  return out;
}

export function useStemInfo(trackId: number) {
  return useQuery<TrackStemInfo>({
    queryKey: ["stems", trackId],
    queryFn:  async () =>
      (await api.get(`/api/tracks/${trackId}/stems`)).data,
    // Poll mỗi 3s khi đang pending/running
    refetchInterval: (q) => {
      const s = q.state.data?.status;
      return (s === "pending" || s === "running") ? 3000 : false;
    },
  });
}

export function useProjectStemStatus(projectId: number) {
  return useQuery<ProjectStemStatus>({
    queryKey: ["stems", "project", projectId],
    queryFn:  async () =>
      (await api.get(`/api/projects/${projectId}/stems/status`)).data,
    refetchInterval: (q) =>
      q.state.data?.is_processing ? 3000 : false,
  });
}

export function useAnalyzeStem(trackId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () =>
      api.post(`/api/tracks/${trackId}/stems/analyze`),
    onSuccess: () =>
      qc.invalidateQueries({ queryKey: ["stems", trackId] }),
  });
}

export function useAnalyzeAllStems(projectId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () =>
      api.post(`/api/projects/${projectId}/stems/analyze-all`),
    onSuccess: () =>
      qc.invalidateQueries({ queryKey: ["stems"] }),
  });
}

export function useUpdateStemVolumes(trackId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (volumes: Partial<StemVolumes>) =>
      api.patch(`/api/tracks/${trackId}/stems/volumes`, toVolPayload(volumes)),
    onSuccess: () =>
      qc.invalidateQueries({ queryKey: ["stems", trackId] }),
  });
}

export function useApplyAllVolumes(projectId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (volumes: StemVolumes) =>
      api.patch(`/api/projects/${projectId}/stems/volumes-all`,
                toVolPayload(volumes)),
    onSuccess: () =>
      qc.invalidateQueries({ queryKey: ["stems"] }),
  });
}

// URL builder cho audio endpoint (browser fetch trực tiếp, không qua axios)
export function getStemAudioUrl(trackId: number, stemName: string): string {
  return `${API_BASE}/api/tracks/${trackId}/stems/audio/${stemName}`;
}

export function useDeleteStems(trackId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () =>
      api.delete(`/api/tracks/${trackId}/stems`),
    onSuccess: () =>
      qc.invalidateQueries({ queryKey: ["stems", trackId] }),
  });
}
