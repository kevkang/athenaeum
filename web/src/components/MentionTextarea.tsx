import { useEffect, useRef, useState } from 'react';
import { useMutation, useQuery } from '@tanstack/react-query';
import { linksApi, type KnowledgeRef, type RefType, type RelType, REL_LABELS } from '../api/links';
import styles from './MentionTextarea.module.css';

interface Props {
  value: string;
  onChange: (val: string) => void;
  projectId: string;
  fromType: RefType;
  fromId: string;
  placeholder?: string;
  rows?: number;
  className?: string;
}

const REL_OPTIONS: RelType[] = ['supports', 'contradicts', 'extends', 'cites', 'related'];

export function MentionTextarea({
  value,
  onChange,
  projectId,
  fromType,
  fromId,
  placeholder,
  rows = 5,
  className,
}: Props) {
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const popoverRef = useRef<HTMLDivElement>(null);

  const [mentionOpen, setMentionOpen] = useState(false);
  const [mentionStart, setMentionStart] = useState(0);
  const [mentionQuery, setMentionQuery] = useState('');
  const [selectedRef, setSelectedRef] = useState<KnowledgeRef | null>(null);
  const [selectedRel, setSelectedRel] = useState<RelType>('related');

  const { data: refs = [] } = useQuery({
    queryKey: ['refs', projectId, mentionQuery],
    queryFn: () => linksApi.searchRefs(projectId, mentionQuery),
    enabled: mentionOpen,
  });

  const createLink = useMutation({
    mutationFn: (ref: { toType: RefType; toId: string; rel: RelType }) =>
      linksApi.create({
        project_id: projectId,
        from_type: fromType,
        from_id: fromId,
        to_type: ref.toType,
        to_id: ref.toId,
        rel: ref.rel,
      }),
  });

  // Close popover on outside click
  useEffect(() => {
    if (!mentionOpen) return;
    const handler = (e: MouseEvent) => {
      if (
        popoverRef.current &&
        !popoverRef.current.contains(e.target as Node) &&
        textareaRef.current &&
        !textareaRef.current.contains(e.target as Node)
      ) {
        cancel();
      }
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, [mentionOpen]);

  const cancel = () => {
    // Remove the dangling [[ from the text
    if (mentionStart !== -1) {
      const cursor = textareaRef.current?.selectionStart ?? mentionStart + 2;
      onChange(value.slice(0, mentionStart) + value.slice(cursor));
    }
    setMentionOpen(false);
    setSelectedRef(null);
    setMentionQuery('');
  };

  const handleChange = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    const val = e.target.value;
    const cursor = e.target.selectionStart ?? val.length;
    const textBefore = val.slice(0, cursor);
    const lastBracket = textBefore.lastIndexOf('[[');

    if (lastBracket !== -1 && !textBefore.slice(lastBracket).includes(']]')) {
      const query = textBefore.slice(lastBracket + 2);
      setMentionQuery(query);
      setMentionStart(lastBracket);
      setMentionOpen(true);
    } else {
      setMentionOpen(false);
    }

    onChange(val);
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (mentionOpen && e.key === 'Escape') {
      e.preventDefault();
      cancel();
    }
  };

  const confirmSelection = () => {
    if (!selectedRef) return;
    const cursor = textareaRef.current?.selectionStart ?? mentionStart + 2 + mentionQuery.length;
    const chip = `[[${selectedRef.ref_type}:${selectedRef.id}|${selectedRef.title}]]`;
    const newVal = value.slice(0, mentionStart) + chip + value.slice(cursor);
    onChange(newVal);

    createLink.mutate({
      toType: selectedRef.ref_type as RefType,
      toId: selectedRef.id,
      rel: selectedRel,
    });

    setMentionOpen(false);
    setSelectedRef(null);
    setMentionQuery('');
    // Restore focus and move cursor after chip
    setTimeout(() => {
      if (textareaRef.current) {
        const pos = mentionStart + chip.length;
        textareaRef.current.focus();
        textareaRef.current.setSelectionRange(pos, pos);
      }
    }, 0);
  };

  return (
    <div className={styles.wrap}>
      <textarea
        ref={textareaRef}
        className={`${styles.textarea} ${className ?? ''}`}
        value={value}
        onChange={handleChange}
        onKeyDown={handleKeyDown}
        placeholder={placeholder}
        rows={rows}
      />

      {mentionOpen && (
        <div ref={popoverRef} className={styles.popover}>
          {selectedRef ? (
            <div className={styles.relPicker}>
              <div className={styles.popoverLabel}>
                Link to <strong>{selectedRef.title}</strong> as:
              </div>
              <div className={styles.relOptions}>
                {REL_OPTIONS.map((rel) => (
                  <button
                    key={rel}
                    className={`${styles.relBtn} ${selectedRel === rel ? styles.relBtnActive : ''}`}
                    onClick={() => setSelectedRel(rel)}
                  >
                    {REL_LABELS[rel]}
                  </button>
                ))}
              </div>
              <div className={styles.relActions}>
                <button className={styles.cancelBtn} onClick={() => setSelectedRef(null)}>
                  ← Back
                </button>
                <button className={styles.confirmBtn} onClick={confirmSelection}>
                  Insert link
                </button>
              </div>
            </div>
          ) : (
            <>
              <div className={styles.popoverLabel}>
                Type to search · <kbd>Esc</kbd> to cancel
              </div>
              {refs.length === 0 && mentionQuery.length > 0 && (
                <div className={styles.empty}>No matches</div>
              )}
              {refs.map((ref) => (
                <button
                  key={`${ref.ref_type}:${ref.id}`}
                  className={styles.refRow}
                  onClick={() => setSelectedRef(ref)}
                >
                  <span className={`${styles.refTypeBadge} ${styles[ref.ref_type]}`}>
                    {ref.ref_type === 'paper' ? 'P' : 'S'}
                  </span>
                  <span className={styles.refTitle}>{ref.title}</span>
                  {ref.subtitle && <span className={styles.refSub}>{ref.subtitle}</span>}
                </button>
              ))}
            </>
          )}
        </div>
      )}
    </div>
  );
}
