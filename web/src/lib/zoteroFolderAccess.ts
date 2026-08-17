/** Client-side access to a user-picked Zotero data directory, so "Zotero
 * (Local)" works against a hosted deployment — the server has no filesystem
 * access to the browsing user's machine, so the browser has to read
 * zotero.sqlite and resolve attachment PDFs itself (see
 * ZoteroBrowserImportPanel + api/zotero_local.py's browser-import/parse
 * endpoint, which only ever receives the bare sqlite file).
 *
 * Two pickers, one interface:
 *  - `showDirectoryPicker()` (Chrome/Edge only — Firefox has explicitly
 *    declined to implement it on privacy/security grounds) gives a real,
 *    re-resolvable directory handle: attachment lookups walk the tree lazily.
 *  - `<input webkitdirectory>` (Chrome, Firefox, Edge, Safari) gives a flat
 *    FileList with each file's path relative to the picked folder — no
 *    persisted handle, so re-importing later means re-picking the folder.
 */

export interface ZoteroFolderHandle {
  sqliteFile: File;
  /** False for the <input webkitdirectory> fallback — re-scanning later
   * requires the user to pick the folder again rather than silently
   * re-reading a saved handle. */
  supportsPersistence: boolean;
  /** relativePath is relative to the Zotero data directory itself, e.g.
   * "storage/ABCD1234/paper.pdf" (matches attachment_relative_path from the
   * parse endpoint). Returns null if the file can't be found — a missing
   * attachment on disk isn't exceptional, Zotero libraries routinely
   * reference files that were since moved or never fully downloaded. */
  getAttachmentFile(relativePath: string): Promise<File | null>;
}

interface FileSystemFileHandleLike {
  getFile(): Promise<File>;
}

type FileSystemPermissionState = 'granted' | 'denied' | 'prompt';

interface FileSystemDirectoryHandleLike {
  readonly name: string;
  getFileHandle(name: string): Promise<FileSystemFileHandleLike>;
  getDirectoryHandle(name: string): Promise<FileSystemDirectoryHandleLike>;
  queryPermission(descriptor: { mode: 'read' }): Promise<FileSystemPermissionState>;
  requestPermission(descriptor: { mode: 'read' }): Promise<FileSystemPermissionState>;
}

declare global {
  interface Window {
    showDirectoryPicker?: () => Promise<FileSystemDirectoryHandleLike>;
  }
}

const NO_SQLITE_MESSAGE =
  'No zotero.sqlite found in the selected folder — pick your Zotero data ' +
  'directory (Zotero: Preferences → Advanced → "Show Data Directory").';

export function isDirectoryPickerSupported(): boolean {
  return typeof window !== 'undefined' && typeof window.showDirectoryPicker === 'function';
}

export function isPickerCancelled(error: unknown): boolean {
  return error instanceof DOMException && error.name === 'AbortError';
}

export async function pickZoteroFolder(): Promise<ZoteroFolderHandle> {
  return isDirectoryPickerSupported() ? pickViaFileSystemAccess() : pickViaInputFallback();
}

/** Builds the ZoteroFolderHandle wrapper around a directory handle, whether
 * it just came out of a fresh picker or was restored from IndexedDB — same
 * lazy directory-resolution logic either way. */
async function buildHandleFromRoot(
  rootHandle: FileSystemDirectoryHandleLike,
): Promise<ZoteroFolderHandle> {
  const sqliteHandle = await rootHandle.getFileHandle('zotero.sqlite').catch(() => null);
  if (!sqliteHandle) throw new Error(NO_SQLITE_MESSAGE);
  const sqliteFile = await sqliteHandle.getFile();

  const dirCache = new Map<string, FileSystemDirectoryHandleLike>([['', rootHandle]]);

  async function resolveDir(path: string): Promise<FileSystemDirectoryHandleLike | null> {
    const cached = dirCache.get(path);
    if (cached) return cached;
    const segments = path.split('/');
    const name = segments.pop()!;
    const parent = await resolveDir(segments.join('/'));
    if (!parent) return null;
    try {
      const child = await parent.getDirectoryHandle(name);
      dirCache.set(path, child);
      return child;
    } catch {
      return null;
    }
  }

  return {
    sqliteFile,
    supportsPersistence: true,
    async getAttachmentFile(relativePath: string): Promise<File | null> {
      const segments = relativePath.split('/');
      const filename = segments.pop()!;
      const dir = await resolveDir(segments.join('/'));
      if (!dir) return null;
      try {
        const fileHandle = await dir.getFileHandle(filename);
        return await fileHandle.getFile();
      } catch {
        return null;
      }
    },
  };
}

async function pickViaFileSystemAccess(): Promise<ZoteroFolderHandle> {
  const rootHandle = await window.showDirectoryPicker!();
  void saveStoredHandle(rootHandle);
  return buildHandleFromRoot(rootHandle);
}

function pickViaInputFallback(): Promise<ZoteroFolderHandle> {
  return new Promise((resolve, reject) => {
    const input = document.createElement('input');
    input.type = 'file';
    input.webkitdirectory = true;
    input.multiple = true;
    input.style.display = 'none';
    document.body.appendChild(input);

    input.addEventListener(
      'change',
      () => {
        document.body.removeChild(input);
        const files = Array.from(input.files ?? []);
        if (files.length === 0) {
          reject(new DOMException('No folder selected', 'AbortError'));
          return;
        }

        // webkitRelativePath is "<PickedFolderName>/zotero.sqlite" or
        // "<PickedFolderName>/storage/ABCD1234/paper.pdf" — strip the
        // top-level folder name so paths line up with
        // attachment_relative_path, which is relative to the data
        // directory itself, not to whatever the user named the folder.
        const byPath = new Map<string, File>();
        for (const file of files) {
          const stripped = file.webkitRelativePath.split('/').slice(1).join('/');
          byPath.set(stripped, file);
        }

        const sqliteFile = byPath.get('zotero.sqlite');
        if (!sqliteFile) {
          reject(new Error(NO_SQLITE_MESSAGE));
          return;
        }

        resolve({
          sqliteFile,
          supportsPersistence: false,
          async getAttachmentFile(relativePath: string): Promise<File | null> {
            return byPath.get(relativePath) ?? null;
          },
        });
      },
      { once: true },
    );

    // <input type=file> gives no reliable cancel event across browsers —
    // if the user dismisses the dialog, this promise just never settles,
    // same as every other native file input in this app.
    input.click();
  });
}

