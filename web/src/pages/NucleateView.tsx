import { useCallback, useMemo, useRef, useState } from 'react';
import { useNavigate, useParams, useSearch } from '@tanstack/react-router';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { nucleateApi } from '../api/nucleate';
import { useNucleus } from '../components/nucleate/useNucleus';
import NucleusSwitcher from '../components/nucleate/NucleusSwitcher';
import StageList from '../components/nucleate/StageList';
import ChatHistory from '../components/nucleate/ChatHistory';
import NeedsFullTextQueue from '../components/nucleate/NeedsFullTextQueue';
import PromptBox from '../components/nucleate/PromptBox';
import panel from '../components/nucleate/NucleatePanel.module.css';
import styles from './NucleateView.module.css';

// The left panel's share of the width: resizable within the handoff's bounds
// (0-0.4), with a floor so its controls stay usable, and collapsible to 0.
const PANEL_MIN = 0.2;
const PANEL_MAX = 0.4;
const PANEL_DEFAULT = 0.3;

function errorText(e: unknown): string {
  return (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail
    ?? (e as Error)?.message ?? 'Something went wrong';
}

/**
 * Nucleate: the AI archivist grows a corpus out from a question, one
 * researcher-directed step at a time. Left: the nucleus's switcher, stages,
 * chat, needs-full-text queue and prompt. Centre: the canvas.
 */
export default function NucleateView() {
  const { projectId } = useParams({ from: '/app/projects/$projectId/nucleate' });
  const { nucleus: nucleusParam } = useSearch({ from: '/app/projects/$projectId/nucleate' });
  const navigate = useNavigate();
  const queryClient = useQueryClient();

  const { data: nuclei = [], isLoading: nucleiLoading } = useQuery({
    queryKey: ['nuclei', projectId],
    queryFn: () => nucleateApi.list(projectId),
  });
  const nucleusId = nucleusParam ?? nuclei[nuclei.length - 1]?.id ?? null;
  const { detail, run, events, refetch } = useNucleus(nucleusId);

  const [selected, setSelected] = useState<string[]>([]);
  const [promptError, setPromptError] = useState<string | null>(null);

  const select = useCallback((id: string) => {
    navigate({ to: '/projects/$projectId/nucleate', params: { projectId }, search: { nucleus: id } });
    setSelected([]);
  }, [navigate, projectId]);

  const create = useMutation({
    mutationFn: (question: string) => nucleateApi.create(projectId, question),
    onSuccess: async (d) => {
      await queryClient.invalidateQueries({ queryKey: ['nuclei', projectId] });
      select(d.nucleus.id);
    },
  });

  const send = useMutation({
    mutationFn: (text: string) => nucleateApi.prompt(nucleusId!, text, selected),
    onMutate: () => setPromptError(null),
    onSuccess: () => {
      setSelected([]);
      void refetch();
    },
    onError: (e) => setPromptError(errorText(e)),
  });

  const running = run?.status === 'running';
  const nodes = useMemo(() => detail?.nodes ?? [], [detail]);
  const anchors = nodes.filter((n) => selected.includes(n.node_id));

  // ── resizable split ──
  const body = useRef<HTMLDivElement>(null);
  const [frac, setFrac] = useState(PANEL_DEFAULT);
  const [collapsed, setCollapsed] = useState(false);
  function startResize(e: React.MouseEvent) {
    e.preventDefault();
    const rect = body.current?.getBoundingClientRect();
    if (!rect) return;
    const move = (ev: MouseEvent) =>
      setFrac(Math.min(PANEL_MAX, Math.max(PANEL_MIN, (ev.clientX - rect.left) / rect.width)));
    const up = () => {
      window.removeEventListener('mousemove', move);
      window.removeEventListener('mouseup', up);
    };
    window.addEventListener('mousemove', move);
    window.addEventListener('mouseup', up);
  }

  return (
    <div className={styles.page} ref={body}>
      {collapsed ? (
        <button className={styles.rail} onClick={() => setCollapsed(false)} aria-label="Show panel">
          ›
        </button>
      ) : (
        <>
          <aside className={panel.panel} style={{ width: `${frac * 100}%` }}>
            {!nucleiLoading && (
              <NucleusSwitcher
                key={nuclei.length}
                nuclei={nuclei}
                selectedId={nucleusId}
                onSelect={select}
                onCreate={(q) => create.mutate(q)}
                creating={create.isPending}
              />
            )}
            {create.isError && <div className={panel.errorLine}>{errorText(create.error)}</div>}
            {detail && (
              <>
                <StageList
                  run={run}
                  events={events}
                  onCancel={() => run && void nucleateApi.cancel(run.id).then(() => refetch())}
                />
                <ChatHistory prompts={detail.prompts} nodes={nodes} />
                <NeedsFullTextQueue nodes={nodes} onSupplied={() => void refetch()} />
                <PromptBox
                  anchors={anchors}
                  onRemoveAnchor={(id) => setSelected((s) => s.filter((x) => x !== id))}
                  onSend={(t) => send.mutate(t)}
                  busy={send.isPending}
                  disabledReason={running ? 'The archivist is working on the last step…' : null}
                  error={promptError}
                />
              </>
            )}
          </aside>
          <div className={styles.divider} onMouseDown={startResize} role="separator" aria-orientation="vertical">
            <button
              className={styles.collapseBtn}
              onMouseDown={(e) => e.stopPropagation()}
              onClick={() => setCollapsed(true)}
              aria-label="Hide panel"
            >
              ‹
            </button>
          </div>
        </>
      )}

      <main className={styles.canvasArea}>
        {detail ? (
          <div className={styles.placeholder}>
            {detail.nodes.length} node{detail.nodes.length === 1 ? '' : 's'} · canvas arrives in 8b
          </div>
        ) : (
          <div className={styles.placeholder}>
            {nuclei.length === 0 && !nucleiLoading
              ? 'State a question in the left panel. The archivist will propose papers that bear on it.'
              : ''}
          </div>
        )}
      </main>
    </div>
  );
}
