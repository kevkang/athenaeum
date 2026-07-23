import { api } from './client';

export interface EventOut {
  id: string;
  project_seq: number;
  actor_id: string | null;
  actor_display_name: string | null;
  kind: string;
  entity_kind: string;
  entity_id: string | null;
  prev_json: Record<string, unknown> | null;
  payload_json: Record<string, unknown>;
  created_at: string;
}

export interface HistoryPage {
  events: EventOut[];
  next_before_seq: number | null;
}

export const historyApi = {
  list: (projectId: string, opts?: { beforeSeq?: number; limit?: number }) => {
    const params: Record<string, string | number> = {};
    if (opts?.beforeSeq !== undefined) params.before_seq = opts.beforeSeq;
    if (opts?.limit !== undefined) params.limit = opts.limit;
    return api
      .get<HistoryPage>(`/projects/${projectId}/history`, { params })
      .then((r) => r.data);
  },

  undo: (projectId: string, seq: number) =>
    api
      .post<EventOut>(`/projects/${projectId}/events/${seq}/undo`)
      .then((r) => r.data),
};
