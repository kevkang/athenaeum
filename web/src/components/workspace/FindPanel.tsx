import { useState, useEffect, useRef, useCallback } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { statementsApi } from '../../api/statements';
import { papersApi } from '../../api/papers';
import { zoteroApi, zoteroFindApi, type ZoteroFindResult, type ZoteroFindCorpus } from '../../api/zotero';
import { zoteroLocalApi } from '../../api/zoteroLocal';
import { openAlexFindApi, type OpenAlexFindResult } from '../../api/openalexFind';
import { webSearchFindApi, type WebSearchFindResult } from '../../api/websearchFind';
import { synthesisApi, type Synthesis, type EvidenceFull } from '../../api/syntheses';
import { useUIStore } from '../../store/ui';
import { trunc } from '../../lib/format';
import styles from '../../pages/Workspace.module.css';
import FindResultCard, { type FindImportState } from './FindResultCard';

interface TraceEvent { kind: string; ord?: number; payload?: Record<string, unknown> }

function stepLabel(kind: string): string {
  const map: Record<string, string> = {
    query_interp: 'interpreting claim', retrieval: 'retrieving passages',
    extract: 'extracting evidence', signal: 'identifying signals', verify: 'verifying citations',
  };
  return map[kind] ?? kind;
}

function LiveTrace({ synthesisId, onDone }: { synthesisId: string; onDone: () => void }) {
  const [steps, setSteps] = useState<TraceEvent[]>([]);
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const ws = new WebSocket(synthesisApi.streamUrl(synthesisId));
    ws.onmessage = (e) => {
      const event: TraceEvent = JSON.parse(e.data);
      if (event.kind === 'done' || event.kind === 'failed') { onDone(); ws.close(); return; }
      setSteps((prev) => {
        if (event.ord !== undefined && prev.some((s) => s.ord === event.ord)) return prev;
        return [...prev, event];
      });
    };
    return () => { if (ws.readyState < 2) ws.close(); };
  }, [synthesisId, onDone]);

  useEffect(() => { bottomRef.current?.scrollIntoView({ behavior: 'smooth' }); }, [steps]);

  return (
    <div className={styles.liveTrace}>
      {steps.map((s, i) => (
        <div key={i} className={styles.traceStep}>
          <span className={styles.traceKind}>{stepLabel(s.kind)}</span>
          {s.payload && (
            <span className={styles.traceText}>
              {Object.entries(s.payload)
                .filter(([k]) => ['query', 'n_hits', 'n_passages', 'rate'].includes(k))
                .map(([k, v]) => `${k}=${JSON.stringify(v)}`).join(' ')}
            </span>
          )}
        </div>
      ))}
      <div ref={bottomRef} />
    </div>
  );
}

