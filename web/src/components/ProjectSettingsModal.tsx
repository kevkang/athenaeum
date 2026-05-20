import { useState, useEffect } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { projectsApi } from '../api/projects';
import { membersApi } from '../api/members';
import { useAuthStore } from '../store/auth';
import styles from './ProjectSettingsModal.module.css';

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

type Tab = 'general' | 'members';

export default function ProjectSettingsModal({
  projectId,
  onClose,
}: {
  projectId: string;
  onClose: () => void;
}) {
  const [tab, setTab] = useState<Tab>('general');
  const currentUserId = useAuthStore((s) => s.user?.id);
  const queryClient = useQueryClient();

  // ── General tab state ──────────────────────────────────────────────────────

  const { data: project } = useQuery({
    queryKey: ['project', projectId],
    queryFn: () => projectsApi.get(projectId),
  });

  const [title, setTitle] = useState('');
  const [description, setDescription] = useState('');
  const [isPublic, setIsPublic] = useState(false);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    if (project) {
      setTitle(project.title);
      setDescription(project.description ?? '');
      setIsPublic(project.is_public);
    }
  }, [project]);

  const updateMutation = useMutation({
    mutationFn: () =>
      projectsApi.update(projectId, {
        title: title.trim(),
        description: description.trim() || undefined,
        is_public: isPublic,
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['project', projectId] });
      queryClient.invalidateQueries({ queryKey: ['projects'] });
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
    },
  });

  const isOwner = project?.owner_id === currentUserId;

  // ── Members tab state ──────────────────────────────────────────────────────

  const { data: members } = useQuery({
    queryKey: ['members', projectId],
    queryFn: () => membersApi.list(projectId),
    enabled: tab === 'members',
  });

  const [newUserId, setNewUserId] = useState('');
  const [newRole, setNewRole] = useState('member');

  const addMutation = useMutation({
    mutationFn: () => membersApi.add(projectId, newUserId.trim(), newRole),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['members', projectId] });
      setNewUserId('');
    },
  });

  const removeMutation = useMutation({
    mutationFn: (userId: string) => membersApi.remove(projectId, userId),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['members', projectId] }),
  });

  const roleMutation = useMutation({
    mutationFn: ({ userId, role }: { userId: string; role: string }) =>
      membersApi.updateRole(projectId, userId, role),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['members', projectId] }),
  });

  return (
    <div className={styles.overlay} onClick={(e) => e.target === e.currentTarget && onClose()}>
      <div className={styles.modal}>
        <div className={styles.header}>
          <div className={styles.title}>Project settings</div>
          <div className={styles.tabs}>
            {(['general', 'members'] as Tab[]).map((t) => (
              <button
                key={t}
                className={`${styles.tab} ${tab === t ? styles.tabActive : ''}`}
                onClick={() => setTab(t)}
              >
                {t.charAt(0).toUpperCase() + t.slice(1)}
              </button>
            ))}
          </div>
        </div>

        <div className={styles.body}>
          {/* ── General tab ── */}
          {tab === 'general' && (
            <>
              <div className={styles.field}>
                <label className={styles.label}>Title</label>
                <input
                  className={styles.input}
                  value={title}
                  onChange={(e) => setTitle(e.target.value)}
                  disabled={!isOwner}
                />
              </div>

              <div className={styles.field}>
                <label className={styles.label}>Description</label>
                <textarea
                  className={styles.textarea}
                  value={description}
                  onChange={(e) => setDescription(e.target.value)}
                  disabled={!isOwner}
                />
              </div>

              <div className={styles.field}>
                <label className={styles.checkbox}>
                  <input
                    type="checkbox"
                    checked={isPublic}
                    onChange={(e) => setIsPublic(e.target.checked)}
                    disabled={!isOwner}
                  />
                  Make this project publicly discoverable
                </label>
              </div>

              {isOwner && (
                <div className={styles.saveRow}>
                  <button
                    className={styles.saveBtn}
                    disabled={!title.trim() || updateMutation.isPending}
                    onClick={() => updateMutation.mutate()}
                  >
                    {updateMutation.isPending ? 'Saving…' : 'Save changes'}
                  </button>
                  {saved && <span className={styles.savedHint}>Saved</span>}
                </div>
              )}

              {!isOwner && (
                <p style={{ fontFamily: 'Inter, sans-serif', fontSize: 12, color: 'var(--text-faint)', marginTop: 4 }}>
                  Only the project owner can edit settings.
                </p>
              )}
            </>
          )}

          {/* ── Members tab ── */}
          {tab === 'members' && (
            <>
              <div className={styles.sectionLabel}>
                Members · {members?.length ?? '…'}
              </div>

              <div className={styles.memberList}>
                {(members ?? []).map((m) => (
                  <div key={m.user_id} className={styles.memberRow}>
                    <span className={styles.memberId}>{m.user_id}</span>
                    {m.user_id === currentUserId && (
                      <span className={styles.memberYou}>you</span>
                    )}

                    {isOwner && m.role !== 'owner' ? (
                      <select
                        className={styles.roleSelect}
                        value={m.role}
                        onChange={(e) =>
                          roleMutation.mutate({ userId: m.user_id, role: e.target.value })
                        }
                      >
                        {['editor', 'viewer', 'member'].map((r) => (
                          <option key={r} value={r}>{r}</option>
                        ))}
                      </select>
                    ) : (
                      <span className={styles.roleLabel}>{m.role}</span>
                    )}

                    {isOwner && m.role !== 'owner' && (
                      <button
                        className={styles.removeMemberBtn}
                        onClick={() => removeMutation.mutate(m.user_id)}
                        disabled={removeMutation.isPending}
                      >
                        remove
                      </button>
                    )}
                  </div>
                ))}
              </div>

              {isOwner && (
                <div className={styles.addMemberForm}>
                  <div className={styles.sectionLabel}>Add member</div>
                  <div className={styles.addMemberRow}>
                    <input
                      className={styles.uuidInput}
                      value={newUserId}
                      onChange={(e) => setNewUserId(e.target.value)}
                      placeholder="user UUID"
                    />
                    <select
                      className={styles.addRoleSelect}
                      value={newRole}
                      onChange={(e) => setNewRole(e.target.value)}
                    >
                      {['member', 'editor', 'viewer'].map((r) => (
                        <option key={r} value={r}>{r}</option>
                      ))}
                    </select>
                    <button
                      className={styles.addBtn}
                      disabled={!UUID_RE.test(newUserId.trim()) || addMutation.isPending}
                      onClick={() => addMutation.mutate()}
                    >
                      Add
                    </button>
                  </div>
                  {addMutation.error && (
                    <div className={styles.errorMsg}>
                      {(addMutation.error as Error).message}
                    </div>
                  )}
                </div>
              )}
            </>
          )}
        </div>

        <div className={styles.footer}>
          <button className={styles.closeBtn} onClick={onClose}>Close</button>
        </div>
      </div>
    </div>
  );
}
