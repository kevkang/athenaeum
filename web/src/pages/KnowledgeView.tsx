import { useState, useEffect, useRef, useCallback } from 'react';
import { useParams } from '@tanstack/react-router';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { bricksApi, type Brick, type BuildsOn, type RestsOn } from '../api/bricks';
import { projectsApi } from '../api/projects';
import styles from './KnowledgeView.module.css';

// ── Helpers ───────────────────────────────────────────────────────────────────

const BRICK_TYPES = ['observation', 'claim', 'methods', 'foundation', 'synthesis', 'other'] as const;

function typeClass(type: string): string {
  if (BRICK_TYPES.includes(type as typeof BRICK_TYPES[number])) return type;
  return 'other';
}

function formatDate(iso: string) {
  return new Date(iso).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
}

// ── AssertionEditor — autosave on debounce ────────────────────────────────────

function AssertionEditor({ brick }: { brick: Brick }) {
  const [text, setText] = useState(brick.assertion);
  const [saving, setSaving] = useState(false);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const mutation = useMutation({
    mutationFn: (val: string) => bricksApi.update(brick.id, { assertion: val }),
    onSettled: () => setSaving(false),
  });

  const handleChange = useCallback((e: React.ChangeEvent<HTMLTextAreaElement>) => {
    const val = e.target.value;
    setText(val);
    setSaving(true);
    if (timerRef.current) clearTimeout(timerRef.current);
    timerRef.current = setTimeout(() => mutation.mutate(val), 1000);
  }, [mutation]);

  useEffect(() => {
    setText(brick.assertion);
  }, [brick.id, brick.assertion]);

  return (
    <div className={styles.assertionSection}>
      <textarea
        className={styles.assertionTextArea}
        value={text}
        onChange={handleChange}
        placeholder="State the claim, finding, or observation this brick represents."
        disabled={brick.status === 'cemented'}
      />
      <div className={`${styles.saveHint} ${saving ? styles.saveHintSaving : ''}`}>
        {saving ? 'Saving…' : brick.status === 'cemented' ? 'Cemented — edit to revert to draft' : 'Saved automatically'}
      </div>
    </div>
  );
}

// ── BuildsOnSection ───────────────────────────────────────────────────────────

function BuildsOnSection({
  brick,
  allBricks,
}: {
  brick: Brick;
  allBricks: Brick[];
}) {
  const queryClient = useQueryClient();
  const [selectedParent, setSelectedParent] = useState('');

  const { data: edges } = useQuery({
    queryKey: ['builds-on', brick.id],
    queryFn: () => bricksApi.buildsOn(brick.id),
  });

  const addMutation = useMutation({
    mutationFn: (parentId: string) => bricksApi.addBuildsOn(brick.id, parentId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['builds-on', brick.id] });
      setSelectedParent('');
    },
  });

  const removeMutation = useMutation({
    mutationFn: (parentId: string) => bricksApi.removeBuildsOn(brick.id, parentId),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['builds-on', brick.id] }),
  });

  const parentIds = new Set((edges ?? []).map((e: BuildsOn) => e.parent_brick_id));
  const candidates = allBricks.filter((b) => b.id !== brick.id && !parentIds.has(b.id));

  return (
    <div className={styles.connectionSection}>
      <div className={styles.sectionHeader}>Builds on · {edges?.length ?? 0} connection{edges?.length !== 1 ? 's' : ''}</div>

      <div className={styles.connectionList}>
        {(edges ?? []).map((edge: BuildsOn) => {
          const parent = allBricks.find((b) => b.id === edge.parent_brick_id);
          return (
            <div key={edge.parent_brick_id} className={styles.connectionRow}>
              <span className={`${styles.typeBadge} ${styles[typeClass(parent?.type ?? '')]}`}>
                {parent?.type ?? 'brick'}
              </span>
              <span className={styles.connectionText}>
                {parent?.assertion ?? edge.parent_brick_id}
              </span>
              <button
                className={styles.removeBtn}
                onClick={() => removeMutation.mutate(edge.parent_brick_id)}
                disabled={removeMutation.isPending}
              >
                remove
              </button>
            </div>
          );
        })}
      </div>

      {candidates.length > 0 && (
        <div className={styles.addBuildsOnRow}>
          <select
            className={styles.parentSelect}
            value={selectedParent}
            onChange={(e) => setSelectedParent(e.target.value)}
          >
            <option value="">— select parent brick —</option>
            {candidates.map((b) => (
              <option key={b.id} value={b.id}>
                [{b.type}] {b.assertion.slice(0, 80)}{b.assertion.length > 80 ? '…' : ''}
              </option>
            ))}
          </select>
          <button
            className={styles.addBtn}
            disabled={!selectedParent || addMutation.isPending}
            onClick={() => selectedParent && addMutation.mutate(selectedParent)}
          >
            + Add
          </button>
        </div>
      )}
    </div>
  );
}

