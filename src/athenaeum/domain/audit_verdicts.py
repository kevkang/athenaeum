"""Audit's graded verdicts: names, definitions, and how evaluation treats each.

Kept in one module so Audit's prompt, the evaluation harness and the human
labeling guide use the same words with the same meanings. Decisions behind it
(review remediation plan, D1 and later amendments):

- `secondhand` is a separate yes/no flag rather than a verdict, because a
  quote can be secondhand *and*, say, an overreach.
- A quote whose words don't match its source gets `differs_from_source`, and
  that is decided by the deterministic source check (domain/source_check.py),
  never by the model.
- A quote that is on topic without establishing the claim, or unrelated to
  it, counts as `misread` rather than getting a verdict of its own. The
  citation-integrity corpus (Sarol et al., 2024) splits these out as
  NOT_SUBSTANTIATE and IRRELEVANT; here they were deliberately folded in.
"""
from __future__ import annotations

from typing import Final, Literal

SUPPORTED: Final = "supported"
MISREAD: Final = "misread"
OVERREACH: Final = "overreach"
INDETERMINATE: Final = "indeterminate"
DIFFERS_FROM_SOURCE: Final = "differs_from_source"

Verdict = Literal["supported", "misread", "overreach", "indeterminate", "differs_from_source"]

VERDICTS: tuple[Verdict, ...] = (SUPPORTED, MISREAD, OVERREACH, INDETERMINATE, DIFFERS_FROM_SOURCE)

DEFINITIONS: dict[Verdict, str] = {
    SUPPORTED: "The quote directly supports the claim as written.",
    MISREAD: (
        "The quote is in the source but doesn't back the claim: it says something "
        "materially different, contradicts the claim, is on topic without establishing "
        "it, or is unrelated to it."
    ),
    OVERREACH: (
        "The quote supports a narrower claim than the one made: the claim goes beyond "
        "it in scope, magnitude, conditions, or population."
    ),
    INDETERMINATE: (
        "There isn't enough context to judge, including a citation with no specific quote."
    ),
    DIFFERS_FROM_SOURCE: (
        "The quote's words don't match this server's copy of the cited paper. Decided by "
        "the source check, never by the model."
    ),
}

SECONDHAND_DEFINITION: Final = (
    "A yes/no flag, separate from the verdict: the quoted passage itself cites another "
    "work for this point, so the cited paper is not the original source."
)

# Verdicts a model decides. differs_from_source comes from a deterministic
# check, so it's left out of macro-F1: a category right by construction would
# inflate the average and hide weakness on the hard ones.
JUDGMENT_VERDICTS: tuple[Verdict, ...] = (SUPPORTED, MISREAD, OVERREACH, INDETERMINATE)

# True verdicts meaning the citation is wrong in a way a reader must be warned
# about. Showing any of these as `supported` is the dangerous error. An
# indeterminate citation isn't known to be wrong, so it isn't counted here.
BAD_VERDICTS: tuple[Verdict, ...] = (MISREAD, OVERREACH, DIFFERS_FROM_SOURCE)
