import { useEffect, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { statementsApi, type Statement, type Evidence } from '../api/statements';
import { papersApi, type Paper } from '../api/papers';
import { highlightsApi } from '../api/highlights';
import { passagesApi } from '../api/passages';
import { trunc, stripHtml, formatCitation } from '../lib/format';
import StatementPicker from './StatementPicker';
import styles from './EvidenceLinkPopover.module.css';

type EvidenceTab = 'search' | 'highlights' | 'papers' | 'statement';

function EvidenceRow({
  evidence, papers, statements,
}: {
  evidence: Evidence;
  papers: Paper[];
  statements: Statement[];
}) {
  const { data: passage } = useQuery({
    queryKey: ['passage-evidence', evidence.target_id],
    queryFn: () => passagesApi.get(evidence.target_id),
    enabled: evidence.target_kind === 'passage',
  });

  if (evidence.target_kind === 'passage') {
    if (!passage) return <div className={styles.evidenceRow}>Loading…</div>;
    return (
      <div className={styles.evidenceRow}>
        <div className={styles.evidenceQuote}>&ldquo;{trunc(passage.verbatim_text, 140)}&rdquo;</div>
        <div className={styles.evidenceCite}>{formatCitation(passage.paper_first_author, passage.paper_year)}</div>
      </div>
    );
  }
  if (evidence.target_kind === 'paper') {
    const paper = papers.find((p) => p.id === evidence.target_id);
    return <div className={styles.evidenceRow}>{paper ? stripHtml(paper.title) : 'Paper'}</div>;
  }
  if (evidence.target_kind === 'statement') {
    const s = statements.find((s) => s.id === evidence.target_id);
    return <div className={styles.evidenceRow}>{s ? trunc(s.assertion, 100) : 'Statement'}</div>;
  }
  return <div className={styles.evidenceRow}>{evidence.target_kind}</div>;
}

export default function EvidenceLinkPopover({
  projectId,
  x,
  y,
  selectedText,
  existingStatementId,
  pendingEvidence,
  onClose,
  onStatementLinked,
  onUnlink,
}: {
  projectId: string;
  x: number;
  y: number;
  selectedText: string;
  existingStatementId: string | null;
  /** A source dragged straight into this selection (e.g. from the Sources
   * panel) — auto-attached as evidence the moment a statement exists, so
   * "bring evidence in" and "cite it" collapse into one motion. */
  pendingEvidence?: { targetKind: string; targetId: string } | null;
  onClose: () => void;
  onStatementLinked: (statementId: string) => void;
  onUnlink: () => void;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const queryClient = useQueryClient();

  const [statementId, setStatementId] = useState<string | null>(existingStatementId);
  const [chooseMode, setChooseMode] = useState<'create' | 'link'>('create');
  const [assertionDraft, setAssertionDraft] = useState(selectedText);
  const [evidenceTab, setEvidenceTab] = useState<EvidenceTab>('search');
  const [searchQuery, setSearchQuery] = useState('');
  const pendingAttachedRef = useRef(false);

  useEffect(() => {
    function handleClickOutside(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) onClose();
    }
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, [onClose]);

  const { data: allStatements } = useQuery({
    queryKey: ['statements', projectId],
    queryFn: () => statementsApi.list(projectId),
  });
  const statement = allStatements?.find((s) => s.id === statementId) ?? null;

  const createStatementMutation = useMutation({
    mutationFn: () => statementsApi.create(projectId, 'claim', assertionDraft),
    onSuccess: (created) => {
      setStatementId(created.id);
      onStatementLinked(created.id);
      queryClient.invalidateQueries({ queryKey: ['statements', projectId] });
    },
  });

  const handlePickExisting = (picked: Statement) => {
    setStatementId(picked.id);
    onStatementLinked(picked.id);
  };

  const { data: evidence, refetch: refetchEvidence } = useQuery({
    queryKey: ['evidence', statementId],
    queryFn: () => statementsApi.evidence(statementId!),
    enabled: !!statementId,
  });

  const addEvidenceMutation = useMutation({
    mutationFn: (payload: { targetKind: string; targetId: string }) =>
      statementsApi.addEvidence(statementId!, payload.targetKind, payload.targetId, 'evidence'),
    onSuccess: () => refetchEvidence(),
  });

  useEffect(() => {
    if (!statementId || !pendingEvidence || pendingAttachedRef.current) return;
    pendingAttachedRef.current = true;
    addEvidenceMutation.mutate(pendingEvidence);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [statementId, pendingEvidence]);

  const { data: searchResults, isFetching: searchPending } = useQuery({
    queryKey: ['compose-evidence-search', projectId, searchQuery],
    queryFn: () => papersApi.search(searchQuery, projectId),
    enabled: evidenceTab === 'search' && searchQuery.trim().length > 2,
  });

  const { data: projectHighlights } = useQuery({
    queryKey: ['highlights', projectId],
    queryFn: () => highlightsApi.list(projectId),
    enabled: evidenceTab === 'highlights',
  });

  const { data: projectPapers } = useQuery({
    queryKey: ['papers-for-project', projectId],
    queryFn: () => papersApi.listForProject(projectId),
  });

  return (
    <div ref={ref} className={styles.popover} style={{ left: x, top: y }}>
      {!statementId ? (
        <>
          <div className={styles.tabRow}>
            <button
              className={`${styles.tabBtn} ${chooseMode === 'create' ? styles.tabBtnActive : ''}`}
              onClick={() => setChooseMode('create')}
            >
              Create statement
            </button>
            <button
              className={`${styles.tabBtn} ${chooseMode === 'link' ? styles.tabBtnActive : ''}`}
              onClick={() => setChooseMode('link')}
            >
              Link existing
            </button>
          </div>
          {chooseMode === 'create' ? (
            <div className={styles.createBlock}>
              <textarea
                className={styles.assertionTextarea}
                value={assertionDraft}
                onChange={(e) => setAssertionDraft(e.target.value)}
                rows={3}
                autoFocus
              />
              <button
                className={styles.primaryBtn}
                disabled={assertionDraft.trim().length < 3 || createStatementMutation.isPending}
                onClick={() => createStatementMutation.mutate()}
              >
                {createStatementMutation.isPending ? 'Creating…' : 'Create & link'}
              </button>
            </div>
          ) : (
            <StatementPicker projectId={projectId} onPick={handlePickExisting} />
          )}
        </>
      ) : (
        <>
          <div className={styles.header}>
            <span className={styles.headerType}>{statement?.type ?? 'statement'}</span>
            <span className={styles.headerAssertion}>{trunc(statement?.assertion ?? '', 90)}</span>
          </div>
          {existingStatementId && (
            <button className={styles.unlinkBtn} onClick={onUnlink}>Unlink this text</button>
          )}

          <div className={styles.evidenceList}>
            {evidence?.length === 0 && <div className={styles.empty}>No evidence yet</div>}
            {evidence?.map((e) => (
              <EvidenceRow
                key={`${e.target_kind}-${e.target_id}`}
                evidence={e}
                papers={projectPapers ?? []}
                statements={allStatements ?? []}
              />
            ))}
          </div>

          <div className={styles.tabRow}>
            <button className={`${styles.tabBtn} ${evidenceTab === 'search' ? styles.tabBtnActive : ''}`} onClick={() => setEvidenceTab('search')}>Search</button>
            <button className={`${styles.tabBtn} ${evidenceTab === 'highlights' ? styles.tabBtnActive : ''}`} onClick={() => setEvidenceTab('highlights')}>Highlights</button>
            <button className={`${styles.tabBtn} ${evidenceTab === 'papers' ? styles.tabBtnActive : ''}`} onClick={() => setEvidenceTab('papers')}>Papers</button>
            <button className={`${styles.tabBtn} ${evidenceTab === 'statement' ? styles.tabBtnActive : ''}`} onClick={() => setEvidenceTab('statement')}>Statement</button>
          </div>

          {evidenceTab === 'search' && (
            <div className={styles.tabContent}>
              <input
                className={styles.searchInput}
                placeholder="Search papers you've read…"
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
              />
              {searchPending && <div className={styles.empty}>Searching…</div>}
              {searchResults?.hits.map((hit) => (
                <button
                  key={hit.sentence_id}
                  className={styles.pickRow}
                  onClick={() => addEvidenceMutation.mutate({ targetKind: 'passage', targetId: hit.sentence_id })}
                >
                  <div className={styles.evidenceQuote}>&ldquo;{trunc(hit.text, 120)}&rdquo;</div>
                  <div className={styles.evidenceCite}>{stripHtml(hit.paper.title)}</div>
                </button>
              ))}
            </div>
          )}

          {evidenceTab === 'highlights' && (
            <div className={styles.tabContent}>
              {(projectHighlights ?? []).length === 0 && <div className={styles.empty}>No highlights yet — mark passages while reading in Workspace.</div>}
              {projectHighlights?.map((h) => (
                <button
                  key={h.id}
                  className={styles.pickRow}
                  onClick={() => addEvidenceMutation.mutate({ targetKind: 'passage', targetId: h.sentence_ids[0] })}
                >
                  <div className={styles.evidenceQuote}>&ldquo;{trunc(h.verbatim_text, 120)}&rdquo;</div>
                  <div className={styles.evidenceCite}>{formatCitation(h.paper_first_author, h.paper_year)}</div>
                </button>
              ))}
            </div>
          )}

          {evidenceTab === 'papers' && (
            <div className={styles.tabContent}>
              {projectPapers?.map((p) => (
                <button
                  key={p.id}
                  className={styles.pickRow}
                  onClick={() => addEvidenceMutation.mutate({ targetKind: 'paper', targetId: p.id })}
                >
                  <div className={styles.evidenceQuote}>{stripHtml(p.title)}</div>
                </button>
              ))}
            </div>
          )}

          {evidenceTab === 'statement' && (
            <div className={styles.tabContent}>
              <StatementPicker
                projectId={projectId}
                excludeId={statementId}
                onPick={(picked) => addEvidenceMutation.mutate({ targetKind: 'statement', targetId: picked.id })}
              />
            </div>
          )}
        </>
      )}
    </div>
  );
}
