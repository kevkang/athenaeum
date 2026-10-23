import { useState } from 'react';
import { useMutation, useQuery } from '@tanstack/react-query';
import { nucleateApi, type CanvasNode } from '../../api/nucleate';
import { papersApi } from '../../api/papers';
import { zoteroApi, type ZoteroSearchResult } from '../../api/zotero';
import { zoteroLocalApi } from '../../api/zoteroLocal';
import styles from './NodeDrawer.module.css';

type ZoteroSource = 'sync' | 'local';

function errorText(e: unknown): string {
  return (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail
    ?? (e as Error)?.message ?? 'Something went wrong';
}

/**
 * Supplying a paper's full text from the node drawer (D-N23), the same ways
 * the Read tab adds papers: drop a PDF, choose one, or take it from Zotero.
 * The paper joins the library either way, and re-ingests; its node gains
 * full-text evidence on its own when that finishes (D-N15).
 */
export default function SupplyPaper({
  node,
  projectId,
  nucleusId,
  prominent,
  onSupplied,
}: {
  node: CanvasNode;
  projectId: string;
  nucleusId: string;
  /** "User must supply this paper": shown as the next step, not an option. */
  prominent: boolean;
  onSupplied: () => void;
}) {
  const [over, setOver] = useState(false);
  const [received, setReceived] = useState<string | null>(null);
  const [zotero, setZotero] = useState<ZoteroSource | null>(null);
  const [query, setQuery] = useState(node.paper.doi ?? node.paper.title);

  const { data: sync } = useQuery({ queryKey: ['zotero-status'], queryFn: zoteroApi.getStatus });
  const { data: local } = useQuery({ queryKey: ['zotero-local-status'], queryFn: zoteroLocalApi.getStatus });
  const sources: ZoteroSource[] = [
    ...(sync?.connected ? ['sync' as const] : []),
    ...(local?.available ? ['local' as const] : []),
  ];

  const upload = useMutation({
    mutationFn: async (file: File) => {
      await nucleateApi.addToLibrary(nucleusId, node.node_id);
      await papersApi.reupload(node.paper.id, file);
      return file.name;
    },
    onSuccess: (name) => {
      setReceived(`${name} received.`);
      onSupplied();
    },
  });

  const search = useQuery({
    queryKey: ['zotero-supply', zotero, query],
    queryFn: () => (zotero === 'local' ? zoteroLocalApi : zoteroApi).search(query, projectId),
    enabled: zotero !== null && query.trim().length >= 3,
  });

  const take = useMutation({
    mutationFn: (item: ZoteroSearchResult) =>
      (zotero === 'local' ? zoteroLocalApi : zoteroApi).supplyPaper(projectId, item.item_key, node.paper.id),
    onSuccess: (_paper, item) => {
      setReceived(`Taken from Zotero: ${item.title}.`);
      setZotero(null);
      onSupplied();
    },
  });

  function accept(files: FileList | null) {
    const file = files?.[0];
    if (file) upload.mutate(file);
  }

  if (received) {
    return (
      <section className={styles.section}>
        <div className={styles.supplied}>
          {received} Athenaeum is reading it now; this paper’s evidence appears when that’s done.
          It’s in your library.
        </div>
      </section>
    );
  }

  const busy = upload.isPending || take.isPending;
  return (
    <section className={styles.section} data-prominent={prominent}>
      <div className={styles.sectionLabel}>{prominent ? 'Supply this paper' : 'Add the full text'}</div>
      {prominent && (
        <p className={styles.supplyNote}>
          No full text or abstract could be found{node.paper.oa_checked_at ? ', and Unpaywall has no open-access copy' : ''}.
          Supply the PDF to draw evidence from it.
        </p>
      )}
      <label
        className={styles.dropZone}
        data-over={over}
        onDragOver={(e) => {
          e.preventDefault();
          setOver(true);
        }}
        onDragLeave={() => setOver(false)}
        onDrop={(e) => {
          e.preventDefault();
          setOver(false);
          if (!busy) accept(e.dataTransfer.files);
        }}
      >
        {upload.isPending ? 'Uploading…' : 'Drop a PDF here, or choose one'}
        <input
          type="file"
          accept=".pdf,.txt,application/pdf,text/plain"
          hidden
          disabled={busy}
          onChange={(e) => {
            accept(e.target.files);
            e.target.value = '';
          }}
        />
      </label>

      {sources.length > 0 && zotero === null && (
        <div className={styles.inline}>
          {sources.map((s) => (
            <button key={s} className={styles.btnQuiet} disabled={busy} onClick={() => setZotero(s)}>
              {sources.length > 1 ? `Find in Zotero (${s === 'local' ? 'local' : 'web'})` : 'Find in Zotero'}
            </button>
          ))}
        </div>
      )}
      {zotero !== null && (
        <div className={styles.zoteroSupply}>
          <div className={styles.inline}>
            <input
              className={styles.input}
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search your Zotero library"
              autoFocus
            />
            <button className={styles.btnQuiet} onClick={() => setZotero(null)}>Cancel</button>
          </div>
          {search.isFetching && <div className={styles.faint}>Searching…</div>}
          {search.data?.length === 0 && <div className={styles.faint}>Nothing in Zotero matches.</div>}
          <ul className={styles.zoteroResults}>
            {search.data?.map((item) => (
              <li key={item.item_key}>
                <div className={styles.zoteroTitle}>{item.title}</div>
                <div className={styles.faint}>
                  {[item.creators[0], item.year, item.doi && `doi:${item.doi}`].filter(Boolean).join(' · ')}
                </div>
                <button className={styles.btn} disabled={busy} onClick={() => take.mutate(item)}>
                  Use this
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}
      {(upload.isError || take.isError || search.isError) && (
        <div className={styles.error}>{errorText(upload.error ?? take.error ?? search.error)}</div>
      )}
    </section>
  );
}
