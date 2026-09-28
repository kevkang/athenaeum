import { useState } from 'react';
import type { Nucleus } from '../../api/nucleate';
import styles from './NucleatePanel.module.css';

const NEW = '__new__';

/**
 * Switches between a project's nuclei, like switching chats in one window,
 * with "New nucleus" at the bottom. A new nucleus is just a question.
 */
export default function NucleusSwitcher({
  nuclei,
  selectedId,
  onSelect,
  onCreate,
  creating,
}: {
  nuclei: Nucleus[];
  selectedId: string | null;
  onSelect: (id: string) => void;
  onCreate: (question: string) => void;
  creating: boolean;
}) {
  const [asking, setAsking] = useState(nuclei.length === 0);
  const [question, setQuestion] = useState('');

  if (asking || nuclei.length === 0) {
    return (
      <form
        className={styles.newNucleus}
        onSubmit={(e) => {
          e.preventDefault();
          const q = question.trim();
          if (q) onCreate(q);
        }}
      >
        <label className={styles.sectionLabel} htmlFor="new-nucleus">
          {nuclei.length === 0 ? 'State a question to start' : 'New nucleus'}
        </label>
        <textarea
          id="new-nucleus"
          className={styles.promptInput}
          rows={2}
          value={question}
          placeholder="How quickly do microbes grow in soil?"
          onChange={(e) => setQuestion(e.target.value)}
          autoFocus
        />
        <div className={styles.newNucleusActions}>
          {nuclei.length > 0 && (
            <button type="button" className={styles.linkBtn} onClick={() => setAsking(false)}>
              Cancel
            </button>
          )}
          <button type="submit" className={styles.sendBtn} disabled={creating || !question.trim()}>
            {creating ? 'Starting…' : 'Start'}
          </button>
        </div>
      </form>
    );
  }

  return (
    <select
      className={styles.switcher}
      value={selectedId ?? ''}
      onChange={(e) => {
        if (e.target.value === NEW) {
          setQuestion('');
          setAsking(true);
        } else {
          onSelect(e.target.value);
        }
      }}
      aria-label="Nucleus"
    >
      {nuclei.map((n) => (
        <option key={n.id} value={n.id}>{n.question}</option>
      ))}
      <option value={NEW}>+ New nucleus…</option>
    </select>
  );
}
