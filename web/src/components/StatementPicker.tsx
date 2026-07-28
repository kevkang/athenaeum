import { useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { statementsApi, type Statement } from '../api/statements';
import { trunc } from '../lib/format';
import styles from './StatementPicker.module.css';

export default function StatementPicker({
  projectId,
  onPick,
  excludeId,
}: {
  projectId: string;
  onPick: (statement: Statement) => void;
  excludeId?: string;
}) {
  const [query, setQuery] = useState('');

  const { data: statements } = useQuery({
    queryKey: ['statements', projectId],
    queryFn: () => statementsApi.list(projectId),
  });

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    return (statements ?? [])
      .filter((s) => s.id !== excludeId)
      .filter((s) => !q || s.assertion.toLowerCase().includes(q))
      .slice(0, 20);
  }, [statements, query, excludeId]);

  return (
    <div className={styles.picker}>
      <input
        className={styles.searchInput}
        placeholder="Search statements…"
        value={query}
        onChange={(e) => setQuery(e.target.value)}
        autoFocus
      />
      <div className={styles.list}>
        {filtered.length === 0 && <div className={styles.empty}>No statements found</div>}
        {filtered.map((s) => (
          <button key={s.id} className={styles.row} onClick={() => onPick(s)}>
            <span className={`${styles.typeTag} ${s.type === 'question' ? styles.typeTagQuestion : ''}`}>
              {s.type}
            </span>
            <span className={styles.assertion}>{trunc(s.assertion, 90)}</span>
          </button>
        ))}
      </div>
    </div>
  );
}
