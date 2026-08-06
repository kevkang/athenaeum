import { useState, type RefObject } from 'react';
import { useEditorState, type Editor } from '@tiptap/react';
import { trunc } from '../lib/format';
import styles from './ComposeTOC.module.css';

interface HeadingEntry {
  level: number;
  text: string;
  pos: number;
}

/** Walks the doc for heading nodes on every transaction via useEditorState
 * (same subscription mechanism ComposeView's toolbar active-state uses) —
 * cheap enough for a document-sized outline, and keeps the outline live as
 * the user types without a separate TipTap extension. */
function useHeadingOutline(editor: Editor | null): HeadingEntry[] {
  return (
    useEditorState({
      editor,
      selector: ({ editor }) => {
        if (!editor) return [];
        const headings: HeadingEntry[] = [];
        editor.state.doc.descendants((node, pos) => {
          if (node.type.name === 'heading') {
            headings.push({ level: node.attrs.level as number, text: node.textContent, pos });
          }
        });
        return headings;
      },
    }) ?? []
  );
}

export default function ComposeTOC({
  editor,
  scrollContainerRef,
}: {
  editor: Editor;
  /** The `.editorScroll` div — scrolled directly rather than trusting
   * TipTap's own `scrollIntoView()` command, which turned out to be an
   * unreliable no-op for this panel (its "already in view" heuristic
   * doesn't account for the floating TOC overlaying part of the column). */
  scrollContainerRef: RefObject<HTMLDivElement | null>;
}) {
  const headings = useHeadingOutline(editor);
  const [collapsed, setCollapsed] = useState(false);

  const visible = collapsed ? headings.filter((h) => h.level === 1) : headings;

  const goTo = (pos: number) => {
    editor.chain().focus().setTextSelection(pos).run();
    const container = scrollContainerRef.current;
    if (!container) return;
    const coords = editor.view.coordsAtPos(pos);
    const containerRect = container.getBoundingClientRect();
    const targetTop = container.scrollTop + (coords.top - containerRect.top) - 24;
    container.scrollTo({ top: targetTop, behavior: 'smooth' });
  };

  return (
    <div className={styles.wrap}>
      <div className={styles.head}>
        <span className={styles.title}>Contents</span>
        <button
          className={styles.collapseBtn}
          title={collapsed ? 'Show all heading levels' : 'Show only top-level headings'}
          onClick={() => setCollapsed((v) => !v)}
        >
          {collapsed ? '▸' : '▾'}
        </button>
      </div>
      {visible.length === 0 ? (
        <div className={styles.empty}>No headings yet.</div>
      ) : (
        <div className={styles.list}>
          {visible.map((h) => (
            <button
              key={h.pos}
              className={styles.entry}
              style={{ paddingLeft: 8 + (h.level - 1) * 12 }}
              onClick={() => goTo(h.pos)}
            >
              {trunc(h.text || 'Untitled heading', 42)}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
