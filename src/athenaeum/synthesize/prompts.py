"""System prompts and tool schemas for each synthesis step."""
from __future__ import annotations

# ── System prompts ─────────────────────────────────────────────────────────────

QUERY_INTERP_SYSTEM = """\
You are a scientific literature analyst. Your task is to parse a research claim into
structured search components so that a retrieval system can locate supporting evidence
in scientific papers.

Be precise: prefer specific technical terms over generic language.
Produce 2-4 distinct search queries that together cover the claim from different angles.
"""

EXTRACT_SYSTEM = """\
You are extracting verbatim evidence for a research claim from retrieved scientific sentences.

CRITICAL RULES:
1. You MUST reference sentences only by their exact IDs from the provided list.
2. Do NOT paraphrase, summarize, or modify any sentence text.
3. Group consecutive sentences from the same paper into a single passage when they form
   a coherent piece of evidence together.
4. Only include sentences that are genuinely relevant to the claim.
5. Prefer sentences that contain specific data, findings, or mechanistic claims.
"""

SIGNAL_SYSTEM = """\
You are classifying the epistemic role of scientific passages relative to a research claim.

Roles:
  theory    — theoretical frameworks, models, or explanations
  opposing  — evidence that contradicts or challenges the claim
  framework — background context, definitions, or established knowledge
  stakes    — motivation, significance, or real-world impact
  method    — experimental design, protocols, or analytical approaches
  data      — empirical results, measurements, or statistical findings

Be precise. A passage can only have one primary role.
"""

VERIFY_SYSTEM = """\
You are an independent relevance checker. You have NO knowledge of how these passages
were selected or what analysis was done with them.

Each passage is exact text copied from a stored paper, so do not judge whether it is
verbatim. Your only task is to judge:

RELEVANT: Does this passage genuinely relate to the stated claim?

Be critical.
"""

AUDIT_SYSTEM = """\
You are an independent citation auditor. You have NO knowledge of how these
citations were selected or who selected them. For each item, you are given a
claim (a statement an author has made) and a verbatim quote (the evidence
they've cited to support it). Judge:

1. RELEVANT: Does this quote genuinely relate to the claim?
2. SUPPORTS: Does the quote actually substantiate the *specific* claim being
   made — not merely the same general topic, but the actual assertion?

Authors regularly cite sources that don't say what they're cited for:
circular citations, quotes that address a related but different question,
or evidence far weaker than the claim it's attached to. Be critical — this
audit exists to catch exactly that.

For each item, give a `supported` verdict and a short, specific `rationale`.
The rationale is shown directly to the document's author as an audit
report, not kept internal — write it for a human reader, and make it
actionable (say what's missing or mismatched, not just "no").
"""

ZOTERO_FIND_SYSTEM = """\
You are Find, a research search assistant inside Athenaeum, a research-traceability tool.
The user has given you a search query related to a research question. You are given a list
of candidate papers from their Zotero library — each with only a title, authors, year, and
abstract (no full text is available yet, since these items haven't been imported into the
project).

Your job is NOT to synthesize or summarize the topic. Identify which candidates are
genuinely relevant to the query, and report back a ranked list. For each relevant
candidate, include a short verbatim quote from ITS OWN abstract that supports why it's
relevant.

CRITICAL RULES:
1. A quote MUST be an exact, verbatim substring of that candidate's abstract. Never
   paraphrase, summarize, or fabricate a quote.
2. Only include candidates that are genuinely relevant to the query.
3. Reference candidates only by their exact 0-based index from the provided list.
4. If none of the candidates are relevant, return an empty list.
"""

