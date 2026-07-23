import { useState, useEffect, useRef, useCallback, useMemo } from 'react';
import { useParams } from '@tanstack/react-router';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import cytoscape from 'cytoscape';
import dagre from 'cytoscape-dagre';
import { api } from '../api/client';
import {
  statementsApi,
  type Statement,
  type StatementNode,
  type StatementEdge,
  type StatementRelation,
  type Evidence,
} from '../api/statements';
import { papersApi, type Paper, type Section, type Sentence, type OpenAlexResult, type SentenceEvidence, firstAuthor } from '../api/papers';
import { zoteroApi, zoteroFindApi, type ZoteroSearchResult, type ZoteroFindResult, type ZoteroFindCorpus } from '../api/zotero';
import { zoteroLocalApi } from '../api/zoteroLocal';
import { openAlexFindApi, type OpenAlexFindResult } from '../api/openalexFind';
import { webSearchFindApi, type WebSearchFindResult } from '../api/websearchFind';
import {
  backtraceApi,
  type BacktraceCandidate,
  type BacktraceHop,
  type BacktraceStepResult,
} from '../api/backtrace';
import { passagesApi } from '../api/passages';
import ZoteroBrowserModal from '../components/ZoteroBrowserModal';
import ZoteroBrowserImportModal from '../components/ZoteroBrowserImportModal';
import LocalLibraryPicker from '../components/LocalLibraryPicker';
import { synthesisApi, type Synthesis, type EvidenceFull } from '../api/syntheses';
import { useUIStore } from '../store/ui';
import { trunc, stripHtml, safeHtml, formatDate, formatCitation, formatModelName, typeClass } from '../lib/format';
import styles from './Workspace.module.css';

cytoscape.use(dagre);

// ── Types ─────────────────────────────────────────────────────────────────────

interface StatementGraph { nodes: StatementNode[]; edges: StatementEdge[] }

type PanelMode = null | { kind: 'statement'; id: string } | { kind: 'new' };
type NodeFilter = 'all' | 'statements' | 'questions';

const RELATIONS: StatementRelation[] = ['relates_to', 'supports', 'tension'];
// W/E/R hold+drag-between-nodes shortcuts, per spec (not mnemonic).
const RELATION_KEYS: Record<string, StatementRelation> = { w: 'tension', e: 'relates_to', r: 'supports' };
const DROPPABLE_SUFFIXES = ['.pdf', '.txt'];
const PASSAGE_MIME = 'application/x-athenaeum-passage';
const READER_MIN_WIDTH = 320;
const READER_MAX_WIDTH = 900;
const READER_DEFAULT_WIDTH = 460;

function relationLabel(r: StatementRelation): string {
  return r === 'relates_to' ? 'relates to' : r === 'supports' ? 'supports' : 'in tension with';
}

function isProcessing(p: Paper) {
  return p.status === 'ghost' && Date.now() - new Date(p.created_at).getTime() < 10 * 60 * 1000;
}

function isDroppableFile(file: File): boolean {
  const name = file.name.toLowerCase();
  return DROPPABLE_SUFFIXES.some((suf) => name.endsWith(suf));
}

interface UploadQueueItem {
  id: string;
  name: string;
  status: 'pending' | 'uploading' | 'done' | 'error';
  error?: string;
}

// ── Canvas element builder ────────────────────────────────────────────────────

function buildElements(graph: StatementGraph, nodeFilter: NodeFilter) {
  const visible = graph.nodes.filter((n) => {
    if (nodeFilter === 'questions') return n.type === 'question';
    if (nodeFilter === 'statements') return n.type !== 'question';
    return true;
  });
  const visibleIds = new Set(visible.map((n) => n.id));

  return [
    ...visible.map((n) => ({
      data: {
        id: n.id, kind: 'statement', label: trunc(n.assertion, 62), type: n.type, status: n.status,
        evidence_count: n.evidence_count,
      },
    })),
    ...graph.edges
      .filter((e) => visibleIds.has(e.from_statement_id) && visibleIds.has(e.to_statement_id))
      .map((e) => ({
        data: {
          id: `edge-${e.from_statement_id}-${e.to_statement_id}`,
          source: e.from_statement_id,
          target: e.to_statement_id,
          kind: e.relation,
        },
      })),
  ];
}

const CY_STYLE: cytoscape.StylesheetStyle[] = [
  {
    selector: 'node',
    style: {
      label: 'data(label)', color: '#e6e1d6', 'font-family': 'Inter, sans-serif',
      'font-size': '10.5px', 'text-wrap': 'wrap', 'text-valign': 'center',
      'text-halign': 'center', padding: '8px',
    },
  },
  {
    selector: 'node[kind = "statement"]',
    style: {
      shape: 'roundrectangle', width: '180px', height: '52px',
      'background-color': '#1f1d18', 'border-width': 1, 'border-color': '#8a7a5c',
      'font-style': 'italic', 'text-max-width': '164px',
    },
  },
  {
    selector: 'node[kind = "statement"][status = "locked"]',
    style: { 'background-color': '#1a221a', 'border-color': '#8aa57c', 'border-width': 1.5 },
  },
  {
    selector: 'node[kind = "statement"][type = "question"]',
    style: {
      'background-color': '#1a1a26', 'border-color': '#5c6b8a', 'font-style': 'normal',
    },
  },
  {
    selector: 'node[kind = "statement"][type = "question"][status = "resolved"]',
    style: { 'border-color': '#5a8a6a', 'background-color': '#1a231a' },
  },
  {
    selector: 'node:selected',
    style: { 'background-color': '#2a2a1e', 'border-color': '#c9a876', 'border-width': 2.5 },
  },
  {
    selector: 'node.edge-drag-source',
    style: { 'border-color': '#c9a876', 'border-width': 3, 'border-style': 'dashed' },
  },
  {
    // relates_to: neutral / white
    selector: 'edge[kind = "relates_to"]',
    style: {
      width: 1.5, 'line-color': '#a8a29a', 'target-arrow-color': '#a8a29a',
      'target-arrow-shape': 'triangle', 'curve-style': 'bezier', 'arrow-scale': 0.9,
    },
  },
  {
    // supports: positive / green
    selector: 'edge[kind = "supports"]',
    style: {
      width: 1.5, 'line-color': '#8aa57c', 'target-arrow-color': '#8aa57c',
      'target-arrow-shape': 'triangle', 'curve-style': 'bezier', 'arrow-scale': 0.9,
    },
  },
  {
    // tension: negative / red
    selector: 'edge[kind = "tension"]',
    style: {
      width: 1.5, 'line-style': 'dashed', 'line-color': '#c67a5c', 'target-arrow-color': '#c67a5c',
      'target-arrow-shape': 'triangle', 'curve-style': 'bezier', 'arrow-scale': 0.9,
    },
  },
  {
    selector: 'edge:selected',
    style: { 'line-color': '#c9a876', 'target-arrow-color': '#c9a876' },
  },
];

// ── Papers sidebar (far left) ───────────────────────────────────────────────────

