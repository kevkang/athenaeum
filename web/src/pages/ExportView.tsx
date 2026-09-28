import { useCallback, useMemo, useState } from 'react';
import { useParams } from '@tanstack/react-router';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '../api/client';
import { projectsApi } from '../api/projects';
import { statementsApi } from '../api/statements';
import styles from './ExportView.module.css';

// ── JSON syntax highlighter ───────────────────────────────────────────────────
// The archive is pretty-printed JSON (.ath 2.0), one key or value per line.

const UUID_RE = /^"(urn:uuid:)?[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"$/i;
const REF_RE  = /^"doi:/;
const NUM_RE  = /^-?\d+(\.\d+)?([eE][+-]?\d+)?$/;
const LINE_RE = /^(\s*)("(?:[^"\\]|\\.)*"\s*:\s*)?(.*?)(,?)$/;

function colorValue(v: string, s: typeof styles): React.ReactNode {
  if (v === '') return null;
  if (v === 'null') return <span className={s.tokFaint}>{v}</span>;
  if (v === 'true' || v === 'false') return <span className={s.tokMeta}>{v}</span>;
  if (NUM_RE.test(v)) return <span className={s.tokDefault}>{v}</span>;
  if (UUID_RE.test(v) || REF_RE.test(v)) return <span className={s.tokAnchor}>{v}</span>;
  if (v.startsWith('"')) return <span className={s.tokString}>{v}</span>;
  return <span className={s.tokFaint}>{v}</span>; // brackets and braces
}

function tokenizeLine(line: string, s: typeof styles): React.ReactNode {
  const m = line.match(LINE_RE);
  if (!m) return <>{line}</>;
  const [, indent, key, value, comma] = m;
  return (
    <>
      {indent}
      {key && <span className={s.tokKey}>{key}</span>}
      {colorValue(value, s)}
      {comma && <span className={s.tokSep}>{comma}</span>}
    </>
  );
}

// ── Stats from the parsed archive ─────────────────────────────────────────────

interface ArchiveCounts {
  papers?: unknown[];
  passages?: unknown[];
  statements?: unknown[];
  nuclei?: unknown[];
  nodes?: unknown[];
  edges?: { kind: string }[];
  version?: string;
}

function parseStats(text: string) {
  let data: ArchiveCounts = {};
  try {
    data = JSON.parse(text) as ArchiveCounts;
  } catch {
    // A preview that can't be parsed still shows its text.
  }
  return {
    lines:      text.split('\n').length,
    chars:      text.length,
    version:    data.version ?? '?',
    papers:     data.papers?.length ?? 0,
    nuclei:     data.nuclei?.length ?? 0,
    nodes:      data.nodes?.length ?? 0,
    relations:  data.edges?.filter((e) => e.kind === 'semantic').length ?? 0,
    passages:   data.passages?.length ?? 0,
    statements: data.statements?.length ?? 0,
  };
}

// ── ExportView ────────────────────────────────────────────────────────────────

