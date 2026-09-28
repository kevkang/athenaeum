import { api } from './client';
import type { Highlight } from './highlights';

export interface ZoteroHighlightCandidate {
  annotation_item_key: string;
  zotero_text: string;
  comment: string | null;
  color: string | null;
  matched: boolean;
  match_ratio: number | null;
  sentence_ids: string[] | null;
  verbatim_text: string | null;
  already_imported: boolean;
}

export const zoteroHighlightsApi = {
  preview: (paperId: string, projectId: string) =>
    api
      .get<{ candidates: ZoteroHighlightCandidate[] }>(`/papers/${paperId}/zotero-highlights/preview`, {
        params: { project_id: projectId },
      })
      .then((r) => r.data.candidates),

  import: (
    paperId: string,
    projectId: string,
    items: { sentence_ids: string[]; verbatim_text: string; annotation_item_key: string }[],
  ) =>
    api
      .post<Highlight[]>(`/papers/${paperId}/zotero-highlights/import`, {
        project_id: projectId,
        items,
      })
      .then((r) => r.data),
};