function PapersSidebar({
  projectId, papers, papersLoading, selectedPaperId, onSelectPaper, isDragOver, uploadQueue, onClearUploadQueue,
}: {
  projectId: string;
  papers: Paper[];
  papersLoading: boolean;
  selectedPaperId: string | null;
  onSelectPaper: (id: string | null) => void;
  isDragOver: boolean;
  uploadQueue: UploadQueueItem[];
  onClearUploadQueue: () => void;
}) {
  const queryClient = useQueryClient();
  const openAccountModal = useUIStore((s) => s.openAccountModal);
  const [addOpen, setAddOpen] = useState(false);
  const [addMode, setAddMode] = useState<'doi' | 'search' | 'upload' | 'zotero' | 'zotero-local'>('doi');
  const [doi, setDoi] = useState('');
  const [searchQuery, setSearchQuery] = useState('');
  const [debouncedQuery, setDebouncedQuery] = useState('');
  const [zoteroQuery, setZoteroQuery] = useState('');
  const [debouncedZoteroQuery, setDebouncedZoteroQuery] = useState('');
  const [zoteroLocalQuery, setZoteroLocalQuery] = useState('');
  const [debouncedZoteroLocalQuery, setDebouncedZoteroLocalQuery] = useState('');
  const [zoteroBrowser, setZoteroBrowser] = useState<'sync' | 'local' | null>(null);
  const [uploadFile, setUploadFile] = useState<File | null>(null);
  const [uploadTitle, setUploadTitle] = useState('');
  const uploadInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    const t = setTimeout(() => setDebouncedQuery(searchQuery.trim()), 350);
    return () => clearTimeout(t);
  }, [searchQuery]);

  useEffect(() => {
    const t = setTimeout(() => setDebouncedZoteroQuery(zoteroQuery.trim()), 350);
    return () => clearTimeout(t);
  }, [zoteroQuery]);

  useEffect(() => {
    const t = setTimeout(() => setDebouncedZoteroLocalQuery(zoteroLocalQuery.trim()), 350);
    return () => clearTimeout(t);
  }, [zoteroLocalQuery]);

  const ingestMutation = useMutation({
    mutationFn: () => papersApi.ingest(doi.trim(), projectId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['papers', projectId] });
      setDoi('');
      setAddOpen(false);
    },
  });

  const addFromSearchMutation = useMutation({
    mutationFn: (d: string) => papersApi.ingest(d, projectId),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['papers', projectId] }),
  });

  const uploadMutation = useMutation({
    mutationFn: () => {
      if (!uploadFile) throw new Error('No file selected');
      return papersApi.upload(uploadFile, projectId, {
        title: uploadTitle.trim() || undefined,
      });
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['papers', projectId] });
      setUploadFile(null);
      setUploadTitle('');
      if (uploadInputRef.current) uploadInputRef.current.value = '';
      setAddOpen(false);
    },
  });

  const { data: searchResults, isFetching: searchFetching } = useQuery({
    queryKey: ['openalex-search', debouncedQuery],
    queryFn: () => papersApi.searchOpenalex(debouncedQuery),
    enabled: debouncedQuery.length >= 3,
    staleTime: 60_000,
  });

  const { data: zoteroStatus } = useQuery({
    queryKey: ['zotero-status'],
    queryFn: () => zoteroApi.getStatus(),
    enabled: addMode === 'zotero',
  });

  const { data: zoteroResults, isFetching: zoteroFetching } = useQuery({
    queryKey: ['zotero-search', projectId, debouncedZoteroQuery],
    queryFn: () => zoteroApi.search(debouncedZoteroQuery, projectId),
    enabled: addMode === 'zotero' && !!zoteroStatus?.connected && debouncedZoteroQuery.length >= 3,
    staleTime: 30_000,
  });

  const importFromZoteroMutation = useMutation({
    mutationFn: (itemKey: string) => zoteroApi.importItem(projectId, itemKey),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['papers', projectId] }),
  });

  const { data: zoteroLocalStatus } = useQuery({
    queryKey: ['zotero-local-status'],
    queryFn: () => zoteroLocalApi.getStatus(),
    enabled: addMode === 'zotero-local',
  });

  const [showLibraryPicker, setShowLibraryPicker] = useState(false);
  const [showBrowserImport, setShowBrowserImport] = useState(false);
  const selectLibraryMutation = useMutation({
    mutationFn: (relativePath: string) => zoteroLocalApi.selectLibrary(relativePath),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['zotero-local-status'] });
      setShowLibraryPicker(false);
    },
  });
  const clearLibraryMutation = useMutation({
    mutationFn: () => zoteroLocalApi.clearLibrary(),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['zotero-local-status'] }),
  });

  const { data: zoteroLocalResults, isFetching: zoteroLocalFetching } = useQuery({
    queryKey: ['zotero-local-search', projectId, debouncedZoteroLocalQuery],
    queryFn: () => zoteroLocalApi.search(debouncedZoteroLocalQuery, projectId),
    enabled: addMode === 'zotero-local' && !!zoteroLocalStatus?.available && debouncedZoteroLocalQuery.length >= 3,
    staleTime: 30_000,
  });

  const importFromZoteroLocalMutation = useMutation({
    mutationFn: (itemKey: string) => zoteroLocalApi.importItem(projectId, itemKey),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['papers', projectId] }),
  });

  const deletePaperMutation = useMutation({
    mutationFn: (paperId: string) => papersApi.delete(paperId, projectId),
    onSuccess: (_data, paperId) => {
      queryClient.invalidateQueries({ queryKey: ['papers', projectId] });
      if (paperId === selectedPaperId) onSelectPaper(null);
    },
  });

  const alreadyInProject = new Set(papers.map((p) => p.doi).filter(Boolean) as string[]);

  return (
    <aside
      className={styles.sidebar}
      style={isDragOver ? { outline: '2px dashed var(--accent)', outlineOffset: -4 } : undefined}
    >
      {isDragOver && (
        <div className={styles.emptyHint} style={{ padding: '10px 12px', marginBottom: 8 }}>
          Drop PDFs here to add them to this project
        </div>
      )}

      {uploadQueue.length > 0 && (
        <div className={styles.sidebarSection}>
          <div className={styles.sidebarSectionHeader}>
            <span>Uploads</span>
            <button className={styles.sidebarAddBtn} onClick={onClearUploadQueue}>
              ×
            </button>
          </div>
          <div className={styles.paperList}>
            {uploadQueue.map((item) => (
              <div key={item.id} className={styles.paperItem} style={{ cursor: 'default' }}>
                <span
                  className={`${styles.statusDot} ${
                    item.status === 'done' ? styles.full_text
                    : item.status === 'error' ? styles.failed
                    : styles.processing
                  }`}
                />
                <span className={styles.paperTitle} title={item.error}>{trunc(item.name, 40)}</span>
                {item.status === 'uploading' && <span className={styles.processingLabel}>uploading</span>}
                {item.status === 'error' && <span className={styles.processingLabel}>{item.error ?? 'error'}</span>}
              </div>
            ))}
          </div>
        </div>
      )}

      <div className={styles.sidebarSection}>
        <div className={styles.sidebarSectionHeader}>
          <span>Papers</span>
          <span className={styles.sidebarCount}>{papers.length}</span>
          <button className={styles.sidebarAddBtn} onClick={() => setAddOpen((v) => !v)}>
            {addOpen ? '×' : '+'}
          </button>
        </div>

        {addOpen && (
          <div className={styles.addPaperForm}>
            <div className={styles.addModeTabs}>
              <button
                className={`${styles.addModeTab} ${addMode === 'doi' ? styles.addModeTabActive : ''}`}
                onClick={() => setAddMode('doi')}
              >DOI</button>
              <button
                className={`${styles.addModeTab} ${addMode === 'search' ? styles.addModeTabActive : ''}`}
                onClick={() => setAddMode('search')}
              >Search</button>
              <button
                className={`${styles.addModeTab} ${addMode === 'upload' ? styles.addModeTabActive : ''}`}
                onClick={() => setAddMode('upload')}
              >Upload</button>
              <button
                className={`${styles.addModeTab} ${addMode === 'zotero' ? styles.addModeTabActive : ''}`}
                onClick={() => setAddMode('zotero')}
              >Zotero (Sync)</button>
              <button
                className={`${styles.addModeTab} ${addMode === 'zotero-local' ? styles.addModeTabActive : ''}`}
                onClick={() => setAddMode('zotero-local')}
              >Zotero (Local)</button>
            </div>

            {addMode === 'doi' && (
              <>
                <input
                  className={styles.doiInput}
                  value={doi}
                  onChange={(e) => setDoi(e.target.value)}
                  placeholder="DOI  e.g. 10.xxxx/xxxx"
                  onKeyDown={(e) => e.key === 'Enter' && doi.trim() && ingestMutation.mutate()}
                />
                <button
                  className={styles.ingestBtn}
                  disabled={!doi.trim() || ingestMutation.isPending}
                  onClick={() => ingestMutation.mutate()}
                >
                  {ingestMutation.isPending ? '…' : 'Add'}
                </button>
                {ingestMutation.error && (
                  <div className={styles.formError}>{String((ingestMutation.error as Error).message)}</div>
                )}
              </>
            )}

            {addMode === 'search' && (
              <>
                <input
                  className={styles.doiInput}
                  value={searchQuery}
                  onChange={(e) => setSearchQuery(e.target.value)}
                  placeholder="Title, topic, or keywords…"
                  autoFocus
                />
                {searchFetching && <div className={styles.searchHint}>Searching…</div>}
                {searchResults && searchResults.length === 0 && !searchFetching && debouncedQuery.length >= 3 && (
                  <div className={styles.searchHint}>No results</div>
                )}
                {searchResults && searchResults.length > 0 && (
                  <div className={styles.searchResults}>
                    {searchResults.map((r: OpenAlexResult) => {
                      const inProject = r.doi ? alreadyInProject.has(r.doi) : false;
                      return (
                        <div key={r.openalex_id ?? r.doi ?? r.title} className={styles.searchResultItem}>
                          <div className={styles.searchResultTitle} dangerouslySetInnerHTML={safeHtml(r.title)} />
                          <div className={styles.searchResultMeta}>
                            {[r.first_author && `${r.first_author} et al.`, r.year, r.venue].filter(Boolean).join(' · ')}
                            {r.cited_by_count > 0 && <span className={styles.citedBy}> · {r.cited_by_count.toLocaleString()} citations</span>}
                          </div>
                          <button
                            className={styles.searchResultAddBtn}
                            disabled={!r.doi || inProject || addFromSearchMutation.isPending}
                            title={!r.doi ? 'No DOI available' : inProject ? 'Already in project' : 'Add to project'}
                            onClick={() => r.doi && addFromSearchMutation.mutate(r.doi)}
                          >
                            {inProject ? '✓' : '+'}
                          </button>
                        </div>
                      );
                    })}
                  </div>
                )}
              </>
            )}

            {addMode === 'upload' && (
              <>
                <input
                  ref={uploadInputRef}
                  type="file"
                  accept=".pdf,.txt,application/pdf,text/plain"
                  className={styles.uploadFileInput}
                  onChange={(e) => setUploadFile(e.target.files?.[0] ?? null)}
                />
                <input
                  className={styles.doiInput}
                  value={uploadTitle}
                  onChange={(e) => setUploadTitle(e.target.value)}
                  placeholder="Title (optional)"
                />
                <button
                  className={styles.ingestBtn}
                  disabled={!uploadFile || uploadMutation.isPending}
                  onClick={() => uploadMutation.mutate()}
                >
                  {uploadMutation.isPending ? 'Uploading…' : 'Upload'}
                </button>
                {uploadMutation.error && (
                  <div className={styles.formError}>{String((uploadMutation.error as Error).message)}</div>
                )}
                <div className={styles.searchHint}>Tip: you can also drag PDFs anywhere onto this page.</div>
              </>
            )}

            {addMode === 'zotero' && (
              <>
                {!zoteroStatus?.connected ? (
                  <div className={styles.searchHint}>
                    Connect your Zotero library to search it here.{' '}
                    <button className={styles.zoteroConnectLink} onClick={() => openAccountModal()}>
                      Connect Zotero
                    </button>
                  </div>
                ) : (
                  <>
                    <input
                      className={styles.doiInput}
                      value={zoteroQuery}
                      onChange={(e) => setZoteroQuery(e.target.value)}
                      placeholder="Author, title, or keywords…"
                      autoFocus
                    />
                    <div className={styles.searchHint}>
                      <button className={styles.zoteroConnectLink} onClick={() => setZoteroBrowser('sync')}>
                        Browse full library →
                      </button>
                    </div>
                    {zoteroFetching && <div className={styles.searchHint}>Searching…</div>}
                    {zoteroResults && zoteroResults.length === 0 && !zoteroFetching && debouncedZoteroQuery.length >= 3 && (
                      <div className={styles.searchHint}>No results</div>
                    )}
                    {zoteroResults && zoteroResults.length > 0 && (
                      <div className={styles.searchResults}>
                        {zoteroResults.map((r: ZoteroSearchResult) => (
                          <div key={r.item_key} className={styles.searchResultItem}>
                            <div className={styles.searchResultTitle}>{r.title}</div>
                            <div className={styles.searchResultMeta}>
                              {[r.creators[0], r.year, r.venue].filter(Boolean).join(' · ')}
                            </div>
                            <button
                              className={styles.searchResultAddBtn}
                              disabled={r.already_in_project || importFromZoteroMutation.isPending}
                              title={r.already_in_project ? 'Already in project' : 'Import from Zotero'}
                              onClick={() => importFromZoteroMutation.mutate(r.item_key)}
                            >
                              {r.already_in_project ? '✓' : '+'}
                            </button>
                          </div>
                        ))}
                      </div>
                    )}
                    {importFromZoteroMutation.error && (
                      <div className={styles.formError}>
                        {String(
                          (importFromZoteroMutation.error as { response?: { data?: { detail?: string } } })
                            ?.response?.data?.detail ?? 'Import failed',
                        )}
                      </div>
                    )}
                  </>
                )}
              </>
            )}

            {addMode === 'zotero-local' && (
              <>
                <div className={styles.searchHint} style={{ marginBottom: 8 }}>
                  <button className={styles.ingestBtn} onClick={() => setShowBrowserImport(true)}>
                    Import from Zotero folder…
                  </button>
                </div>
                {!zoteroLocalStatus?.available ? (
                  <div className={styles.zoteroLocalSetup}>
                    <div className={styles.searchHint}>
                      That reads your Zotero folder right here in the browser — nothing to
                      configure server-side. (If Athenaeum happens to be running on the same
                      machine as Zotero, you can alternatively point the server at your Zotero
                      data directory directly: Preferences → Advanced → "Show Data Directory".)
                    </div>
                    <button
                      className={styles.ingestBtn}
                      onClick={() => setShowLibraryPicker(true)}
                    >
                      Select local library…
                    </button>
                  </div>
                ) : (
                  <>
                    <div className={styles.searchHint}>
                      Connected: <code>{zoteroLocalStatus.relative_path}</code>{' '}
                      <button
                        className={styles.zoteroConnectLink}
                        onClick={() => clearLibraryMutation.mutate()}
                        disabled={clearLibraryMutation.isPending}
                      >
                        Disconnect
                      </button>
                    </div>
                    <input
                      className={styles.doiInput}
                      value={zoteroLocalQuery}
                      onChange={(e) => setZoteroLocalQuery(e.target.value)}
                      placeholder="Author, title, or keywords…"
                      autoFocus
                    />
                    <div className={styles.searchHint}>
                      <button className={styles.zoteroConnectLink} onClick={() => setZoteroBrowser('local')}>
                        Browse full library →
                      </button>
                    </div>
                    {zoteroLocalFetching && <div className={styles.searchHint}>Searching…</div>}
                    {zoteroLocalResults && zoteroLocalResults.length === 0 && !zoteroLocalFetching
                      && debouncedZoteroLocalQuery.length >= 3 && (
                      <div className={styles.searchHint}>No results</div>
                    )}
                    {zoteroLocalResults && zoteroLocalResults.length > 0 && (
                      <div className={styles.searchResults}>
                        {zoteroLocalResults.map((r: ZoteroSearchResult) => (
                          <div key={r.item_key} className={styles.searchResultItem}>
                            <div className={styles.searchResultTitle}>{r.title}</div>
                            <div className={styles.searchResultMeta}>
                              {[r.creators[0], r.year, r.venue].filter(Boolean).join(' · ')}
                            </div>
                            <button
                              className={styles.searchResultAddBtn}
                              disabled={r.already_in_project || importFromZoteroLocalMutation.isPending}
                              title={r.already_in_project ? 'Already in project' : 'Import from local Zotero library'}
                              onClick={() => importFromZoteroLocalMutation.mutate(r.item_key)}
                            >
                              {r.already_in_project ? '✓' : '+'}
                            </button>
                          </div>
                        ))}
                      </div>
                    )}
                    {importFromZoteroLocalMutation.error && (
                      <div className={styles.formError}>
                        {String(
                          (importFromZoteroLocalMutation.error as { response?: { data?: { detail?: string } } })
                            ?.response?.data?.detail ?? 'Import failed',
                        )}
                      </div>
                    )}
                  </>
                )}
              </>
            )}
          </div>
        )}

        <div className={styles.paperList}>
          {papersLoading && <div className={styles.listHint}>Loading…</div>}
          {papers.map((p) => (
            <div
              key={p.id}
              className={`${styles.paperItem} ${p.id === selectedPaperId ? styles.paperItemActive : ''}`}
            >
              <button className={styles.paperItemMain} onClick={() => onSelectPaper(p.id)}>
                <span className={`${styles.statusDot} ${isProcessing(p) ? styles.processing : styles[p.status]}`} />
                <span className={styles.paperTitle}>{trunc(stripHtml(p.title), 48)}</span>
                {isProcessing(p) && <span className={styles.processingLabel}>processing</span>}
              </button>
              <button
                className={styles.paperDeleteBtn}
                title="Remove from this project"
                disabled={deletePaperMutation.isPending}
                onClick={(e) => {
                  e.stopPropagation();
                  deletePaperMutation.mutate(p.id);
                }}
              >×</button>
            </div>
          ))}
          {!papersLoading && papers.length === 0 && (
            <div className={styles.listHint}>No papers yet — add one above, or drag PDFs anywhere onto this page.</div>
          )}
        </div>
      </div>

      {zoteroBrowser && (
        <ZoteroBrowserModal
          projectId={projectId}
          onClose={() => setZoteroBrowser(null)}
          client={zoteroBrowser === 'local' ? zoteroLocalApi : zoteroApi}
          title={zoteroBrowser === 'local' ? 'Browse local Zotero library' : 'Browse Zotero library'}
          sourceKey={zoteroBrowser}
        />
      )}

      {showLibraryPicker && (
        <LocalLibraryPicker
          onClose={() => setShowLibraryPicker(false)}
          onSelect={(relativePath) => selectLibraryMutation.mutate(relativePath)}
          isSelecting={selectLibraryMutation.isPending}
          selectError={
            selectLibraryMutation.error
              ? String(
                  (selectLibraryMutation.error as { response?: { data?: { detail?: string } } })
                    ?.response?.data?.detail ?? 'Could not select that folder',
                )
              : undefined
          }
        />
      )}

      {showBrowserImport && (
        <ZoteroBrowserImportModal
          projectId={projectId}
          onClose={() => setShowBrowserImport(false)}
        />
      )}
    </aside>
  );
}

