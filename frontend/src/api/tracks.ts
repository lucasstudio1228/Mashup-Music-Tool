import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from './client';
import type { ScanResult, Track } from '../types';

export function useTracks(projectId: number | undefined) {
  return useQuery({
    queryKey: ['tracks', projectId],
    enabled: projectId !== undefined,
    queryFn: async () =>
      (await api.get<Track[]>(`/api/projects/${projectId}/tracks`)).data,
  });
}

function invalidate(qc: ReturnType<typeof useQueryClient>, projectId: number) {
  qc.invalidateQueries({ queryKey: ['tracks', projectId] });
  qc.invalidateQueries({ queryKey: ['project', projectId] });
  qc.invalidateQueries({ queryKey: ['projects'] });
}

export function useScanFolder(projectId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (folderPath: string) =>
      (
        await api.post<ScanResult>(`/api/projects/${projectId}/tracks/scan`, {
          folder_path: folderPath,
        })
      ).data,
    onSuccess: () => invalidate(qc, projectId),
  });
}

export function useAddFile(projectId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (filePath: string) =>
      (
        await api.post<Track>(`/api/projects/${projectId}/tracks/add-file`, {
          file_path: filePath,
        })
      ).data,
    onSuccess: () => invalidate(qc, projectId),
  });
}

export function useUploadTrack(projectId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (file: File) => {
      const form = new FormData();
      form.append('file', file);
      const res = await api.post<Track>(
        `/api/projects/${projectId}/tracks/upload`,
        form,
        {
          // File dài có thể to → tắt timeout mặc định của axios cho request này.
          timeout: 0,
          headers: { 'Content-Type': 'multipart/form-data' },
        },
      );
      return res.data;
    },
    onSuccess: () => invalidate(qc, projectId),
  });
}

export function useDeleteTrack(projectId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (trackId: number) =>
      (await api.delete(`/api/projects/${projectId}/tracks/${trackId}`)).data,
    onSuccess: () => invalidate(qc, projectId),
  });
}

export function useDeleteAllTracks(projectId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async () =>
      (await api.delete(`/api/projects/${projectId}/tracks`)).data,
    onSuccess: () => invalidate(qc, projectId),
  });
}
