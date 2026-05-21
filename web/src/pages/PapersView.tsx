import { useState } from 'react';
import { useParams } from '@tanstack/react-router';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';

import { papersApi, firstAuthor, type Paper, type SearchHit } from '../api/papers';
import { projectsApi } from '../api/projects';
import styles from './PapersView.module.css';

// ── Ingest form ──────────────────────────────────────────────────────────────

function IngestForm({ projectId }: { projectId: string }) {
  const [doi, setDoi] = useState('');
  const queryClient = useQueryClient();

  const mutation = useMutation({
    mutationFn: () => papersApi.ingest(doi.trim(), projectId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['papers', projectId] });
      setDoi('');
    },
  });

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (doi.trim()) mutation.mutate();
  };

  return (
    <div className={styles.ingestForm}>
      <div className={styles.ingestLabel}>Add paper by DOI</div>
      <form className={styles.ingestRow} onSubmit={handleSubmit}>
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
          disabled={!doi.trim() || mutation.isPending}
        >
          {mutation.isPending ? '…' : 'Add'}
        </button>
      </form>
      {mutation.error && (
        <div className={styles.ingestError}>
          {(mutation.error as Error).message}
        </div>
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

function PaperDetail({ paper }: { paper: Paper }) {
  const queryClient = useQueryClient();
  const retryMutation = useMutation({
    mutationFn: () => papersApi.retry(paper.id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['papers'] }),
  });

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

  // Group sentences by section
  const sentencesBySection: Record<string, string[]> = {};
  if (sentences) {
    for (const s of sentences) {
      if (!sentencesBySection[s.section_id]) sentencesBySection[s.section_id] = [];
      sentencesBySection[s.section_id].push(s.text);
    }
  }

  return (
    <div className={styles.paperDetail}>
      <div className={styles.paperDetailTitle}>{paper.title}</div>

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
        </div>
      )}

      {paper.abstract && (
        <div className={styles.paperAbstract}>{paper.abstract}</div>
      )}

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
            <PaperDetail paper={selectedPaper} />
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
