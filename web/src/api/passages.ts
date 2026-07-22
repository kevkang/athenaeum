import { api } from './client';

export interface PassageEvidence {
  id: string;
  paper_id: string;
  verbatim_text: string;
  verified: boolean;
  paper_title: string;
  paper_year: number | null;
  paper_first_author: string | null;
}

export const passagesApi = {
  get: (passageId: string) =>
    api.get<PassageEvidence>(`/passages/${passageId}`).then((r) => r.data),
};
