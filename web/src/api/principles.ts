import { api } from './client';

export const principlesApi = {
  get: () => api.get<{ html: string }>('/public/principles').then((r) => r.data.html),
};