// ── Reader pane (middle) ─────────────────────────────────────────────────────

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

function ReaderPane({
  paperId, projectId, onBacktrace, backtracePending, backtraceError,
}: {
  paperId: string;
  projectId: string;
  onBacktrace: (sentenceId: string, text: string) => void;
  backtracePending: boolean;
  backtraceError: string | null;
}) {
  const queryClient = useQueryClient();
  const [expandAbstract, setExpandAbstract] = useState(false);
  const [openSection, setOpenSection] = useState<string | null>(null);
  const [fullTextView, setFullTextView] = useState(false);

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
      </div>

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
            Highlight any text, then drag the button that appears onto the graph — drop it on
            empty space for a new statement, or onto an existing one to add as evidence there.
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
                        return (
                          <span
                            key={s.id}
                            className={`${styles.sentenceSpan} ${evs ? styles.evidenceHighlight : ''}`}
                            data-sentence-id={s.id}
                            title={evs?.map((e) => `Supports statement: ${e.assertion}`).join('\n')}
                          >
                            {s.text}{' '}
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
                        return (
                          <div
                            key={s.id}
                            className={styles.sentence}
                            data-sentence-id={s.id}
                            title={evs?.map((e) => `Supports statement: ${e.assertion}`).join('\n')}
                          >
                            <span className={`${styles.sentenceText} ${evs ? styles.evidenceHighlight : ''}`}>
                              {s.text}
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
            draggable
            onDragStart={(e) => {
              e.dataTransfer.setData(
                PASSAGE_MIME,
                JSON.stringify({ sentenceId: selectionTip.sentenceId, text: selectionTip.text }),
              );
              e.dataTransfer.effectAllowed = 'copy';
            }}
            onDragEnd={() => setSelectionTip(null)}
          >
            ⠿ Drag to workspace
          </button>
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
            {backtracePending ? '🔍 Starting…' : '🔍 Backtrace'}
          </button>
        </div>
      )}

      {backtraceError && <div className={styles.formError}>{backtraceError}</div>}
    </div>
  );
}

// ── New statement quick-create ────────────────────────────────────────────────

