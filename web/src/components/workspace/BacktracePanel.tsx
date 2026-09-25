import { useState, useEffect, useRef, useCallback } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import {
  backtraceApi,
  type BacktraceCandidate,
  type BacktraceHop,
} from '../../api/backtrace';
import { papersApi } from '../../api/papers';
import { useUIStore } from '../../store/ui';
import { trunc, formatCitation } from '../../lib/format';
import styles from '../../pages/Workspace.module.css';
import FindResultCard, { AiNote, type FindImportState } from './FindResultCard';

// Backtrace hop resolution runs synchronously inside its request handler
// (unlike synthesis's arq job), so there's no persisted step log to poll —
// instead each mutating call streams ephemeral progress pings over a
// short-lived websocket keyed by a client-minted stream_id. See
// api/backtrace.py's module docstring for the protocol.
//
// This panel deliberately stays a single-path stepper — it always continues
// from whichever hop is currently the tail, one branch at a time, the way
// backtrace behaved before it became a tree. Exploring several branches at
// once (the tree view) lives in BacktraceTree.tsx instead; this is the
// "quick trace while reading" tool.
//
// Resolving a step is two calls, not one: /rank asks which of the current
// paper's references plausibly support the claim (no hop created yet), and
// committing one of them via /hops actually creates it. Ranking happens
// automatically as soon as there's something to rank, so the primary button
// can read "Trace further (via Smith et al. 2020) →" — naming the top
// candidate before the user even clicks — keeping the common case a single
// click despite the two-call split underneath (see the plan's "Key design
// decisions" for why: nothing auto-commits without the user seeing it).

/** Axios's own `.message` on a failed request is a generic "Request failed
 * with status code 503" — the backend's actual `detail` text (e.g. "LLM
 * provider not configured") is far more actionable, so prefer that. */
