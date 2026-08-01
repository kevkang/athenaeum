import { api } from './client';

export interface Dataset {
  id: string;
  project_id: string;
  kind: string;
  title: string;
  storage_uri: string | null;
  created_at: string;
}

export const figuresApi = {
  upload: (file: File, projectId: string, title?: string) => {
    const form = new FormData();
    form.append('file', file);
    form.append('project_id', projectId);
    if (title) form.append('title', title);
    return api.post<Dataset>('/datasets/upload', form).then((r) => r.data);
  },
};
