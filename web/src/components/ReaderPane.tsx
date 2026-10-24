import { useState, useEffect, useRef, useMemo } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { papersApi, type Section, type Sentence, type SentenceEvidence, firstAuthor } from '../api/papers';
import { highlightsApi, type Highlight } from '../api/highlights';
import { safeHtml } from '../lib/format';
import ZoteroHighlightImportModal from './ZoteroHighlightImportModal';
import styles from '../pages/Workspace.module.css';

export const PASSAGE_MIME = 'application/x-athenaeum-passage';

/** Wraps a Find-imported quote within the rendered abstract in a soft-blue
 * highlight, distinct from the yellow statement-evidence highlight — this
 * marks text found pre-import, not yet tied to any statement. Falls back to
 * the plain abstract (no highlight, no error) if the quote can't be located
 * verbatim, e.g. the abstract was re-fetched/edited since import. */
function renderAbstractWithFoundQuote(
  abstract: string,
  foundQuote: string | null,
  foundRelevanceNote: string | null,
) {
  if (!foundQuote) return abstract;
  const idx = abstract.indexOf(foundQuote);
  if (idx === -1) return abstract;
  return (
    <>
      {abstract.slice(0, idx)}
      <mark className={styles.foundQuoteHighlight} title={foundRelevanceNote ?? undefined}>
        {foundQuote}
      </mark>
      {abstract.slice(idx + foundQuote.length)}
    </>
  );
}

