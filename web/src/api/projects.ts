import { api } from './client';

export interface Project {
  id: string;
  title: string;
  description: string | null;
  license: string;
  is_public: boolean;
  owner_id: string;
  created_at: string;
}

export interface ProjectCreate {
  title: string;
  description?: string;
  license?: string;
  is_public?: boolean;
}

export const projectsApi = {
  list: () => api.get<Project[]>('/projects').then((r) => r.data),
  get: (id: string) => api.get<Project>(`/projects/${id}`).then((r) => r.data),
  create: (payload: ProjectCreate) =>
    api.post<Project>('/projects', payload).then((r) => r.data),
  update: (id: string, payload: Partial<ProjectCreate>) =>
    api.patch<Project>(`/projects/${id}`, payload).then((r) => r.data),

  importArchive: (file: File) => {
    const form = new FormData();
    form.append('file', file);
    return api
      .post<{ project_id: string }>('/projects/import', form)
      .then((r) => r.data);
  },
};
