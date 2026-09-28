"""A graded variant of Audit, for evaluation only.

Production Audit answers yes/no per quote. This variant answers with a named
verdict — supported, misread, overreach or indeterminate — for the evidence
taken together, plus one per quote. Two reasons to have it:

1. A yes/no answer says a citation is wrong without saying how, so a run of
   disagreements can't be sorted into "the model misread the quote" and "the
   claim outran the quote". Those need different fixes.
2. Evidence annotated as a set is evidence only as a set. Rolling per-quote
   answers up with a rule chosen afterwards — all of them, or any of them —
   invents an item-level verdict the model never gave. Asking once for the
   set is the question the labels actually answer.

The framing paragraphs are copied from AUDIT_SYSTEM as it stands today, on
purpose: this variant should differ from production in what it is asked to
produce, not in how critical it is told to be. It is still a different prompt,
so its results describe this variant and not the production checker. The
verdict names and definitions come from domain/audit_verdicts.py, so the
prompt, the labeling guide and the scoring can't drift apart.

`differs_from_source` is deliberately absent from the choices: whether a
quote's words match the cited paper is decided by the deterministic source
check (domain/source_check.py), never by a model.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Final

import structlog

from athenaeum.domain.audit_verdicts import (
    BAD_VERDICTS,
    DEFINITIONS,
    JUDGMENT_VERDICTS,
    SECONDHAND_DEFINITION,
    SUPPORTED,
    VERDICTS,
)
from athenaeum.evaluation.items import EvalItem
from athenaeum.evaluation.runner import PREDICTION_ERROR, Prediction, ScoringSpec
from athenaeum.provider.base import Message, Provider

log = structlog.get_logger()

_VERDICT_LINES = "\n".join(f"- {name}: {DEFINITIONS[name]}" for name in JUDGMENT_VERDICTS)

GRADED_AUDIT_SYSTEM: Final = f"""\
You are an independent citation auditor. You have NO knowledge of how these
citations were selected or who selected them. You are given a claim (a
statement an author has made) and the verbatim quotes they have cited to
support it. The quotes are one piece of evidence: judge them together, as the
author offered them.

Authors regularly cite sources that don't say what they're cited for:
circular citations, quotes that address a related but different question,
or evidence far weaker than the claim it's attached to. Be critical — this
audit exists to catch exactly that.

Choose exactly one verdict for the evidence as a whole:

{_VERDICT_LINES}

Then give the same kind of verdict for each quote on its own, so a reader can
see which quote carried the judgment.

Also report `secondhand`: {SECONDHAND_DEFINITION}