function NewStatementForm({ projectId, onCreated }: { projectId: string; onCreated: (id: string) => void }) {
  const queryClient = useQueryClient();
  const [type, setType] = useState('claim');
  const [text, setText] = useState('');

  const createMutation = useMutation({
    mutationFn: () => statementsApi.create(projectId, type, text.trim()),
    onSuccess: (s) => {
      queryClient.invalidateQueries({ queryKey: ['graph', projectId] });
      setText('');
      onCreated(s.id);
    },
  });

  return (
    <div className={styles.sidebarSection}>
      <div className={styles.sidebarSectionHeader}>New statement</div>
      <select
        className={styles.typeSelect}
        value={type}
        onChange={(e) => setType(e.target.value)}
      >
        {['claim', 'observation', 'question', 'foundation', 'methods', 'synthesis', 'other'].map((t) => (
          <option key={t} value={t}>{t}</option>
        ))}
      </select>
      <textarea
        className={styles.statementTextarea}
        value={text}
        onChange={(e) => setText(e.target.value)}
        placeholder={type === 'question' ? 'State your question…' : 'State the claim or finding…'}
        rows={3}
      />
      <button
        className={styles.createBtn}
        disabled={text.trim().length < 3 || createMutation.isPending}
        onClick={() => createMutation.mutate()}
      >
        {createMutation.isPending ? 'Creating…' : 'Create'}
      </button>
    </div>
  );
}

// ── Statement detail panel ─────────────────────────────────────────────────────

function AssertionEditor({ statement }: { statement: Statement }) {
  const [text, setText] = useState(statement.assertion);
  const [saving, setSaving] = useState(false);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const mutation = useMutation({
    mutationFn: (val: string) => statementsApi.update(statement.id, { assertion: val }),
    onSettled: () => setSaving(false),
  });

  // Reset the local draft when a different statement (or its server-side
  // assertion) changes — done during render via state (not a ref: refs can't
  // be read or written during render), avoiding a stale-then-corrected
  // double render (see React's "adjusting state when a prop changes" guidance).
  const currentKey = `${statement.id}:${statement.assertion}`;
  const [prevStatementKey, setPrevStatementKey] = useState(currentKey);
  if (currentKey !== prevStatementKey) {
    setPrevStatementKey(currentKey);
    if (text !== statement.assertion) setText(statement.assertion);
  }

  const handleChange = useCallback((e: React.ChangeEvent<HTMLTextAreaElement>) => {
    const val = e.target.value;
    setText(val);
    setSaving(true);
    if (timerRef.current) clearTimeout(timerRef.current);
    timerRef.current = setTimeout(() => mutation.mutate(val), 1000);
  }, [mutation]);

  const isLocked = statement.status === 'locked';

  return (
    <div className={styles.assertionSection}>
      <textarea
        className={styles.assertionTextarea}
        value={text}
        onChange={handleChange}
        disabled={isLocked}
        placeholder={statement.type === 'question' ? 'State your question…' : 'State the claim or finding…'}
        rows={4}
      />
      <div className={`${styles.saveHint} ${saving ? styles.saveHintSaving : ''}`}>
        {saving ? 'Saving…' : isLocked ? 'Locked — edit to unlock' : 'Auto-saved'}
      </div>
    </div>
  );
}

function StatementDetailPanel({
  statementId, projectId, allStatements, papers,
}: {
  statementId: string;
  projectId: string;
  allStatements: StatementNode[];
  papers: Paper[];
}) {
  const queryClient = useQueryClient();

  const { data: statement } = useQuery({
    queryKey: ['statement', statementId],
    queryFn: () => api.get<Statement>(`/statements/${statementId}`).then((r) => r.data),
  });

  const { data: evidence } = useQuery({
    queryKey: ['evidence', statementId],
    queryFn: () => statementsApi.evidence(statementId),
  });

  const { data: edges } = useQuery({
    queryKey: ['edges', statementId],
    queryFn: () => statementsApi.edges(statementId),
  });

  const [targetKind, setTargetKind] = useState<'paper' | 'statement' | 'passage' | 'dataset'>('paper');
  const [targetId, setTargetId] = useState('');
  const [role, setRole] = useState('');
  const [relationTargetId, setRelationTargetId] = useState('');
  const [relation, setRelation] = useState<StatementRelation>('relates_to');

  const [prevTargetKind, setPrevTargetKind] = useState(targetKind);
  if (targetKind !== prevTargetKind) {
    setPrevTargetKind(targetKind);
    if (targetId !== '') setTargetId('');
  }

  const lockMutation = useMutation({
    mutationFn: () => statementsApi.lock(statementId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['statement', statementId] });
      queryClient.invalidateQueries({ queryKey: ['graph', projectId] });
    },
  });

  const unlockMutation = useMutation({
    mutationFn: () => statementsApi.unlock(statementId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['statement', statementId] });
      queryClient.invalidateQueries({ queryKey: ['graph', projectId] });
    },
  });

  const deleteMutation = useMutation({
    mutationFn: () => statementsApi.delete(statementId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['graph', projectId] });
    },
  });

  const statusMutation = useMutation({
    mutationFn: (status: string) => statementsApi.update(statementId, { status }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['statement', statementId] }),
  });

  const addEvidenceMutation = useMutation({
    mutationFn: () => statementsApi.addEvidence(statementId, targetKind, targetId, role || 'evidence'),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['evidence', statementId] });
      queryClient.invalidateQueries({ queryKey: ['graph', projectId] });
      queryClient.invalidateQueries({ predicate: (q) => q.queryKey[0] === 'evidence-sentences' });
      setTargetId('');
    },
  });

  const removeEvidenceMutation = useMutation({
    mutationFn: ({ kind, id }: { kind: string; id: string }) => statementsApi.removeEvidence(statementId, kind, id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['evidence', statementId] });
      queryClient.invalidateQueries({ queryKey: ['graph', projectId] });
      queryClient.invalidateQueries({ predicate: (q) => q.queryKey[0] === 'evidence-sentences' });
    },
  });

  const addEdgeMutation = useMutation({
    mutationFn: () => statementsApi.addEdge(statementId, relationTargetId, relation),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['edges', statementId] });
      queryClient.invalidateQueries({ queryKey: ['graph', projectId] });
      setRelationTargetId('');
    },
  });

  const removeEdgeMutation = useMutation({
    mutationFn: (otherId: string) => statementsApi.removeEdge(statementId, otherId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['edges', statementId] });
      queryClient.invalidateQueries({ queryKey: ['graph', projectId] });
    },
  });

  if (!statement) return <div className={styles.panelLoading}>Loading…</div>;

  const linkedKeys = new Set((evidence ?? []).map((e: Evidence) => `${e.target_kind}:${e.target_id}`));
  const availablePapers = papers.filter((p) => !linkedKeys.has(`paper:${p.id}`));
  const availableStatements = allStatements.filter((s) => s.id !== statementId && !linkedKeys.has(`statement:${s.id}`));

  const relatedIds = new Set((edges ?? []).map((e) => (e.from_statement_id === statementId ? e.to_statement_id : e.from_statement_id)));
  const relationTargetOptions = allStatements.filter((s) => s.id !== statementId && !relatedIds.has(s.id));

  const isQuestion = statement.type === 'question';
  const needsUUIDInput = targetKind === 'passage' || targetKind === 'dataset';
  const canAddEvidence = needsUUIDInput
    ? /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(targetId)
    : targetId.trim().length > 0;

  function evidenceLabel(e: Evidence) {
    if (e.target_kind === 'paper') {
      const found = papers.find((p) => p.id === e.target_id);
      return found ? trunc(found.title, 50) : e.target_id;
    }
    if (e.target_kind === 'statement') {
      const found = allStatements.find((s) => s.id === e.target_id);
      return found ? trunc(found.assertion, 50) : e.target_id;
    }
    return `${e.target_kind} · ${e.target_id.slice(0, 8)}…`;
  }

  return (
    <div className={styles.panelContent}>
      <div className={styles.statementPanelHeader}>
        <span className={`${styles.typeBadge} ${styles[typeClass(statement.type)]}`}>{statement.type}</span>
        <span className={`${styles.statusDot} ${styles[statement.status]}`} />
        <span className={styles.statementStatus}>{statement.status}</span>

        {!isQuestion && statement.status === 'draft' && (
          <button
            className={styles.lockBtn}
            onClick={() => lockMutation.mutate()}
            disabled={lockMutation.isPending || statement.assertion.trim().length < 5}
          >
            Lock
          </button>
        )}
        {!isQuestion && statement.status === 'locked' && (
          <button
            className={styles.lockBtn}
            onClick={() => unlockMutation.mutate()}
            disabled={unlockMutation.isPending}
          >
            Unlock
          </button>
        )}
        {isQuestion && (
          <select
            className={styles.questionStatusSelect}
            value={statement.status}
            onChange={(e) => statusMutation.mutate(e.target.value)}
          >
            <option value="open">open</option>
            <option value="resolved">resolved</option>
            <option value="wontfix">won't fix</option>
          </select>
        )}
        {statement.status !== 'locked' && (
          <button
            className={styles.deleteBtn}
            onClick={() => deleteMutation.mutate()}
            disabled={deleteMutation.isPending}
          >
            ×
          </button>
        )}
      </div>

      <AssertionEditor statement={statement} />

      {/* Evidence */}
      <div className={styles.edgeSection}>
        <div className={styles.edgeSectionLabel}>Evidence</div>
        {(evidence ?? []).map((e: Evidence) => (
          <div key={`${e.target_kind}:${e.target_id}`} className={styles.edgeChip}>
            <span className={styles.edgeChipKind}>{e.target_kind}</span>
            <span className={styles.edgeChipText}>{evidenceLabel(e)}</span>
            <span className={styles.edgeChipRole}>{e.role}</span>
            <button
              className={styles.edgeRemoveBtn}
              onClick={() => removeEvidenceMutation.mutate({ kind: e.target_kind, id: e.target_id })}
            >×</button>
          </div>
        ))}
        <div className={styles.edgeAddRow}>
          <select className={styles.kindSelect} value={targetKind} onChange={(e) => setTargetKind(e.target.value as typeof targetKind)}>
            <option value="paper">paper</option>
            <option value="statement">statement</option>
            <option value="passage">passage</option>
            <option value="dataset">dataset</option>
          </select>
          {targetKind === 'paper' ? (
            <select className={styles.targetSelect} value={targetId} onChange={(e) => setTargetId(e.target.value)}>
              <option value="">— select —</option>
              {availablePapers.map((p) => (
                <option key={p.id} value={p.id}>{trunc(p.title.replace(/<[^>]*>/g, ''), 40)}</option>
              ))}
            </select>
          ) : targetKind === 'statement' ? (
            <select className={styles.targetSelect} value={targetId} onChange={(e) => setTargetId(e.target.value)}>
              <option value="">— select —</option>
              {availableStatements.map((s) => (
                <option key={s.id} value={s.id}>{trunc(s.assertion, 40)}</option>
              ))}
            </select>
          ) : (
            <input className={styles.targetSelect} value={targetId} onChange={(e) => setTargetId(e.target.value)} placeholder="UUID" />
          )}
          <input className={styles.roleInput} value={role} onChange={(e) => setRole(e.target.value)} placeholder="role" list="statement-roles" />
          <datalist id="statement-roles">
            {['evidence', 'supports', 'contradicts', 'data', 'methodology', 'context'].map((r) => (
              <option key={r} value={r} />
            ))}
          </datalist>
          <button
            className={styles.edgeAddBtn}
            disabled={!canAddEvidence || addEvidenceMutation.isPending}
            onClick={() => addEvidenceMutation.mutate()}
          >+</button>
        </div>
      </div>

      {/* Relations */}
      <div className={styles.edgeSection}>
        <div className={styles.edgeSectionLabel}>Relations</div>
        {(edges ?? []).map((e) => {
          const otherId = e.from_statement_id === statementId ? e.to_statement_id : e.from_statement_id;
          const other = allStatements.find((s) => s.id === otherId);
          const isOutgoing = e.from_statement_id === statementId;
          return (
            <div key={otherId} className={styles.edgeChip}>
              <span className={styles.edgeChipKind}>{isOutgoing ? relationLabel(e.relation) : `← ${relationLabel(e.relation)}`}</span>
              <span className={styles.edgeChipText}>{other ? trunc(other.assertion, 45) : otherId.slice(0, 8)}</span>
              <button className={styles.edgeRemoveBtn} onClick={() => removeEdgeMutation.mutate(otherId)}>×</button>
            </div>
          );
        })}
        <div className={styles.edgeAddRow}>
          <select className={styles.kindSelect} value={relation} onChange={(e) => setRelation(e.target.value as StatementRelation)}>
            {RELATIONS.map((r) => (
              <option key={r} value={r}>{relationLabel(r)}</option>
            ))}
          </select>
          <select className={styles.targetSelect} style={{ flex: 1 }} value={relationTargetId} onChange={(e) => setRelationTargetId(e.target.value)}>
            <option value="">— select statement —</option>
            {relationTargetOptions.map((s) => (
              <option key={s.id} value={s.id}>[{s.type}] {trunc(s.assertion, 40)}</option>
            ))}
          </select>
          <button
            className={styles.edgeAddBtn}
            disabled={!relationTargetId || addEdgeMutation.isPending}
            onClick={() => addEdgeMutation.mutate()}
          >+</button>
        </div>
      </div>

      <div className={styles.statementMeta}>
        Drafted {formatDate(statement.drafted_at)}
        {statement.locked_at && ` · Locked ${formatDate(statement.locked_at)}`}
        {statement.built_from_synthesis_id && ' · From synthesis'}
      </div>
    </div>
  );
}

