import { api } from './client';

export interface LLMCredentialsStatus {
  connected: boolean;
  provider: string | null;
}

export const llmCredentialsApi = {
  getStatus: () => api.get<LLMCredentialsStatus>('/llm/credentials').then((r) => r.data),

  saveCredentials: (apiKey: string) =>
    api.put<LLMCredentialsStatus>('/llm/credentials', { api_key: apiKey }).then((r) => r.data),

  disconnect: () => api.delete('/llm/credentials').then(() => undefined),
};
