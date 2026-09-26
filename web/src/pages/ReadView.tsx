import { useState, useEffect, useRef } from 'react';
import { useParams } from '@tanstack/react-router';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { papersApi, type Paper } from '../api/papers';
import { backtraceApi } from '../api/backtrace';
import ReaderPane from '../components/ReaderPane';
import PapersSidebar, { type UploadQueueItem } from '../components/workspace/PapersSidebar';
import BacktracePanel from '../components/workspace/BacktracePanel';
import styles from './Workspace.module.css';

// Files droppable anywhere on the page to add a paper to the project.
const DROPPABLE_SUFFIXES = ['.pdf', '.txt'];

function isDroppableFile(file: File): boolean {
  const name = file.name.toLowerCase();
  return DROPPABLE_SUFFIXES.some((suf) => name.endsWith(suf));
}

/**
 * Read: the project's papers on the left, the selected paper on the right.
 *
 * This replaced the three-pane Workspace when the statement graph was retired.
 * The right pane keeps two modes rather than one: `read` is the paper itself,
 * and `backtrace` is the single-path stepper, which exists specifically to
 * trace a claim without leaving the paper it was read in (see
 * BacktracePanel's own module comment). Multi-branch exploration is the
 * Backtrace tab's tree instead.
 *
 * There is no resize divider: it used to size the reader against the graph
 * pane, and with nothing to size against, the sidebar keeps its fixed width
 * and the reader takes the rest.
 */
