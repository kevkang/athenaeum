import { api } from './client';

export interface ComposeDocument {
  id: string;
  project_id: string;
  content_json: Record<string, unknown>;
  version: number;
  updated_by: string | null;
  updated_at: string;
}

export const composeApi = {
  get: (projectId: string) =>
    api.get<ComposeDocument>(`/projects/${projectId}/compose-document`).then((r) => r.data),
  save: (projectId: string, contentJson: Record<string, unknown>, version: number) =>
    api
      .put<ComposeDocument>(`/projects/${projectId}/compose-document`, {
        content_json: contentJson,
        version,
      })
      .then((r) => r.data),
};
