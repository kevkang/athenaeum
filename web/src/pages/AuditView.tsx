import { useMemo, useState } from 'react';
import { useNavigate, useParams, useSearch } from '@tanstack/react-router';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  auditDraftsApi,
  type DraftCitation,
  type DraftFinding,
} from '../api/auditDrafts';
import styles from './AuditView.module.css';

/**
 * Audit: check a manuscript's citations without building a corpus first.
 *
 * Results are grouped by outcome rather than listed in draft order, and the
 * grouping is the point. A citation that could not be resolved, or that
 * resolved to a work with no readable text, was never checked — it is not a
 * citation that failed. Per-row badges leave that distinction to whoever is
 * skimming; sections make it structural.
 *
 * Progress comes from polling the run while it is running rather than the
 * websocket the Compose panel uses. Each citation and finding is committed as
 * it is produced, so polling shows the same progressive fill with one protocol
 * instead of two. The websocket remains available if this ever needs to be
 * cheaper.
 */

/** Sections in the order a reader should meet them: what is wrong first, then
 * what needs them, then what could not be checked, then what held up. */
type Bucket = 'not_supported' | 'needs_input' | 'not_checked' | 'supported';

const BUCKET_LABELS: Record<Bucket, string> = {
  not_supported: 'Not supported',
  needs_input: 'Needs your input',
  not_checked: "Couldn't check",
  supported: 'Supported',
};

const BUCKET_NOTES: Record<Bucket, string> = {
  not_supported: 'The cited text does not establish the claim as written.',
  needs_input: 'Several works match this citation. Nothing is checked until you pick one.',
  not_checked:
    'These citations were never examined — no match was found, or the matched work had no '
    + 'readable text. That is not the same as a citation that failed.',
  supported: 'The cited text supports the claim.',
};

const BUCKET_ORDER: Bucket[] = ['not_supported', 'needs_input', 'not_checked', 'supported'];

const SOURCE_LABELS: Record<string, string> = {
  project_full_text: 'full text',
  abstract: 'abstract',
  no_text: 'no text available',
};

function bucketFor(citation: DraftCitation, finding: DraftFinding | undefined): Bucket {
  if (citation.resolution === 'needs_confirmation') return 'needs_input';
  // No finding means nothing judged this citation, whatever the reason.
  if (!finding) return 'not_checked';
  return finding.supported ? 'supported' : 'not_supported';
}

function citationLabel(citation: DraftCitation): string {
  if (citation.matched_title) {
    const author = citation.matched_authors_json?.[0];
    const year = citation.matched_year;
    return [author, year, citation.matched_title].filter(Boolean).join(' · ');
  }
  return citation.citation_text;
}

