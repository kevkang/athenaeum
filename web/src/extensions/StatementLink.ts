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

  // Marks are inclusive by default: typing (or splitting a block with
  // Enter) right at the end of a marked span carries the mark onto
  // whatever comes next. That's fine for bold/italic, but here it means a
  // line break inside or at the end of an evidence-linked span leaves the
  // new paragraph "infected" with the same evidence-underline, even though
  // its text was never verified against anything. Tiptap's own `Link` mark
  // sets this false for the identical reason.
  inclusive: false,

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
