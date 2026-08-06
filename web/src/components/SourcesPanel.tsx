import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { papersApi, type Paper } from '../api/papers';
import { highlightsApi } from '../api/highlights';
import { formatCitation, trunc, stripHtml } from '../lib/format';
import ReaderPane, { PASSAGE_MIME } from './ReaderPane';
import ReferencesList from './ReferencesList';
import styles from './SourcesPanel.module.css';

type SourcesTab = 'papers' | 'highlights' | 'refs';

export default function SourcesPanel({ projectId }: { projectId: string }) {
  const [tab, setTab] = useState<SourcesTab>('papers');
  const [selectedPaperId, setSelectedPaperId] = useState<string | null>(null);

  const { data: papers } = useQuery({
    queryKey: ['papers-for-project', projectId],
    queryFn: () => papersApi.listForProject(projectId),
  });

  const { data: highlights } = useQuery({
    queryKey: ['highlights', projectId],
    queryFn: () => highlightsApi.list(projectId),
    enabled: tab === 'highlights',
  });

  return (
    <div className={styles.panel}>
      <div className={styles.tabRow}>
        <button
          className={`${styles.tabBtn} ${tab === 'papers' ? styles.tabBtnActive : ''}`}
          onClick={() => setTab('papers')}
        >
          Papers
        </button>
        <button
          className={`${styles.tabBtn} ${tab === 'highlights' ? styles.tabBtnActive : ''}`}
          onClick={() => setTab('highlights')}
        >
          Highlights
        </button>
        <button
          className={`${styles.tabBtn} ${tab === 'refs' ? styles.tabBtnActive : ''}`}
          onClick={() => setTab('refs')}
        >
          References
        </button>
      </div>

      {tab === 'papers' && (
        selectedPaperId ? (
          <>
            <button className={styles.backBtn} onClick={() => setSelectedPaperId(null)}>← Papers</button>
            <ReaderPane paperId={selectedPaperId} projectId={projectId} showBacktrace={false} />
          </>
        ) : (
          <div className={styles.paperList}>
            {(papers ?? []).length === 0 && <div className={styles.empty}>No papers in this project yet.</div>}
            {papers?.map((p: Paper) => (
              <button key={p.id} className={styles.paperRow} onClick={() => setSelectedPaperId(p.id)}>
                <span className={`${styles.statusDot} ${styles[p.status]}`} />
                <span className={styles.paperTitle}>{stripHtml(p.title)}</span>
              </button>
            ))}
          </div>
        )
      )}

      {tab === 'highlights' && (
        <div className={styles.highlightList}>
          {(highlights ?? []).length === 0 && (
            <div className={styles.empty}>
              No highlights yet — select text in a paper's reader pane (Workspace or here) and click
              ★ Highlight to bookmark it for later.
            </div>
          )}
          {highlights?.map((h) => (
            <div
              key={h.id}
              className={styles.highlightRow}
              draggable
              onDragStart={(e) => {
                e.dataTransfer.setData(
                  PASSAGE_MIME,
                  JSON.stringify({ sentenceId: h.sentence_ids[0], text: h.verbatim_text }),
                );
                e.dataTransfer.effectAllowed = 'copy';
              }}
              title="Drag into the document to cite this as evidence"
            >
              <div className={styles.highlightQuote}>&ldquo;{trunc(h.verbatim_text, 130)}&rdquo;</div>
              <div className={styles.highlightCite}>{formatCitation(h.paper_first_author, h.paper_year)}</div>
            </div>
          ))}
        </div>
      )}

      {tab === 'refs' && <ReferencesList projectId={projectId} />}
    </div>
  );
}
