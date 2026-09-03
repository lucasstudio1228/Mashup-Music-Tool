import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { api } from './client';
import type { AppSettings, AppSettingsUpdate } from '../types';

export function useSettings() {
  return useQuery<AppSettings>({
    queryKey: ['settings'],
    queryFn: async () => {
      const res = await api.get('/api/settings');
      return res.data;
    },
    staleTime: 60_000, // Không refetch quá thường xuyên
  });
}

export function useUpdateSettings() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (data: AppSettingsUpdate) => {
      const res = await api.patch('/api/settings', data);
      return res.data as AppSettings;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['settings'] });
    },
  });
}