// ── Evidence-preview popover (anchored to a graph node, separate from the
// fuller detail panel — a quick glance at quotes + citations, not for editing) ─

function EvidencePreviewRow({ evidence, papers }: { evidence: Evidence; papers: Paper[] }) {
  const isPassage = evidence.target_kind === 'passage';
  const { data: passage } = useQuery({
    queryKey: ['passage-evidence', evidence.target_id],
    queryFn: () => passagesApi.get(evidence.target_id),
    enabled: isPassage,
    staleTime: 5 * 60_000,
  });

  if (isPassage) {
    if (!passage) return <div className={styles.evidencePopoverRow}>Loading…</div>;
    return (
      <div className={styles.evidencePopoverRow}>
        <div className={styles.evidencePopoverQuote}>&ldquo;{trunc(passage.verbatim_text, 160)}&rdquo;</div>
        <div className={styles.evidencePopoverCite}>
          {formatCitation(passage.paper_first_author, passage.paper_year)}
        </div>
      </div>
    );
  }

  if (evidence.target_kind === 'paper') {
    const paper = papers.find((p) => p.id === evidence.target_id);
    return (
      <div className={styles.evidencePopoverRow}>
        <div className={styles.evidencePopoverQuote}>{paper ? stripHtml(paper.title) : 'Paper'}</div>
        <div className={styles.evidencePopoverCite}>
          {paper ? formatCitation(firstAuthor(paper.authors_json), paper.year) : evidence.target_id.slice(0, 8)}
        </div>
      </div>
    );
  }

  return (
    <div className={styles.evidencePopoverRow}>
      <div className={styles.evidencePopoverQuote}>{evidence.target_kind} · {evidence.target_id.slice(0, 8)}…</div>
    </div>
  );
}

function EvidencePopover({
  statementId, x, y, papers, onClose,
}: {
  statementId: string;
  x: number;
  y: number;
  papers: Paper[];
  onClose: () => void;
}) {
  const popoverRef = useRef<HTMLDivElement>(null);
  const { data: evidence } = useQuery({
    queryKey: ['evidence', statementId],
    queryFn: () => statementsApi.evidence(statementId),
  });

  useEffect(() => {
    function handleClickOutside(e: MouseEvent) {
      if (popoverRef.current && !popoverRef.current.contains(e.target as Node)) onClose();
    }
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, [onClose]);

  return (
    <div ref={popoverRef} className={styles.evidencePopover} style={{ left: x, top: y }}>
      <div className={styles.evidencePopoverHeader}>
        Evidence {evidence ? `· ${evidence.length}` : ''}
      </div>
      {evidence && evidence.length === 0 && (
        <div className={styles.evidencePopoverRow}>No evidence linked yet.</div>
      )}
      {(evidence ?? []).map((e) => (
        <EvidencePreviewRow key={`${e.target_kind}:${e.target_id}`} evidence={e} papers={papers} />
      ))}
    </div>
  );
}

// ── Double-click-to-create popup (also reached via the context menu) ───────────

function QuickCreatePopup({
  x, y, onCreate, onClose, isPending,
}: {
  x: number;
  y: number;
  onCreate: (text: string) => void;
  onClose: () => void;
  isPending: boolean;
}) {
  const [text, setText] = useState('');
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    function handleClickOutside(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) onClose();
    }
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, [onClose]);

  return (
    <div ref={ref} className={styles.quickCreatePopup} style={{ left: x, top: y }}>
      <textarea
        className={styles.quickCreateTextarea}
        value={text}
        onChange={(e) => setText(e.target.value)}
        placeholder="State the claim or finding…"
        rows={2}
        autoFocus
        onKeyDown={(e) => {
          if (e.key === 'Enter' && (e.metaKey || e.ctrlKey) && text.trim().length >= 3) onCreate(text);
          if (e.key === 'Escape') onClose();
        }}
      />
      <button
        className={styles.quickCreateBtn}
        disabled={text.trim().length < 3 || isPending}
        onClick={() => onCreate(text)}
      >
        {isPending ? 'Creating…' : 'Create statement'}
      </button>
    </div>
  );
}

// ── Right-click context menu (background: create; node/edge: delete) ──────────

function CanvasContextMenu({
  x, y, kind, onCreateHere, onDelete, onClose,
}: {
  x: number;
  y: number;
  kind: 'background' | 'node' | 'edge';
  onCreateHere: () => void;
  onDelete: () => void;
  onClose: () => void;
}) {
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    function handleClickOutside(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) onClose();
    }
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, [onClose]);

  return (
    <div ref={ref} className={styles.contextMenu} style={{ left: x, top: y }}>
      {kind === 'background' && (
        <button className={styles.contextMenuItem} onClick={onCreateHere}>+ Create statement here</button>
      )}
      {kind === 'node' && (
        <button className={`${styles.contextMenuItem} ${styles.contextMenuItemDanger}`} onClick={onDelete}>
          Delete statement
        </button>
      )}
      {kind === 'edge' && (
        <button className={`${styles.contextMenuItem} ${styles.contextMenuItemDanger}`} onClick={onDelete}>
          Delete relation
        </button>
      )}
    </div>
  );
}

// ── Synthesis panel ───────────────────────────────────────────────────────────

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

// ── Backtrace live status ───────────────────────────────────────────────────
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

type FindImportState = 'importable' | 'already_in_project' | 'imported' | 'no_doi';

/** Renders LLM-generated commentary (a relevance note, match reasoning, etc.)
 * distinctly from verbatim paper text: labeled by kind so the reader knows
 * which question it's answering, quoted, normal-cased, gray-blue italic,
 * with an attribution to the actual model that produced it — never plain
 * uppercase label styling, which is for short static UI headers only. */
function AiNote({ text, model, label }: { text: string; model: string; label: string }) {
  return (
    <div className={styles.aiNote}>
      <span className={styles.aiNoteLabel}>{label}:</span>
      <span className={styles.aiNoteText}>&ldquo;{text}&rdquo;</span>
      <span className={styles.aiNoteAttribution}>&mdash; {formatModelName(model)}</span>
    </div>
  );
}