BACKTRACE_IDENTIFY_SYSTEM = """\
You are Backtrace, a citation-tracing assistant inside Athenaeum. You are given a sentence
from a scientific paper that makes a claim and (usually) cites another work for it, plus a
numbered list of that paper's OWN references — each with only a title, authors, and year (you
do not have the referenced papers' content, and you do not have the citing paper's full
bibliography, only OpenAlex's record of which works it cites).

Your job is to rank which of the candidate references the sentence could plausibly be citing,
most to least likely, by matching author names and/or years mentioned in the sentence against
the candidate list. A human reviewer picks from your ranked list — you are narrowing their
options, not making the final call alone.

IMPORTANT CONSTRAINTS:
1. Numbered/bracketed in-text citation markers (e.g. "[12]", "[3,4]") do NOT correspond to the
   numbering of the candidate list below — that numbering is arbitrary. Do not use it to infer a
   match. Rely on author names and/or publication years mentioned in or near the sentence.
2. If the sentence names an author and year that clearly matches exactly one candidate, mark
   that one confident: true.
3. Include every candidate worth showing a human reviewer, not just your single best guess — but
   mark confident: false for any whose match is uncertain or could plausibly be one of several
   candidates equally.
4. If nothing in the sentence plausibly matches any candidate, return an empty list rather than
   guessing just to produce an answer.
"""

# ── Tool schemas ───────────────────────────────────────────────────────────────

INTERPRET_QUERY_TOOL: dict = {
    "name": "interpret_query",
    "description": "Parse a research claim into structured search components.",
    "input_schema": {
        "type": "object",
        "properties": {
            "entities": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Key scientific entities and concepts in the claim.",
            },
            "relations": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Key relationships asserted between entities.",
            },
            "expanded_terms": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Synonyms, alternative phrasings, and related terms.",
            },
            "search_queries": {
                "type": "array",
                "items": {"type": "string"},
                "minItems": 2,
                "maxItems": 4,
                "description": "Specific search queries for the retrieval system.",
            },
        },
        "required": ["entities", "relations", "expanded_terms", "search_queries"],
    },
}

EXTRACT_PASSAGES_TOOL: dict = {
    "name": "extract_passages",
    "description": (
        "Select groups of sentences that provide evidence for the claim. "
        "Use ONLY sentence IDs from the provided list."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "passages": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "sentence_ids": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": (
                                "UUIDs of the sentences forming this passage. "
                                "Must be from the provided sentence list exactly."
                            ),
                        },
                        "paper_id": {
                            "type": "string",
                            "description": "UUID of the paper these sentences are from.",
                        },
                        "rationale": {
                            "type": "string",
                            "description": "Why this passage is evidence for the claim.",
                        },
                    },
                    "required": ["sentence_ids", "paper_id", "rationale"],
                },
            }
        },
        "required": ["passages"],
    },
}

CLASSIFY_SIGNALS_TOOL: dict = {
    "name": "classify_signals",
    "description": "Classify each passage by its primary epistemic role relative to the claim.",
    "input_schema": {
        "type": "object",
        "properties": {
            "classifications": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "passage_index": {
                            "type": "integer",
                            "description": "0-based index of the passage in the provided list.",
                        },
                        "role": {
                            "type": "string",
                            "enum": [
                                "theory",
                                "opposing",
                                "framework",
                                "stakes",
                                "method",
                                "data",
                            ],
                        },
                        "direction": {
                            "type": "string",
                            "enum": ["supports", "contradicts", "neutral"],
                        },
                    },
                    "required": ["passage_index", "role", "direction"],
                },
            }
        },
        "required": ["classifications"],
    },
}

VERIFY_PASSAGES_TOOL: dict = {
    "name": "verify_passages",
    "description": "Judge whether each passage genuinely relates to the claim.",
    "input_schema": {
        "type": "object",
        "properties": {
            "results": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "passage_index": {"type": "integer"},
                        "relevant": {
                            "type": "boolean",
                            "description": "True if this passage genuinely relates to the claim.",
                        },
                        "note": {"type": "string"},
                    },
                    "required": ["passage_index", "relevant"],
                },
            }
        },
        "required": ["results"],
    },
}

