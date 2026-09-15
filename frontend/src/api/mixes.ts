import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from './client';
import type { Mix, MixCreate } from '../types';

export function useMixes(projectId: number | undefined) {
  return useQuery({
    queryKey: ['mixes', projectId],
    enabled: projectId !== undefined,
    queryFn: async () =>
      (await api.get<Mix[]>(`/api/projects/${projectId}/mixes`)).data,
    // Auto refetch khi có mix đang chạy để cập nhật progress bar nhỏ.
    refetchInterval: (query) => {
      const data = query.state.data as Mix[] | undefined;
      const active = data?.some(
        (m) => m.status === 'running' || m.status === 'pending',
      );
      return active ? 2000 : false;
    },
  });
}

export function useCreateMix(projectId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (data: MixCreate) =>
      (await api.post<Mix>(`/api/projects/${projectId}/mixes`, data)).data,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['mixes', projectId] });
      qc.invalidateQueries({ queryKey: ['project', projectId] });
    },
  });
}

export function useCancelMix(projectId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (mixId: number) =>
      (await api.post(`/api/mixes/${mixId}/cancel`)).data,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['mixes', projectId] });
    },
  });
}

export function useDeleteMix(projectId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (mixId: number) =>
      (await api.delete(`/api/mixes/${mixId}`)).data,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['mixes', projectId] });
      qc.invalidateQueries({ queryKey: ['project', projectId] });
    },
  });
}
