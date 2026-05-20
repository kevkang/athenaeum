"""System prompts and tool schemas for each synthesis step."""
from __future__ import annotations

# ── System prompts ─────────────────────────────────────────────────────────────

QUERY_INTERP_SYSTEM = """\
You are a scientific literature analyst. Your task is to parse a research claim into
structured search components so that a retrieval system can locate supporting evidence
in scientific papers.

Be precise: prefer specific technical terms over generic language.
Produce 2–4 distinct search queries that together cover the claim from different angles.
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
You are an independent citation verifier. You have NO knowledge of how these passages
were selected or what analysis was done with them. Your only task is to verify:

1. VERBATIM: Is the passage text consistent with verbatim text from a scientific paper?
   (Look for: natural scientific prose, appropriate hedging, specific terminology,
    statistical language. Flag obvious hallucinations: impossible statistics,
    contradictory claims within the text, non-existent technical terms.)
2. RELEVANT: Does this passage genuinely relate to the stated claim?

Your verification is the last defence against hallucination. Be critical.
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
    "description": "Verify that each passage is verbatim scientific text and is relevant to the claim.",
    "input_schema": {
        "type": "object",
        "properties": {
            "results": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "passage_index": {"type": "integer"},
                        "verbatim_plausible": {
                            "type": "boolean",
                            "description": (
                                "True if the text is consistent with verbatim scientific prose "
                                "(not paraphrased or hallucinated)."
                            ),
                        },
                        "relevant": {
                            "type": "boolean",
                            "description": "True if this passage genuinely relates to the claim.",
                        },
                        "note": {"type": "string"},
                    },
                    "required": ["passage_index", "verbatim_plausible", "relevant"],
                },
            }
        },
        "required": ["results"],
    },
}
