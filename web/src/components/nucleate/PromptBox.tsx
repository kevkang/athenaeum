import { useState } from 'react';
import type { CanvasNode } from '../../api/nucleate';
import { nodeLabel } from './labels';
import styles from './NucleatePanel.module.css';

/**
 * Directs the next step. Nodes selected on the canvas become anchor chips
 * ("Expanding from: Caro 2023") and the prompt applies to them; with nothing
 * selected it applies to the nucleus.
 */
export default function PromptBox({
  anchors,
  onRemoveAnchor,
  onSend,
  busy,
  disabledReason,
  error,
}: {
  anchors: CanvasNode[];
  onRemoveAnchor: (nodeId: string) => void;
  onSend: (text: string) => void;
  busy: boolean;
  disabledReason: string | null;
  error: string | null;
}) {
  const [text, setText] = useState('');
  const disabled = busy || !!disabledReason;

  function send() {
    const t = text.trim();
    if (!t || disabled) return;
    onSend(t);
    setText('');
  }

  return (
    <div className={styles.prompt}>
      {anchors.length > 0 && (
        <div className={styles.chips}>
          <span className={styles.chipsLabel}>Expanding from:</span>
          {anchors.map((a) => (
            <span key={a.node_id} className={styles.chip}>
              {nodeLabel(a)}
              <button aria-label={`Remove ${nodeLabel(a)}`} onClick={() => onRemoveAnchor(a.node_id)}>
                ×
              </button>
            </span>
          ))}
        </div>
      )}
      <div className={styles.promptRow}>
        <textarea
          id="nucleate-prompt"
          className={styles.promptInput}
          value={text}
          rows={2}
          placeholder={disabledReason ?? (anchors.length ? 'Expand from the selected papers…' : 'Direct the next step…')}
          disabled={disabled}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
              e.preventDefault();
              send();
            }
          }}
        />
        <button className={styles.sendBtn} disabled={disabled || !text.trim()} onClick={send}>
          Send
        </button>
      </div>
      {error && <div className={styles.errorLine}>{error}</div>}
    </div>
  );
}
