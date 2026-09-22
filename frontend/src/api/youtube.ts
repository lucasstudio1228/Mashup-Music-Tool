import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "./client";

// ── GPMLogin profiles + config ──────────────────────────────────
export type GpmProfile = {
  id: string;
  name: string;
  group_id?: string;
  group_name?: string;
};

export type GpmProfilesResult = {
  ok: boolean;
  base_url: string;
  profiles: GpmProfile[];
  error?: string;
};

export function useGpmProfiles() {
  return useQuery<GpmProfilesResult>({
    queryKey: ["gpm-profiles"],
    queryFn: async () => (await api.get("/api/youtube/gpm/profiles")).data,
    staleTime: 30_000,
  });
}

export type GpmConfig = {
  gpm_api_base_url: string;
  gpm_exe_path: string | null;
};

export function useGpmConfig() {
  return useQuery<GpmConfig>({
    queryKey: ["gpm-config"],
    queryFn: async () => (await api.get("/api/youtube/gpm/config")).data,
  });
}

export function useUpdateGpmConfig() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: Partial<GpmConfig>) =>
      api.patch("/api/youtube/gpm/config", body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["gpm-config"] });
      qc.invalidateQueries({ queryKey: ["gpm-profiles"] });
    },
  });
}

// ── Channel mappings ────────────────────────────────────────────
export type ChannelMapping = {
  id: number;
  instrument: string;
  music_style: string;
  gpm_profile_id: string;
  channel_name: string;
  default_hashtags: string;
  language: string;
};

export type MappingBody = Omit<ChannelMapping, "id">;

export function useChannelMappings() {
  return useQuery<ChannelMapping[]>({
    queryKey: ["channel-mappings"],
    queryFn: async () => (await api.get("/api/youtube/mappings")).data,
  });
}

export function useCreateMapping() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: MappingBody) => api.post("/api/youtube/mappings", body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["channel-mappings"] }),
  });
}

export function useUpdateMapping() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, body }: { id: number; body: MappingBody }) =>
      api.patch(`/api/youtube/mappings/${id}`, body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["channel-mappings"] }),
  });
}

export function useDeleteMapping() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: number) => api.delete(`/api/youtube/mappings/${id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["channel-mappings"] }),
  });
}