export default function FindPanel({ projectId, onStatementCreated }: { projectId: string; onStatementCreated: (id: string) => void }) {
  const queryClient = useQueryClient();
  const [query, setQuery] = useState('');
  const [corpus, setCorpus] = useState('project');
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [zoteroResults, setZoteroResults] = useState<ZoteroFindResult[] | null>(null);
  const [zoteroImportedKeys, setZoteroImportedKeys] = useState<Set<string>>(new Set());
  const [openAlexResults, setOpenAlexResults] = useState<OpenAlexFindResult[] | null>(null);
  const [openAlexImportedIds, setOpenAlexImportedIds] = useState<Set<string>>(new Set());
  const [webSearchResults, setWebSearchResults] = useState<WebSearchFindResult[] | null>(null);

  const isZoteroCorpus = corpus === 'zotero_sync' || corpus === 'zotero_local';
  const isOpenAlexCorpus = corpus === 'openalex';
  const isWebSearchCorpus = corpus === 'web_search';

  const { data: syntheses } = useQuery({
    queryKey: ['syntheses', projectId],
    queryFn: () => synthesisApi.listForProject(projectId),
    refetchInterval: (q) => q.state.data?.some((s: Synthesis) => s.status === 'running') ? 3000 : false,
  });

  const { data: activeSynth } = useQuery({
    queryKey: ['synthesis', selectedId],
    queryFn: () => synthesisApi.get(selectedId!),
    enabled: !!selectedId,
    refetchInterval: (q) => q.state.data?.status === 'running' ? 2000 : false,
  });

  const { data: evidence } = useQuery({
    queryKey: ['evidence-full', selectedId],
    queryFn: () => synthesisApi.evidenceFull(selectedId!),
    enabled: !!selectedId && activeSynth?.status === 'done',
  });

  const createMutation = useMutation({
    mutationFn: () => synthesisApi.create(query.trim(), projectId, corpus),
    onSuccess: (s) => {
      queryClient.invalidateQueries({ queryKey: ['syntheses', projectId] });
      setQuery('');
      setSelectedId(s.id);
    },
  });

  const zoteroFindMutation = useMutation({
    mutationFn: () => zoteroFindApi.find(query.trim(), projectId, corpus as ZoteroFindCorpus),
    onSuccess: (results) => setZoteroResults(results),
  });

  const zoteroImportMutation = useMutation({
    mutationFn: ({ itemKey, quote, note }: { itemKey: string; quote: string; note: string }) =>
      corpus === 'zotero_sync'
        ? zoteroApi.importItem(projectId, itemKey, { foundQuote: quote, foundRelevanceNote: note })
        : zoteroLocalApi.importItem(projectId, itemKey, { foundQuote: quote, foundRelevanceNote: note }),
    onSuccess: (_paper, { itemKey }) => {
      queryClient.invalidateQueries({ queryKey: ['papers', projectId] });
      setZoteroImportedKeys((prev) => new Set(prev).add(itemKey));
    },
  });

  const openAlexFindMutation = useMutation({
    mutationFn: () => openAlexFindApi.find(query.trim(), projectId),
    onSuccess: (results) => setOpenAlexResults(results),
  });

  const openAlexImportMutation = useMutation({
    mutationFn: ({ doi, quote, note }: { doi: string; quote: string; note: string }) =>
      papersApi.ingest(doi, projectId, { foundQuote: quote, foundRelevanceNote: note }),
    onSuccess: (_paper, { doi }) => {
      queryClient.invalidateQueries({ queryKey: ['papers', projectId] });
      setOpenAlexImportedIds((prev) => new Set(prev).add(doi));
    },
  });

  const webSearchFindMutation = useMutation({
    mutationFn: () => webSearchFindApi.find(query.trim(), projectId),
    onSuccess: (results) => setWebSearchResults(results),
  });

  const deleteMutation = useMutation({
    mutationFn: (id: string) => synthesisApi.delete(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['syntheses', projectId] });
      setSelectedId(null);
    },
  });

  const createFromEvidence = useMutation({
    mutationFn: async (ev: EvidenceFull) => {
      const statement = await statementsApi.create(projectId, 'claim', ev.verbatim_text, selectedId ?? undefined);
      await statementsApi.addEvidence(statement.id, 'passage', ev.passage_id, ev.role);
      return statement;
    },
    onSuccess: (statement) => {
      queryClient.invalidateQueries({ queryKey: ['graph', projectId] });
      queryClient.invalidateQueries({ predicate: (q) => q.queryKey[0] === 'evidence-sentences' });
      onStatementCreated(statement.id);
    },
  });

  const handleDone = useCallback(() => {
    queryClient.invalidateQueries({ queryKey: ['synthesis', selectedId] });
    queryClient.invalidateQueries({ queryKey: ['syntheses', projectId] });
    queryClient.invalidateQueries({ queryKey: ['evidence-full', selectedId] });
  }, [queryClient, selectedId, projectId]);

  const activeMutation = isZoteroCorpus
    ? zoteroFindMutation
    : isOpenAlexCorpus
      ? openAlexFindMutation
      : isWebSearchCorpus
        ? webSearchFindMutation
        : createMutation;

  const handleRun = () => activeMutation.mutate();
  const running = activeMutation.isPending;
  const runError = activeMutation.error;
  const isSearchCorpus = isZoteroCorpus || isOpenAlexCorpus || isWebSearchCorpus;

  return (
    <div className={styles.panelContent}>
      <div className={styles.panelKind}>find</div>

      <div className={styles.synthForm}>
        <textarea
          className={styles.synthClaimInput}
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="What are you searching for?"
          rows={3}
        />
        <div className={styles.synthFormRow}>
          <select
            className={styles.corpusSelect}
            value={corpus}
            onChange={(e) => {
              setCorpus(e.target.value);
              setZoteroResults(null);
              setOpenAlexResults(null);
              setWebSearchResults(null);
            }}
          >
            <option value="project">Papers in this project</option>
            <option value="global">Papers in all projects</option>
            <option value="zotero_sync">Zotero (sync)</option>
            <option value="zotero_local">Zotero (local)</option>
            <option value="openalex">OpenAlex</option>
            <option value="web_search">LLM knowledge search</option>
          </select>
          <button
            className={styles.runBtn}
            disabled={query.trim().length < 10 || running}
            onClick={handleRun}
          >
            {running && <span className={`${styles.statusDot} ${styles.processing}`} />}
            {running ? (isSearchCorpus ? 'Searching…' : 'Starting…') : 'Run'}
          </button>
        </div>
        <div className={styles.synthFormRow}>
          <button
            type="button"
            className={styles.viewToggleBtn}
            onClick={() => useUIStore.getState().openAccountModal()}
          >
            Configure LLM
          </button>
        </div>
        {runError && (
          <div className={styles.formError}>{(runError as Error).message}</div>
        )}
      </div>

      {isZoteroCorpus ? (
        zoteroResults && (
          <div className={styles.evidenceList}>
            <div className={styles.panelSectionLabel}>
              Results · {zoteroResults.length} paper{zoteroResults.length !== 1 ? 's' : ''}
            </div>
            {zoteroResults.length === 0 && (
              <div className={styles.searchHint}>No relevant papers found in your Zotero library.</div>
            )}
            {zoteroResults.map((r) => {
              const imported = r.already_in_project || zoteroImportedKeys.has(r.item_key);
              return (
                <FindResultCard
                  key={r.item_key}
                  title={r.title}
                  authors={r.creators}
                  year={r.year}
                  venue={r.venue}
                  visitUrl={r.doi ? `https://doi.org/${r.doi}` : null}
                  quote={r.quote}
                  relevanceNote={r.relevance_note}
                  model={r.model}
                  importState={imported ? 'already_in_project' : 'importable'}
                  importing={zoteroImportMutation.isPending}
                  onImport={() =>
                    zoteroImportMutation.mutate({ itemKey: r.item_key, quote: r.quote, note: r.relevance_note })
                  }
                />
              );
            })}
          </div>
        )
      ) : isOpenAlexCorpus ? (
        openAlexResults && (
          <div className={styles.evidenceList}>
            <div className={styles.panelSectionLabel}>
              Results · {openAlexResults.length} paper{openAlexResults.length !== 1 ? 's' : ''}
            </div>
            {openAlexResults.length === 0 && (
              <div className={styles.searchHint}>No relevant papers found on OpenAlex.</div>
            )}
            {openAlexResults.map((r) => {
              const imported = r.already_in_project || (!!r.doi && openAlexImportedIds.has(r.doi));
              const importState: FindImportState = !r.doi ? 'no_doi' : imported ? 'already_in_project' : 'importable';
              return (
                <FindResultCard
                  key={r.openalex_id ?? r.doi ?? r.title}
                  title={r.title}
                  authors={r.creators}
                  year={r.year}
                  venue={r.venue}
                  visitUrl={r.doi ? `https://doi.org/${r.doi}` : null}
                  quote={r.quote}
                  relevanceNote={r.relevance_note}
                  model={r.model}
                  importState={importState}
                  importing={openAlexImportMutation.isPending}
                  onImport={() =>
                    r.doi && openAlexImportMutation.mutate({ doi: r.doi, quote: r.quote, note: r.relevance_note })
                  }
                />
              );
            })}
          </div>
        )
      ) : isWebSearchCorpus ? (
        webSearchResults && (
          <div className={styles.evidenceList}>
            <div className={styles.panelSectionLabel}>
              Results · {webSearchResults.length} source{webSearchResults.length !== 1 ? 's' : ''}
            </div>
            {webSearchResults.length === 0 && (
              <div className={styles.searchHint}>No relevant sources found.</div>
            )}
            {webSearchResults.map((r) => (
              <FindResultCard
                key={r.url}
                title={r.title}
                authors={[]}
                year={null}
                venue={r.source}
                visitUrl={r.url}
                quote={r.quote}
                relevanceNote={r.relevance_note}
                model={r.model}
                importState="no_doi"
              />
            ))}
          </div>
        )
      ) : (
        <>
          {syntheses && syntheses.length > 0 && (
            <div className={styles.synthList}>
              <div className={styles.panelSectionLabel}>Past syntheses</div>
              {syntheses.map((s: Synthesis) => (
                <button
                  key={s.id}
                  className={`${styles.synthItem} ${s.id === selectedId ? styles.synthItemActive : ''}`}
                  onClick={() => setSelectedId(s.id === selectedId ? null : s.id)}
                >
                  <span className={`${styles.statusDot} ${styles[s.status]}`} />
                  <span className={styles.synthItemClaim}>{trunc(s.claim_text, 60)}</span>
                </button>
              ))}
            </div>
          )}

          {activeSynth && (
            <div className={styles.synthDetail}>
              <div className={styles.synthDetailHeader}>
                <span className={`${styles.statusDot} ${styles[activeSynth.status]}`} />
                <span className={styles.synthStatus}>
                  {activeSynth.status === 'running' && 'Running…'}
                  {activeSynth.status === 'done' && 'Done'}
                  {activeSynth.status === 'failed' && 'Failed'}
                </span>
                {activeSynth.verification_rate !== null && (
                  <span className={styles.vrBadge}>
                    {(activeSynth.verification_rate * 100).toFixed(0)}% verified
                  </span>
                )}
                <button
                  className={styles.deleteBtn}
                  onClick={() => deleteMutation.mutate(activeSynth.id)}
                  disabled={deleteMutation.isPending}
                >×</button>
              </div>

              <div className={styles.synthClaim}>{activeSynth.claim_text}</div>

              {activeSynth.status === 'running' && (
                <LiveTrace synthesisId={activeSynth.id} onDone={handleDone} />
              )}

              {evidence && evidence.length > 0 && (
                <div className={styles.evidenceList}>
                  <div className={styles.panelSectionLabel}>
                    Evidence · {evidence.length} passage{evidence.length !== 1 ? 's' : ''}
                  </div>
                  {evidence.map((ev: EvidenceFull) => (
                    <FindResultCard
                      key={ev.passage_id}
                      title={ev.paper_title}
                      authors={ev.paper_first_author ? [ev.paper_first_author] : []}
                      year={ev.paper_year}
                      venue={ev.paper_venue}
                      visitUrl={ev.paper_doi ? `https://doi.org/${ev.paper_doi}` : null}
                      quote={ev.verbatim_text}
                      relevanceNote={ev.rationale}
                      model={activeSynth.model}
                      roleLabel={ev.role}
                      verified={ev.verified}
                      importState="already_in_project"
                      onPromote={() => createFromEvidence.mutate(ev)}
                      promoting={createFromEvidence.isPending}
                    />
                  ))}
                </div>
              )}
            </div>
          )}
        </>
      )}
    </div>
  );
}
