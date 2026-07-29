import { useState, useRef } from 'react';
import { Link, useNavigate } from '@tanstack/react-router';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { projectsApi, type Project } from '../api/projects';
import ProjectSettingsModal from '../components/ProjectSettingsModal';
import styles from './ProjectListPage.module.css';

function formatDate(iso: string) {
  return new Date(iso).toLocaleDateString('en-US', { year: 'numeric', month: 'short', day: 'numeric' });
}

function NewProjectModal({ onClose }: { onClose: () => void }) {
  const queryClient = useQueryClient();
  const [title, setTitle] = useState('');
  const [description, setDescription] = useState('');
  const [isPublic, setIsPublic] = useState(false);

  const mutation = useMutation({
    mutationFn: () => projectsApi.create({ title, description: description || undefined, is_public: isPublic }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['projects'] });
      onClose();
    },
  });

  return (
    <div className={styles.modalOverlay} onClick={(e) => e.target === e.currentTarget && onClose()}>
      <div className={styles.modal}>
        <div className={styles.modalTitle}>New project</div>

        <div className={styles.field}>
          <label className={styles.label}>Title</label>
          <input
            className={styles.input}
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            placeholder="e.g. Soil microbial C efficiency — temperate forests"
            autoFocus
          />
        </div>

        <div className={styles.field}>
          <label className={styles.label}>Description</label>
          <textarea
            className={styles.textarea}
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            placeholder="Optional — what is this project investigating?"
          />
        </div>

        <div className={styles.field}>
          <label className={styles.checkbox}>
            <input
              type="checkbox"
              checked={isPublic}
              onChange={(e) => setIsPublic(e.target.checked)}
            />
            Make this project publicly discoverable
          </label>
        </div>

        {mutation.error && (
          <div className={styles.error}>
            {(mutation.error as Error).message}
          </div>
        )}

        <div className={styles.modalActions}>
          <button className={styles.cancelBtn} onClick={onClose}>Cancel</button>
          <button
            className={styles.createBtn}
            disabled={!title.trim() || mutation.isPending}
            onClick={() => mutation.mutate()}
          >
            {mutation.isPending ? 'Creating…' : 'Create project'}
          </button>
        </div>
      </div>
    </div>
  );
}

function ImportModal({ onClose }: { onClose: () => void }) {
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const fileRef = useRef<HTMLInputElement>(null);
  const [filename, setFilename] = useState('');
  const [file, setFile] = useState<File | null>(null);

  const mutation = useMutation({
    mutationFn: () => projectsApi.importArchive(file!),
    onSuccess: (data) => {
      queryClient.invalidateQueries({ queryKey: ['projects'] });
      onClose();
      navigate({ to: '/projects/$projectId', params: { projectId: data.project_id } });
    },
  });

  return (
    <div className={styles.modalOverlay} onClick={(e) => e.target === e.currentTarget && onClose()}>
      <div className={styles.modal}>
        <div className={styles.modalTitle}>Import project archive</div>

        <div className={styles.field}>
          <label className={styles.label}>Archive file (.ath)</label>
          <input
            ref={fileRef}
            type="file"
            accept=".ath,.yaml,.yml"
            style={{ display: 'none' }}
            onChange={(e) => {
              const f = e.target.files?.[0] ?? null;
              setFile(f);
              setFilename(f?.name ?? '');
            }}
          />
          <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
            <input
              className={styles.input}
              value={filename}
              readOnly
              placeholder="No file selected"
              onClick={() => fileRef.current?.click()}
              style={{ cursor: 'pointer' }}
            />
            <button
              className={styles.cancelBtn}
              style={{ whiteSpace: 'nowrap' }}
              onClick={() => fileRef.current?.click()}
            >
              Choose file
            </button>
          </div>
        </div>

        <p style={{ fontFamily: 'Inter, sans-serif', fontSize: 12, color: 'var(--text-faint)', marginBottom: 16, lineHeight: 1.6 }}>
          Imports an <code style={{ fontFamily: 'JetBrains Mono, monospace', fontSize: 11 }}>.ath</code> archive as a new project. Papers, statements, syntheses, and provenance log are all restored.
        </p>

        {mutation.error && (
          <div className={styles.error}>{(mutation.error as Error).message}</div>
        )}

        <div className={styles.modalActions}>
          <button className={styles.cancelBtn} onClick={onClose}>Cancel</button>
          <button
            className={styles.createBtn}
            disabled={!file || mutation.isPending}
            onClick={() => mutation.mutate()}
          >
            {mutation.isPending ? 'Importing…' : 'Import'}
          </button>
        </div>
      </div>
    </div>
  );
}

export default function ProjectListPage() {
  const [showModal, setShowModal] = useState(false);
  const [showImport, setShowImport] = useState(false);
  const [settingsProjectId, setSettingsProjectId] = useState<string | null>(null);
  const { data: projects, isLoading, error } = useQuery({
    queryKey: ['projects'],
    queryFn: projectsApi.list,
  });

  return (
    <>
      {/* This page renders inside AppShell which provides the chrome.
          ProjectList has no sidebar, so it spans the full body width. */}
      <div className={styles.page}>
        <div className={styles.header}>
          <h1 className={styles.title}>Your projects</h1>
          <p className={styles.subtitle}>
            Each project is a contained research argument — papers, statements, and syntheses.
          </p>
        </div>

        <div className={styles.actions}>
          <button className={styles.newBtn} onClick={() => setShowModal(true)}>
            + New project
          </button>
          <button className={styles.importBtn} onClick={() => setShowImport(true)}>
            Import .ath
          </button>
        </div>

        {isLoading && (
          <p className={`${styles.subtitle}`} style={{ marginTop: 32 }}>Loading…</p>
        )}

        {error && (
          <div className={styles.error}>
            Failed to load projects. {(error as Error).message}
          </div>
        )}

        {projects && projects.length === 0 && (
          <div className={styles.empty}>
            <div className={styles.emptyTitle}>No projects yet</div>
            <div className={styles.emptyDesc}>
              Create your first project to start building your argument.
            </div>
          </div>
        )}

        {projects && projects.length > 0 && (
          <div className={styles.grid}>
            {projects.map((p: Project) => (
              <Link
                key={p.id}
                to="/projects/$projectId"
                params={{ projectId: p.id }}
                className={styles.projectCard}
              >
                <button
                  className={styles.projectSettingsBtn}
                  title="Project settings"
                  onClick={(e) => {
                    e.preventDefault();
                    e.stopPropagation();
                    setSettingsProjectId(p.id);
                  }}
                >
                  ⚙
                </button>
                <div className={styles.projectTitle}>{p.title}</div>
                {p.description && (
                  <div className={styles.projectDesc}>{p.description}</div>
                )}
                <div className={styles.projectMeta}>
                  <span>Created {formatDate(p.created_at)}</span>
                  <span>{p.license}</span>
                  {p.is_public && <span className={styles.publicBadge}>Public</span>}
                </div>
              </Link>
            ))}
          </div>
        )}
      </div>

      {showModal && <NewProjectModal onClose={() => setShowModal(false)} />}
      {showImport && <ImportModal onClose={() => setShowImport(false)} />}
      {settingsProjectId && (
        <ProjectSettingsModal
          projectId={settingsProjectId}
          onClose={() => setSettingsProjectId(null)}
        />
      )}
    </>
  );
}
