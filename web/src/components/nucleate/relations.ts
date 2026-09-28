import type { Provenance, SemanticEdge, SemanticRelation } from '../../api/nucleate';

/** How each relation reads between two paper labels: "Rivera 2023 uses the
 * method of Koch 2018". The terms and their meanings are domain/relations.py's. */
export const RELATION_LABELS: Record<SemanticRelation, string> = {
  uses_method_of: 'uses the method of',
  uses_data_from: 'uses data from',
  extends: 'extends',
  supports: 'supports',
  disagrees_with: 'disagrees with',
  obtains_background_from: 'draws background from',
};

/** One line each, for the relation picker: enough to choose between them.
 * Condensed from domain/relations.py's definitions. */
export const RELATION_HINTS: Record<SemanticRelation, string> = {
  uses_method_of: 'A applies a method, protocol, instrument or software from B.',
  uses_data_from: 'A reuses data that B produced or published.',
  extends: 'A builds directly on B and goes beyond it.',
  supports: 'A endorses or argues for B’s position or findings.',
  disagrees_with: 'A’s claims or results conflict with B’s.',
  obtains_background_from: 'A relies on B for context, not its methods, data or results.',
};

export const EDGE_PROVENANCE_COPY: Record<Provenance, string> = {
  ai_proposed: 'Proposed by the archivist · not yet reviewed',
  ai_proposed_accepted: 'Proposed by the archivist · accepted by you',
  ai_proposed_corrected: 'Proposed by the archivist · changed by you',
  user_set: 'Set by you',
};

/** Unreviewed relations are AI text, drawn as the archivist's proposals. */
export function isUnreviewed(edge: SemanticEdge): boolean {
  return edge.provenance === 'ai_proposed';
}

export const OUTCOME_COPY = {
  proposed: 'relation proposed',
  no_full_text: 'no full text to read',
  no_mention: 'not cited by name',
  no_relation: 'cited, but no clear relation',
  already_recorded: 'already recorded',
} as const;