function FindResultCard({
  title, authors, year, venue, visitUrl, quote, relevanceNote, model,
  roleLabel, verified, importState, onImport, importing, onPromote, promoting,
}: {
  title: string;
  authors: string[];
  year: number | null;
  venue: string | null;
  visitUrl: string | null;
  quote: string;
  relevanceNote: string | null;
  model: string | null;
  roleLabel?: string;
  verified?: boolean;
  importState: FindImportState;
  onImport?: () => void;
  importing?: boolean;
  onPromote?: () => void;
  promoting?: boolean;
}) {
  const citation = formatCitation(authors[0] ?? null, year);
  const importTitle =
    importState === 'no_doi' ? 'Not an importable paper'
    : importState === 'already_in_project' ? 'Already in project'
    : importState === 'imported' ? 'Imported'
    : 'Import into project';
  const importLabel =
    importState === 'importable' ? '+ import'
    : importState === 'no_doi' ? '—'
    : '✓ imported';

  return (
    <div className={`${styles.evidenceCard} ${roleLabel ? styles[roleLabel] ?? '' : ''}`}>
      <div className={styles.evidenceHeader}>
        <span className={styles.evidencePaper}>
          {title}{citation ? `, ${citation}` : ''}
          {venue && <span className={styles.evidenceVenue}> · {venue}</span>}
        </span>
        {roleLabel && <span className={styles.evidenceRole}>{roleLabel}</span>}
        {verified && <span className={styles.verifiedDot} title="verified" />}
        <div className={styles.findCardActions}>
          {visitUrl && (
            <a className={styles.visitBtn} href={visitUrl} target="_blank" rel="noreferrer">
              visit
            </a>
          )}
          <button
            className={styles.evidenceCreateBtn}
            disabled={importState !== 'importable' || importing}
            title={importTitle}
            onClick={() => onImport?.()}
          >
            {importLabel}
          </button>
          {onPromote && (
            <button
              className={styles.evidenceCreateBtn}
              disabled={promoting}
              onClick={onPromote}
              title="Promote to statement"
            >→ statement</button>
          )}
        </div>
      </div>
      <div className={styles.evidenceQuote}>&ldquo;{quote}&rdquo;</div>
      {relevanceNote && (
        model
          ? <AiNote text={relevanceNote} model={model} label="Relevance" />
          : <div className={styles.searchHint}><strong>Relevance:</strong> {relevanceNote}</div>
      )}
    </div>
  );
}

