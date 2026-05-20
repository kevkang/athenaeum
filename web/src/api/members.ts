import { api } from './client';

export interface Member {
  user_id: string;
  role: string;
  added_at: string;
}

export const membersApi = {
  list: (projectId: string) =>
    api.get<Member[]>(`/projects/${projectId}/members`).then((r) => r.data),

  add: (projectId: string, userId: string, role: string) =>
    api
      .post<Member>(`/projects/${projectId}/members`, { user_id: userId, role })
      .then((r) => r.data),

  updateRole: (projectId: string, userId: string, role: string) =>
    api
      .patch<Member>(`/projects/${projectId}/members/${userId}`, { role })
      .then((r) => r.data),

  remove: (projectId: string, userId: string) =>
    api.delete(`/projects/${projectId}/members/${userId}`),
};
