"""Predictors the evaluation runner can score.

Only today's production Audit exists so far, unchanged, so the first real
measurement is a baseline taken before any prompt change. That is the plan's
rule: build the measurement before tuning the thing it measures.
"""
from __future__ import annotations

from typing import Final

from athenaeum.domain.audit_verdicts import (
    BAD_VERDICTS,
    JUDGMENT_VERDICTS,
    SUPPORTED,
    VERDICTS,
)
from athenaeum.evaluation.items import EvalItem
from athenaeum.evaluation.runner import PREDICTION_ERROR, Prediction, ScoringSpec
from athenaeum.provider.base import Provider
from athenaeum.synthesize.audit import audit_evidence

NOT_SUPPORTED: Final = "not_supported"


class BinaryAuditPredictor:
    """Today's Audit: the production audit_evidence() call and prompt, applied
    to one labeled citation at a time.

    An item's quotes go in one call, which is the shape production uses for a
    statement carrying several citations. Audit answers per quote, so the
    item-level verdict is a rollup: supported only when every quote in the
    item is supported. That is the strict reading of evidence annotated as a
    set, where the sentences are evidence only together.
    """

    def __init__(self, provider: Provider, model: str) -> None:
        self._provider = provider
        self._model = model
        self.name = f"audit-binary:{model}"
        # Response integrity for the whole run. A quote whose verdict was
        # skipped is scored as not supported, so without these counts a run
        # where the model answered badly is indistinguishable from one where
        # it answered well. Plain += is safe here: items run concurrently but
        # on one event loop, and nothing awaits between reading and writing.
        self.quotes_checked = 0
        self.malformed_entries = 0
        self.missing_verdicts = 0

    async def predict(self, item: EvalItem) -> Prediction:
        checked = await audit_evidence(
            self._provider, item.claim, list(item.quotes), self._model
        )
        self.quotes_checked += len(item.quotes)
        self.malformed_entries += checked.malformed_entries
        self.missing_verdicts += checked.missing_verdicts
        results = checked.results
        quote_verdicts = tuple(
            SUPPORTED if supported else NOT_SUPPORTED for supported, _rationale in results
        )
        verdict = (
            SUPPORTED
            if quote_verdicts and all(v == SUPPORTED for v in quote_verdicts)
            else NOT_SUPPORTED
        )
        rationale = " | ".join(text for _supported, text in results)
        return Prediction(item.id, verdict, rationale, quote_verdicts)


def binary_spec() -> ScoringSpec:
    """How to score the pass/fail Audit against graded labels. It can emit only
    supported or not_supported, so the report shows its confusion matrix and
    dangerous errors, and has no macro-F1."""
    return ScoringSpec(
        true_labels=VERDICTS,
        predicted_labels=(SUPPORTED, NOT_SUPPORTED, PREDICTION_ERROR),
        judgment_labels=JUDGMENT_VERDICTS,
        bad_labels=BAD_VERDICTS,
        safe_prediction=SUPPORTED,
    )
