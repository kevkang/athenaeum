import { useState, useEffect, useRef, useCallback } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import {
  backtraceApi,
  type BacktraceCandidate,
  type BacktraceHop,
  type BacktraceStepResult,
} from '../../api/backtrace';
import { papersApi } from '../../api/papers';
import { trunc, formatCitation } from '../../lib/format';
import styles from '../../pages/Workspace.module.css';
import FindResultCard, { AiNote, type FindImportState } from './FindResultCard';

// Backtrace hop resolution runs synchronously inside its request handler
// (unlike synthesis's arq job), so there's no persisted step log to poll —
// instead each mutating call streams ephemeral progress pings over a
// short-lived websocket keyed by a client-minted stream_id. See
// api/backtrace.py's module docstring for the protocol.

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
  chainId, initialResult, projectId,
}: {
  chainId: string;
  initialResult: BacktraceStepResult;
  projectId: string;
}) {
  const queryClient = useQueryClient();
  const [manualCandidates, setManualCandidates] = useState<BacktraceCandidate[] | null>(initialResult.candidates);
  const [stepMessage, setStepMessage] = useState<string | null>(initialResult.message);
  const [stepMessageModel, setStepMessageModel] = useState<string | null>(initialResult.model);
  const [expandedCandidate, setExpandedCandidate] = useState<string | null>(null);
  const [importedHopIds, setImportedHopIds] = useState<Set<string>>(new Set());
  const [importedCandidateIds, setImportedCandidateIds] = useState<Set<string>>(new Set());
  const reuploadInputRef = useRef<HTMLInputElement>(null);
  const reuploadTargetRef = useRef<string | null>(null);

  const { data: detail } = useQuery({
    queryKey: ['backtrace-chain', chainId],
    queryFn: () => backtraceApi.get(chainId),
    initialData: { chain: initialResult.chain, hops: initialResult.hop ? [initialResult.hop] : [] },
    refetchInterval: (q) =>
      q.state.data?.hops.some((h) => h.resolution_status === 'ingesting') ? 3000 : false,
  });

  const { data: originPaper } = useQuery({
    queryKey: ['paper', detail.chain.origin_paper_id],
    queryFn: () => papersApi.get(detail.chain.origin_paper_id),
  });

  const invalidateChain = () => queryClient.invalidateQueries({ queryKey: ['backtrace-chain', chainId] });

  const { step: liveStep, beginStream, endStream } = useBacktraceStepStream();

  const continueMutation = useMutation({
    mutationFn: async () => backtraceApi.continueChain(chainId, await beginStream()),
    onSettled: endStream,
    onSuccess: (result) => {
      setManualCandidates(result.candidates);
      setStepMessage(result.message);
      setStepMessageModel(result.model);
      invalidateChain();
    },
  });

  const pickMutation = useMutation({
    mutationFn: async (openalexId: string) => backtraceApi.pick(chainId, openalexId, await beginStream()),
    onSettled: endStream,
    onSuccess: (result) => {
      setManualCandidates(result.candidates);
      setStepMessage(result.message);
      setStepMessageModel(result.model);
      setExpandedCandidate(null);
      invalidateChain();
    },
  });

  const stopMutation = useMutation({
    mutationFn: () => backtraceApi.stop(chainId),
    onSuccess: () => invalidateChain(),
  });

  const recheckMutation = useMutation({
    mutationFn: async (hopId: string) => backtraceApi.recheckHop(chainId, hopId, await beginStream()),
    onSettled: endStream,
    onSuccess: (result) => {
      setStepMessage(result.message);
      setStepMessageModel(result.model);
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

  const hops = detail.hops;
  const last = hops[hops.length - 1] as BacktraceHop | undefined;
  const canDecide =
    !!last && last.user_decision === 'pending' &&
    (last.resolution_status === 'abstract_match' || last.resolution_status === 'full_text_match');
  const canStopOnly = !!last && last.user_decision === 'pending' && last.resolution_status === 'no_match';
  const stopped = last?.user_decision === 'stopped_here';

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
                  {recheckMutation.isPending && <BacktraceLoadingStep step={liveStep} />}
                </div>
              )}

              {isLast && canDecide && (
                <div className={styles.paperActionsRow}>
                  <button
                    className={styles.retryBtn}
                    onClick={() => continueMutation.mutate()}
                    disabled={continueMutation.isPending}
                  >
                    {continueMutation.isPending ? 'Tracing…' : 'Trace further →'}
                  </button>
                  <button className={styles.retryBtn} onClick={() => stopMutation.mutate()} disabled={stopMutation.isPending}>
                    Stop here ✓
                  </button>
                  {continueMutation.isPending && <BacktraceLoadingStep step={liveStep} />}
                </div>
              )}

              {isLast && canStopOnly && (
                <div className={styles.paperActionsRow}>
                  <button className={styles.retryBtn} onClick={() => stopMutation.mutate()} disabled={stopMutation.isPending}>
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
        <div className={styles.searchHint}>✓ Chain stopped — this is marked as the source.</div>
      )}

      {manualCandidates && (
        <div className={styles.evidenceList}>
          <div className={styles.panelSectionLabel}>Pick a reference</div>
          {stepMessage && (
            stepMessageModel
              ? <AiNote text={stepMessage} model={stepMessageModel} label="Reasoning" />
              : <div className={styles.searchHint}><strong>Reasoning:</strong> {stepMessage}</div>
          )}
          {manualCandidates.length === 0 ? (
            <div className={styles.searchHint}>No unvisited references left to choose from.</div>
          ) : (
            manualCandidates.map((c) => {
              const expanded = expandedCandidate === c.openalex_id;
              const candidateImported = importedCandidateIds.has(c.openalex_id);
              const citation = formatCitation(c.authors[0] ?? null, c.year);
              return (
                <div key={c.openalex_id} className={styles.evidenceCard}>
                  <button
                    className={styles.synthItem}
                    onClick={() => setExpandedCandidate(expanded ? null : c.openalex_id)}
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
                          onClick={() => pickMutation.mutate(c.openalex_id)}
                          disabled={pickMutation.isPending}
                        >
                          {pickMutation.isPending ? 'Tracing…' : 'Continue backtrace →'}
                        </button>
                      </div>
                      {pickMutation.isPending && expanded && <BacktraceLoadingStep step={liveStep} />}
                    </div>
                  )}
                </div>
              );
            })
          )}
        </div>
      )}

      {!manualCandidates && stepMessage && hops.length === 0 && (
        <div className={styles.searchHint}>{stepMessage}</div>
      )}
    </div>
  );
}