export default function ReaderPane({
  paperId, projectId, onBacktrace, backtracePending = false, backtraceError = null, showBacktrace = true,
}: {
  paperId: string;
  projectId: string;
  onBacktrace?: (sentenceId: string, text: string) => void;
  backtracePending?: boolean;
  backtraceError?: string | null;
  showBacktrace?: boolean;
}) {
  const queryClient = useQueryClient();
  const [expandAbstract, setExpandAbstract] = useState(false);
  const [openSection, setOpenSection] = useState<string | null>(null);
  const [fullTextView, setFullTextView] = useState(false);
  const [zoteroHighlightModalOpen, setZoteroHighlightModalOpen] = useState(false);

  const { data: paper } = useQuery({
    queryKey: ['paper', paperId],
    queryFn: () => papersApi.get(paperId),
  });

  const { data: sections } = useQuery({
    queryKey: ['sections', paperId],
    queryFn: () => papersApi.sections(paperId),
    enabled: paper?.status === 'full_text',
  });

  const { data: sentences } = useQuery({
    queryKey: ['sentences', paperId, openSection],
    queryFn: () => papersApi.sentences(paperId, openSection ?? undefined),
    enabled: !!openSection && !fullTextView,
  });

  const { data: allSentences } = useQuery({
    queryKey: ['sentences', paperId, 'all'],
    queryFn: () => papersApi.sentences(paperId),
    enabled: fullTextView,
  });

  const { data: evidenceSentences } = useQuery({
    queryKey: ['evidence-sentences', paperId],
    queryFn: () => papersApi.evidenceSentences(paperId),
    enabled: paper?.status === 'full_text',
  });

  const evidenceBySentence = useMemo(() => {
    const map = new Map<string, SentenceEvidence[]>();
    for (const e of evidenceSentences ?? []) {
      const list = map.get(e.sentence_id);
      if (list) list.push(e);
      else map.set(e.sentence_id, [e]);
    }
    return map;
  }, [evidenceSentences]);

  const { data: highlights } = useQuery({
    queryKey: ['highlights', projectId, paperId],
    queryFn: () => highlightsApi.list(projectId, paperId),
    enabled: paper?.status === 'full_text',
  });

  // v1 highlights are always single-sentence (see Highlight.sentence_ids —
  // schema-forward for multi-sentence spans, but the UI never writes more
  // than one element), so a sentence maps to at most one highlight.
  const highlightBySentence = useMemo(() => {
    const map = new Map<string, Highlight>();
    for (const h of highlights ?? []) {
      for (const sid of h.sentence_ids) map.set(sid, h);
    }
    return map;
  }, [highlights]);

  const createHighlightMutation = useMutation({
    mutationFn: (payload: { sentenceId: string; text: string }) =>
      highlightsApi.create({
        project_id: projectId,
        paper_id: paperId,
        sentence_ids: [payload.sentenceId],
        verbatim_text: payload.text,
      }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['highlights', projectId, paperId] }),
  });

  const deleteHighlightMutation = useMutation({
    mutationFn: (highlightId: string) => highlightsApi.delete(highlightId),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['highlights', projectId, paperId] }),
  });

  const passagesRef = useRef<HTMLDivElement>(null);
  const selectionTipBtnRef = useRef<HTMLDivElement>(null);
  const [selectionTip, setSelectionTip] = useState<{
    text: string;
    sentenceId: string;
    x: number;
    y: number;
  } | null>(null);

  const retryMutation = useMutation({
    mutationFn: () => papersApi.retry(paperId),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['papers', projectId] }),
  });

  const reuploadInputRef = useRef<HTMLInputElement>(null);
  const reuploadMutation = useMutation({
    mutationFn: (file: File) => papersApi.reupload(paperId, file),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['paper', paperId] });
      queryClient.invalidateQueries({ queryKey: ['papers', projectId] });
      if (reuploadInputRef.current) reuploadInputRef.current.value = '';
    },
  });

  // Bound at the document level (not on the passagesRef container directly):
  // that div only exists once paper/sections finish loading, and since this
  // effect only runs once on mount, attaching straight to the container would
  // capture a still-null ref forever. Reading passagesRef.current live inside
  // the handler instead means it doesn't matter when the container shows up.
  useEffect(() => {
    const handleMouseUp = (e: MouseEvent) => {
      const container = passagesRef.current;
      if (!container || !container.contains(e.target as Node)) return;
      if (selectionTipBtnRef.current?.contains(e.target as Node)) return;
      const sel = window.getSelection();
      if (!sel || sel.isCollapsed || !sel.rangeCount) { setSelectionTip(null); return; }
      const text = sel.toString().trim();
      if (!text) { setSelectionTip(null); return; }
      let node: Node | null = sel.anchorNode;
      let sentenceId: string | null = null;
      while (node) {
        if (node instanceof Element && (node as HTMLElement).dataset.sentenceId) {
          sentenceId = (node as HTMLElement).dataset.sentenceId!;
          break;
        }
        node = node.parentElement;
      }
      if (!sentenceId) { setSelectionTip(null); return; }
      const range = sel.getRangeAt(0);
      const rect = range.getBoundingClientRect();
      setSelectionTip({ text, sentenceId, x: rect.left + rect.width / 2, y: rect.bottom + 6 });
    };

    const handleMouseDown = (e: MouseEvent) => {
      if (selectionTipBtnRef.current?.contains(e.target as Node)) return;
      setSelectionTip(null);
    };

    document.addEventListener('mouseup', handleMouseUp);
    document.addEventListener('mousedown', handleMouseDown);
    return () => {
      document.removeEventListener('mouseup', handleMouseUp);
      document.removeEventListener('mousedown', handleMouseDown);
    };
  }, []);

  if (!paper) return <div className={styles.panelLoading}>Loading…</div>;

  const author = firstAuthor(paper.authors_json);
  const meta = [author && `${author} et al.`, paper.year, paper.venue].filter(Boolean).join(' · ');
  // A missing openalex_id alone doesn't disable Backtrace — the backend
  // retries resolving one from the paper's own DOI when the chain starts
  // (common for Zotero imports, which never get OpenAlex-matched at import
  // time). Only block up front when there's truly nothing to resolve from.
  const backtraceDisabledReason = !paper.openalex_id && !paper.doi
    ? 'This paper has no DOI, so it can’t be matched to an OpenAlex record.'
    : null;

  return (
    <div className={styles.panelContent}>
      <div className={styles.panelKind}>paper</div>
      <div className={styles.panelTitle} dangerouslySetInnerHTML={safeHtml(paper.title)} />
      {meta && <div className={styles.panelMeta}>{meta}</div>}

      <div className={styles.paperStatusRow}>
        <span className={`${styles.statusDot} ${styles[paper.status]}`} />
        <span className={styles.paperStatusLabel}>{paper.status.replace('_', ' ')}</span>
        {paper.doi && <span className={styles.paperDoi}>{paper.doi}</span>}
        {paper.zotero_item_key && <span className={styles.zoteroBadge}>Zotero</span>}
        {paper.zotero_item_key && paper.status === 'full_text' && (
          <button className={styles.retryBtn} onClick={() => setZoteroHighlightModalOpen(true)}>
            Import Zotero highlights
          </button>
        )}
      </div>

      {zoteroHighlightModalOpen && (
        <ZoteroHighlightImportModal
          paperId={paperId}
          projectId={projectId}
          zoteroSource={paper.zotero_source}
          zoteroItemKey={paper.zotero_item_key}
          onClose={() => setZoteroHighlightModalOpen(false)}
        />
      )}

      {paper.status === 'failed' && (
        <div className={styles.paperActionsRow}>
          <button
            className={styles.retryBtn}
            onClick={() => retryMutation.mutate()}
            disabled={retryMutation.isPending}
          >
            {retryMutation.isPending ? 'Retrying…' : 'Retry ingestion'}
          </button>
          <button
            className={styles.retryBtn}
            onClick={() => reuploadInputRef.current?.click()}
            disabled={reuploadMutation.isPending}
          >
            {reuploadMutation.isPending ? 'Uploading…' : 'Upload PDF'}
          </button>
        </div>
      )}

      <input
        ref={reuploadInputRef}
        type="file"
        accept=".pdf,.txt,application/pdf,text/plain"
        style={{ display: 'none' }}
        onChange={(e) => {
          const f = e.target.files?.[0];
          if (f) reuploadMutation.mutate(f);
        }}
      />

      {paper.abstract && (
        <div className={styles.abstractSection}>
          <div className={styles.panelSectionLabel}>Abstract</div>
          <div className={`${styles.abstractText} ${expandAbstract ? '' : styles.abstractClamped}`}>
            {renderAbstractWithFoundQuote(paper.abstract, paper.found_quote, paper.found_relevance_note)}
          </div>
          {paper.abstract.length > 280 && (
            <button className={styles.expandBtn} onClick={() => setExpandAbstract((v) => !v)}>
              {expandAbstract ? 'Collapse' : 'Expand'}
            </button>
          )}
        </div>
      )}

      {paper.status === 'full_text' && sections && sections.length > 0 && (
        <div className={styles.sectionsSection} ref={passagesRef}>
          <div className={styles.panelSectionLabel}>
            Passages
            <button className={styles.viewToggleBtn} onClick={() => setFullTextView((v) => !v)}>
              {fullTextView ? 'Section view' : 'Full text view'}
            </button>
          </div>
          <div className={styles.searchHint} style={{ marginBottom: 8 }}>
            Highlight any text to bookmark it, or to trace the claim it makes back through its
            own citations.
          </div>

          {fullTextView ? (
            <div className={styles.fullTextView}>
              {sections.map((sec: Section) => (
                <div key={sec.id} className={styles.fullTextSection}>
                  <div className={styles.fullTextSectionLabel}>{sec.label}</div>
                  <div className={styles.fullTextBody}>
                    {allSentences
                      ?.filter((s: Sentence) => s.section_id === sec.id)
                      .map((s: Sentence) => {
                        const evs = evidenceBySentence.get(s.id);
                        const hl = highlightBySentence.get(s.id);
                        return (
                          <span
                            key={s.id}
                            className={`${styles.sentenceSpan} ${evs ? styles.evidenceHighlight : ''} ${hl ? styles.userHighlight : ''}`}
                            data-sentence-id={s.id}
                            title={evs?.map((e) => `Supports statement: ${e.assertion}`).join('\n')}
                          >
                            {s.text}
                            {hl && (
                              <button
                                className={styles.unhighlightBtn}
                                title="Remove highlight"
                                onClick={(e) => { e.stopPropagation(); deleteHighlightMutation.mutate(hl.id); }}
                              >
                                ×
                              </button>
                            )}
                            {' '}
                          </span>
                        );
                      })}
                  </div>
                </div>
              ))}
            </div>
          ) : (
            sections.map((sec: Section) => (
              <div key={sec.id} className={styles.sectionItem}>
                <button
                  className={styles.sectionToggle}
                  onClick={() => setOpenSection(openSection === sec.id ? null : sec.id)}
                >
                  <span>{sec.label}</span>
                  <span className={styles.sectionArrow}>{openSection === sec.id ? '▾' : '▸'}</span>
                </button>
                {openSection === sec.id && (
                  <div className={styles.sentenceList}>
                    {sentences
                      ?.filter((s: Sentence) => s.section_id === sec.id)
                      .map((s: Sentence) => {
                        const evs = evidenceBySentence.get(s.id);
                        const hl = highlightBySentence.get(s.id);
                        return (
                          <div
                            key={s.id}
                            className={styles.sentence}
                            data-sentence-id={s.id}
                            title={evs?.map((e) => `Supports statement: ${e.assertion}`).join('\n')}
                          >
                            <span className={`${styles.sentenceText} ${evs ? styles.evidenceHighlight : ''} ${hl ? styles.userHighlight : ''}`}>
                              {s.text}
                              {hl && (
                                <button
                                  className={styles.unhighlightBtn}
                                  title="Remove highlight"
                                  onClick={(e) => { e.stopPropagation(); deleteHighlightMutation.mutate(hl.id); }}
                                >
                                  ×
                                </button>
                              )}
                            </span>
                          </div>
                        );
                      })}
                  </div>
                )}
              </div>
            ))
          )}
        </div>
      )}

      {paper.status === 'ghost' && (
        <div className={styles.ghostNote}>
          <div>Full text not available. Add a PDF to see passages.</div>
          <button
            className={styles.retryBtn}
            onClick={() => reuploadInputRef.current?.click()}
            disabled={reuploadMutation.isPending}
          >
            {reuploadMutation.isPending ? 'Uploading…' : 'Upload PDF'}
          </button>
          {reuploadMutation.error && (
            <div className={styles.formError}>{String((reuploadMutation.error as Error).message)}</div>
          )}
        </div>
      )}

      {selectionTip && (
        <div
          ref={selectionTipBtnRef}
          className={styles.selectionTooltipGroup}
          style={{ left: selectionTip.x, top: selectionTip.y }}
        >
          <button
            className={styles.selectionTooltip}
            style={{ cursor: createHighlightMutation.isPending ? 'wait' : 'pointer' }}
            disabled={createHighlightMutation.isPending}
            onClick={() => {
              createHighlightMutation.mutate({ sentenceId: selectionTip.sentenceId, text: selectionTip.text });
              setSelectionTip(null);
            }}
          >
            {createHighlightMutation.isPending ? '★ Highlighting…' : '★ Highlight'}
          </button>
          {showBacktrace && onBacktrace && (
            <button
              className={styles.selectionTooltip}
              style={{ cursor: backtraceDisabledReason ? 'not-allowed' : 'pointer', opacity: backtraceDisabledReason ? 0.5 : 1 }}
              disabled={!!backtraceDisabledReason || backtracePending}
              title={backtraceDisabledReason ?? undefined}
              onClick={() => {
                onBacktrace(selectionTip.sentenceId, selectionTip.text);
                setSelectionTip(null);
              }}
            >
              {backtracePending && <span className={`${styles.statusDot} ${styles.processing}`} />}
              {backtracePending ? '🔍 Starting…' : '🔍 Backtrace'}
            </button>
          )}
        </div>
      )}

      {backtraceError && <div className={styles.formError}>{backtraceError}</div>}
    </div>
  );
}