export default function ExportView() {
  const { projectId } = useParams({ from: '/app/projects/$projectId/export' });
  const queryClient = useQueryClient();
  const [pendingDownload, setPendingDownload] = useState(false);

  const { data: project } = useQuery({
    queryKey: ['project', projectId],
    queryFn: () => projectsApi.get(projectId),
  });

  const { data: statements } = useQuery({
    queryKey: ['statements', projectId],
    queryFn: () => statementsApi.list(projectId),
  });

  const unlockedStatements = (statements ?? []).filter((s) => s.status === 'draft');

  const { data: archiveText, isLoading, error, dataUpdatedAt } = useQuery({
    queryKey: ['export', projectId],
    queryFn: () =>
      api
        .get<string>(`/projects/${projectId}/export`, { responseType: 'text' })
        .then((r) => r.data),
    staleTime: 60_000,
  });

  const stats = useMemo(() => (archiveText ? parseStats(archiveText) : null), [archiveText]);

  const lines = useMemo(() => archiveText?.split('\n') ?? [], [archiveText]);

  const filename = project
    ? `${project.title.slice(0, 40).replace(/[\s/]/g, '_')}.ath`
    : 'project.ath';

  const downloadNow = useCallback((text: string) => {
    const blob = new Blob([text], { type: 'application/ld+json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  }, [filename]);

  const lockAllMutation = useMutation({
    mutationFn: () => statementsApi.lockAll(projectId),
    onSuccess: async () => {
      queryClient.invalidateQueries({ queryKey: ['statements', projectId] });
      queryClient.invalidateQueries({ queryKey: ['graph', projectId] });
      if (pendingDownload) {
        const fresh = await queryClient.fetchQuery({
          queryKey: ['export', projectId],
          queryFn: () =>
            api.get<string>(`/projects/${projectId}/export`, { responseType: 'text' }).then((r) => r.data),
        });
        downloadNow(fresh);
        setPendingDownload(false);
      }
    },
  });

  const handleDownload = useCallback(() => {
    if (!archiveText) return;
    if (statements === undefined) return; // don't skip the lock check while still loading
    if (unlockedStatements.length > 0) {
      setPendingDownload(true);
      return;
    }
    downloadNow(archiveText);
  }, [archiveText, statements, unlockedStatements.length, downloadNow]);

  const exportedAt = dataUpdatedAt
    ? new Date(dataUpdatedAt).toLocaleString('en-US', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })
    : '—';

  return (
    <div className={styles.layout}>
      {/* ── Sidebar ── */}
      <aside className={styles.sidebar}>
        {/* Download */}
        <div className={styles.sidebarSection}>
          <div className={styles.sidebarLabel}>Archive</div>
          {pendingDownload ? (
            <div className={styles.lockPrompt}>
              <div className={styles.lockPromptText}>
                {unlockedStatements.length} statement{unlockedStatements.length !== 1 ? 's are' : ' is'} still
                unlocked. All statements must be locked before exporting.
              </div>
              <div className={styles.lockPromptActions}>
                <button
                  className={styles.downloadBtn}
                  onClick={() => lockAllMutation.mutate()}
                  disabled={lockAllMutation.isPending}
                >
                  {lockAllMutation.isPending ? 'Locking…' : 'Lock all & export'}
                </button>
                <button className={styles.cancelBtn} onClick={() => setPendingDownload(false)}>
                  Cancel
                </button>
              </div>
            </div>
          ) : (
            <button
              className={styles.downloadBtn}
              onClick={handleDownload}
              disabled={!archiveText}
            >
              <span className={styles.downloadIcon}>↓</span>
              Download .ath
            </button>
          )}
          {!pendingDownload && unlockedStatements.length > 0 && (
            <div className={styles.lockHint}>
              {unlockedStatements.length} unlocked statement{unlockedStatements.length !== 1 ? 's' : ''} — exporting
              will ask you to lock {unlockedStatements.length !== 1 ? 'them' : 'it'} first.
            </div>
          )}
          <div className={styles.versionBadge}>.ath {stats?.version ?? '2.0'} · JSON-LD</div>
        </div>

        {/* Stats */}
        {stats && (
          <div className={styles.sidebarSection}>
            <div className={styles.sidebarLabel}>Contents</div>
            {[
              { label: 'Papers',    value: stats.papers },
              { label: 'Nuclei',    value: stats.nuclei },
              { label: 'Nodes',     value: stats.nodes },
              { label: 'Relations', value: stats.relations },
              { label: 'Passages',  value: stats.passages },
              { label: 'Statements', value: stats.statements },
              { label: 'Lines',     value: stats.lines.toLocaleString() },
              { label: 'Size',      value: `${(stats.chars / 1024).toFixed(1)} KB` },
            ].map(({ label, value }) => (
              <div key={label} className={styles.statRow}>
                <span>{label}</span>
                <span className={styles.statValue}>{value}</span>
              </div>
            ))}
          </div>
        )}

        {/* Metadata */}
        <div className={styles.sidebarSection}>
          <div className={styles.sidebarLabel}>Metadata</div>
          <div className={styles.metaRow}>
            <div><span className={styles.metaKey}>Project </span><span className={styles.metaVal}>{project?.title ?? '…'}</span></div>
            <div><span className={styles.metaKey}>License </span><span className={styles.metaVal}>{project?.license ?? '—'}</span></div>
            <div><span className={styles.metaKey}>Previewed </span><span className={styles.metaVal}>{exportedAt}</span></div>
          </div>
        </div>
      </aside>

      {/* ── Archive preview ── */}
      <div className={styles.main}>
        <div className={styles.previewHeader}>
          <span className={styles.filenameChip}>{filename}</span>
          {stats && (
            <span className={styles.lineCount}>{stats.lines.toLocaleString()} lines</span>
          )}
        </div>

        {isLoading && <div className={styles.centered}>Loading archive…</div>}

        {error && (
          <div className={styles.centered}>
            <span className={styles.errorMsg}>Failed to load export — is the backend running?</span>
          </div>
        )}

        {archiveText && (
          <div className={styles.codeScroll}>
            <pre className={styles.codePre}>
              {lines.map((line, i) => (
                <div key={i} className={styles.codeLine}>
                  <span className={styles.lineNo}>{i + 1}</span>
                  <span className={styles.lineContent}>{tokenizeLine(line, styles)}</span>
                </div>
              ))}
            </pre>
          </div>
        )}
      </div>
    </div>
  );
}
