import { useEffect, useState } from 'react';
import type { Editor } from '@tiptap/react';
import { findTable, TableMap } from '@tiptap/pm/tables';
import styles from './ComposeTableControls.module.css';

/** Floating +/- handles at each row/column edge of whichever table the
 * cursor is currently inside. TipTap's table commands (addRowAfter,
 * deleteColumn, etc.) act on the *current selection*, not an arbitrary
 * index, so every handle first moves the selection into the relevant
 * row/column, then runs the command.
 *
 * Geometry comes straight from the rendered DOM (the `<table>`'s own rows
 * and cells) rather than being derived purely from ProseMirror coordinates —
 * table rows/cells are real DOM elements, so measuring them directly is
 * simpler and more robust than reconstructing layout from document
 * positions. TableMap is still used to resolve a (row, col) into the
 * document position each handle needs to select before running its
 * command. */
export default function ComposeTableControls({ editor }: { editor: Editor }) {
  const [, setTick] = useState(0);

  useEffect(() => {
    const bump = () => setTick((t) => t + 1);
    editor.on('transaction', bump);
    window.addEventListener('resize', bump);
    window.addEventListener('scroll', bump, true);
    return () => {
      editor.off('transaction', bump);
      window.removeEventListener('resize', bump);
      window.removeEventListener('scroll', bump, true);
    };
  }, [editor]);

  if (!editor.isActive('table')) return null;

  const found = findTable(editor.state.selection.$anchor);
  if (!found) return null;
  const map = TableMap.get(found.node);
  const dom = editor.view.nodeDOM(found.pos) as HTMLElement | null;
  const tableEl = dom instanceof HTMLTableElement ? dom : (dom?.querySelector('table') ?? null);
  if (!tableEl) return null;

  const selectCell = (row: number, col: number) => {
    const pos = found.start + map.positionAt(row, col, found.node);
    editor.chain().focus().setTextSelection(pos).run();
  };
  const addRowAfter = (row: number) => { selectCell(row, 0); editor.chain().focus().addRowAfter().run(); };
  const addRowBefore = (row: number) => { selectCell(row, 0); editor.chain().focus().addRowBefore().run(); };
  const deleteRow = (row: number) => { selectCell(row, 0); editor.chain().focus().deleteRow().run(); };
  const addColAfter = (col: number) => { selectCell(0, col); editor.chain().focus().addColumnAfter().run(); };
  const addColBefore = (col: number) => { selectCell(0, col); editor.chain().focus().addColumnBefore().run(); };
  const deleteCol = (col: number) => { selectCell(0, col); editor.chain().focus().deleteColumn().run(); };

  const rowHandles = [];
  for (let r = 0; r < map.height; r++) {
    const rowEl = tableEl.rows[r];
    if (!rowEl) continue;
    const rect = rowEl.getBoundingClientRect();
    rowHandles.push(
      <div key={`row-${r}`} className={styles.edgeHandle} style={{ left: rect.left - 34, top: rect.top + rect.height / 2 - 9 }}>
        <button title="Insert row after" onClick={() => addRowAfter(r)}>+</button>
        <button title="Delete row" onClick={() => deleteRow(r)}>−</button>
      </div>,
    );
  }
  const firstRow = tableEl.rows[0];
  if (firstRow) {
    const rect = firstRow.getBoundingClientRect();
    rowHandles.push(
      <div key="row-lead" className={styles.edgeHandleLead} style={{ left: rect.left - 34, top: rect.top - 18 }}>
        <button title="Insert row above" onClick={() => addRowBefore(0)}>+</button>
      </div>,
    );
  }

  const colHandles = [];
  for (let c = 0; c < map.width; c++) {
    const cellEl = firstRow?.cells[c];
    if (!cellEl) continue;
    const rect = cellEl.getBoundingClientRect();
    colHandles.push(
      <div key={`col-${c}`} className={styles.edgeHandleCol} style={{ left: rect.left + rect.width / 2 - 18, top: rect.top - 34 }}>
        <button title="Insert column after" onClick={() => addColAfter(c)}>+</button>
        <button title="Delete column" onClick={() => deleteCol(c)}>−</button>
      </div>,
    );
  }
  if (firstRow?.cells[0]) {
    const rect = firstRow.cells[0].getBoundingClientRect();
    colHandles.push(
      <div key="col-lead" className={styles.edgeHandleColLead} style={{ left: rect.left - 18, top: rect.top - 34 }}>
        <button title="Insert column before" onClick={() => addColBefore(0)}>+</button>
      </div>,
    );
  }

  return (
    <>
      {rowHandles}
      {colHandles}
    </>
  );
}
