import { useState, useEffect, useRef } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { papersApi, type Paper, type OpenAlexResult } from '../../api/papers';
import { zoteroApi, type ZoteroSearchResult } from '../../api/zotero';
import { zoteroLocalApi } from '../../api/zoteroLocal';
import ZoteroBrowserModal from '../ZoteroBrowserModal';
import ZoteroBrowserImportModal from '../ZoteroBrowserImportModal';
import LocalLibraryPicker from '../LocalLibraryPicker';
import { useUIStore } from '../../store/ui';
import { trunc, stripHtml, safeHtml } from '../../lib/format';
import styles from '../../pages/Workspace.module.css';

export interface UploadQueueItem {
  id: string;
  name: string;
  status: 'pending' | 'uploading' | 'done' | 'error';
  error?: string;
}

function isProcessing(p: Paper) {
  return p.status === 'ghost' && Date.now() - new Date(p.created_at).getTime() < 10 * 60 * 1000;
}

export default function PapersSidebar({
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
