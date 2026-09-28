import { STAGES, type ExpansionEvent, type Stage } from '../../api/nucleate';

/* Deriving the stage list from an expansion's events. Separate from the
   component so it can be tested, and so the component file exports only a
   component (fast refresh). */

export type StageState = 'pending' | 'current' | 'completed' | 'failed';

export interface StageView {
  state: StageState;
  detail: string;
}

/** Plain, descriptive copy from an event's real counts (handoff Part V):
 * "Retrieved 34 candidates", "Selected 7", "Verifying 5 of 7". */
function describe(stage: Stage, e: ExpansionEvent): string {
  const c = e.counts ?? {};
  if (e.status === 'failed') return e.message ?? 'Failed.';
  if (stage === 'retrieving' && e.status === 'completed') {
    const parts = [`Retrieved ${c.retrieved ?? 0} candidates`];
    if (c.web_unmatched) {
      parts.push(`${c.web_unmatched} web ${c.web_unmatched === 1 ? 'page' : 'pages'} matched no paper`);
    }
    if (c.openalex_failed) {
      parts.push(`${c.openalex_failed} OpenAlex ${c.openalex_failed === 1 ? 'search' : 'searches'} failed`);
    }
    return parts.join(' · ');
  }
  if (stage === 'ranking' && e.status === 'completed') {
    const parts = [`Selected ${c.selected ?? 0}`];
    if (c.rejected_skipped) parts.push(`${c.rejected_skipped} rejected skipped`);
    return parts.join(' · ');
  }
  if (c.of !== undefined) {
    const skipped = c.skipped ? ` · ${c.skipped} skipped` : '';
    return `${c.done ?? 0} of ${c.of}${skipped}`;
  }
  return e.message ?? '';
}

export function stageViews(events: ExpansionEvent[]): Record<Stage, StageView> {
  const views = Object.fromEntries(
    STAGES.map((s) => [s, { state: 'pending' as StageState, detail: '' }]),
  ) as Record<Stage, StageView>;
  for (const e of events) {
    if (e.node_id) continue; // a node's own status, not the stage's
    const state: StageState =
      e.status === 'completed' || e.status === 'failed' ? e.status : 'current';
    views[e.stage] = { state, detail: describe(e.stage, e) };
  }
  return views;
}