/* ── Persisting the picked directory handle ──
 *
 * FileSystemDirectoryHandle is structured-cloneable, so IndexedDB can store
 * it directly (no serialization needed) — this is what lets a returning user
 * skip re-picking their whole Zotero folder every session. The <input
 * webkitdirectory> fallback has no equivalent: a FileList has no persisted
 * handle, so those users are unaffected by any of this (pickZoteroFolder()
 * never calls into it for that path).
 *
 * The OS-level grant behind a saved handle can still lapse (e.g. after a
 * browser restart) without the handle itself becoming invalid — that shows
 * up as queryPermission() returning 'prompt' rather than 'granted', which is
 * why restoring is split into a fully silent check (safe to call on mount)
 * and an explicit reconnect that requires a user gesture (permission
 * prompts cannot be triggered otherwise).
 */

const DB_NAME = 'athenaeum-zotero-folder';
const STORE_NAME = 'handles';
const HANDLE_KEY = 'root';

function openHandleDb(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(DB_NAME, 1);
    req.onupgradeneeded = () => {
      req.result.createObjectStore(STORE_NAME);
    };
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}

async function saveStoredHandle(handle: FileSystemDirectoryHandleLike): Promise<void> {
  try {
    const db = await openHandleDb();
    await new Promise<void>((resolve, reject) => {
      const tx = db.transaction(STORE_NAME, 'readwrite');
      tx.objectStore(STORE_NAME).put(handle, HANDLE_KEY);
      tx.oncomplete = () => resolve();
      tx.onerror = () => reject(tx.error);
    });
    db.close();
  } catch {
    // Persistence is a nicety on top of picking, not a requirement for it —
    // a write failure here shouldn't surface as an import error.
  }
}

async function loadStoredHandle(): Promise<FileSystemDirectoryHandleLike | null> {
  try {
    const db = await openHandleDb();
    const handle = await new Promise<FileSystemDirectoryHandleLike | null>((resolve, reject) => {
      const tx = db.transaction(STORE_NAME, 'readonly');
      const req = tx.objectStore(STORE_NAME).get(HANDLE_KEY);
      req.onsuccess = () => resolve((req.result as FileSystemDirectoryHandleLike | undefined) ?? null);
      req.onerror = () => reject(req.error);
    });
    db.close();
    return handle;
  } catch {
    return null;
  }
}

async function clearStoredHandle(): Promise<void> {
  try {
    const db = await openHandleDb();
    await new Promise<void>((resolve, reject) => {
      const tx = db.transaction(STORE_NAME, 'readwrite');
      tx.objectStore(STORE_NAME).delete(HANDLE_KEY);
      tx.oncomplete = () => resolve();
      tx.onerror = () => reject(tx.error);
    });
    db.close();
  } catch {
    // best-effort — worst case a stale handle sticks around and the next
    // restore attempt fails harmlessly instead.
  }
}

/** Silently restores a previously-picked folder if, and only if, its
 * permission is already granted from a prior visit — safe to call on
 * mount with no user gesture. Returns null for "nothing to restore" and
 * for "restore is possible but needs a click" alike; use
 * `getReconnectableFolderName()` to distinguish the latter for the UI. */
export async function tryAutoRestoreZoteroFolder(): Promise<ZoteroFolderHandle | null> {
  if (!isDirectoryPickerSupported()) return null;
  const handle = await loadStoredHandle();
  if (!handle) return null;
  try {
    const state = await handle.queryPermission({ mode: 'read' });
    if (state !== 'granted') return null;
    return await buildHandleFromRoot(handle);
  } catch {
    await clearStoredHandle();
    return null;
  }
}

/** Name of a saved folder that could be reconnected with one click (its
 * permission needs re-confirming, e.g. after a browser restart) — or null
 * if there's nothing saved, or the saved grant was denied outright, in
 * which case the saved handle is dropped and a fresh pick is needed. */
export async function getReconnectableFolderName(): Promise<string | null> {
  if (!isDirectoryPickerSupported()) return null;
  const handle = await loadStoredHandle();
  if (!handle) return null;
  try {
    const state = await handle.queryPermission({ mode: 'read' });
    if (state === 'denied') {
      await clearStoredHandle();
      return null;
    }
    return handle.name;
  } catch {
    await clearStoredHandle();
    return null;
  }
}

/** Re-requests permission on the saved folder handle — must be called from
 * within a user-gesture handler (e.g. a button's onClick), same as any
 * File System Access permission prompt. */
export async function reconnectZoteroFolder(): Promise<ZoteroFolderHandle> {
  const handle = await loadStoredHandle();
  if (!handle) throw new Error('No saved Zotero folder to reconnect to.');
  const state = await handle.requestPermission({ mode: 'read' });
  if (state !== 'granted') {
    throw new DOMException('Permission to access the saved folder was not granted', 'AbortError');
  }
  return buildHandleFromRoot(handle);
}

/** Forgets the saved folder handle, so the next pick starts fresh. */
export async function forgetZoteroFolder(): Promise<void> {
  await clearStoredHandle();
}