// ── RestsOnSection ────────────────────────────────────────────────────────────

function RestsOnSection({ brick, allBricks }: { brick: Brick; allBricks: Brick[] }) {
  const queryClient = useQueryClient();
  const [kind, setKind] = useState<'paper' | 'passage' | 'dataset' | 'brick'>('passage');
  const [targetId, setTargetId] = useState('');
  const [role, setRole] = useState('evidence');

  const { data: edges } = useQuery({
    queryKey: ['rests-on', brick.id],
    queryFn: () => bricksApi.restsOn(brick.id),
  });

  const addMutation = useMutation({
    mutationFn: () => bricksApi.addRestsOn(brick.id, kind, targetId.trim(), role.trim() || 'evidence'),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['rests-on', brick.id] });
      setTargetId('');
    },
  });

  const removeMutation = useMutation({
    mutationFn: ({ targetKind, tId }: { targetKind: string; tId: string }) =>
      bricksApi.removeRestsOn(brick.id, targetKind, tId),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['rests-on', brick.id] }),
  });

  const isValidUUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(targetId.trim());

  function labelFor(edge: RestsOn) {
    if (edge.target_kind === 'brick') {
      const found = allBricks.find((b) => b.id === edge.target_id);
      if (found) return found.assertion.slice(0, 80) + (found.assertion.length > 80 ? '…' : '');
    }
    return edge.target_id;
  }

  return (
    <div className={styles.connectionSection}>
      <div className={styles.sectionHeader}>Rests on · {edges?.length ?? 0} source{edges?.length !== 1 ? 's' : ''}</div>

      <div className={styles.connectionList}>
        {(edges ?? []).map((edge: RestsOn) => (
          <div key={`${edge.target_kind}:${edge.target_id}`} className={styles.connectionRow}>
            <span className={styles.connectionMeta}>{edge.target_kind}</span>
            <span className={styles.connectionText}>{labelFor(edge)}</span>
            <span className={styles.connectionMeta}>{edge.role}</span>
            <button
              className={styles.removeBtn}
              onClick={() => removeMutation.mutate({ targetKind: edge.target_kind, tId: edge.target_id })}
              disabled={removeMutation.isPending}
            >
              remove
            </button>
          </div>
        ))}
      </div>

      <div className={styles.addRestsOnRow}>
        <select
          className={styles.kindSelect}
          value={kind}
          onChange={(e) => setKind(e.target.value as typeof kind)}
        >
          <option value="passage">passage</option>
          <option value="paper">paper</option>
          <option value="brick">brick</option>
          <option value="dataset">dataset</option>
        </select>
        <input
          className={styles.targetIdInput}
          value={targetId}
          onChange={(e) => setTargetId(e.target.value)}
          placeholder="target UUID"
        />
        <input
          className={styles.roleInput}
          value={role}
          onChange={(e) => setRole(e.target.value)}
          placeholder="role"
        />
        <button
          className={styles.addBtn}
          disabled={!isValidUUID || addMutation.isPending}
          onClick={() => addMutation.mutate()}
        >
          + Add
        </button>
      </div>
    </div>
  );
}

// ── BrickDetail ───────────────────────────────────────────────────────────────

