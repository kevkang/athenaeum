export function trunc(s: string, n: number): string {
  return s.length > n ? s.slice(0, n - 1) + '…' : s;
}

export function stripHtml(s: string): string {
  return s.replace(/<[^>]*>/g, '');
}

export function safeHtml(s: string): { __html: string } {
  const noForeignTags = s.replace(/<(?!\/?(?:i|em|b|strong|sup|sub)\b)[^>]*>/gi, '');
  const noAttrs = noForeignTags.replace(/<(\/?(?:i|em|b|strong|sup|sub))\b[^>]*>/gi, '<$1>');
  return { __html: noAttrs };
}

export function formatDate(iso: string): string {
  return new Date(iso).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
}

const KNOWN_STATEMENT_TYPES = ['observation', 'methods', 'foundation', 'synthesis', 'claim', 'question', 'other'];

export function typeClass(type: string): string {
  return KNOWN_STATEMENT_TYPES.includes(type) ? type : 'other';
}

/** e.g. formatModelName('claude-haiku-4-5-20251001') -> "Claude Haiku 4.5" —
 * used to attribute AI-generated quotes/summaries to the model that actually
 * produced them, rather than a hardcoded guess. Falls back to the raw id for
 * anything that doesn't match the expected "claude-<family>-<major>-<minor>"
 * shape (future model names, non-Claude providers, etc). */
export function formatModelName(modelId: string): string {
  const m = modelId.match(/^claude-(opus|sonnet|haiku)-(\d+)-(\d+)/);
  if (!m) return modelId;
  const [, family, major, minor] = m;
  return `Claude ${family[0].toUpperCase()}${family.slice(1)} ${major}.${minor}`;
}

/** e.g. formatCitation('Ada Borton', 2026) -> "Borton et al. 2026" — takes
 * just the first author's surname the way firstAuthor() already returns it
 * (a full display name), so this only needs to grab the last word of it. */
export function formatCitation(firstAuthor: string | null, year: number | null): string {
  const surname = firstAuthor?.trim().split(/\s+/).pop();
  if (!surname && !year) return 'Unknown source';
  if (!surname) return String(year);
  return year ? `${surname} et al. ${year}` : `${surname} et al.`;
}
