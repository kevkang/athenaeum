import { useEffect, useRef, useState } from 'react';
import { useParams } from '@tanstack/react-router';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useEditor, useEditorState, EditorContent, ReactNodeViewRenderer, type Editor } from '@tiptap/react';
import { InputRule } from '@tiptap/core';
import StarterKit from '@tiptap/starter-kit';
import { BlockMath, InlineMath } from '@tiptap/extension-mathematics';
import { CodeBlockLowlight } from '@tiptap/extension-code-block-lowlight';
import { TableKit } from '@tiptap/extension-table';
import { createLowlight, common } from 'lowlight';
import { composeApi, type CitationNumbering, type ComposeImportWarning } from '../api/compose';
import { figuresApi } from '../api/figures';
import { StatementLink, statementMarkersRef } from '../extensions/StatementLink';
import { Figure } from '../extensions/Figure';
import EvidenceLinkPopover from '../components/EvidenceLinkPopover';
import EvidenceHoverTooltip from '../components/EvidenceHoverTooltip';
import StatementPicker from '../components/StatementPicker';
import SourcesPanel from '../components/SourcesPanel';
import AuditPanel from '../components/AuditPanel';
import ComposeTOC from '../components/ComposeTOC';
import ComposeTableControls from '../components/ComposeTableControls';
import ComposeCodeBlock from '../components/ComposeCodeBlock';
import ReferencesList from '../components/ReferencesList';
import { PASSAGE_MIME } from '../components/ReaderPane';
import '../components/ComposeEditor.css';
import 'katex/dist/katex.min.css';
import styles from './ComposeView.module.css';

type SaveState = 'idle' | 'unsaved' | 'saving' | 'saved' | 'conflict' | 'error';

const AUTOSAVE_DELAY_MS = 1500;

const HEADING_LEVELS = [1, 2, 3, 4] as const;

const lowlight = createLowlight(common);

// The shipped input rules in @tiptap/extension-mathematics trigger inline
// math on `$$...$$` and block math on `$$$...$$$` — backwards from the
// conventional KaTeX-default delimiters (`$inline$` / `$$block$$`) that
// .athmd's own grammar already documents (see archive/athmd.py). Re-declare
// just the input rules with the corrected delimiter counts; node type,
// rendering, and click-to-edit behavior are untouched.
const ComposeBlockMath = BlockMath.extend({
  addInputRules() {
    return [
      new InputRule({
        find: /^\$\$([^$]+)\$\$$/,
        handler: ({ state, range, match }) => {
          const [, latex] = match;
          const { tr } = state;
          const $from = state.doc.resolve(range.from);
          const node = this.type.create({ latex });
          const consumesHostTextblock =
            $from.depth > 0 && $from.parent.isTextblock && range.from === $from.start() && range.to === $from.end();
          const canReplaceHostTextblock =
            consumesHostTextblock && $from.node(-1).canReplaceWith($from.index(-1), $from.indexAfter(-1), this.type);
          const replacementRange = canReplaceHostTextblock ? { from: $from.before(), to: $from.after() } : range;
          tr.replaceWith(replacementRange.from, replacementRange.to, node);
        },
      }),
    ];
  },
});

const ComposeInlineMath = InlineMath.extend({
  addInputRules() {
    return [
      new InputRule({
        find: /(?<!\$)\$([^$\n]+?)\$(?!\$)/,
        handler: ({ state, range, match }) => {
          const latex = match[1];
          const { tr } = state;
          tr.replaceWith(range.from, range.to, this.type.create({ latex }));
        },
      }),
    ];
  },
});

const ComposeCodeBlockLowlight = CodeBlockLowlight.extend({
  addNodeView() {
    return ReactNodeViewRenderer(ComposeCodeBlock);
  },
});

// Mathematics.configure's onClick options only receive (node, pos), not the
// editor — extensions are handed to useEditor once at construction, before
// the editor exists, so a closure over a component-local variable would be
// permanently stale. A plain module-level object (not `useRef`, which
// React's stricter hooks lint forbids mutating once it's flowed into a
// hook's config) is the bridge instead — same technique as this file's own
// `statementMarkersRef` import. Safe as a singleton since only one
// ComposeView is ever mounted at a time.
const composeEditorRef: { current: Editor | null } = { current: null };