function BrickDetail({
  brick,
  allBricks,
  onDeleted,
}: {
  brick: Brick;
  allBricks: Brick[];
  onDeleted: () => void;
}) {
  const queryClient = useQueryClient();

  const cementMutation = useMutation({
    mutationFn: () => bricksApi.cement(brick.id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['bricks', brick.project_id] }),
  });

  const typeMutation = useMutation({
    mutationFn: (type: string) => bricksApi.update(brick.id, { type }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['bricks', brick.project_id] }),
  });

  const deleteMutation = useMutation({
    mutationFn: () => bricksApi.delete(brick.id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['bricks', brick.project_id] });
      onDeleted();
    },
  });

  return (
    <div className={styles.brickDetail}>
      <div className={styles.brickHeader}>
        <select
          className={styles.brickTypeSelect}
          value={BRICK_TYPES.includes(brick.type as typeof BRICK_TYPES[number]) ? brick.type : 'other'}
          onChange={(e) => typeMutation.mutate(e.target.value)}
          disabled={brick.status === 'cemented'}
        >
          {BRICK_TYPES.map((t) => (
            <option key={t} value={t}>{t}</option>
          ))}
        </select>

        <div className={`${styles.statusChip} ${styles[brick.status]}`}>
          <span className={`${styles.statusDot} ${styles[brick.status]}`} />
          {brick.status}
        </div>

        {brick.status === 'draft' && (
          <button
            className={styles.cementBtn}
            onClick={() => cementMutation.mutate()}
            disabled={cementMutation.isPending || brick.assertion.trim().length < 5}
            title="Cement this brick — mark it as a stable, citable claim"
          >
            Cement
          </button>
        )}

        {brick.status === 'draft' && (
          <button
            className={styles.deleteBtn}
            onClick={() => deleteMutation.mutate()}
            disabled={deleteMutation.isPending}
          >
            Delete
          </button>
        )}
      </div>

      <AssertionEditor brick={brick} />

      <BuildsOnSection brick={brick} allBricks={allBricks} />
      <RestsOnSection brick={brick} allBricks={allBricks} />

      <div className={styles.provenanceRow}>
        <span>Drafted {formatDate(brick.drafted_at)}</span>
        {brick.cemented_at && <span>Cemented {formatDate(brick.cemented_at)}</span>}
        {brick.built_from_synthesis_id && <span>Derived from synthesis</span>}
      </div>
    </div>
  );
}

// ── NewBrickForm ──────────────────────────────────────────────────────────────

function NewBrickForm({
  projectId,
  onCreated,
  onCancel,
}: {
  projectId: string;
  onCreated: (b: Brick) => void;
  onCancel: () => void;
}) {
  const [assertion, setAssertion] = useState('');
  const [type, setType] = useState<string>('observation');

  const mutation = useMutation({
    mutationFn: () => bricksApi.create(projectId, type, assertion.trim()),
    onSuccess: (b) => {
      setAssertion('');
      onCreated(b);
    },
  });

  return (
    <div className={styles.newBrickForm}>
      <div className={styles.formLabel}>New brick — state a claim or observation</div>
      <textarea
        className={styles.newAssertionTextarea}
        value={assertion}
        onChange={(e) => setAssertion(e.target.value)}
        placeholder="A discrete, citable claim. Be precise — this will anchor your synthesis."
        autoFocus
      />
      <div className={styles.formRow}>
        <select
          className={styles.typeSelect}
          value={type}
          onChange={(e) => setType(e.target.value)}
        >
          {BRICK_TYPES.map((t) => (
            <option key={t} value={t}>{t}</option>
          ))}
        </select>
        <button
          className={styles.createBtn}
          disabled={assertion.trim().length < 5 || mutation.isPending}
          onClick={() => mutation.mutate()}
        >
          {mutation.isPending ? 'Creating…' : 'Create brick'}
        </button>
        <button className={styles.cancelBtn} onClick={onCancel}>
          Cancel
        </button>
      </div>
    </div>
  );
}

// ── Main view ─────────────────────────────────────────────────────────────────

