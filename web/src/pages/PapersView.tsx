import { useRef, useState, useEffect } from 'react';
import { useParams } from '@tanstack/react-router';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';

import { papersApi, firstAuthor, type Paper, type SearchHit } from '../api/papers';
import { projectsApi } from '../api/projects';
import { MentionTextarea } from '../components/MentionTextarea';
import { LinksPanel } from '../components/LinksPanel';
import styles from './PapersView.module.css';

// ── Ingest form ──────────────────────────────────────────────────────────────

function IngestForm({ projectId }: { projectId: string }) {
  const [doi, setDoi] = useState('');
  const [uploadFile, setUploadFile] = useState<File | null>(null);
  const [uploadTitle, setUploadTitle] = useState('');
  const queryClient = useQueryClient();
  const fileInputRef = useRef<HTMLInputElement>(null);

  const doiMutation = useMutation({
    mutationFn: () => papersApi.ingest(doi.trim(), projectId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['papers', projectId] });
      setDoi('');
    },
  });

  const uploadMutation = useMutation({
    mutationFn: () => papersApi.upload(uploadFile!, projectId, { title: uploadTitle || undefined }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['papers', projectId] });
      setUploadFile(null);
      setUploadTitle('');
      if (fileInputRef.current) fileInputRef.current.value = '';
    },
  });

  const handleDoiSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (doi.trim()) doiMutation.mutate();
  };

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const f = e.target.files?.[0] ?? null;
    setUploadFile(f);
    if (f) setUploadTitle(f.name.replace(/\.[^.]+$/, ''));
  };

  return (
    <div className={styles.ingestForm}>
      <div className={styles.ingestLabel}>Add by DOI</div>
      <form className={styles.ingestRow} onSubmit={handleDoiSubmit}>
        <input
          className={styles.doiInput}
          value={doi}
          onChange={(e) => setDoi(e.target.value)}
          placeholder="10.xxxx/xxxx"
          spellCheck={false}
        />
        <button
          className={styles.ingestBtn}
          type="submit"
          disabled={!doi.trim() || doiMutation.isPending}
        >
          {doiMutation.isPending ? '…' : 'Add'}
        </button>
      </form>
      {doiMutation.error && (
        <div className={styles.ingestError}>{(doiMutation.error as Error).message}</div>
      )}

      <div className={styles.ingestDivider}>or upload PDF / TXT</div>

      <input
        ref={fileInputRef}
        type="file"
        accept=".pdf,.txt"
        className={styles.fileInputHidden}
        onChange={handleFileChange}
      />

      {!uploadFile ? (
        <button className={styles.uploadPickBtn} onClick={() => fileInputRef.current?.click()}>
          Choose file…
        </button>
      ) : (
        <div className={styles.uploadRow}>
          <input
            className={styles.uploadTitleInput}
            value={uploadTitle}
            onChange={(e) => setUploadTitle(e.target.value)}
            placeholder="Title (optional)"
          />
          <button
            className={styles.ingestBtn}
            onClick={() => uploadMutation.mutate()}
            disabled={uploadMutation.isPending}
          >
            {uploadMutation.isPending ? '…' : 'Upload'}
          </button>
          <button
            className={styles.uploadCancelBtn}
            onClick={() => {
              setUploadFile(null);
              setUploadTitle('');
              if (fileInputRef.current) fileInputRef.current.value = '';
            }}
          >
            ✕
          </button>
        </div>
      )}
      {uploadMutation.error && (
        <div className={styles.ingestError}>{(uploadMutation.error as Error).message}</div>
      )}
    </div>
  );
}

// ── Paper list item ──────────────────────────────────────────────────────────

function PaperItem({
  paper,
  active,
  onClick,
}: {
  paper: Paper;
  active: boolean;
  onClick: () => void;
}) {
  const author = firstAuthor(paper.authors_json);
  return (
    <div
      className={`${styles.paperItem} ${active ? styles.paperItemActive : ''}`}
      onClick={onClick}
    >
      <div className={styles.paperItemTitle}>{paper.title}</div>
      <div className={styles.paperItemMeta}>
        <span className={`${styles.statusDot} ${styles[paper.status]}`} />
        {author && <span>{author}</span>}
        {paper.year && <span>{paper.year}</span>}
        {paper.venue && <span>{paper.venue}</span>}
      </div>
    </div>
  );
}

// ── Search bar ───────────────────────────────────────────────────────────────

function SearchBar({
  projectId,
  onResults,
  onClear,
}: {
  projectId: string;
  onResults: (hits: SearchHit[], query: string) => void;
  onClear: () => void;
}) {
  const [query, setQuery] = useState('');
  const [mode, setMode] = useState('hybrid');

  const mutation = useMutation({
    mutationFn: () => papersApi.search(query, projectId, mode),
    onSuccess: (data) => onResults(data.hits, data.query),
  });

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (query.trim()) {
      mutation.mutate();
    } else {
      onClear();
    }
  };

  const handleChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    setQuery(e.target.value);
    if (!e.target.value) onClear();
  };

  return (
    <form className={styles.searchBar} onSubmit={handleSubmit}>
      <input
        className={styles.searchInput}
        value={query}
        onChange={handleChange}
        placeholder="Search across all paper text…"
      />
      <select
        className={styles.searchModeSelect}
        value={mode}
        onChange={(e) => setMode(e.target.value)}
      >
        <option value="hybrid">Hybrid</option>
        <option value="vector">Vector</option>
        <option value="text">Text</option>
      </select>
      <button
        className={styles.searchBtn}
        type="submit"
        disabled={mutation.isPending}
      >
        {mutation.isPending ? 'Searching…' : 'Search'}
      </button>
    </form>
  );
}