Every rationale is shown directly to the document's author as an audit report,
not kept internal — write it for a human reader, and make it actionable (say
what's missing or mismatched, not just "no").
"""

GRADED_AUDIT_TOOL: dict = {
    "name": "audit_evidence_graded",
    "description": (
        "Judge the evidence cited for one claim: a verdict for the quotes taken "
        "together, and a verdict for each quote on its own."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "set_verdict": {
                "type": "string",
                "enum": list(JUDGMENT_VERDICTS),
                "description": "The verdict for all the quotes taken together as one body "
                               "of evidence for the claim.",
            },
            "set_rationale": {
                "type": "string",
                "description": (
                    "A short, specific explanation written for the document's author, "
                    "not an internal note."
                ),
            },
            "secondhand": {"type": "boolean", "description": SECONDHAND_DEFINITION},
            "confidence": {
                "type": "number",
                "minimum": 0,
                "maximum": 1,
                "description": "How confident you are in the set verdict, 0 to 1.",
            },
            "per_quote": {
                "type": "array",
                "description": "One entry per quote, in any order, identified by index.",
                "items": {
                    "type": "object",
                    "properties": {
                        "evidence_index": {"type": "integer"},
                        "verdict": {"type": "string", "enum": list(JUDGMENT_VERDICTS)},
                        "rationale": {"type": "string"},
                    },
                    "required": ["evidence_index", "verdict", "rationale"],
                },
            },
        },
        "required": ["set_verdict", "set_rationale", "per_quote"],
    },
}

# A quote the response said nothing usable about. Kept distinct from every
# verdict name so no scoring can mistake silence for a judgment.
NO_VERDICT: Final = ""


@dataclass(frozen=True)
class GradedAudit:
    """One graded call's answer, plus what in the response could not be used.

    `set_verdict` is NO_VERDICT when the response carried no usable verdict
    for the evidence as a whole. That is a failed call, not an `indeterminate`
    judgment: the model saying "I can't tell" and the model returning nothing
    are different events and must not be scored as the same one.
    """

    set_verdict: str
    set_rationale: str
    quote_verdicts: tuple[str, ...]
    secondhand: bool | None = None
    confidence: float | None = None
    # Per-quote entries only. A missing set verdict is not an entry, and is
    # counted below: the two have different denominators (quotes, items), and
    # adding them together once produced a failure rate that described neither.
    malformed_entries: int = 0
    missing_verdicts: int = 0
    # 1 when the response carried no usable set verdict, whether or not it was
    # then recovered, and 1 more when it was recovered from the only quote's
    # verdict. Recovery loses no evidence, so it is reported, not failed on.
    missing_set_verdict: int = 0
    recovered_set_verdict: int = 0


def _recover_single_quote_verdict(quote_verdicts: tuple[str, ...]) -> str:
    """The set verdict for a one-quote item, read off that quote's verdict.

    On a run of 54 items, claude-sonnet-5 omitted the required `set_verdict`
    field on three single-quote items while answering the per-quote question
    fully — apparently treating the two questions as the same one. For a
    single quote they are: "do these quotes together support the claim" and
    "does this quote support the claim" ask the same thing when there is one
    quote. Taking the verdict from it is an identity, not a rollup.

    Deliberately limited to exactly one quote. With two or more, combining
    per-quote verdicts into a set verdict would invent the answer the model
    was asked for and didn't give, which is the rule this variant exists to
    get rid of.
    """
    if len(quote_verdicts) == 1 and quote_verdicts[0] != NO_VERDICT:
        return quote_verdicts[0]
    return NO_VERDICT


def _usable_index(idx: Any, n: int) -> bool:
    # `bool` is an `int` in Python, so True would otherwise pass as index 1.
    return isinstance(idx, int) and not isinstance(idx, bool) and 0 <= idx < n


def _read_per_quote(raw: Any, n_quotes: int, model: str) -> tuple[dict[int, str], int]:
    """Per-quote verdicts by index, and how many entries could not be used."""
    malformed = 0
    entries: list[Any] = []
    if isinstance(raw, list):
        entries = raw
    elif raw is not None:
        log.warning("graded_audit.per_quote_not_a_list", got=type(raw).__name__, model=model)
        malformed += 1

    verdicts: dict[int, str] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            log.warning("graded_audit.unexpected_entry", got=type(entry).__name__, model=model)
            malformed += 1
            continue
        idx = entry.get("evidence_index", -1)
        verdict = entry.get("verdict")
        if not _usable_index(idx, n_quotes) or idx in verdicts or verdict not in VERDICTS:
            # No quote to attach this to, or a verdict outside the taxonomy.
            # The first answer for an index is kept, so a repeat can't quietly
            # overturn one.
            log.warning(
                "graded_audit.unusable_entry",
                index=repr(idx), verdict=repr(verdict), n_quotes=n_quotes, model=model,
            )
            malformed += 1
            continue
        verdicts[idx] = str(verdict)
    return verdicts, malformed


async def audit_evidence_graded(
    checker: Provider,
    assertion: str,
    quotes: list[str],
    checker_model_name: str,
) -> GradedAudit:
    """Ask for one verdict over all the quotes, plus one per quote."""
    if not quotes:
        return GradedAudit(NO_VERDICT, "", ())

    quote_lines = "\n\n".join(f"[{i}] {q}" for i, q in enumerate(quotes))
    user_msg = (
        f"CLAIM:\n{assertion}\n\n"
        f"CITED EVIDENCE ({len(quotes)} quote{'s' if len(quotes) > 1 else ''}, "
        f"offered together):\n{quote_lines}\n\n"
        "Judge whether this evidence, taken together, supports the claim."
    )
    _, tool_result = await checker.complete(
        messages=[Message(role="user", content=user_msg)],
        system=GRADED_AUDIT_SYSTEM,
        max_tokens=2048,
        tools=[GRADED_AUDIT_TOOL],
    )
    if tool_result is None:
        log.warning("graded_audit.no_tool_result", n_quotes=len(quotes), model=checker_model_name)
        return GradedAudit(
            NO_VERDICT, "", (NO_VERDICT,) * len(quotes),
            missing_verdicts=len(quotes), missing_set_verdict=1,
        )

    payload = tool_result.get("input") or {}
    if not isinstance(payload, dict):
        log.warning("graded_audit.input_not_an_object", model=checker_model_name)
        return GradedAudit(
            NO_VERDICT, "", (NO_VERDICT,) * len(quotes),
            malformed_entries=1, missing_verdicts=len(quotes), missing_set_verdict=1,
        )

    set_verdict = payload.get("set_verdict")
    malformed = 0
    missing_set_verdict = 0
    if set_verdict not in JUDGMENT_VERDICTS:
        log.warning(
            "graded_audit.unusable_set_verdict", got=repr(set_verdict), model=checker_model_name
        )
        set_verdict = NO_VERDICT
        missing_set_verdict = 1

    per_quote, per_quote_malformed = _read_per_quote(
        payload.get("per_quote"), len(quotes), checker_model_name
    )
    malformed += per_quote_malformed

    quote_verdicts = tuple(per_quote.get(i, NO_VERDICT) for i in range(len(quotes)))
    recovered = 0
    if set_verdict == NO_VERDICT:
        set_verdict = _recover_single_quote_verdict(quote_verdicts)
        if set_verdict != NO_VERDICT:
            recovered = 1
            log.info("graded_audit.recovered_set_verdict_from_single_quote", verdict=set_verdict)

    confidence = payload.get("confidence")
    secondhand = payload.get("secondhand")
    return GradedAudit(
        set_verdict=str(set_verdict),
        set_rationale=str(payload.get("set_rationale") or ""),
        quote_verdicts=quote_verdicts,
        secondhand=bool(secondhand) if isinstance(secondhand, bool) else None,
        confidence=float(confidence) if isinstance(confidence, int | float) else None,
        malformed_entries=malformed,
        missing_verdicts=len(quotes) - len(per_quote),
        missing_set_verdict=missing_set_verdict,
        recovered_set_verdict=recovered,
    )


def verdict_from_row(row: dict) -> tuple[str, bool]:
    """A recorded prediction's verdict, and whether it came from recovery.

    Reads a run written before `set_verdict_recovered` existed the same way as
    one written after, by applying the recovery rule to the row's per-quote
    verdicts. Runs recorded after the field exists are taken at their word.
    """
    verdict = row["verdict"]
    if verdict != PREDICTION_ERROR:
        return verdict, bool(row.get("set_verdict_recovered"))
    recovered = _recover_single_quote_verdict(tuple(row.get("quote_verdicts") or ()))
    if recovered != NO_VERDICT:
        return recovered, True
    return PREDICTION_ERROR, False


class GradedAuditPredictor:
    """Audit asked for a graded verdict over each item's evidence as a whole.

    There is no rollup: the item-level verdict is the one the model gave for
    the set. A call that returns no usable set verdict is recorded as an
    error, never as `indeterminate` — a failed call is not a judgment.
    """

    def __init__(self, provider: Provider, model: str) -> None:
        self._provider = provider
        self._model = model
        self.name = f"audit-graded:{model}"
        # Response integrity for the whole run; see BinaryAuditPredictor.
        self.quotes_checked = 0
        self.malformed_entries = 0
        self.missing_verdicts = 0
        # Item-level, on their own denominator: responses with no usable set
        # verdict, and the subset of those recovered from a single quote.
        self.missing_set_verdicts = 0
        self.recovered_set_verdicts = 0
        # Which items those were, so a recovered verdict is traceable in the
        # predictions file rather than only in a total.
        self.recovered: dict[str, bool] = {}
        # Kept per item so a tabulation can use them without a second run.
        self.secondhand: dict[str, bool | None] = {}
        self.confidence: dict[str, float | None] = {}

    async def predict(self, item: EvalItem) -> Prediction:
        graded = await audit_evidence_graded(
            self._provider, item.claim, list(item.quotes), self._model
        )
        self.quotes_checked += len(item.quotes)
        self.malformed_entries += graded.malformed_entries
        self.missing_verdicts += graded.missing_verdicts
        self.missing_set_verdicts += graded.missing_set_verdict
        self.recovered_set_verdicts += graded.recovered_set_verdict
        self.recovered[item.id] = bool(graded.recovered_set_verdict)
        self.secondhand[item.id] = graded.secondhand
        self.confidence[item.id] = graded.confidence
        verdict = graded.set_verdict if graded.set_verdict != NO_VERDICT else PREDICTION_ERROR
        rationale = graded.set_rationale or (
            "No usable set verdict returned for this item."
            if verdict == PREDICTION_ERROR
            else "Set verdict taken from the item's only quote; no set rationale was given."
        )
        return Prediction(item.id, verdict, rationale, graded.quote_verdicts)


def graded_spec() -> ScoringSpec:
    """How to score the graded variant against graded labels.

    It can emit every verdict a model decides, so macro-F1 is defined;
    `differs_from_source` stays a true label it can never predict, because
    that verdict is the deterministic source check's to give.
    """
    return ScoringSpec(
        true_labels=VERDICTS,
        predicted_labels=(*JUDGMENT_VERDICTS, PREDICTION_ERROR),
        judgment_labels=JUDGMENT_VERDICTS,
        bad_labels=BAD_VERDICTS,
        safe_prediction=SUPPORTED,
    )
