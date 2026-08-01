import Image from '@tiptap/extension-image';
import { mergeAttributes } from '@tiptap/core';
import { getToken } from '../api/client';

const API_BASE: string = import.meta.env.VITE_API_URL ?? 'http://localhost:8000';

export interface FigureAttrs {
  datasetId: string | null;
  alt: string | null;
}

/** Wraps the official Image node so the *persisted* attribute is a stable
 * `datasetId` — never a raw/expiring S3 URL — while the *live* editor
 * resolves `src` to `/datasets/{id}/file` for actual display. Same
 * technique StatementLink.ts uses for statementLink: a real graph id lives
 * in the ProseMirror JSON, not a snapshot of wherever the bytes happen to
 * be right now. */
export const Figure = Image.extend({
  name: 'image',

  addAttributes() {
    return {
      datasetId: {
        default: null,
        parseHTML: (el: HTMLElement) => el.getAttribute('data-dataset-id'),
        renderHTML: (attrs: FigureAttrs) => ({ 'data-dataset-id': attrs.datasetId }),
      },
      alt: {
        default: null,
        parseHTML: (el: HTMLElement) => el.getAttribute('alt'),
        renderHTML: (attrs: FigureAttrs) => ({ alt: attrs.alt ?? '' }),
      },
    };
  },

  parseHTML() {
    return [{ tag: 'img[data-dataset-id]' }];
  },

  renderHTML({ HTMLAttributes, node }) {
    const datasetId = node.attrs.datasetId as string | null;
    const token = getToken();
    const src = datasetId && token
      ? `${API_BASE}/datasets/${datasetId}/file?token=${encodeURIComponent(token)}`
      : '';
    return ['img', mergeAttributes(HTMLAttributes, { src, class: 'compose-figure' })];
  },
});