// ── Search results ───────────────────────────────────────────────────────────

function SearchResults({ hits, query }: { hits: SearchHit[]; query: string }) {
  return (
    <div>
      <div className={styles.searchResultsHeader}>
        {hits.length} result{hits.length !== 1 ? 's' : ''} for "{query}"
      </div>
      {hits.map((hit) => (
        <div key={hit.sentence_id} className={styles.hitCard}>
          <div className={styles.hitMeta}>
            <span className={styles.hitPaper}>{hit.paper.title}</span>
            <span className={styles.hitSection}>{hit.section_label}</span>
            <span className={styles.hitScore}>{hit.score.toFixed(3)}</span>
          </div>
          <div className={styles.hitText}>{hit.text}</div>
        </div>
      ))}
    </div>
  );
}

// ── Paper detail ─────────────────────────────────────────────────────────────

function PaperDetail({
  paper,
  projectId,
  onRemoved,
}: {
  paper: Paper;
  projectId: string;
  onRemoved: () => void;
}) {
  const queryClient = useQueryClient();
  const reuploadRef = useRef<HTMLInputElement>(null);

  const retryMutation = useMutation({
    mutationFn: () => papersApi.retry(paper.id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['papers'] }),
  });

  const removeMutation = useMutation({
    mutationFn: () => projectsApi.removePaper(projectId, paper.id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['papers', projectId] });
      onRemoved();
    },
  });

  const reuploadMutation = useMutation({
    mutationFn: (file: File) => papersApi.reupload(paper.id, file),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['papers', projectId] }),
  });

  const summarizeMutation = useMutation({
    mutationFn: () => papersApi.summarize(paper.id),
    onSuccess: (updated) => {
      setEditAbstract(updated.abstract ?? '');
      queryClient.invalidateQueries({ queryKey: ['papers', projectId] });
    },
  });

  // Editable title / abstract
  const [editTitle, setEditTitle] = useState(paper.title);
  const [editAbstract, setEditAbstract] = useState(paper.abstract ?? '');

  useEffect(() => {
    setEditTitle(paper.title);
    setEditAbstract(paper.abstract ?? '');
  }, [paper.id]);

  // Debounced autosave — only fires when user has actually changed values
  useEffect(() => {
    const t = setTimeout(() => {
      const newTitle = editTitle.trim();
      const newAbstract = editAbstract;
      if (newTitle && (newTitle !== paper.title || newAbstract !== (paper.abstract ?? ''))) {
        papersApi.update(paper.id, { title: newTitle, abstract: newAbstract }).then(() =>
          queryClient.invalidateQueries({ queryKey: ['papers', projectId] })
        );
      }
    }, 1000);
    return () => clearTimeout(t);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [editTitle, editAbstract]);

  const { data: sections } = useQuery({
    queryKey: ['sections', paper.id],
    queryFn: () => papersApi.sections(paper.id),
    enabled: paper.status === 'full_text',
  });

  const { data: sentences } = useQuery({
    queryKey: ['sentences', paper.id],
    queryFn: () => papersApi.sentences(paper.id),
    enabled: paper.status === 'full_text',
  });

  const author = firstAuthor(paper.authors_json);

  const sentencesBySection: Record<string, string[]> = {};
  if (sentences) {
    for (const s of sentences) {
      if (!sentencesBySection[s.section_id]) sentencesBySection[s.section_id] = [];
      sentencesBySection[s.section_id].push(s.text);
    }
  }

  const handleReupload = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (file) reuploadMutation.mutate(file);
    if (reuploadRef.current) reuploadRef.current.value = '';
  };

  return (
    <div className={styles.paperDetail}>
      <div className={styles.paperDetailActions}>
        <button
          className={styles.removeBtn}
          onClick={() => removeMutation.mutate()}
          disabled={removeMutation.isPending}
        >
          {removeMutation.isPending ? 'Removing…' : 'Remove from project'}
        </button>
      </div>

      <input
        className={styles.paperTitleInput}
        value={editTitle}
        onChange={(e) => setEditTitle(e.target.value)}
      />

      <div className={styles.paperDetailMeta}>
        {author && <span><strong>{author}</strong>{Array.isArray(paper.authors_json) && paper.authors_json.length > 1 ? ' et al.' : ''}</span>}
        {paper.year && <span>{paper.year}</span>}
        {paper.venue && <span><em>{paper.venue}</em></span>}
        {paper.doi && (
          <a
            className={styles.doiLink}
            href={`https://doi.org/${paper.doi}`}
            target="_blank"
            rel="noreferrer"
          >
            {paper.doi}
          </a>
        )}
        <span className={`${styles.statusBadge} ${styles[paper.status]}`}>
          {paper.status === 'full_text' ? 'Full text' : paper.status === 'ghost' ? 'Metadata only' : 'Failed'}
        </span>
      </div>

      {paper.status === 'ghost' && (
        <div className={styles.spinnerRow}>
          <span className={styles.ingesting}>⟳</span>
          Full text ingestion queued — check back shortly.
        </div>
      )}

      {paper.status === 'failed' && (
        <div className={styles.spinnerRow}>
          <span>Ingestion failed.</span>
          <button
            className={styles.retryBtn}
            onClick={() => retryMutation.mutate()}
            disabled={retryMutation.isPending}
          >
            {retryMutation.isPending ? 'Retrying…' : 'Retry ingestion'}
          </button>
          <input
            ref={reuploadRef}
            type="file"
            accept=".pdf,.txt"
            className={styles.fileInputHidden}
            onChange={handleReupload}
          />
          <button
            className={styles.retryBtn}
            onClick={() => reuploadRef.current?.click()}
            disabled={reuploadMutation.isPending}
          >
            {reuploadMutation.isPending ? 'Uploading…' : 'Upload PDF / TXT'}
          </button>
        </div>
      )}

      {!editAbstract && paper.status === 'full_text' && (
        <div className={styles.generateRow}>
          <button
            className={styles.generateBtn}
            onClick={() => summarizeMutation.mutate()}
            disabled={summarizeMutation.isPending}
          >
            {summarizeMutation.isPending ? 'Generating…' : '✦ Generate abstract'}
          </button>
          {summarizeMutation.error && (
            <span className={styles.generateError}>
              {(summarizeMutation.error as Error).message}
            </span>
          )}
        </div>
      )}

      <MentionTextarea
        className={styles.abstractTextarea}
        value={editAbstract}
        onChange={setEditAbstract}
        projectId={projectId}
        fromType="paper"
        fromId={paper.id}
        placeholder="Abstract…"
        rows={5}
      />

      {sections && sections.map((section, i) => {
        const sectionSentences = sentencesBySection[section.id] ?? [];
        return (
          <div key={section.id}>
            {i > 0 && <div className={styles.sectionDivider} />}
            <div className={styles.sectionLabel}>{section.label}</div>
            <div className={styles.sectionContent}>
              {sectionSentences.join(' ')}
            </div>
          </div>
        );
      })}

      <LinksPanel projectId={projectId} refType="paper" refId={paper.id} />
    </div>
  );
}

