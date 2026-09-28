import { useEffect, useRef } from 'react';
import type { CanvasNode, NucleusPrompt } from '../../api/nucleate';
import { nodeLabel } from './labels';
import styles from './NucleatePanel.module.css';

/**
 * The nucleus's chat: every instruction and what the archivist did about it.
 * It doubles as the method record, so nothing here is ever edited or hidden.
 * The archivist's replies are AI text and are styled as such.
 */
export default function ChatHistory({
  prompts,
  nodes,
}: {
  prompts: NucleusPrompt[];
  nodes: CanvasNode[];
}) {
  const end = useRef<HTMLDivElement>(null);
  const byId = new Map(nodes.map((n) => [n.node_id, n]));
  const last = prompts[prompts.length - 1];

  useEffect(() => {
    end.current?.scrollIntoView({ block: 'end' });
  }, [prompts.length, last?.expansion?.reply_text]);

  return (
    <div className={styles.chat}>
      {prompts.map((p) => {
        const anchors = p.anchor_node_ids.map((id) => byId.get(id)).filter(Boolean) as CanvasNode[];
        const exp = p.expansion;
        return (
          <div key={p.id} className={styles.turn}>
            <div className={styles.userMsg}>
              {anchors.length > 0 && (
                <div className={styles.msgAnchors}>
                  From: {anchors.map(nodeLabel).join(', ')}
                </div>
              )}
              {p.text}
            </div>
            {exp?.reply_text ? (
              <div className={styles.archivistMsg}>{exp.reply_text}</div>
            ) : exp?.status === 'running' ? (
              <div className={styles.workingMsg}>Working on it…</div>
            ) : exp?.status === 'failed' ? (
              <div className={styles.failedMsg}>This step failed before finishing.</div>
            ) : null}
          </div>
        );
      })}
      <div ref={end} />
    </div>
  );
}
