import { useEffect, useRef, useState } from 'react';
import { useParams } from '@tanstack/react-router';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useEditor, EditorContent } from '@tiptap/react';
import StarterKit from '@tiptap/starter-kit';
import { composeApi } from '../api/compose';
import { StatementLink } from '../extensions/StatementLink';
import EvidenceLinkPopover from '../components/EvidenceLinkPopover';
import StatementPicker from '../components/StatementPicker';
import SourcesPanel from '../components/SourcesPanel';
import { PASSAGE_MIME } from '../components/ReaderPane';
import '../components/ComposeEditor.css';
import styles from './ComposeView.module.css';

type SaveState = 'idle' | 'unsaved' | 'saving' | 'saved' | 'conflict' | 'error';

const AUTOSAVE_DELAY_MS = 1500;

const HEADING_LEVELS = [1, 2, 3, 4] as const;

export default function ComposeView() {
  const { projectId } = useParams({ from: '/app/projects/$projectId/compose' });
  const queryClient = useQueryClient();

  const { data: doc } = useQuery({
    queryKey: ['compose-document', projectId],
    queryFn: () => composeApi.get(projectId),
  });

  const [saveState, setSaveState] = useState<SaveState>('idle');
  // Mirrors `saveState` for the autosave effect below, which intentionally
  // does NOT depend on `saveState` (see that effect's comment) but still
  // needs the current value inside its stable `onUpdate` closure.
  const saveStateRef = useRef<SaveState>('idle');
  useEffect(() => {
    saveStateRef.current = saveState;
  }, [saveState]);
  const versionRef = useRef(1);
  const loadedRef = useRef(false);
  const [sourcesOpen, setSourcesOpen] = useState(true);
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
        codeBlock: false,
        code: false,
        horizontalRule: false,
        strike: false,
        underline: false,
        link: false,
        heading: { levels: [...HEADING_LEVELS] },
      }),
      StatementLink,
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

  // Load the fetched document into the editor exactly once — TipTap's
  // `content` option only seeds the editor on creation, and re-applying it
  // on every refetch would clobber in-progress edits.
  useEffect(() => {
    if (!editor || !doc || loadedRef.current) return;
    editor.commands.setContent(doc.content_json, { emitUpdate: false });
    versionRef.current = doc.version;
    loadedRef.current = true;
  }, [editor, doc]);

  const saveMutation = useMutation({
    mutationFn: (json: Record<string, unknown>) => composeApi.save(projectId, json, versionRef.current),
    onSuccess: (saved) => {
      versionRef.current = saved.version;
      setSaveState('saved');
    },
    onError: (err) => {
      const status = (err as { response?: { status?: number } })?.response?.status;
      setSaveState(status === 409 ? 'conflict' : 'error');
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
            className={`${styles.toolbarBtn} ${editor.isActive('heading', { level }) ? styles.toolbarBtnActive : ''}`}
            onClick={() => editor.chain().focus().toggleHeading({ level }).run()}
          >
            H{level}
          </button>
        ))}
        <div className={styles.toolbarDivider} />
        <button
          className={`${styles.toolbarBtn} ${editor.isActive('bold') ? styles.toolbarBtnActive : ''}`}
          onClick={() => editor.chain().focus().toggleBold().run()}
        >
          B
        </button>
        <button
          className={`${styles.toolbarBtn} ${editor.isActive('italic') ? styles.toolbarBtnActive : ''}`}
          onClick={() => editor.chain().focus().toggleItalic().run()}
        >
          I
        </button>
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
          className={`${styles.toolbarBtn} ${sourcesOpen ? styles.toolbarBtnActive : ''} ${styles.sourcesToggle}`}
          onClick={() => setSourcesOpen((v) => !v)}
        >
          Sources
        </button>

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

      <div className={styles.body}>
        <div className={styles.editorScroll}>
          <div className={styles.editorColumn}>
            <EditorContent editor={editor} />
          </div>
        </div>
        {sourcesOpen && (
          <div className={styles.sourcesPanelWrap}>
            <SourcesPanel projectId={projectId} />
          </div>
        )}
      </div>

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
