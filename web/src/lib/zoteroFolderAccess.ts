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

interface FileSystemDirectoryHandleLike {
  getFileHandle(name: string): Promise<FileSystemFileHandleLike>;
  getDirectoryHandle(name: string): Promise<FileSystemDirectoryHandleLike>;
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

async function pickViaFileSystemAccess(): Promise<ZoteroFolderHandle> {
  const rootHandle = await window.showDirectoryPicker!();

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