function errorDetail(err: unknown, fallback: string): string {
  return (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail ?? fallback;
}

const BACKTRACE_STEP_LABELS: Record<string, string> = {
  fetching_references: "fetching this paper's references",
  identifying_reference: 'identifying the cited reference',
  fetching_reference_details: 'fetching reference details',
  searching_abstract: 'searching the abstract for a quote',
  searching_full_text: 'searching full text for evidence',
};

function backtraceStepLabel(step: string | null): string {
  if (!step) return 'starting';
  return BACKTRACE_STEP_LABELS[step] ?? step;
}

function BacktraceLoadingStep({ step }: { step: string | null }) {
  return (
    <div className={styles.backtraceLoadingStep}>
      <span className={`${styles.statusDot} ${styles.processing}`} />
      {backtraceStepLabel(step)}&hellip;
    </div>
  );
}

/** Opens the step-status websocket and waits for it to connect *before* the
 * caller fires its mutation, so the very first event can't be published
 * before anyone is subscribed to hear it. Falls back to firing the request
 * anyway after a short timeout if the socket is slow or unavailable — the
 * live text is a nicety, never a dependency for the request itself. */
function useBacktraceStepStream() {
  const [step, setStep] = useState<string | null>(null);
  const wsRef = useRef<WebSocket | null>(null);

  const beginStream = useCallback((): Promise<string> => {
    const streamId = crypto.randomUUID();
    setStep(null);
    return new Promise((resolve) => {
      let settled = false;
      const settle = () => {
        if (!settled) { settled = true; resolve(streamId); }
      };
      const ws = new WebSocket(backtraceApi.stepStreamUrl(streamId));
      wsRef.current = ws;
      ws.onopen = settle;
      ws.onerror = settle;
      ws.onmessage = (e) => {
        const msg = JSON.parse(e.data);
        if (msg.kind === 'done') { ws.close(); return; }
        setStep(msg.kind);
      };
      setTimeout(settle, 800);
    });
  }, []);

  const endStream = useCallback(() => {
    wsRef.current?.close();
    wsRef.current = null;
    setStep(null);
  }, []);

  useEffect(() => () => wsRef.current?.close(), []);

  return { step, beginStream, endStream };
}

export default function BacktracePanel({
  chainId, projectId,
}: {
  chainId: string;
  projectId: string;
}) {
  const queryClient = useQueryClient();
  const [importedHopIds, setImportedHopIds] = useState<Set<string>>(new Set());
  const [importedCandidateIds, setImportedCandidateIds] = useState<Set<string>>(new Set());
  const [recheckMessage, setRecheckMessage] = useState<string | null>(null);
  const reuploadInputRef = useRef<HTMLInputElement>(null);
  const reuploadTargetRef = useRef<string | null>(null);

  const { data: detail } = useQuery({
    queryKey: ['backtrace-chain', chainId],
    queryFn: () => backtraceApi.get(chainId),
    refetchInterval: (q) =>
      q.state.data?.hops.some((h) => h.resolution_status === 'ingesting') ? 3000 : false,
  });

  const { data: originPaper } = useQuery({
    queryKey: ['paper', detail?.chain.origin_paper_id],
    queryFn: () => papersApi.get(detail!.chain.origin_paper_id),
    enabled: !!detail,
  });

  const invalidateChain = () => queryClient.invalidateQueries({ queryKey: ['backtrace-chain', chainId] });

  const { step: liveStep, beginStream, endStream } = useBacktraceStepStream();

  // ── Ranking the current frontier (the tail hop, or the origin if there
  // are no hops yet) — advisory, no hop created. Re-fires automatically
  // whenever the frontier moves to a new decidable hop.
  const [rankedCandidates, setRankedCandidates] = useState<BacktraceCandidate[] | null>(null);
  const [rankMessage, setRankMessage] = useState<string | null>(null);
  const [rankModel, setRankModel] = useState<string | null>(null);
  const [showOtherCandidates, setShowOtherCandidates] = useState(false);
  const [expandedCandidateId, setExpandedCandidateId] = useState<string | null>(null);
  const lastRankedKeyRef = useRef<string | null>(null);

  const rankMutation = useMutation({
    mutationFn: async (parentHopId: string | null) =>
      backtraceApi.rank(chainId, parentHopId, await beginStream()),
    onSettled: endStream,
    onSuccess: (result) => {
      setRankedCandidates(result.candidates);
      setRankMessage(result.message);
      setRankModel(result.model);
    },
  });

  const hops = detail?.hops ?? [];
  const last = hops[hops.length - 1] as BacktraceHop | undefined;
  const canDecide =
    !!last && last.user_decision === 'pending' &&
    (last.resolution_status === 'abstract_match' || last.resolution_status === 'full_text_match');
  const canStopOnly = !!last && last.user_decision === 'pending' && last.resolution_status === 'no_match';
  const stopped = last?.user_decision === 'stopped_here';

  // '__origin__' once loaded with zero hops, a hop id once its resolution
  // makes it decidable, or null when there's nothing to rank right now
  // (still resolving, a dead end, or already stopped).
  const frontierKey = !detail ? null : hops.length === 0 ? '__origin__' : canDecide ? last!.id : null;

  useEffect(() => {
    if (!frontierKey || frontierKey === lastRankedKeyRef.current) return;
    lastRankedKeyRef.current = frontierKey;
    setRankedCandidates(null);
    setRankMessage(null);
    setRankModel(null);
    setShowOtherCandidates(false);
    setExpandedCandidateId(null);
    rankMutation.mutate(frontierKey === '__origin__' ? null : frontierKey);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [frontierKey]);

  const commitMutation = useMutation({
    mutationFn: async (
      { parentHopId, candidate }: { parentHopId: string | null; candidate: BacktraceCandidate },
    ) =>
      backtraceApi.commitHop(
        chainId, parentHopId, candidate.openalex_id, candidate.reasoning, await beginStream(),
      ),
    onSettled: endStream,
    onSuccess: () => {
      setRankedCandidates(null);
      setRankMessage(null);
      setRankModel(null);
      setShowOtherCandidates(false);
      setExpandedCandidateId(null);
      invalidateChain();
    },
  });

  const stopHopMutation = useMutation({
    mutationFn: (hopId: string) => backtraceApi.stopHop(chainId, hopId),
    onSuccess: () => invalidateChain(),
  });

  const recheckMutation = useMutation({
    mutationFn: async (hopId: string) => backtraceApi.recheckHop(chainId, hopId, await beginStream()),
    onSettled: endStream,
    onSuccess: (result) => {
      setRecheckMessage(result.message);
      invalidateChain();
    },
  });

  const reuploadMutation = useMutation({
    mutationFn: ({ paperId, file }: { paperId: string; file: File }) => papersApi.reupload(paperId, file),
    onSuccess: () => invalidateChain(),
  });

  const importMutation = useMutation({
    mutationFn: (hop: BacktraceHop) =>
      papersApi.ingest(hop.to_doi!, projectId, {
        foundQuote: hop.quote ?? undefined,
        foundRelevanceNote: hop.relevance_note ?? undefined,
      }),
    onSuccess: (_paper, hop) => {
      queryClient.invalidateQueries({ queryKey: ['papers', projectId] });
      setImportedHopIds((prev) => new Set(prev).add(hop.id));
    },
  });

  const candidateImportMutation = useMutation({
    mutationFn: (c: BacktraceCandidate) => papersApi.ingest(c.doi!, projectId),
    onSuccess: (_paper, c) => {
      queryClient.invalidateQueries({ queryKey: ['papers', projectId] });
      setImportedCandidateIds((prev) => new Set(prev).add(c.openalex_id));
    },
  });

  const hopImportState = (hop: BacktraceHop): FindImportState => {
    const imported = !!hop.to_paper_id || importedHopIds.has(hop.id);
    return imported ? 'already_in_project' : hop.to_doi ? 'importable' : 'no_doi';
  };
  const importableHops = hops.filter((h) => hopImportState(h) === 'importable');

  const importAllMutation = useMutation({
    mutationFn: async () => {
      for (const h of importableHops) {
        await importMutation.mutateAsync(h);
      }
    },
  });

  const renderFrontierActions = (parentHopId: string | null) => (
    <div className={styles.paperActionsRow} style={{ flexWrap: 'wrap' }}>
      {rankMutation.isPending && !rankedCandidates ? (
        <BacktraceLoadingStep step={liveStep} />
      ) : rankMutation.isError ? (
        <>
          <div className={styles.formError}>
            {errorDetail(rankMutation.error, 'Could not rank candidates')}
          </div>
          <button
            type="button"
            className={styles.viewToggleBtn}
            onClick={() => useUIStore.getState().openAccountModal()}
          >
            Configure LLM
          </button>
          <button
            className={styles.retryBtn}
            onClick={() => rankMutation.mutate(parentHopId)}
            disabled={rankMutation.isPending}
          >
            Retry
          </button>
        </>
      ) : rankedCandidates && rankedCandidates.length > 0 ? (
        <>
          <button
            className={styles.retryBtn}
            onClick={() => commitMutation.mutate({ parentHopId, candidate: rankedCandidates[0] })}
            disabled={commitMutation.isPending}
          >
            {commitMutation.isPending
              ? 'Tracing…'
              : `Trace further (via ${
                  formatCitation(rankedCandidates[0].authors[0] ?? null, rankedCandidates[0].year)
                }) →`}
          </button>
          {rankedCandidates.length > 1 && (
            <button className={styles.retryBtn} onClick={() => setShowOtherCandidates((v) => !v)}>
              {showOtherCandidates
                ? 'Hide others'
                : `See ${rankedCandidates.length - 1} other candidate${rankedCandidates.length === 2 ? '' : 's'}`}
            </button>
          )}
          {parentHopId && (
            <button
              className={styles.retryBtn}
              onClick={() => stopHopMutation.mutate(parentHopId)}
              disabled={stopHopMutation.isPending}
            >
              Stop here ✓
            </button>
          )}
        </>
      ) : rankedCandidates ? (
        <>
          {rankMessage && <div className={styles.searchHint}>{rankMessage}</div>}
          {parentHopId && (
            <button
              className={styles.retryBtn}
              onClick={() => stopHopMutation.mutate(parentHopId)}
              disabled={stopHopMutation.isPending}
            >
              Stop here
            </button>
          )}
        </>
      ) : null}
      {commitMutation.isPending && <BacktraceLoadingStep step={liveStep} />}
      {commitMutation.isError && (
        <div className={styles.formError}>
          {errorDetail(commitMutation.error, 'Could not trace further')}
        </div>
      )}

      {showOtherCandidates && rankedCandidates && rankedCandidates.length > 1 && (
        <div className={styles.evidenceList} style={{ width: '100%' }}>
          {rankedCandidates.slice(1).map((c) => {
            const expanded = expandedCandidateId === c.openalex_id;
            const candidateImported = importedCandidateIds.has(c.openalex_id);
            const citation = formatCitation(c.authors[0] ?? null, c.year);
            return (
              <div key={c.openalex_id} className={styles.evidenceCard}>
                <button
                  className={styles.synthItem}
                  onClick={() => setExpandedCandidateId(expanded ? null : c.openalex_id)}
                >
                  <span className={styles.synthItemClaim}>
                    {c.title}{citation ? `, ${citation}` : ''}
                  </span>
                </button>
                {expanded && (
                  <div className={styles.candidateDetail}>
                    <div className={styles.panelMeta}>
                      {c.authors.join(', ') || 'Unknown authors'}{c.venue ? ` · ${c.venue}` : ''}
                    </div>
                    {c.doi && <div className={styles.paperDoi}>{c.doi}</div>}
                    {c.reasoning && (
                      rankModel
                        ? <AiNote text={c.reasoning} model={rankModel} label="Reasoning" />
                        : <div className={styles.searchHint}><strong>Reasoning:</strong> {c.reasoning}</div>
                    )}
                    <div className={styles.paperActionsRow}>
                      {c.doi && (
                        <a className={styles.visitBtn} href={`https://doi.org/${c.doi}`} target="_blank" rel="noreferrer">
                          visit
                        </a>
                      )}
                      <button
                        className={styles.retryBtn}
                        onClick={() => candidateImportMutation.mutate(c)}
                        disabled={!c.doi || candidateImported || candidateImportMutation.isPending}
                      >
                        {candidateImported ? '✓ imported' : '+ import'}
                      </button>
                      <button
                        className={styles.retryBtn}
                        onClick={() => commitMutation.mutate({ parentHopId, candidate: c })}
                        disabled={commitMutation.isPending}
                      >
                        {commitMutation.isPending ? 'Tracing…' : 'Continue backtrace →'}
                      </button>
                    </div>
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );

  if (!detail) return null;

  return (
    <div className={styles.panelContent}>
      <div className={styles.panelKind}>backtrace</div>
      <div className={styles.synthClaim}>{detail.chain.origin_text}</div>
      {originPaper && (
        <div className={styles.panelMeta}>from &ldquo;{trunc(originPaper.title, 60)}&rdquo;</div>
      )}
      {importableHops.length > 0 && (
        <div className={styles.paperActionsRow}>
          <button
            className={styles.retryBtn}
            onClick={() => importAllMutation.mutate()}
            disabled={importAllMutation.isPending}
          >
            {importAllMutation.isPending ? 'Importing…' : `Import all (${importableHops.length})`}
          </button>
        </div>
      )}

      {hops.length === 0 && renderFrontierActions(null)}

      <div className={styles.evidenceList}>
        {hops.map((hop, index) => {
          const isLast = hop.id === last?.id;
          const placeholderQuote =
            hop.resolution_status === 'ingesting' ? 'Fetching full text…'
            : hop.resolution_status === 'awaiting_manual_import' ? 'No open-access version found automatically.'
            : hop.resolution_status === 'no_match' ? 'No supporting text found in this paper.'
            : 'Resolving…';

          return (
            <div key={hop.id}>
              {index > 0 && <div className={styles.backtraceConnector}>↓ cites</div>}
              <FindResultCard
                title={hop.to_title}
                authors={hop.to_authors_json ?? []}
                year={hop.to_year}
                venue={hop.to_venue}
                visitUrl={hop.to_doi ? `https://doi.org/${hop.to_doi}` : null}
                quote={hop.quote ?? placeholderQuote}
                relevanceNote={hop.relevance_note}
                model={hop.model}
                importState={hopImportState(hop)}
                onImport={() => importMutation.mutate(hop)}
                importing={importMutation.isPending}
              />
              {hop.match_reasoning && (
                hop.model
                  ? <AiNote text={hop.match_reasoning} model={hop.model} label="Match reasoning" />
                  : <div className={styles.searchHint}><strong>Match reasoning:</strong> {hop.match_reasoning}</div>
              )}

              {isLast && hop.resolution_status === 'awaiting_manual_import' && (
                <div className={styles.ghostNote}>
                  <div>Add this paper&rsquo;s full text manually to continue tracing — upload a PDF, or add it to Zotero and sync, then check again.</div>
                  <div className={styles.paperActionsRow}>
                    <button
                      className={styles.retryBtn}
                      onClick={() => {
                        reuploadTargetRef.current = hop.to_paper_id;
                        reuploadInputRef.current?.click();
                      }}
                      disabled={reuploadMutation.isPending}
                    >
                      {reuploadMutation.isPending ? 'Uploading…' : 'Upload PDF'}
                    </button>
                    <button
                      className={styles.retryBtn}
                      onClick={() => recheckMutation.mutate(hop.id)}
                      disabled={recheckMutation.isPending}
                    >
                      {recheckMutation.isPending ? 'Checking…' : 'Check again'}
                    </button>
                  </div>
                  {recheckMessage && <div className={styles.searchHint}>{recheckMessage}</div>}
                  {recheckMutation.isPending && <BacktraceLoadingStep step={liveStep} />}
                </div>
              )}

              {isLast && canDecide && renderFrontierActions(hop.id)}

              {isLast && canStopOnly && (
                <div className={styles.paperActionsRow}>
                  <button
                    className={styles.retryBtn}
                    onClick={() => stopHopMutation.mutate(hop.id)}
                    disabled={stopHopMutation.isPending}
                  >
                    Stop here
                  </button>
                </div>
              )}
            </div>
          );
        })}
      </div>

      <input
        ref={reuploadInputRef}
        type="file"
        accept=".pdf,.txt,application/pdf,text/plain"
        style={{ display: 'none' }}
        onChange={(e) => {
          const f = e.target.files?.[0];
          const paperId = reuploadTargetRef.current;
          if (f && paperId) reuploadMutation.mutate({ paperId, file: f });
          e.target.value = '';
        }}
      />

      {stopped && (
        // A stopped hop is only a source if it actually found supporting
        // text. Stopping on a dead end must not read as reaching an origin,
        // and even a resolved stop is only the earliest point reachable with
        // the papers available, not a proven original.
        <div className={styles.searchHint}>
          {last?.resolution_status === 'abstract_match' || last?.resolution_status === 'full_text_match'
            ? '✓ Chain stopped here: the earliest source reachable given corpus coverage, not necessarily the original.'
            : 'Chain stopped at a dead end: no supporting text was found here, so this is not a source.'}
        </div>
      )}
    </div>
  );
}
