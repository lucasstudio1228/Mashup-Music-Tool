import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from './client';
import type {
  BatchCreate,
  BatchRun,
  BatchStyleOption,
  GenreOption,
  InstrumentOption,
  Project,
  ProjectCreate,
  ProjectDetail,
  ProjectUpdate,
} from '../types';

export function useProjects() {
  return useQuery({
    queryKey: ['projects'],
    queryFn: async () => (await api.get<Project[]>('/api/projects')).data,
  });
}

export function useProject(id: number | undefined) {
  return useQuery({
    queryKey: ['project', id],
    enabled: id !== undefined,
    queryFn: async () =>
      (await api.get<ProjectDetail>(`/api/projects/${id}`)).data,
  });
}

export function useCreateProject() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (data: ProjectCreate) =>
      (await api.post<Project>('/api/projects', data)).data,
    onSuccess: () => qc.invalidateQueries({ queryKey: ['projects'] }),
  });
}

export function useUpdateProject(id: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (data: ProjectUpdate) =>
      (await api.patch<Project>(`/api/projects/${id}`, data)).data,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['projects'] });
      qc.invalidateQueries({ queryKey: ['project', id] });
    },
  });
}

// ---------- Sản xuất hàng loạt ----------
/** Phong cách ảnh cho form tạo lô (chưa có project → route riêng, cùng nguồn config.STYLES). */
export function useInstrumentCatalog() {
  return useQuery<{ instruments: InstrumentOption[]; genres: GenreOption[] }>({
    queryKey: ['instrument-catalog'],
    queryFn: async () => (await api.get('/api/projects/instruments')).data,
    staleTime: Infinity,
  });
}

export function useBatchStyles() {
  return useQuery<{ default: string; styles: BatchStyleOption[] }>({
    queryKey: ['batch-styles'],
    queryFn: async () =>
      (await api.get('/api/projects/batch/styles')).data,
    staleTime: Infinity,
  });
}

export function useCreateBatch() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (data: BatchCreate) =>
      // AI nghĩ N ý tưởng trong request này (thường 30–120s) → không dùng timeout
      // 30s mặc định, nếu không UI báo lỗi trong khi server vẫn tạo lô.
      (await api.post<BatchRun>('/api/projects/batch', data, { timeout: 600_000 })).data,
    onSuccess: () => qc.invalidateQueries({ queryKey: ['projects'] }),
  });
}

/** Lô đang chạy trên server (null nếu không có) — poll 15s. */
export function useActiveBatch() {
  return useQuery<{ batch_key: string | null }>({
    queryKey: ['batch-active'],
    queryFn: async () => (await api.get('/api/projects/batch/active')).data,
    refetchInterval: 15000,
  });
}

/** Poll tiến độ lô mỗi 5s khi còn đang chạy (khớp nhịp poll của orchestrator). */
export function useBatchStatus(batchKey: string | null | undefined) {
  return useQuery({
    queryKey: ['batch', batchKey],
    enabled: Boolean(batchKey),
    queryFn: async () =>
      (await api.get<BatchRun>(`/api/projects/batch/${batchKey}`)).data,
    refetchInterval: (query) =>
      query.state.data && query.state.data.status !== 'running' ? false : 5000,
  });
}

export function useCancelBatch() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (batchKey: string) =>
      (await api.post<BatchRun>(`/api/projects/batch/${batchKey}/cancel`)).data,
    onSuccess: (data) => {
      qc.invalidateQueries({ queryKey: ['batch', data.batch_key] });
      qc.invalidateQueries({ queryKey: ['projects'] });
    },
  });
}

export function useResumeBatch() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (batchKey: string) =>
      (await api.post<BatchRun>(`/api/projects/batch/${batchKey}/resume`)).data,
    onSuccess: (data) => {
      qc.invalidateQueries({ queryKey: ['batch', data.batch_key] });
      qc.invalidateQueries({ queryKey: ['projects'] });
    },
  });
}

export function useDeleteProject() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (id: number) =>
      (await api.delete(`/api/projects/${id}`)).data,
    onSuccess: () => qc.invalidateQueries({ queryKey: ['projects'] }),
  });
}

// ---------- Đổi tên project ----------
/** none = chỉ đổi tên · text = vẽ lại chữ thumbnail/intro + ghép lại video ·
 *  regen = tạo lại ảnh bìa (Gemini) + clip 00 (Flow) rồi như text. */
export type RenameRefresh = 'none' | 'text' | 'regen';

export interface RenameResult {
  old_name: string;
  name: string;
  changed: boolean;
  moved: boolean;
  job: string | null;
  project: Project;
}

export function useRenameProject(id: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (data: { name: string; refresh: RenameRefresh }) =>
      (await api.post<RenameResult>(`/api/projects/${id}/rename`, data)).data,
    // Tên đổi → đường dẫn media/ảnh/thumbnail đổi theo: làm mới mọi truy vấn.
    onSuccess: () => qc.invalidateQueries(),
  });
}