// ── Main view ────────────────────────────────────────────────────────────────

export default function PapersView() {
  const { projectId } = useParams({ from: '/app/projects/$projectId/papers' });
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [searchResults, setSearchResults] = useState<{ hits: SearchHit[]; query: string } | null>(null);

  const { data: project } = useQuery({
    queryKey: ['project', projectId],
    queryFn: () => projectsApi.get(projectId),
  });

  const { data: papers, isLoading } = useQuery({
    queryKey: ['papers', projectId],
    queryFn: () => papersApi.listForProject(projectId),
    refetchInterval: (query) => {
      // Keep polling while any paper is still ingesting
      const hasPending = query.state.data?.some((p: Paper) => p.status === 'ghost');
      return hasPending ? 5000 : false;
    },
  });

  const selectedPaper = papers?.find((p) => p.id === selectedId) ?? null;

  return (
    <div className={styles.layout}>
      {/* ── Sidebar ── */}
      <aside className={styles.sidebar}>
        <div className={styles.sidebarTop}>
          <div className={styles.projectLabel}>Active project</div>
          <div className={styles.projectName}>{project?.title ?? '…'}</div>
        </div>

        <IngestForm projectId={projectId} />

        <div className={styles.paperListHeader}>
          Papers{papers ? ` · ${papers.length}` : ''}
        </div>

        <div className={styles.paperList}>
          {isLoading && (
            <div className={styles.spinnerRow} style={{ padding: '12px 10px' }}>
              Loading…
            </div>
          )}
          {papers?.map((p) => (
            <PaperItem
              key={p.id}
              paper={p}
              active={p.id === selectedId}
              onClick={() => {
                setSelectedId(p.id);
                setSearchResults(null);
              }}
            />
          ))}
        </div>
      </aside>

      {/* ── Main panel ── */}
      <div className={styles.main}>
        <SearchBar
          projectId={projectId}
          onResults={(hits, query) => {
            setSearchResults({ hits, query });
            setSelectedId(null);
          }}
          onClear={() => setSearchResults(null)}
        />

        <div className={styles.content}>
          {searchResults ? (
            <SearchResults hits={searchResults.hits} query={searchResults.query} />
          ) : selectedPaper ? (
            <PaperDetail
              paper={selectedPaper}
              projectId={projectId}
              onRemoved={() => setSelectedId(null)}
            />
          ) : (
            <div className={styles.emptyState}>
              <div className={styles.emptyStateTitle}>No paper selected</div>
              <div className={styles.emptyStateDesc}>
                Add a paper by DOI, then click it to read its full text.<br />
                Use the search bar to find sentences across all papers.
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