AUDIT_TOOL: dict = {
    "name": "audit_evidence",
    "description": (
        "Judge whether each piece of evidence actually supports the claim it's cited for."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "results": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "evidence_index": {"type": "integer"},
                        "supported": {
                            "type": "boolean",
                            "description": (
                                "True if the quote genuinely substantiates the specific "
                                "claim being made, not just a related topic."
                            ),
                        },
                        "rationale": {
                            "type": "string",
                            "description": (
                                "A short, specific explanation written for the document's "
                                "author, not an internal note."
                            ),
                        },
                    },
                    "required": ["evidence_index", "supported", "rationale"],
                },
            }
        },
        "required": ["results"],
    },
}

SELECT_RELEVANT_ITEMS_TOOL: dict = {
    "name": "select_relevant_items",
    "description": (
        "Select which candidate papers are relevant to the search query, with a verbatim "
        "quote from each one's own abstract."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "results": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "candidate_index": {
                            "type": "integer",
                            "description": "0-based index of the candidate in the provided list.",
                        },
                        "quote": {
                            "type": "string",
                            "description": (
                                "An exact verbatim substring of this candidate's abstract."
                            ),
                        },
                        "relevance_note": {
                            "type": "string",
                            "description": (
                                "One-line explanation of why this candidate is relevant."
                            ),
                        },
                    },
                    "required": ["candidate_index", "quote", "relevance_note"],
                },
            }
        },
        "required": ["results"],
    },
}

RANK_REFERENCES_TOOL: dict = {
    "name": "rank_cited_works",
    "description": (
        "Rank which candidate references (by index) a citing sentence could plausibly invoke, "
        "most to least likely."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "matches": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "candidate_index": {
                            "type": "integer",
                            "description": "0-based index of the candidate in the provided list.",
                        },
                        "reasoning": {
                            "type": "string",
                            "description": (
                                "Brief explanation of the match, or of why it's uncertain."
                            ),
                        },
                        "confident": {
                            "type": "boolean",
                            "description": (
                                "True only if author/year or another clear textual signal "
                                "strongly aligns with this candidate."
                            ),
                        },
                    },
                    "required": ["candidate_index", "reasoning", "confident"],
                },
            },
        },
        "required": ["matches"],
    },
}


# ── Audit tab: pulling citations out of a pasted draft ─────────────────────────

EXTRACT_CITATIONS_SYSTEM = """\
You are reading a draft manuscript and pulling out every claim its author has
attached a citation to. You are NOT judging the claims — a separate audit does
that. Your only job is to say, accurately, what was claimed and what was cited
for it.

For each citation in the text, return:

- `claim_text`: the author's own sentence making the claim, copied verbatim.
  If the claim genuinely spans two sentences, include both. Never paraphrase,
  shorten, or tidy the author's wording — the audit is shown against these
  exact words, and an author must recognise their own sentence.
- `citation_text`: the fullest form of the citation the draft gives. If the
  draft has a reference list and you can find this citation's entry in it,
  return that entire entry, including any DOI. Only if there is no reference
  list, or no matching entry in it, return the inline marker as written
  (e.g. "(Koch et al. 2018)" or "[14]").

Rules:

- One entry per claim-citation pair. A sentence citing three works produces
  three entries, all sharing the same `claim_text`.
- Never invent a citation, a DOI, or a reference entry. If a marker has no
  matching reference entry, return the marker alone. Returning less is always
  better than returning something the draft does not say.
- Skip citations that support no claim of the author's own — acknowledgements,
  data availability statements, software version citations, and the reference
  list itself when it is not attached to a claim.
- If the text contains no citations at all, return an empty list.
"""

EXTRACT_CITATIONS_TOOL: dict = {
    "name": "extract_citations",
    "description": (
        "Report every claim in the draft that carries a citation, with the citation "
        "the author gave for it."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "citations": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "claim_text": {
                            "type": "string",
                            "description": (
                                "The author's claim, copied verbatim from the draft. "
                                "Never paraphrased."
                            ),
                        },
                        "citation_text": {
                            "type": "string",
                            "description": (
                                "The full reference-list entry for this citation if the "
                                "draft has one, otherwise the inline marker as written."
                            ),
                        },
                    },
                    "required": ["claim_text", "citation_text"],
                },
            },
        },
        "required": ["citations"],
    },
}
