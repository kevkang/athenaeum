import { useEffect, useMemo, useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import {
  zoteroApi,
  type ZoteroBrowseClient,
  type ZoteroCollection,
  type ZoteroSortDirection,
  type ZoteroSortField,
} from '../api/zotero';
import styles from './ZoteroBrowserModal.module.css';

const LIMIT = 50;

// A column's sensible default sort direction when you first click it —
// e.g. dates make more sense starting newest-first, titles A-first.
const DEFAULT_DIRECTION: Record<ZoteroSortField, ZoteroSortDirection> = {
  title: 'asc',
  creator: 'asc',
  year: 'desc',
  date_added: 'desc',
};

interface CollectionNode extends ZoteroCollection {
  children: CollectionNode[];
}

function buildTree(collections: ZoteroCollection[]): CollectionNode[] {
  const byKey = new Map<string, CollectionNode>(
    collections.map((c) => [c.key, { ...c, children: [] }]),
  );
  const roots: CollectionNode[] = [];
  for (const node of byKey.values()) {
    if (node.parent_key && byKey.has(node.parent_key)) {
      byKey.get(node.parent_key)!.children.push(node);
    } else {
      roots.push(node);
    }
  }
  const sortByName = (a: CollectionNode, b: CollectionNode) => a.name.localeCompare(b.name);
  const sortRecursive = (nodes: CollectionNode[]) => {
    nodes.sort(sortByName);
    nodes.forEach((n) => sortRecursive(n.children));
  };
  sortRecursive(roots);
  return roots;
}

function CollectionTreeItem({
  node,
  depth,
  selected,
  onSelect,
}: {
  node: CollectionNode;
  depth: number;
  selected: string | null;
  onSelect: (key: string) => void;
}) {
  return (
    <>
      <button
        className={`${styles.collectionItem} ${selected === node.key ? styles.collectionItemActive : ''}`}
        style={{ paddingLeft: 10 + depth * 14 }}
        onClick={() => onSelect(node.key)}
        title={node.name}
      >
        {node.name}
      </button>
      {node.children.map((child) => (
        <CollectionTreeItem
          key={child.key}
          node={child}
          depth={depth + 1}
          selected={selected}
          onSelect={onSelect}
        />
      ))}
    </>
  );
}

function formatDate(iso: string | null): string {
  if (!iso) return '—';
  return new Date(iso).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
}

export default function ZoteroBrowserModal({
  projectId,
  onClose,
  client = zoteroApi,
  title = 'Browse Zotero library',
  sourceKey = 'sync',
}: {
  projectId: string;
  onClose: () => void;
  /** Defaults to the Web API client (zoteroApi) — pass zoteroLocalApi to
   * browse a bind-mounted local library instead. */
  client?: ZoteroBrowseClient;
  title?: string;
  /** Distinguishes react-query cache entries between sync/local so switching
   * sources doesn't show stale results from the other one. */
  sourceKey?: string;
}) {
  const queryClient = useQueryClient();

  const [selectedCollection, setSelectedCollection] = useState<string | null>(null);
  const [filterQuery, setFilterQuery] = useState('');
  const [debouncedQuery, setDebouncedQuery] = useState('');
  const [sortField, setSortField] = useState<ZoteroSortField>('date_added');
  const [sortDirection, setSortDirection] = useState<ZoteroSortDirection>('desc');
  const [page, setPage] = useState(0);

  useEffect(() => {
    const t = setTimeout(() => setDebouncedQuery(filterQuery.trim()), 350);
    return () => clearTimeout(t);
  }, [filterQuery]);

  // Reset to page 0 whenever the scope/sort/filter changes. Done during
  // render (React's recommended pattern for "adjusting state when a prop
  // changes") rather than in a useEffect, which would paint one frame with
  // the stale page before the effect fires and triggers a second render.
  const scopeKey = `${selectedCollection ?? ''}|${debouncedQuery}|${sortField}|${sortDirection}`;
  const [prevScopeKey, setPrevScopeKey] = useState(scopeKey);
  if (scopeKey !== prevScopeKey) {
    setPrevScopeKey(scopeKey);
    setPage(0);
  }

  const { data: collections } = useQuery({
    queryKey: ['zotero-collections', sourceKey],
    queryFn: () => client.getCollections(),
  });

  const tree = useMemo(() => buildTree(collections ?? []), [collections]);

  const {
    data: browseResult,
    isFetching,
    error,
  } = useQuery({
    queryKey: [
      'zotero-browse',
      sourceKey,
      projectId,
      selectedCollection,
      debouncedQuery,
      sortField,
      sortDirection,
      page,
    ],
    queryFn: () =>
      client.browseItems({
        projectId,
        collectionKey: selectedCollection ?? undefined,
        q: debouncedQuery || undefined,
        start: page * LIMIT,
        limit: LIMIT,
        sort: sortField,
        direction: sortDirection,
      }),
  });

  const importMutation = useMutation({
    mutationFn: (itemKey: string) => client.importItem(projectId, itemKey),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['papers', projectId] });
      queryClient.invalidateQueries({ queryKey: ['zotero-browse', sourceKey] });
    },
  });

  function toggleSort(field: ZoteroSortField) {
    if (field === sortField) {
      setSortDirection((d) => (d === 'asc' ? 'desc' : 'asc'));
    } else {
      setSortField(field);
      setSortDirection(DEFAULT_DIRECTION[field]);
    }
  }

  function sortArrow(field: ZoteroSortField) {
    if (field !== sortField) return null;
    return <span className={styles.sortArrow}>{sortDirection === 'asc' ? '▲' : '▼'}</span>;
  }

  const items = browseResult?.items ?? [];
  const total = browseResult?.total ?? 0;
  const totalPages = Math.max(1, Math.ceil(total / LIMIT));
  const rangeStart = total === 0 ? 0 : page * LIMIT + 1;
  const rangeEnd = Math.min(total, page * LIMIT + items.length);

  return (
    <div className={styles.overlay} onClick={(e) => e.target === e.currentTarget && onClose()}>
      <div className={styles.modal}>
        <div className={styles.header}>
          <div className={styles.title}>{title}</div>
          <input
            className={styles.filterInput}
            value={filterQuery}
            onChange={(e) => setFilterQuery(e.target.value)}
            placeholder="Filter by title, creator, or year…"
          />
          <button className={styles.closeBtn} onClick={onClose}>Close</button>
        </div>

        <div className={styles.body}>
          <aside className={styles.collectionsPane}>
            <button
              className={`${styles.collectionItem} ${selectedCollection === null ? styles.collectionItemActive : ''}`}
              onClick={() => setSelectedCollection(null)}
            >
              All items
            </button>
            {tree.map((node) => (
              <CollectionTreeItem
                key={node.key}
                node={node}
                depth={0}
                selected={selectedCollection}
                onSelect={setSelectedCollection}
              />
            ))}
          </aside>

          <div className={styles.tableWrap}>
            {error && (
              <div className={styles.errorHint}>
                {(error as { response?: { data?: { detail?: string } } })?.response?.data?.detail ??
                  'Could not load items from Zotero.'}
              </div>
            )}

            {!error && (
              <table className={styles.table}>
                <thead>
                  <tr>
                    <th className={`${styles.th} ${sortField === 'title' ? styles.thActive : ''}`} onClick={() => toggleSort('title')}>
                      Title{sortArrow('title')}
                    </th>
                    <th className={`${styles.th} ${sortField === 'creator' ? styles.thActive : ''}`} onClick={() => toggleSort('creator')}>
                      Creator{sortArrow('creator')}
                    </th>
                    <th className={`${styles.th} ${sortField === 'year' ? styles.thActive : ''}`} onClick={() => toggleSort('year')}>
                      Year{sortArrow('year')}
                    </th>
                    <th className={`${styles.th} ${sortField === 'date_added' ? styles.thActive : ''}`} onClick={() => toggleSort('date_added')}>
                      Date added{sortArrow('date_added')}
                    </th>
                    <th className={styles.th} />
                  </tr>
                </thead>
                <tbody>
                  {items.map((item) => (
                    <tr key={item.item_key} className={styles.row}>
                      <td className={styles.rowTitle}>{item.title}</td>
                      <td className={styles.rowCreators}>{item.creators.join(', ') || '—'}</td>
                      <td className={styles.rowMuted}>{item.year ?? '—'}</td>
                      <td className={styles.rowMuted}>{formatDate(item.date_added)}</td>
                      <td>
                        <button
                          className={styles.addBtn}
                          disabled={item.already_in_project || importMutation.isPending}
                          title={item.already_in_project ? 'Already in project' : 'Import into project'}
                          onClick={() => importMutation.mutate(item.item_key)}
                        >
                          {item.already_in_project ? '✓' : '+'}
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}

            {!error && isFetching && <div className={styles.loadingHint}>Loading…</div>}
            {!error && !isFetching && items.length === 0 && (
              <div className={styles.emptyHint}>No items found.</div>
            )}
          </div>
        </div>

        <div className={styles.footer}>
          <span>
            {total > 0 ? `Showing ${rangeStart}–${rangeEnd} of ${total}` : 'No items'}
          </span>
          <div className={styles.pageControls}>
            <button
              className={styles.pageBtn}
              disabled={page === 0}
              onClick={() => setPage((p) => Math.max(0, p - 1))}
            >
              ← Prev
            </button>
            <span>Page {page + 1} of {totalPages}</span>
            <button
              className={styles.pageBtn}
              disabled={page + 1 >= totalPages}
              onClick={() => setPage((p) => p + 1)}
            >
              Next →
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