export default function KnowledgeView() {
  const { projectId } = useParams({ from: '/app/projects/$projectId/knowledge' });
  const queryClient = useQueryClient();
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [showNewForm, setShowNewForm] = useState(false);

  const { data: project } = useQuery({
    queryKey: ['project', projectId],
    queryFn: () => projectsApi.get(projectId),
  });

  const { data: bricks } = useQuery({
    queryKey: ['bricks', projectId],
    queryFn: () => bricksApi.list(projectId),
  });

  const activeBrick = bricks?.find((b) => b.id === selectedId) ?? null;

  const handleCreated = (b: Brick) => {
    queryClient.invalidateQueries({ queryKey: ['bricks', projectId] });
    setSelectedId(b.id);
    setShowNewForm(false);
  };

  const handleDeleted = () => {
    setSelectedId(null);
  };

  const cemented = bricks?.filter((b) => b.status === 'cemented') ?? [];
  const drafts = bricks?.filter((b) => b.status === 'draft') ?? [];

  return (
    <div className={styles.layout}>
      {/* ── Sidebar ── */}
      <aside className={styles.sidebar}>
        <div className={styles.sidebarTop}>
          <div className={styles.projectLabel}>Active project</div>
          <div className={styles.projectName}>{project?.title ?? '…'}</div>
        </div>

        <div className={styles.sidebarListHeader}>
          <span>Bricks{bricks ? ` · ${bricks.length}` : ''}</span>
          <button className={styles.newBrickBtn} onClick={() => { setShowNewForm(true); setSelectedId(null); }}>
            + New
          </button>
        </div>

        <div className={styles.sidebarList}>
          {cemented.length > 0 && drafts.length > 0 && (
            <div style={{ padding: '4px 10px 2px', fontSize: 9, fontFamily: 'Inter, sans-serif', color: 'var(--text-faint)', letterSpacing: '0.1em', textTransform: 'uppercase' }}>
              Cemented
            </div>
          )}
          {cemented.map((b) => (
            <BrickListItem
              key={b.id}
              brick={b}
              active={b.id === selectedId}
              onClick={() => { setSelectedId(b.id); setShowNewForm(false); }}
            />
          ))}

          {cemented.length > 0 && drafts.length > 0 && (
            <div style={{ padding: '8px 10px 2px', fontSize: 9, fontFamily: 'Inter, sans-serif', color: 'var(--text-faint)', letterSpacing: '0.1em', textTransform: 'uppercase' }}>
              Draft
            </div>
          )}
          {drafts.map((b) => (
            <BrickListItem
              key={b.id}
              brick={b}
              active={b.id === selectedId}
              onClick={() => { setSelectedId(b.id); setShowNewForm(false); }}
            />
          ))}

          {bricks?.length === 0 && (
            <div style={{ padding: '20px 10px', fontSize: 12, fontFamily: 'Inter, sans-serif', color: 'var(--text-faint)', textAlign: 'center' }}>
              No bricks yet
            </div>
          )}
        </div>
      </aside>

      {/* ── Main ── */}
      <div className={styles.main}>
        <div className={styles.mainScroll}>
          {showNewForm && (
            <NewBrickForm
              projectId={projectId}
              onCreated={handleCreated}
              onCancel={() => setShowNewForm(false)}
            />
          )}

          {activeBrick ? (
            <BrickDetail
              key={activeBrick.id}
              brick={activeBrick}
              allBricks={bricks ?? []}
              onDeleted={handleDeleted}
            />
          ) : !showNewForm ? (
            <div className={styles.emptyState}>
              <div className={styles.emptyTitle}>No brick selected</div>
              <div className={styles.emptyDesc}>
                Select a brick from the sidebar, or create a new one.
              </div>
            </div>
          ) : null}
        </div>
      </div>
    </div>
  );
}

// ── BrickListItem (extracted to avoid inline creation in JSX) ─────────────────

function BrickListItem({
  brick,
  active,
  onClick,
}: {
  brick: Brick;
  active: boolean;
  onClick: () => void;
}) {
  return (
    <div
      className={`${styles.brickItem} ${active ? styles.brickItemActive : ''}`}
      onClick={onClick}
    >
      <div className={styles.brickItemTop}>
        <span className={`${styles.typeBadge} ${styles[typeClass(brick.type)]}`}>
          {brick.type}
        </span>
      </div>
      <div className={styles.brickItemAssertion}>{brick.assertion}</div>
      <div className={styles.brickItemMeta}>
        <span className={`${styles.statusDot} ${styles[brick.status]}`} />
        <span>{brick.status}</span>
        <span>{formatDate(brick.drafted_at)}</span>
      </div>
    </div>
  );
}