function FindPanel({ projectId, onStatementCreated }: { projectId: string; onStatementCreated: (id: string) => void }) {
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

function BacktracePanel({
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

// ── Right panel (overlays the graph pane) ───────────────────────────────────────

function RightPanel({
  panelMode, onClose, projectId, papers, allStatements, onStatementCreated,
}: {
  panelMode: Exclude<PanelMode, null>;
  onClose: () => void;
  projectId: string;
  papers: Paper[];
  allStatements: StatementNode[];
  onStatementCreated: (id: string) => void;
}) {
  return (
    <div className={styles.rightPanel}>
      <div className={styles.rightPanelClose}>
        <button className={styles.closeBtn} onClick={onClose}>✕</button>
      </div>
      <div className={styles.rightPanelScroll}>
        {panelMode.kind === 'statement' && (
          <StatementDetailPanel
            statementId={panelMode.id}
            projectId={projectId}
            allStatements={allStatements}
            papers={papers}
          />
        )}
        {panelMode.kind === 'new' && (
          <NewStatementForm projectId={projectId} onCreated={onStatementCreated} />
        )}
      </div>
    </div>
  );
}

// ── Main view ─────────────────────────────────────────────────────────────────

export default function WorkspaceView() {
  const { projectId } = useParams({ from: '/app/projects/$projectId/workspace' });
  const queryClient = useQueryClient();
  const containerRef = useRef<HTMLDivElement>(null);
  const cyRef = useRef<cytoscape.Core | null>(null);
  const pendingSelectRef = useRef<string | null>(null);

  const [selectedPaperId, setSelectedPaperId] = useState<string | null>(null);
  const [readerMode, setReaderMode] = useState<'read' | 'find' | 'backtrace'>('read');
  const [activeBacktrace, setActiveBacktrace] = useState<{ chainId: string; initial: BacktraceStepResult } | null>(null);

  const startBacktraceMutation = useMutation({
    mutationFn: ({ sentenceId, text }: { sentenceId: string; text: string }) =>
      backtraceApi.start(projectId, selectedPaperId!, sentenceId, text),
    onSuccess: (result) => {
      setActiveBacktrace({ chainId: result.chain.id, initial: result });
      setReaderMode('backtrace');
    },
  });
  const [panelMode, setPanelMode] = useState<PanelMode>(null);
  const [nodeFilter, setNodeFilter] = useState<NodeFilter>('all');
  const [readerWidth, setReaderWidth] = useState(READER_DEFAULT_WIDTH);
  const resizingRef = useRef(false);

  const [isDragOver, setIsDragOver] = useState(false);
  const dragDepthRef = useRef(0);
  const [uploadQueue, setUploadQueue] = useState<UploadQueueItem[]>([]);

  // ── Graph canvas interaction state ──────────────────────────────────────────
  const [renderTick, setRenderTick] = useState(0);
  const [badgePositions, setBadgePositions] = useState<Record<string, { x: number; y: number }>>({});
  const [evidencePopover, setEvidencePopover] = useState<{ statementId: string; x: number; y: number } | null>(null);
  const [contextMenu, setContextMenu] = useState<
    | { x: number; y: number; kind: 'background' }
    | { x: number; y: number; kind: 'node'; nodeId: string }
    | { x: number; y: number; kind: 'edge'; edgeFrom: string; edgeTo: string }
    | null
  >(null);
  const [quickCreate, setQuickCreate] = useState<{ x: number; y: number } | null>(null);
  const [heldRelationKey, setHeldRelationKey] = useState<StatementRelation | null>(null);
  const heldRelationKeyRef = useRef<StatementRelation | null>(null);
  const edgeDragSourceRef = useRef<{ id: string; relation: StatementRelation } | null>(null);

  const { data: papers = [], isLoading: papersLoading } = useQuery({
    queryKey: ['papers', projectId],
    queryFn: () => papersApi.listForProject(projectId),
    refetchInterval: (q) => {
      const data = q.state.data as Paper[] | undefined;
      if (!data) return false;
      const tenMinAgo = Date.now() - 10 * 60 * 1000;
      return data.some(
        (p) => p.status === 'ghost' && new Date(p.created_at).getTime() > tenMinAgo,
      ) ? 3000 : false;
    },
  });

  const { data: graph, isLoading: graphLoading } = useQuery({
    queryKey: ['graph', projectId],
    queryFn: () => api.get<StatementGraph>(`/projects/${projectId}/graph`).then((r) => r.data),
  });

  const handleStatementCreated = useCallback((id: string) => {
    pendingSelectRef.current = id;
    setPanelMode({ kind: 'statement', id });
  }, []);

  const createFromPassageMutation = useMutation({
    mutationFn: async ({ text, sentenceId }: { text: string; sentenceId: string }) => {
      const statement = await statementsApi.create(projectId, 'claim', text);
      await statementsApi.addEvidence(statement.id, 'passage', sentenceId, 'evidence');
      return statement;
    },
    onSuccess: (statement) => {
      queryClient.invalidateQueries({ queryKey: ['graph', projectId] });
      queryClient.invalidateQueries({ predicate: (q) => q.queryKey[0] === 'evidence-sentences' });
      handleStatementCreated(statement.id);
    },
  });

  // Dropping a dragged passage directly onto an existing statement node nests
  // it there as evidence, instead of spawning a new statement.
  const nestEvidenceMutation = useMutation({
    mutationFn: ({ statementId, sentenceId }: { statementId: string; sentenceId: string }) =>
      statementsApi.addEvidence(statementId, 'passage', sentenceId, 'evidence'),
    onSuccess: (_data, variables) => {
      queryClient.invalidateQueries({ queryKey: ['graph', projectId] });
      queryClient.invalidateQueries({ queryKey: ['evidence', variables.statementId] });
      queryClient.invalidateQueries({ predicate: (q) => q.queryKey[0] === 'evidence-sentences' });
      handleStatementCreated(variables.statementId);
    },
  });

  function findStatementNodeAtPoint(clientX: number, clientY: number): cytoscape.NodeSingular | null {
    const cy = cyRef.current;
    const container = containerRef.current;
    if (!cy || !container) return null;
    const rect = container.getBoundingClientRect();
    const x = clientX - rect.left;
    const y = clientY - rect.top;
    let found: cytoscape.NodeSingular | null = null;
    cy.nodes().forEach((n) => {
      if (found || n.data('kind') !== 'statement') return;
      const bb = n.renderedBoundingBox();
      if (x >= bb.x1 && x <= bb.x2 && y >= bb.y1 && y <= bb.y2) found = n;
    });
    return found;
  }

  // ── Canvas-driven create / delete / connect ─────────────────────────────────

  const quickCreateMutation = useMutation({
    mutationFn: (text: string) => statementsApi.create(projectId, 'claim', text.trim()),
    onSuccess: (statement) => {
      queryClient.invalidateQueries({ queryKey: ['graph', projectId] });
      setQuickCreate(null);
      handleStatementCreated(statement.id);
    },
  });

  const deleteStatementFromCanvasMutation = useMutation({
    mutationFn: (id: string) => statementsApi.delete(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['graph', projectId] });
      setPanelMode((m) => (m?.kind === 'statement' ? null : m));
      setEvidencePopover(null);
    },
  });

  const removeEdgeFromCanvasMutation = useMutation({
    mutationFn: ({ from, other }: { from: string; other: string }) => statementsApi.removeEdge(from, other),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['graph', projectId] }),
  });

  const addEdgeFromCanvasMutation = useMutation({
    mutationFn: ({ from, to, relation }: { from: string; to: string; relation: StatementRelation }) =>
      statementsApi.addEdge(from, to, relation),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['graph', projectId] }),
  });

  function deleteSelectedElements() {
    const cy = cyRef.current;
    if (!cy) return;
    cy.$('node:selected').forEach((n) => {
      if (n.data('status') !== 'locked') deleteStatementFromCanvasMutation.mutate(n.id());
    });
    cy.$('edge:selected').forEach((e) => {
      removeEdgeFromCanvasMutation.mutate({ from: e.data('source'), other: e.data('target') });
    });
  }

  // ── File drag/drop (PDFs anywhere on the page) ──────────────────────────────

  async function processUploadQueue(files: File[]) {
    const items: UploadQueueItem[] = files.map((f) => ({
      id: `${f.name}-${f.size}-${Date.now()}-${Math.random()}`,
      name: f.name,
      status: 'pending',
    }));
    setUploadQueue((q) => [...q, ...items]);

    for (let i = 0; i < files.length; i++) {
      const item = items[i];
      setUploadQueue((q) => q.map((x) => (x.id === item.id ? { ...x, status: 'uploading' } : x)));
      try {
        await papersApi.upload(files[i], projectId, {});
        setUploadQueue((q) => q.map((x) => (x.id === item.id ? { ...x, status: 'done' } : x)));
        queryClient.invalidateQueries({ queryKey: ['papers', projectId] });
      } catch (err) {
        const message = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail
          ?? 'Upload failed';
        setUploadQueue((q) => q.map((x) => (x.id === item.id ? { ...x, status: 'error', error: message } : x)));
      }
    }
  }

  // Safety net: the browser's default action for a dropped file (anywhere in
  // the viewport, not just our drop zone) is to navigate to/open it. Blocking
  // dragover/drop at the window level guarantees that never happens while
  // this page is mounted, regardless of exactly which pane the drop lands on.
  useEffect(() => {
    const preventDefault = (e: DragEvent) => e.preventDefault();
    window.addEventListener('dragover', preventDefault);
    window.addEventListener('drop', preventDefault);
    return () => {
      window.removeEventListener('dragover', preventDefault);
      window.removeEventListener('drop', preventDefault);
    };
  }, []);

  function handlePageDragEnter(e: React.DragEvent) {
    e.preventDefault();
    if (!e.dataTransfer.types.includes('Files')) return;
    dragDepthRef.current += 1;
    setIsDragOver(true);
  }

  function handlePageDragOver(e: React.DragEvent) {
    e.preventDefault();
  }

  function handlePageDragLeave(e: React.DragEvent) {
    e.preventDefault();
    dragDepthRef.current = Math.max(0, dragDepthRef.current - 1);
    if (dragDepthRef.current === 0) setIsDragOver(false);
  }

  function handlePageDrop(e: React.DragEvent) {
    e.preventDefault();
    dragDepthRef.current = 0;
    setIsDragOver(false);
    const files = Array.from(e.dataTransfer.files).filter(isDroppableFile);
    if (files.length > 0) void processUploadQueue(files);
  }

  // ── Passage drag/drop (reader pane → graph pane) ────────────────────────────

  function handleCanvasDragOver(e: React.DragEvent) {
    if (e.dataTransfer.types.includes(PASSAGE_MIME)) e.preventDefault();
  }

  function handleCanvasDrop(e: React.DragEvent) {
    const raw = e.dataTransfer.getData(PASSAGE_MIME);
    if (!raw) return; // an OS file drop, or something else — let it bubble to the page handler
    e.preventDefault();
    e.stopPropagation();
    const { sentenceId, text } = JSON.parse(raw) as { sentenceId: string; text: string };
    const targetNode = findStatementNodeAtPoint(e.clientX, e.clientY);
    if (targetNode) {
      nestEvidenceMutation.mutate({ statementId: targetNode.id(), sentenceId });
    } else {
      createFromPassageMutation.mutate({ text, sentenceId });
    }
  }

  // ── Reader/graph resize divider ─────────────────────────────────────────────

  function handleDividerMouseDown(e: React.MouseEvent) {
    e.preventDefault();
    resizingRef.current = true;
    const startX = e.clientX;
    const startWidth = readerWidth;

    function onMove(ev: MouseEvent) {
      if (!resizingRef.current) return;
      const next = startWidth + (ev.clientX - startX);
      setReaderWidth(Math.max(READER_MIN_WIDTH, Math.min(READER_MAX_WIDTH, next)));
    }
    function onUp() {
      resizingRef.current = false;
      window.removeEventListener('mousemove', onMove);
      window.removeEventListener('mouseup', onUp);
    }
    window.addEventListener('mousemove', onMove);
    window.addEventListener('mouseup', onUp);
  }

  // ── Keyboard shortcuts: W/E/R hold-to-connect, Delete/Backspace to remove ───
  // Must ignore keys while typing in a text input/textarea — this page puts a
  // graph canvas right next to editable text (assertion textarea, reader
  // pane), so a stray "we ran an experiment" must not trigger tension-drawing.
  useEffect(() => {
    function isTypingTarget(el: EventTarget | null): boolean {
      if (!(el instanceof HTMLElement)) return false;
      return el.tagName === 'INPUT' || el.tagName === 'TEXTAREA' || el.isContentEditable;
    }

    function onKeyDown(e: KeyboardEvent) {
      if (isTypingTarget(e.target)) return;
      const relation = RELATION_KEYS[e.key.toLowerCase()];
      if (relation && !heldRelationKeyRef.current) {
        heldRelationKeyRef.current = relation;
        setHeldRelationKey(relation);
        cyRef.current?.autoungrabify(true);
      }
      if ((e.key === 'Delete' || e.key === 'Backspace') && !heldRelationKeyRef.current) {
        deleteSelectedElements();
      }
    }
    function onKeyUp(e: KeyboardEvent) {
      const relation = RELATION_KEYS[e.key.toLowerCase()];
      if (relation && heldRelationKeyRef.current === relation) {
        heldRelationKeyRef.current = null;
        setHeldRelationKey(null);
        cyRef.current?.autoungrabify(false);
        cyRef.current?.nodes().removeClass('edge-drag-source');
        edgeDragSourceRef.current = null;
      }
    }
    window.addEventListener('keydown', onKeyDown);
    window.addEventListener('keyup', onKeyUp);
    return () => {
      window.removeEventListener('keydown', onKeyDown);
      window.removeEventListener('keyup', onKeyUp);
    };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // ── Cytoscape lifecycle ──────────────────────────────────────────────────────

  // Create cytoscape once on mount; event handlers are stable (setPanelMode is a stable setter)
  useEffect(() => {
    if (!containerRef.current) return;
    const cy = cytoscape({
      container: containerRef.current,
      elements: [],
      style: CY_STYLE,
      layout: { name: 'preset' },
      wheelSensitivity: 0.3,
      minZoom: 0.08,
      maxZoom: 3,
    });
    cy.on('tap', 'node', (e: cytoscape.EventObject) => {
      const { id, kind } = e.target.data() as { id: string; kind: string };
      if (kind !== 'statement') return;
      setPanelMode({ kind: 'statement', id });
      setContextMenu(null);
      const rp = e.target.renderedPosition();
      setEvidencePopover({ statementId: id, x: rp.x, y: rp.y });
    });
    cy.on('tap', (e: cytoscape.EventObject) => {
      if (e.target === cy) {
        setPanelMode(null);
        setEvidencePopover(null);
        setContextMenu(null);
      }
    });
    cy.on('dbltap', (e: cytoscape.EventObject) => {
      if (e.target !== cy) return; // only empty-canvas double-clicks create
      setContextMenu(null);
      setQuickCreate({ x: e.renderedPosition.x, y: e.renderedPosition.y });
    });
    cy.on('cxttap', (e: cytoscape.EventObject) => {
      setEvidencePopover(null);
      setQuickCreate(null);
      const { x, y } = e.renderedPosition;
      if (e.target === cy) {
        setContextMenu({ x, y, kind: 'background' });
      } else if (e.target.isNode?.() && e.target.data('kind') === 'statement') {
        setContextMenu({ x, y, kind: 'node', nodeId: e.target.id() });
      } else if (e.target.isEdge?.()) {
        setContextMenu({ x, y, kind: 'edge', edgeFrom: e.target.data('source'), edgeTo: e.target.data('target') });
      }
    });
    // W/E/R hold + drag between two statement nodes draws a typed edge —
    // autoungrabify (toggled by the keydown/keyup effect) stops the source
    // node from just being dragged around while a relation key is held.
    cy.on('mousedown', 'node', (e: cytoscape.EventObject) => {
      const relation = heldRelationKeyRef.current;
      if (!relation || e.target.data('kind') !== 'statement') return;
      edgeDragSourceRef.current = { id: e.target.id(), relation };
      e.target.addClass('edge-drag-source');
    });
    cy.on('mouseup', (e: cytoscape.EventObject) => {
      const source = edgeDragSourceRef.current;
      if (!source) return;
      edgeDragSourceRef.current = null;
      cy.nodes().removeClass('edge-drag-source');
      const target = e.target;
      if (target?.isNode?.() && target.data('kind') === 'statement' && target.id() !== source.id) {
        addEdgeFromCanvasMutation.mutate({ from: source.id, to: target.id(), relation: source.relation });
      }
    });
    cy.on('render', () => setRenderTick((t) => t + 1));
    cyRef.current = cy;
    return () => { cy.destroy(); cyRef.current = null; };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // Incrementally sync elements; only re-layout when nodes are added or removed
  useEffect(() => {
    const cy = cyRef.current;
    if (!cy || !graph) return;

    const next = buildElements(graph, nodeFilter);
    const nextIds = new Set(next.map((e) => e.data.id as string));
    const toRemove = cy.elements().filter((e) => !nextIds.has(e.id()));
    const toAdd = next.filter((e) => !cy.$(`#${e.data.id}`).length);
    const prevNodeCount = cy.nodes().length;

    cy.batch(() => {
      toRemove.remove();
      cy.add(toAdd);
      next.forEach((e) => {
        const el = cy.$(`#${e.data.id}`);
        if (el.length) el.data(e.data);
      });
    });

    const newNodeCount = cy.nodes().length;
    if (newNodeCount !== prevNodeCount) {
      const isFirst = prevNodeCount === 0;
      const saved = isFirst ? null : { zoom: cy.zoom(), pan: { ...cy.pan() } };
      cy.layout({ name: 'dagre', rankDir: 'BT', nodeSep: 40, rankSep: 70, padding: 50 } as cytoscape.LayoutOptions).run();
      if (isFirst) {
        cy.fit(undefined, 50);
      } else if (saved && !pendingSelectRef.current) {
        cy.zoom(saved.zoom);
        cy.pan(saved.pan);
      }
    }

    const pendingId = pendingSelectRef.current;
    if (pendingId) {
      const el = cy.$(`#${pendingId}`);
      if (el.length) {
        cy.elements().unselect();
        el.select();
        cy.animate({ fit: { eles: el, padding: 80 }, duration: 300 });
        pendingSelectRef.current = null;
      }
    }
  }, [graph, nodeFilter]);

  // Evidence-count badge positions — recomputed (via refs, inside an effect,
  // never during render) whenever the graph data changes or cytoscape fires
  // its own 'render' tick (pan/zoom/drag/layout).
  useEffect(() => {
    const cy = cyRef.current;
    if (!cy || !graph) { setBadgePositions({}); return; }
    const positions: Record<string, { x: number; y: number }> = {};
    graph.nodes.filter((n) => n.evidence_count > 0).forEach((n) => {
      const el = cy.$id(n.id);
      if (!el.length) return;
      const bb = el.renderedBoundingBox();
      positions[n.id] = { x: bb.x2 - 8, y: bb.y1 - 6 };
    });
    setBadgePositions(positions);
  }, [graph, renderTick]);

  const nodeCount = graph
    ? { all: graph.nodes.length, questions: graph.nodes.filter((n) => n.type === 'question').length, statements: graph.nodes.filter((n) => n.type !== 'question').length }
    : null;

  const edgeCount = graph ? graph.edges.length : 0;

  return (
    <div
      className={styles.workspace}
      onDragEnter={handlePageDragEnter}
      onDragOver={handlePageDragOver}
      onDragLeave={handlePageDragLeave}
      onDrop={handlePageDrop}
    >
      {/* ── Toolbar ── */}
      <div className={styles.toolbar}>
        <span className={styles.toolbarLabel}>Show</span>
        {(['all', 'statements', 'questions'] as NodeFilter[]).map((f) => (
          <button
            key={f}
            className={`${styles.filterChip} ${nodeFilter === f ? styles.active : ''}`}
            onClick={() => setNodeFilter(f)}
          >
            {f} {nodeCount ? `(${nodeCount[f]})` : ''}
          </button>
        ))}

        <div className={styles.toolbarSep} />

        <button className={styles.toolbarBtn} onClick={() => cyRef.current?.fit(undefined, 40)}>
          Fit
        </button>
        <button className={styles.toolbarBtn} onClick={() => cyRef.current?.reset()}>
          Reset zoom
        </button>

        <span className={styles.countBadge}>{edgeCount} edge{edgeCount !== 1 ? 's' : ''}</span>

        <div className={styles.toolbarSep} />

        <span className={styles.relationLegend}>
          {(['w', 'e', 'r'] as const).map((k) => (
            <span
              key={k}
              className={`${styles.relationLegendKey} ${heldRelationKey === RELATION_KEYS[k] ? styles.relationLegendKeyActive : ''}`}
            >
              {k.toUpperCase()} {relationLabel(RELATION_KEYS[k])}
            </span>
          ))}
          <span className={styles.relationLegendHint}>hold + drag between statements</span>
        </span>

        <div style={{ marginLeft: 'auto' }} />
        <button
          className={`${styles.toolbarBtn} ${panelMode?.kind === 'new' ? styles.active : ''}`}
          onClick={() => setPanelMode(panelMode?.kind === 'new' ? null : { kind: 'new' })}
        >
          + New statement
        </button>
      </div>

      {/* ── Body: papers | reader | graph ── */}
      <div className={styles.workspaceBody}>
        <PapersSidebar
          projectId={projectId}
          papers={papers}
          papersLoading={papersLoading}
          selectedPaperId={selectedPaperId}
          onSelectPaper={setSelectedPaperId}
          isDragOver={isDragOver}
          uploadQueue={uploadQueue}
          onClearUploadQueue={() => setUploadQueue((q) => q.filter((x) => x.status === 'uploading' || x.status === 'pending'))}
        />

        <div className={styles.readerPane} style={{ width: readerWidth }}>
          <div className={styles.readerModeToggle}>
            <button
              className={`${styles.readerModeBtn} ${readerMode === 'read' ? styles.readerModeBtnActive : ''}`}
              onClick={() => setReaderMode('read')}
            >
              Read
            </button>
            <button
              className={`${styles.readerModeBtn} ${readerMode === 'find' ? styles.readerModeBtnActive : ''}`}
              onClick={() => setReaderMode('find')}
            >
              Find
            </button>
            {activeBacktrace && (
              <button
                className={`${styles.readerModeBtn} ${readerMode === 'backtrace' ? styles.readerModeBtnActive : ''}`}
                onClick={() => setReaderMode('backtrace')}
              >
                Backtrace
              </button>
            )}
          </div>
          {isDragOver ? (
            <div className={styles.emptyState} style={{ outline: '2px dashed var(--accent)', outlineOffset: -8 }}>
              <div className={styles.emptyTitle}>Drop to add</div>
              <div className={styles.emptyDesc}>Release to upload these PDFs to this project.</div>
            </div>
          ) : readerMode === 'find' ? (
            <FindPanel projectId={projectId} onStatementCreated={handleStatementCreated} />
          ) : readerMode === 'backtrace' && activeBacktrace ? (
            <BacktracePanel key={activeBacktrace.chainId} chainId={activeBacktrace.chainId} initialResult={activeBacktrace.initial} projectId={projectId} />
          ) : selectedPaperId ? (
            <ReaderPane
              paperId={selectedPaperId}
              projectId={projectId}
              onBacktrace={(sentenceId, text) => startBacktraceMutation.mutate({ sentenceId, text })}
              backtracePending={startBacktraceMutation.isPending}
              backtraceError={
                startBacktraceMutation.isError
                  ? ((startBacktraceMutation.error as { response?: { data?: { detail?: string } } })?.response?.data
                      ?.detail ?? 'Could not start backtrace')
                  : null
              }
            />
          ) : (
            <div className={styles.emptyState}>
              <div className={styles.emptyTitle}>Read</div>
              <div className={styles.emptyDesc}>
                Select a paper from the sidebar to read it, then drag passages onto the graph — or
                drag PDFs in anywhere on this page to add them.
              </div>
            </div>
          )}
        </div>

        <div
          className={styles.resizeDivider}
          onMouseDown={handleDividerMouseDown}
          role="separator"
          aria-orientation="vertical"
        />

        <div
          className={styles.canvasWrapper}
          onDragOver={handleCanvasDragOver}
          onDrop={handleCanvasDrop}
          onContextMenu={(e) => e.preventDefault()}
        >
          <div ref={containerRef} className={styles.cytoscapeContainer} />

          {/* Evidence-count badges — corner overlay per node, repositioned on every cytoscape render tick */}
          <div className={styles.badgeLayer}>
            {graph && graph.nodes
              .filter((n) => n.evidence_count > 0 && badgePositions[n.id])
              .map((n) => (
                <div
                  key={`badge-${n.id}`}
                  className={styles.evidenceBadge}
                  style={{ left: badgePositions[n.id].x, top: badgePositions[n.id].y }}
                  title={`${n.evidence_count} piece${n.evidence_count !== 1 ? 's' : ''} of evidence`}
                >
                  {n.evidence_count}
                </div>
              ))}
          </div>

          {graphLoading && (
            <div className={styles.loadingOverlay}>Loading graph…</div>
          )}

          {!graphLoading && graph && graph.nodes.length === 0 && (
            <div className={styles.emptyState}>
              <div className={styles.emptyTitle}>No statements yet</div>
              <div className={styles.emptyDesc}>
                Create one from the sidebar, double-click empty canvas, or drag a passage here from
                the paper you're reading.
              </div>
            </div>
          )}

          {evidencePopover && (
            <EvidencePopover
              statementId={evidencePopover.statementId}
              x={evidencePopover.x}
              y={evidencePopover.y}
              papers={papers}
              onClose={() => setEvidencePopover(null)}
            />
          )}

          {quickCreate && (
            <QuickCreatePopup
              x={quickCreate.x}
              y={quickCreate.y}
              isPending={quickCreateMutation.isPending}
              onCreate={(text) => quickCreateMutation.mutate(text)}
              onClose={() => setQuickCreate(null)}
            />
          )}

          {contextMenu && (
            <CanvasContextMenu
              x={contextMenu.x}
              y={contextMenu.y}
              kind={contextMenu.kind}
              onClose={() => setContextMenu(null)}
              onCreateHere={() => {
                setQuickCreate({ x: contextMenu.x, y: contextMenu.y });
                setContextMenu(null);
              }}
              onDelete={() => {
                if (contextMenu.kind === 'node') {
                  deleteStatementFromCanvasMutation.mutate(contextMenu.nodeId);
                } else if (contextMenu.kind === 'edge') {
                  removeEdgeFromCanvasMutation.mutate({ from: contextMenu.edgeFrom, other: contextMenu.edgeTo });
                }
                setContextMenu(null);
              }}
            />
          )}

          {panelMode && (
            <RightPanel
              panelMode={panelMode}
              onClose={() => setPanelMode(null)}
              projectId={projectId}
              papers={papers}
              allStatements={graph?.nodes ?? []}
              onStatementCreated={handleStatementCreated}
            />
          )}
        </div>
      </div>
    </div>
  );
}
