import { useQuery } from '@tanstack/react-query';
import { api } from './client';

export interface GlobalTask {
  id: string;
  source: 'video' | 'mix' | 'stem' | 'batch';
  project_id: number | null;
  project_name: string;
  kind: string;
  label: string;
  tab: 'suno' | 'audio' | 'video';
  status: 'pending' | 'running' | 'paused' | 'completed' | 'failed' | 'cancelled';
  percent: number | null;
  message: string;
  error: string | null;
  detail: string;
  ago_seconds?: number;
}

export interface GlobalTasksResponse {
  tasks: GlobalTask[];
  recent: GlobalTask[];
}

/** Mọi tác vụ đang chạy/xếp hàng của cả tool (không phụ thuộc project đang xem). */
export function useGlobalTasks() {
  return useQuery<GlobalTasksResponse>({
    queryKey: ['global-tasks'],
    queryFn: async () => (await api.get('/api/tasks/active')).data,
    refetchInterval: (q) => ((q.state.data?.tasks.length ?? 0) > 0 ? 2000 : 5000),
    refetchIntervalInBackground: true,
    retry: false,
  });
}