export default function AuditView() {
  const { projectId } = useParams({ from: '/app/projects/$projectId/audit' });
  const { run: runId } = useSearch({ from: '/app/projects/$projectId/audit' });
  const navigate = useNavigate();
  const queryClient = useQueryClient();

  const [text, setText] = useState('');

  const createMutation = useMutation({
    mutationFn: () => auditDraftsApi.create(projectId, text),
    // Recorded in the URL rather than component state: an audit takes a while,
    // its results are the product, and losing them to a refresh would be the
    // worst moment to lose anything.
    onSuccess: (run) =>
      navigate({
        to: '/projects/$projectId/audit',
        params: { projectId },
        search: { run: run.id },
      }),
  });

  const { data: detail } = useQuery({
    queryKey: ['audit-draft-run', runId],
    queryFn: () => auditDraftsApi.get(runId!),
    enabled: !!runId,
    // Rows commit as they are produced, so polling fills the sections in as
    // the run works through the draft. Stops as soon as it is terminal.
    refetchInterval: (q) => (q.state.data?.run.status === 'running' ? 1500 : false),
  });

  const confirmMutation = useMutation({
    mutationFn: ({ citationId, openalexId }: { citationId: string; openalexId: string }) =>
      auditDraftsApi.confirm(citationId, openalexId),
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: ['audit-draft-run', runId] }),
  });

  const findingByCitation = useMemo(() => {
    const map = new Map<string, DraftFinding>();
    for (const f of detail?.findings ?? []) {
      if (f.draft_citation_id) map.set(f.draft_citation_id, f);
    }
    return map;
  }, [detail]);

  const buckets = useMemo(() => {
    const grouped: Record<Bucket, DraftCitation[]> = {
      not_supported: [], needs_input: [], not_checked: [], supported: [],
    };
    for (const c of detail?.draft_citations ?? []) {
      grouped[bucketFor(c, findingByCitation.get(c.id))].push(c);
    }
    return grouped;
  }, [detail, findingByCitation]);

  const status = detail?.run.status;
  const total = detail?.draft_citations.length ?? 0;

  return (
    <div className={styles.audit}>
      <div className={styles.composer}>
        <div className={styles.composerHeader}>
          <span className={styles.label}>Check a draft's citations</span>
          {status === 'running' && (
            <span className={styles.running}>
              Working through the draft — {total} citation{total === 1 ? '' : 's'} so far
            </span>
          )}
        </div>
        <textarea
          className={styles.textarea}
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder={
            'Paste a draft here, including its reference list if it has one.\n\n'
            + 'Every claim that carries a citation is pulled out, the citation is matched to a '
            + 'real work, and the claim is checked against what that work actually says.'
          }
          spellCheck={false}
        />
        <div className={styles.composerFooter}>
          <span className={styles.hint}>
            Nothing is added to this project. Papers are looked up, never imported.
          </span>
          <button
            className={styles.runBtn}
            disabled={!text.trim() || createMutation.isPending || status === 'running'}
            onClick={() => createMutation.mutate()}
          >
            {createMutation.isPending || status === 'running' ? 'Checking…' : 'Check citations'}
          </button>
        </div>
        {createMutation.isError && (
          <div className={styles.error}>
            Could not start the audit. {(createMutation.error as Error).message}
          </div>
        )}
      </div>

      <div className={styles.results}>
        {status === 'failed' && (
          <div className={styles.error}>
            This audit stopped before finishing, so the results below are incomplete.
          </div>
        )}

        {!runId && (
          <div className={styles.empty}>
            <div className={styles.emptyTitle}>Nothing checked yet</div>
            <div className={styles.emptyDesc}>
              Paste a draft above. You do not need a corpus, a project library, or anything
              else set up first.
            </div>
          </div>
        )}

        {runId && status !== 'running' && total === 0 && (
          <div className={styles.empty}>
            <div className={styles.emptyTitle}>No citations found</div>
            <div className={styles.emptyDesc}>
              Nothing in this text looked like a claim with a citation attached. If the draft
              uses numbered markers, including its reference list gives far more to work with.
            </div>
          </div>
        )}

        {BUCKET_ORDER.map((bucket) => {
          const items = buckets[bucket];
          if (items.length === 0) return null;
          return (
            <section key={bucket} className={styles.section} data-bucket={bucket}>
              <header className={styles.sectionHeader}>
                <span className={styles.sectionTitle}>{BUCKET_LABELS[bucket]}</span>
                <span className={styles.sectionCount}>{items.length}</span>
              </header>
              <p className={styles.sectionNote}>{BUCKET_NOTES[bucket]}</p>

              {items.map((citation) => {
                const finding = findingByCitation.get(citation.id);
                return (
                  <article key={citation.id} className={styles.card}>
                    <blockquote className={styles.claim}>{citation.claim_text}</blockquote>

                    <div className={styles.meta}>
                      <span className={styles.cited}>{citationLabel(citation)}</span>
                      {citation.evidence_source && (
                        <span className={styles.source}>
                          {SOURCE_LABELS[citation.evidence_source] ?? citation.evidence_source}
                        </span>
                      )}
                      {citation.resolution === 'confirmed' && (
                        <span className={styles.source}>you confirmed this match</span>
                      )}
                    </div>

                    {finding?.quote && (
                      <blockquote className={styles.quote}>“{finding.quote}”</blockquote>
                    )}
                    {finding && <p className={styles.rationale}>{finding.rationale}</p>}

                    {bucket === 'not_checked' && !finding && (
                      <p className={styles.rationale}>
                        {citation.resolution === 'not_found'
                          ? `No work could be matched to “${citation.citation_text}”.`
                          : 'The matched work had no text available to check this claim against.'}
                      </p>
                    )}

                    {citation.resolution === 'needs_confirmation' && (
                      <div className={styles.candidates}>
                        {(citation.candidates_json ?? []).map((candidate) => (
                          <button
                            key={candidate.openalex_id ?? candidate.title}
                            className={styles.candidate}
                            disabled={confirmMutation.isPending || !candidate.openalex_id}
                            onClick={() =>
                              candidate.openalex_id
                              && confirmMutation.mutate({
                                citationId: citation.id,
                                openalexId: candidate.openalex_id,
                              })
                            }
                          >
                            <span className={styles.candidateTitle}>{candidate.title}</span>
                            <span className={styles.candidateMeta}>
                              {[candidate.first_author, candidate.year, candidate.venue]
                                .filter(Boolean)
                                .join(' · ')}
                            </span>
                          </button>
                        ))}
                      </div>
                    )}
                  </article>
                );
              })}
            </section>
          );
        })}

        {detail?.run.malformed_entries ? (
          <div className={styles.error}>
            {detail.run.malformed_entries} of {detail.run.quotes_checked} checks came back
            without a usable verdict. Those citations appear as not supported, but nothing
            actually judged them — run this again before reading anything into them.
          </div>
        ) : null}
      </div>
    </div>
  );
}
