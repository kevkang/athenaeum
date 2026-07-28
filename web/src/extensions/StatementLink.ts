import { Mark, mergeAttributes } from '@tiptap/core';

export interface StatementLinkAttrs {
  statementId: string | null;
}

declare module '@tiptap/core' {
  interface Commands<ReturnType> {
    statementLink: {
      setStatementLink: (statementId: string) => ReturnType;
      unsetStatementLink: () => ReturnType;
    };
  }
}

/** Binds a span of prose to a real graph Statement id. Lives inside the
 * ProseMirror document's own JSON tree, attached to actual text nodes — so
 * unlike an offset-range annotation, it can't drift out of sync as the rest
 * of the document is edited around it. */
export const StatementLink = Mark.create({
  name: 'statementLink',

  addAttributes() {
    return {
      statementId: {
        default: null,
        parseHTML: (el: HTMLElement) => el.getAttribute('data-statement-id'),
        renderHTML: (attrs: StatementLinkAttrs) => ({ 'data-statement-id': attrs.statementId }),
      },
    };
  },

  parseHTML() {
    return [{ tag: 'span[data-statement-id]' }];
  },

  renderHTML({ HTMLAttributes }) {
    return ['span', mergeAttributes(HTMLAttributes, { class: 'statement-link-mark' }), 0];
  },

  addCommands() {
    return {
      setStatementLink:
        (statementId: string) =>
        ({ commands }) =>
          commands.setMark(this.name, { statementId }),
      unsetStatementLink:
        () =>
        ({ commands }) =>
          commands.unsetMark(this.name),
    };
  },
});
