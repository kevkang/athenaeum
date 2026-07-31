import { Mark, mergeAttributes } from '@tiptap/core';
import { Plugin, PluginKey } from '@tiptap/pm/state';
import { Decoration, DecorationSet } from '@tiptap/pm/view';

export interface StatementLinkAttrs {
  statementId: string | null;
}

/** statementId -> the citation sub-ids (e.g. "1.1", "1.2") that statement's
 * own evidence resolves to — see domain/citations.py on the backend. */
export type StatementMarkers = Record<string, string[]>;

declare module '@tiptap/core' {
  interface Commands<ReturnType> {
    statementLink: {
      setStatementLink: (statementId: string) => ReturnType;
      unsetStatementLink: () => ReturnType;
    };
  }
}

const citationMarkersPluginKey = new PluginKey('statementCitationMarkers');

/** Citation numbering changes as evidence is attached/detached and lives in
 * a react-query cache, not in the document itself — Tiptap extension
 * options are fixed at editor construction, so this mutable ref is the
 * bridge: the host component writes fresh markers here and dispatches a
 * no-op transaction (see ComposeView.tsx) to make ProseMirror recompute
 * decorations against the latest value. */
export const statementMarkersRef: { current: StatementMarkers } = { current: {} };

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

  addProseMirrorPlugins() {
    return [
      new Plugin({
        key: citationMarkersPluginKey,
        props: {
          decorations(state) {
            // A single logical statementLink span can be split across
            // several adjacent text nodes (e.g. partly bold) — marks are
            // per text-node, not per-span. Walk the doc in order and only
            // place one marker widget per *run* of consecutive nodes
            // sharing the same statementId, right after the run ends.
            const decorations: Decoration[] = [];
            let runStatementId: string | null = null;
            let runEnd = 0;

            const flush = () => {
              if (runStatementId === null) return;
              const subIds = statementMarkersRef.current[runStatementId] ?? [];
              if (subIds.length === 0) return;
              const widget = document.createElement('span');
              widget.className = 'statement-citation-markers';
              subIds.forEach((subId, i) => {
                if (i > 0) widget.appendChild(document.createTextNode(', '));
                const sup = document.createElement('sup');
                sup.className = 'statement-citation-marker';
                sup.setAttribute('data-citation-subid', subId);
                sup.setAttribute('data-statement-id', runStatementId as string);
                sup.textContent = subId;
                widget.appendChild(sup);
              });
              decorations.push(Decoration.widget(runEnd, widget, { side: 1 }));
            };

            state.doc.descendants((node, pos) => {
              if (!node.isText) {
                flush();
                runStatementId = null;
                return;
              }
              const mark = node.marks.find((m) => m.type.name === 'statementLink');
              const statementId = (mark?.attrs.statementId as string | undefined) ?? null;
              if (statementId !== runStatementId) {
                flush();
                runStatementId = statementId;
              }
              runEnd = pos + node.nodeSize;
            });
            flush();

            return DecorationSet.create(state.doc, decorations);
          },
        },
      }),
    ];
  },
});
