import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from './client';
import type {
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

export function useDeleteProject() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (id: number) =>
      (await api.delete(`/api/projects/${id}`)).data,
    onSuccess: () => qc.invalidateQueries({ queryKey: ['projects'] }),
  });
}