export default function ReadView() {
  const { projectId } = useParams({ from: '/app/projects/$projectId/read' });
  const queryClient = useQueryClient();

  const [selectedPaperId, setSelectedPaperId] = useState<string | null>(null);
  const [readerMode, setReaderMode] = useState<'read' | 'backtrace'>('read');
  // The chain the stepper is currently walking. Independent of the Backtrace
  // tab's own chain selection — starting a trace here should not silently
  // retarget a tree the user left open there.
  const [activeBacktrace, setActiveBacktrace] = useState<{ chainId: string } | null>(null);

  const [isDragOver, setIsDragOver] = useState(false);
  const dragDepthRef = useRef(0);
  const [uploadQueue, setUploadQueue] = useState<UploadQueueItem[]>([]);

  const { data: papers = [], isLoading: papersLoading } = useQuery({
    queryKey: ['papers', projectId],
    queryFn: () => papersApi.listForProject(projectId),
    // A freshly added paper is a 'ghost' until ingestion finishes. Poll only
    // while one recent enough to still be working is present, so an old failed
    // ghost doesn't leave this polling forever.
    refetchInterval: (q) => {
      const data = q.state.data as Paper[] | undefined;
      if (!data) return false;
      const tenMinAgo = Date.now() - 10 * 60 * 1000;
      return data.some(
        (p) => p.status === 'ghost' && new Date(p.created_at).getTime() > tenMinAgo,
      ) ? 3000 : false;
    },
  });

  const startBacktraceMutation = useMutation({
    mutationFn: ({ sentenceId, text }: { sentenceId: string; text: string }) =>
      backtraceApi.start(projectId, selectedPaperId!, sentenceId, text),
    onSuccess: (chain) => {
      setActiveBacktrace({ chainId: chain.id });
      setReaderMode('backtrace');
      // The Backtrace tab reads its chain list fresh, so a trace started here
      // shows up there without any shared state between the two views.
      queryClient.invalidateQueries({ queryKey: ['backtrace-chains', projectId] });
    },
  });

  async function processUploadQueue(files: File[]) {
    const items: UploadQueueItem[] = files.map((f) => ({
      id: `${f.name}-${f.size}-${Date.now()}-${Math.random()}`,
      name: f.name,
      status: 'pending',
    }));
    setUploadQueue((q) => [...q, ...items]);

    for (let i = 0; i < files.length; i++) {
      const item = items[i];
      setUploadQueue((q) => q.map((x) => (x.id === item.id ? { ...x, status: 'uploading' } : x)));
      try {
        await papersApi.upload(files[i], projectId, {});
        setUploadQueue((q) => q.map((x) => (x.id === item.id ? { ...x, status: 'done' } : x)));
        queryClient.invalidateQueries({ queryKey: ['papers', projectId] });
      } catch (err) {
        const message = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail
          ?? 'Upload failed';
        setUploadQueue((q) => q.map((x) => (x.id === item.id ? { ...x, status: 'error', error: message } : x)));
      }
    }
  }

  // Safety net: the browser's default action for a dropped file (anywhere in
  // the viewport, not just our drop zone) is to navigate to/open it. Blocking
  // dragover/drop at the window level guarantees that never happens while
  // this page is mounted, regardless of exactly which pane the drop lands on.
  useEffect(() => {
    const preventDefault = (e: DragEvent) => e.preventDefault();
    window.addEventListener('dragover', preventDefault);
    window.addEventListener('drop', preventDefault);
    return () => {
      window.removeEventListener('dragover', preventDefault);
      window.removeEventListener('drop', preventDefault);
    };
  }, []);

  function handlePageDragEnter(e: React.DragEvent) {
    e.preventDefault();
    if (!e.dataTransfer.types.includes('Files')) return;
    dragDepthRef.current += 1;
    setIsDragOver(true);
  }

  function handlePageDragLeave(e: React.DragEvent) {
    e.preventDefault();
    dragDepthRef.current = Math.max(0, dragDepthRef.current - 1);
    if (dragDepthRef.current === 0) setIsDragOver(false);
  }

  function handlePageDrop(e: React.DragEvent) {
    e.preventDefault();
    dragDepthRef.current = 0;
    setIsDragOver(false);
    const files = Array.from(e.dataTransfer.files).filter(isDroppableFile);
    if (files.length > 0) void processUploadQueue(files);
  }

  const backtraceError = startBacktraceMutation.isError
    ? ((startBacktraceMutation.error as { response?: { data?: { detail?: string } } })?.response
        ?.data?.detail ?? 'Could not start backtrace')
    : null;

  return (
    <div
      className={styles.workspace}
      onDragEnter={handlePageDragEnter}
      onDragOver={(e) => e.preventDefault()}
      onDragLeave={handlePageDragLeave}
      onDrop={handlePageDrop}
    >
      <div className={styles.workspaceBody}>
        <PapersSidebar
          projectId={projectId}
          papers={papers}
          papersLoading={papersLoading}
          selectedPaperId={selectedPaperId}
          onSelectPaper={setSelectedPaperId}
          isDragOver={isDragOver}
          uploadQueue={uploadQueue}
          onClearUploadQueue={() =>
            setUploadQueue((q) => q.filter((x) => x.status === 'uploading' || x.status === 'pending'))
          }
        />

        <div className={styles.readerPane}>
          {activeBacktrace && (
            <div className={styles.readerModeToggle}>
              <button
                className={`${styles.readerModeBtn} ${readerMode === 'read' ? styles.readerModeBtnActive : ''}`}
                onClick={() => setReaderMode('read')}
              >
                Read
              </button>
              <button
                className={`${styles.readerModeBtn} ${readerMode === 'backtrace' ? styles.readerModeBtnActive : ''}`}
                onClick={() => setReaderMode('backtrace')}
              >
                Backtrace
              </button>
            </div>
          )}

          {isDragOver ? (
            <div
              className={styles.emptyState}
              style={{ outline: '2px dashed var(--accent)', outlineOffset: -8 }}
            >
              <div className={styles.emptyTitle}>Drop to add</div>
              <div className={styles.emptyDesc}>Release to upload these PDFs to this project.</div>
            </div>
          ) : readerMode === 'backtrace' && activeBacktrace ? (
            <BacktracePanel
              key={activeBacktrace.chainId}
              chainId={activeBacktrace.chainId}
              projectId={projectId}
            />
          ) : selectedPaperId ? (
            <ReaderPane
              paperId={selectedPaperId}
              projectId={projectId}
              onBacktrace={(sentenceId, text) =>
                startBacktraceMutation.mutate({ sentenceId, text })
              }
              backtracePending={startBacktraceMutation.isPending}
              backtraceError={backtraceError}
            />
          ) : (
            <div className={styles.emptyState}>
              <div className={styles.emptyTitle}>Read</div>
              <div className={styles.emptyDesc}>
                Select a paper from the sidebar to read it — or drag PDFs in anywhere on this page
                to add them.
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