function findEvidenceBySubId(numbering: CitationNumbering, subId: string) {
  for (const reference of numbering.references) {
    const evidence = reference.evidence.find((e) => e.sub_id === subId);
    if (evidence) return { reference, evidence };
  }
  return null;
}

export default function ComposeView() {
  const { projectId } = useParams({ from: '/app/projects/$projectId/compose' });
  const queryClient = useQueryClient();

  const { data: doc } = useQuery({
    queryKey: ['compose-document', projectId],
    queryFn: () => composeApi.get(projectId),
  });

  const { data: citations } = useQuery({
    queryKey: ['compose-citations', projectId],
    queryFn: () => composeApi.citations(projectId),
  });

  const [saveState, setSaveState] = useState<SaveState>('idle');
  const [importWarnings, setImportWarnings] = useState<ComposeImportWarning[]>([]);
  const importFileInputRef = useRef<HTMLInputElement>(null);
  const figureFileInputRef = useRef<HTMLInputElement>(null);
  // Mirrors `saveState` for the autosave effect below, which intentionally
  // does NOT depend on `saveState` (see that effect's comment) but still
  // needs the current value inside its stable `onUpdate` closure.
  const saveStateRef = useRef<SaveState>('idle');
  useEffect(() => {
    saveStateRef.current = saveState;
  }, [saveState]);
  const versionRef = useRef(1);
  const loadedRef = useRef(false);
  const [rightPanel, setRightPanel] = useState<'sources' | 'audit' | null>('sources');
  const [tocOpen, setTocOpen] = useState(false);
  // Auto-generated from the same citation data as the sidebar tab — never
  // part of content_json, recomputed the same way citation numbers already
  // are. Defaults open since it's meant to read as part of the manuscript.
  const [referencesOpen, setReferencesOpen] = useState(true);
  const editorScrollRef = useRef<HTMLDivElement>(null);
  const [evidencePopover, setEvidencePopover] = useState<{
    from: number; to: number; x: number; y: number; text: string; existingStatementId: string | null;
    pendingEvidence?: { targetKind: string; targetId: string } | null;
  } | null>(null);
  const [selectionToolbar, setSelectionToolbar] = useState<{ from: number; to: number; x: number; y: number } | null>(null);

  const editor = useEditor({
    extensions: [
      StarterKit.configure({
        blockquote: false,
        bulletList: false,
        orderedList: false,
        listItem: false,
        listKeymap: false,
        codeBlock: false, // CodeBlockLowlight below replaces it, for real syntax highlighting
        horizontalRule: false,
        strike: false,
        underline: false,
        heading: { levels: [...HEADING_LEVELS] },
        link: { openOnClick: false }, // editing, not browsing — don't navigate away on click
      }),
      ComposeCodeBlockLowlight.configure({ lowlight }),
      TableKit.configure({ table: { resizable: false } }),
      StatementLink,
      ComposeInlineMath.configure({
        onClick: (node, pos) => {
          const latex = window.prompt('Edit inline LaTeX:', node.attrs.latex as string);
          if (latex === null) return;
          if (latex.trim() === '') composeEditorRef.current?.commands.deleteInlineMath({ pos });
          else composeEditorRef.current?.commands.updateInlineMath({ latex, pos });
        },
      }),
      ComposeBlockMath.configure({
        onClick: (node, pos) => {
          const latex = window.prompt('Edit LaTeX block:', node.attrs.latex as string);
          if (latex === null) return;
          if (latex.trim() === '') composeEditorRef.current?.commands.deleteBlockMath({ pos });
          else composeEditorRef.current?.commands.updateBlockMath({ latex, pos });
        },
      }),
      Figure,
    ],
    editorProps: {
      attributes: { class: 'compose-prose' },
      handleDrop(view, event) {
        const raw = event.dataTransfer?.getData(PASSAGE_MIME);
        if (!raw) return false;
        event.preventDefault();
        const { sentenceId, text } = JSON.parse(raw) as { sentenceId: string; text: string };
        const pos = view.posAtCoords({ left: event.clientX, top: event.clientY });
        if (!pos) return true;
        view.dispatch(view.state.tr.insertText(text, pos.pos));
        setSelectionToolbar(null);
        setEvidencePopover({
          from: pos.pos,
          to: pos.pos + text.length,
          x: event.clientX,
          y: event.clientY,
          text,
          existingStatementId: null,
          pendingEvidence: { targetKind: 'passage', targetId: sentenceId },
        });
        return true;
      },
    },
  });

  useEffect(() => {
    composeEditorRef.current = editor;
  }, [editor]);

  // Toolbar active-states (bold/italic/heading) read via this selector
  // instead of calling `editor.isActive(...)` directly in JSX. Tiptap
  // transactions (e.g. Cmd+B) don't by themselves re-render this component
  // — the toolbar was only catching up whenever *some other* state change
  // happened to force a re-render, e.g. the debounced autosave flipping to
  // 'saving'. Worse, `onUpdate` below calls `setSaveState('unsaved')` on
  // every keystroke, but once saveState is already 'unsaved' that's a
  // same-value setState, which React skips — so toggling bold while
  // mid-typing produced no re-render at all until the next state change
  // landed, up to ~1.5s later. `useEditorState` subscribes to the editor's
  // own transactions directly, independent of this component's state.
  const toolbarState = useEditorState({
    editor,
    selector: ({ editor }) => {
      if (!editor) return null;
      return {
        activeHeadingLevel: HEADING_LEVELS.find((level) => editor.isActive('heading', { level })) ?? null,
        isParagraph: editor.isActive('paragraph'),
        isBold: editor.isActive('bold'),
        isItalic: editor.isActive('italic'),
        isCode: editor.isActive('code'),
        isCodeBlock: editor.isActive('codeBlock'),
        isLink: editor.isActive('link'),
      };
    },
  });

  // Only an individual citation marker (the small <sup>, one per evidence
  // sub-id) triggers the tooltip — hovering the statement span itself just
  // gets the plain CSS :hover background wash in ComposeEditor.css, no
  // React state involved.
  const [hoveredMarker, setHoveredMarker] = useState<{ statementId: string; subId: string; x: number; y: number } | null>(null);

  const handleEditorMouseOver = (e: React.MouseEvent) => {
    const target = (e.target as HTMLElement).closest<HTMLElement>('[data-citation-subid]');
    if (!target) return;
    const statementId = target.getAttribute('data-statement-id');
    const subId = target.getAttribute('data-citation-subid');
    if (!statementId || !subId) return;
    const rect = target.getBoundingClientRect();
    setHoveredMarker({ statementId, subId, x: rect.left, y: rect.bottom + 6 });
  };

  const handleEditorMouseOut = (e: React.MouseEvent) => {
    const related = e.relatedTarget as HTMLElement | null;
    if (related?.closest('[data-citation-subid]')) return;
    setHoveredMarker(null);
  };

  // Load the fetched document into the editor exactly once — TipTap's
  // `content` option only seeds the editor on creation, and re-applying it
  // on every refetch would clobber in-progress edits.
  useEffect(() => {
    if (!editor || !doc || loadedRef.current) return;
    editor.commands.setContent(doc.content_json, { emitUpdate: false });
    versionRef.current = doc.version;
    loadedRef.current = true;
  }, [editor, doc]);

  // StatementLink.ts's decoration plugin reads citation markers from a
  // mutable ref (Tiptap extension options are fixed at construction, but
  // markers change as evidence is attached/detached) — this effect is the
  // only place that ref gets written, and dispatching a no-op transaction
  // is what makes ProseMirror actually recompute decorations against the
  // fresh value; nothing else would trigger a redraw on its own since the
  // document itself hasn't changed.
  useEffect(() => {
    if (!editor || !citations) return;
    statementMarkersRef.current = citations.statement_markers;
    editor.view.dispatch(editor.state.tr);
  }, [editor, citations]);

  const saveMutation = useMutation({
    mutationFn: (json: Record<string, unknown>) => composeApi.save(projectId, json, versionRef.current),
    onSuccess: (saved) => {
      versionRef.current = saved.version;
      // Keep the query cache in sync with what was just persisted. Without
      // this, switching away and back within the SPA (no full page reload)
      // re-mounts ComposeView, and the `loadedRef` effect below seeds the
      // editor from whatever `doc` the cache still holds — which, absent
      // this line, is the document as it was when this component first
      // mounted, not the version that was just saved. That reads as "my
      // saved edits vanished," even though they were genuinely persisted
      // (a hard reload, which fetches fresh from the server instead of
      // using the cache, would show them again).
      queryClient.setQueryData(['compose-document', projectId], saved);
      queryClient.invalidateQueries({ queryKey: ['compose-citations', projectId] });
      setSaveState('saved');
    },
    onError: (err) => {
      const status = (err as { response?: { status?: number } })?.response?.status;
      setSaveState(status === 409 ? 'conflict' : 'error');
    },
  });

  const exportAthmdMutation = useMutation({
    mutationFn: () => composeApi.export(projectId),
    onSuccess: (text) => {
      const blob = new Blob([text], { type: 'text/markdown' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = 'compose.athmd';
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
    },
  });

  const importAthmdMutation = useMutation({
    mutationFn: (file: File) => composeApi.importFile(projectId, file),
    onSuccess: (result) => {
      editor?.commands.setContent(result.document.content_json, { emitUpdate: false });
      versionRef.current = result.document.version;
      queryClient.setQueryData(['compose-document', projectId], result.document);
      queryClient.invalidateQueries({ queryKey: ['compose-citations', projectId] });
      setImportWarnings(result.warnings);
      setSaveState('saved');
    },
  });

  const insertFigureMutation = useMutation({
    mutationFn: (file: File) => figuresApi.upload(file, projectId),
    onSuccess: (dataset) => {
      editor
        ?.chain()
        .focus()
        .insertContent({ type: 'image', attrs: { datasetId: dataset.id, alt: dataset.title } })
        .run();
    },
  });

  useEffect(() => {
    if (!editor) return;
    // Deliberately depends on [editor] only, not [saveState]: re-running
    // this effect on every saveState change (e.g. 'unsaved' right after
    // 'saved') would tear down and rebuild the 'update' subscription and,
    // critically, `clearTimeout` the autosave timer that the *same*
    // keystroke's onUpdate call had just scheduled a moment earlier —
    // silently dropping single, isolated edits. `onUpdate` reads the
    // latest saveState via saveStateRef instead of closing over the value
    // from whenever this effect last ran.
    let timer: ReturnType<typeof setTimeout>;
    const onUpdate = () => {
      if (!loadedRef.current || saveStateRef.current === 'conflict') return;
      setSaveState('unsaved');
      clearTimeout(timer);
      timer = setTimeout(() => {
        setSaveState('saving');
        saveMutation.mutate(editor.getJSON());
      }, AUTOSAVE_DELAY_MS);
    };
    editor.on('update', onUpdate);
    return () => {
      editor.off('update', onUpdate);
      clearTimeout(timer);
      // Flush any pending autosave immediately on unmount (e.g. navigating
      // from Compose to Workspace/Export) instead of just letting the
      // `clearTimeout` above discard it — that used to silently drop the
      // last <1.5s of edits on quick navigation, even though the toolbar
      // never showed anything but "Unsaved changes" in between. This must
      // call `editor.getJSON()` directly here rather than from an
      // `editor.on('destroy', ...)` handler: `@tiptap/react`'s `useEditor`
      // defers its actual `editor.destroy()` call by a tick
      // (`EditorInstanceManager.scheduleDestroy`, a `setTimeout(fn, 1)`),
      // which runs *after* this synchronous cleanup — a 'destroy' listener
      // registered here would already have been torn down by the time
      // tiptap gets around to emitting it, so it would never fire at all.
      // The editor itself, however, is still fully intact right now.
      if (saveStateRef.current === 'unsaved') {
        saveMutation.mutate(editor.getJSON());
      }
    };
    // Deliberately omits saveMutation from deps: it's a fresh object most
    // renders, and depending on it would reintroduce the same
    // re-subscribe/cancel race this effect exists to avoid — only
    // `saveMutation.mutate` is actually called, and that stays stable
    // across TanStack Query's re-renders.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [editor]);

  // ── Selection-driven evidence linking ──────────────────────────────────────
  const [insertPickerOpen, setInsertPickerOpen] = useState(false);

  useEffect(() => {
    if (!editor) return;
    const update = () => {
      const { from, to, empty } = editor.state.selection;
      if (empty) { setSelectionToolbar(null); return; }
      const coords = editor.view.coordsAtPos(to);
      setSelectionToolbar({ from, to, x: coords.left, y: coords.bottom + 6 });
    };
    editor.on('selectionUpdate', update);
    return () => { editor.off('selectionUpdate', update); };
  }, [editor]);

  const openEvidencePopover = () => {
    if (!editor || !selectionToolbar) return;
    const { from, to, x, y } = selectionToolbar;
    const text = editor.state.doc.textBetween(from, to, ' ');
    const existingStatementId = editor.isActive('statementLink')
      ? (editor.getAttributes('statementLink').statementId as string | null)
      : null;
    setEvidencePopover({ from, to, x, y, text, existingStatementId });
    setSelectionToolbar(null);
    setHoveredMarker(null);
  };

  const handleStatementLinked = (statementId: string) => {
    if (!editor || !evidencePopover) return;
    editor.chain().setTextSelection({ from: evidencePopover.from, to: evidencePopover.to }).setStatementLink(statementId).run();
  };

  const handleUnlink = () => {
    if (!editor || !evidencePopover) return;
    editor.chain().setTextSelection({ from: evidencePopover.from, to: evidencePopover.to }).unsetStatementLink().run();
    setEvidencePopover(null);
  };

  const reloadLatest = async () => {
    const fresh = await composeApi.get(projectId);
    editor?.commands.setContent(fresh.content_json, { emitUpdate: false });
    versionRef.current = fresh.version;
    queryClient.setQueryData(['compose-document', projectId], fresh);
    queryClient.invalidateQueries({ queryKey: ['compose-citations', projectId] });
    setSaveState('saved');
  };

  const overwriteAnyway = async () => {
    const fresh = await composeApi.get(projectId);
    versionRef.current = fresh.version;
    setSaveState('saving');
    saveMutation.mutate(editor!.getJSON());
  };

  if (!editor) return null;

  return (
    <div className={styles.compose}>
      <div className={styles.toolbar}>
        {HEADING_LEVELS.map((level) => (
          <button
            key={level}
            className={`${styles.toolbarBtn} ${toolbarState?.activeHeadingLevel === level ? styles.toolbarBtnActive : ''}`}
            onClick={() => editor.chain().focus().toggleHeading({ level }).run()}
          >
            H{level}
          </button>
        ))}
        <button
          className={`${styles.toolbarBtn} ${toolbarState?.isParagraph ? styles.toolbarBtnActive : ''}`}
          onClick={() => editor.chain().focus().setParagraph().run()}
        >
          Text
        </button>
        <div className={styles.toolbarDivider} />
        <button
          className={`${styles.toolbarBtn} ${toolbarState?.isBold ? styles.toolbarBtnActive : ''}`}
          onClick={() => editor.chain().focus().toggleBold().run()}
        >
          B
        </button>
        <button
          className={`${styles.toolbarBtn} ${toolbarState?.isItalic ? styles.toolbarBtnActive : ''}`}
          onClick={() => editor.chain().focus().toggleItalic().run()}
        >
          I
        </button>
        <button
          className={`${styles.toolbarBtn} ${toolbarState?.isCode ? styles.toolbarBtnActive : ''}`}
          title="Inline code"
          onClick={() => editor.chain().focus().toggleCode().run()}
        >
          Code
        </button>
        <button
          className={`${styles.toolbarBtn} ${toolbarState?.isCodeBlock ? styles.toolbarBtnActive : ''}`}
          title="Code block"
          onClick={() => editor.chain().focus().toggleCodeBlock().run()}
        >
          Code block
        </button>
        <button
          className={`${styles.toolbarBtn} ${toolbarState?.isLink ? styles.toolbarBtnActive : ''}`}
          title="Link selected text to a URL"
          onClick={() => {
            if (toolbarState?.isLink) {
              editor.chain().focus().unsetLink().run();
              return;
            }
            const url = window.prompt('Link URL:', 'https://');
            if (url && url.trim()) editor.chain().focus().setLink({ href: url.trim() }).run();
          }}
        >
          Link
        </button>
        <button
          className={styles.toolbarBtn}
          title="Insert a table"
          onClick={() => editor.chain().focus().insertTable({ rows: 3, cols: 3, withHeaderRow: true }).run()}
        >
          Table
        </button>
        <div className={styles.toolbarDivider} />
        <button
          className={styles.toolbarBtn}
          title="Insert inline LaTeX (e.g. E = mc^2)"
          onClick={() => {
            const latex = window.prompt('Inline LaTeX:', '');
            if (latex && latex.trim()) editor.chain().focus().insertInlineMath({ latex }).run();
          }}
        >
          Math
        </button>
        <button
          className={styles.toolbarBtn}
          title="Insert a block LaTeX equation"
          onClick={() => {
            const latex = window.prompt('Block LaTeX:', '');
            if (latex && latex.trim()) editor.chain().focus().insertBlockMath({ latex }).run();
          }}
        >
          Math block
        </button>
        <button
          className={styles.toolbarBtn}
          title="Insert a figure (PNG, JPEG, GIF, SVG, WebP)"
          disabled={insertFigureMutation.isPending}
          onClick={() => figureFileInputRef.current?.click()}
        >
          {insertFigureMutation.isPending ? 'Uploading…' : 'Figure'}
        </button>
        <input
          ref={figureFileInputRef}
          type="file"
          accept="image/png,image/jpeg,image/gif,image/svg+xml,image/webp"
          style={{ display: 'none' }}
          onChange={(e) => {
            const f = e.target.files?.[0];
            if (f) insertFigureMutation.mutate(f);
            e.target.value = '';
          }}
        />
        <div className={styles.toolbarDivider} />
        <div className={styles.insertWrap}>
          <button
            className={styles.toolbarBtn}
            onClick={() => setInsertPickerOpen((v) => !v)}
          >
            Insert statement…
          </button>
          {insertPickerOpen && (
            <div className={styles.insertDropdown}>
              <StatementPicker
                projectId={projectId}
                onPick={(picked) => {
                  editor
                    .chain()
                    .focus()
                    .insertContent({
                      type: 'text',
                      marks: [{ type: 'statementLink', attrs: { statementId: picked.id } }],
                      text: picked.assertion,
                    })
                    .run();
                  setInsertPickerOpen(false);
                }}
              />
            </div>
          )}
        </div>

        <button
          className={`${styles.toolbarBtn} ${tocOpen ? styles.toolbarBtnActive : ''} ${styles.sourcesToggle}`}
          onClick={() => setTocOpen((v) => !v)}
        >
          Contents
        </button>

        <button
          className={`${styles.toolbarBtn} ${rightPanel === 'sources' ? styles.toolbarBtnActive : ''}`}
          onClick={() => setRightPanel((v) => (v === 'sources' ? null : 'sources'))}
        >
          Sources
        </button>

        <button
          className={`${styles.toolbarBtn} ${rightPanel === 'audit' ? styles.toolbarBtnActive : ''}`}
          onClick={() => setRightPanel((v) => (v === 'audit' ? null : 'audit'))}
        >
          Audit
        </button>

        <button
          className={styles.toolbarBtn}
          onClick={() => exportAthmdMutation.mutate()}
          disabled={exportAthmdMutation.isPending}
        >
          {exportAthmdMutation.isPending ? 'Exporting…' : '↓ Export .athmd'}
        </button>

        <button
          className={styles.toolbarBtn}
          onClick={() => importFileInputRef.current?.click()}
          disabled={importAthmdMutation.isPending}
        >
          {importAthmdMutation.isPending ? 'Importing…' : '↑ Import .athmd'}
        </button>
        <input
          ref={importFileInputRef}
          type="file"
          accept=".athmd"
          style={{ display: 'none' }}
          onChange={(e) => {
            const f = e.target.files?.[0];
            if (f) importAthmdMutation.mutate(f);
            e.target.value = '';
          }}
        />

        <span className={`${styles.saveIndicator} ${saveState === 'error' ? styles.saveIndicatorError : ''}`}>
          {saveState === 'saving' && 'Saving…'}
          {saveState === 'saved' && 'Saved'}
          {saveState === 'unsaved' && 'Unsaved changes'}
          {saveState === 'error' && 'Could not save'}
          {saveState === 'conflict' && 'Not saved — see below'}
        </span>
      </div>

      {saveState === 'conflict' && (
        <div className={styles.conflictBanner}>
          <span>This document was edited elsewhere since you loaded it.</span>
          <button className={styles.conflictBtn} onClick={reloadLatest}>Reload latest</button>
          <button className={styles.conflictBtn} onClick={overwriteAnyway}>Overwrite anyway</button>
        </div>
      )}

      {importWarnings.length > 0 && (
        <div className={styles.importWarningBanner}>
          <div>
            <div>
              {importWarnings.length === 1
                ? "1 citation link couldn't be resolved and was imported as plain text:"
                : `${importWarnings.length} citation links couldn't be resolved and were imported as plain text:`}
            </div>
            <ul className={styles.importWarningList}>
              {importWarnings.map((w, i) => (
                <li key={i}>&ldquo;{w.span_text}&rdquo;</li>
              ))}
            </ul>
          </div>
          <button className={styles.importWarningDismiss} onClick={() => setImportWarnings([])}>
            Dismiss
          </button>
        </div>
      )}

      <div className={styles.body}>
        <div className={styles.editorScroll} ref={editorScrollRef}>
          <div
            className={styles.editorColumn}
            onMouseOver={handleEditorMouseOver}
            onMouseOut={handleEditorMouseOut}
          >
            <EditorContent editor={editor} />

            <div className={styles.referencesToggleRow}>
              <button
                className={styles.referencesToggleBtn}
                onClick={() => setReferencesOpen((v) => !v)}
              >
                {referencesOpen ? '▾' : '▸'} References
              </button>
            </div>
            {referencesOpen && (
              <div className="compose-prose">
                <ReferencesList projectId={projectId} variant="prose" />
              </div>
            )}
          </div>
        </div>
        {rightPanel === 'sources' && (
          <div className={styles.sourcesPanelWrap}>
            <SourcesPanel projectId={projectId} />
          </div>
        )}
        {rightPanel === 'audit' && (
          <div className={styles.sourcesPanelWrap}>
            <AuditPanel projectId={projectId} />
          </div>
        )}
        {tocOpen && <ComposeTOC editor={editor} scrollContainerRef={editorScrollRef} />}
        <ComposeTableControls editor={editor} />
      </div>

      {hoveredMarker && citations && (() => {
        const match = findEvidenceBySubId(citations, hoveredMarker.subId);
        if (!match) return null;
        return (
          <EvidenceHoverTooltip
            reference={match.reference}
            evidence={match.evidence}
            x={hoveredMarker.x}
            y={hoveredMarker.y}
          />
        );
      })()}

      {selectionToolbar && (
        <button
          className={styles.selectionToolbarBtn}
          style={{ left: selectionToolbar.x, top: selectionToolbar.y }}
          onMouseDown={(e) => e.preventDefault()}
          onClick={openEvidencePopover}
        >
          {editor.isActive('statementLink') ? '◆ Evidence…' : '+ Link evidence'}
        </button>
      )}

      {evidencePopover && (
        <EvidenceLinkPopover
          projectId={projectId}
          x={evidencePopover.x}
          y={evidencePopover.y}
          selectedText={evidencePopover.text}
          existingStatementId={evidencePopover.existingStatementId}
          pendingEvidence={evidencePopover.pendingEvidence}
          onClose={() => setEvidencePopover(null)}
          onStatementLinked={handleStatementLinked}
          onUnlink={handleUnlink}
        />
      )}
    </div>
  );
}
