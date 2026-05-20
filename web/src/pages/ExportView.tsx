import { useCallback, useMemo } from 'react';
import { useParams } from '@tanstack/react-router';
import { useQuery } from '@tanstack/react-query';
import { api } from '../api/client';
import { projectsApi } from '../api/projects';
import styles from './ExportView.module.css';

// ── YAML syntax highlighter ───────────────────────────────────────────────────

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const ISO_RE  = /^\d{4}-\d{2}-\d{2}T/;
const NUM_RE  = /^-?\d+(\.\d+)?$/;

function colorValue(v: string, s: typeof styles): React.ReactNode {
  if (v === 'null' || v === '~') return <span className={s.tokFaint}>{v}</span>;
  if (v === 'true' || v === 'false') return <span className={s.tokMeta}>{v}</span>;
  if (NUM_RE.test(v)) return <span className={s.tokDefault}>{v}</span>;
  if (UUID_RE.test(v)) return <span className={s.tokAnchor}>{v}</span>;
  if (ISO_RE.test(v)) return <span className={s.tokString}>{v}</span>;
  return <span className={s.tokString}>{v}</span>;
}

function tokenizeLine(line: string, s: typeof styles): React.ReactNode {
  const indentMatch = line.match(/^(\s*)/);
  const indent = indentMatch?.[1] ?? '';
  const trimmed = line.slice(indent.length);

  if (trimmed === '' ) return <>{line}</>;
  if (trimmed === '---' || trimmed === '...') return <span className={s.tokMeta}>{line}</span>;
  if (trimmed.startsWith('#')) return <span className={s.tokComment}>{line}</span>;

  // List item
  if (trimmed.startsWith('- ')) {
    const rest = trimmed.slice(2);
    return (
      <>
        {indent && <span>{indent}</span>}
        <span className={s.tokFaint}>- </span>
        {tokenizeKeyValue(rest, s)}
      </>
    );
  }

  // Bare list marker
  if (trimmed === '-') return <><span>{indent}</span><span className={s.tokFaint}>-</span></>;

  return <><span>{indent}</span>{tokenizeKeyValue(trimmed, s)}</>;
}

function tokenizeKeyValue(s_: string, s: typeof styles): React.ReactNode {
  // key: value
  const colonIdx = s_.indexOf(': ');
  if (colonIdx > 0) {
    const key = s_.slice(0, colonIdx);
    const val = s_.slice(colonIdx + 2);
    return (
      <>
        <span className={s.tokKey}>{key}</span>
        <span className={s.tokSep}>: </span>
        {colorValue(val, s)}
      </>
    );
  }
  // key: (no value, block follows)
  if (s_.endsWith(':') && !s_.includes(': ')) {
    return <span className={s.tokKey}>{s_}</span>;
  }
  // Bare value
  return colorValue(s_, s);
}

// ── Stats derived from raw YAML text ─────────────────────────────────────────

function parseStats(text: string) {
  const countSection = (marker: string) =>
    (text.match(new RegExp(`^  - ${marker}:`, 'gm')) ?? []).length;

  return {
    lines:     text.split('\n').length,
    chars:     text.length,
    papers:    countSection('doi'),
    passages:  countSection('verbatim_text'),
    syntheses: countSection('claim_text'),
    bricks:    countSection('assertion'),
  };
}

// ── ExportView ────────────────────────────────────────────────────────────────

export default function ExportView() {
  const { projectId } = useParams({ from: '/app/projects/$projectId/export' });

  const { data: project } = useQuery({
    queryKey: ['project', projectId],
    queryFn: () => projectsApi.get(projectId),
  });

  const { data: yamlText, isLoading, error, dataUpdatedAt } = useQuery({
    queryKey: ['export', projectId],
    queryFn: () =>
      api
        .get<string>(`/projects/${projectId}/export`, { responseType: 'text' })
        .then((r) => r.data),
    staleTime: 60_000,
  });

  const stats = useMemo(() => (yamlText ? parseStats(yamlText) : null), [yamlText]);

  const lines = useMemo(() => yamlText?.split('\n') ?? [], [yamlText]);

  const filename = project
    ? `${project.title.slice(0, 40).replace(/[\s/]/g, '_')}.ath`
    : 'project.ath';

  const handleDownload = useCallback(async () => {
    if (!yamlText) return;
    const blob = new Blob([yamlText], { type: 'application/x-yaml' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  }, [yamlText, filename]);

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
          <button
            className={styles.downloadBtn}
            onClick={handleDownload}
            disabled={!yamlText}
          >
            <span className={styles.downloadIcon}>↓</span>
            Download .ath
          </button>
          <div className={styles.versionBadge}>ath_version: 1</div>
        </div>

        {/* Stats */}
        {stats && (
          <div className={styles.sidebarSection}>
            <div className={styles.sidebarLabel}>Contents</div>
            {[
              { label: 'Papers',    value: stats.papers },
              { label: 'Passages',  value: stats.passages },
              { label: 'Syntheses', value: stats.syntheses },
              { label: 'Bricks',    value: stats.bricks },
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

      {/* ── YAML preview ── */}
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

        {yamlText && (
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
