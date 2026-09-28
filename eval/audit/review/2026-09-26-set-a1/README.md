# Blind review

98 items in `sheet.csv`. Each row gives a claim, the quote or
quotes cited for it, and the cited paper's abstract. Judge whether the quoted
evidence, taken together, supports the claim as written.

Fill in three columns:

- `verdict` — one of the names below, exactly as spelled.
- `note` — a sentence on why, especially where the call was close.
- `secondhand` — `yes` if the quoted passage is itself citing another work for
  this point, otherwise leave blank.

## Verdicts

- `supported` — The quote directly supports the claim as written.
- `misread` — The quote is in the source but doesn't back the claim: it says something materially different, contradicts the claim, is on topic without establishing it, or is unrelated to it.
- `overreach` — The quote supports a narrower claim than the one made: the claim goes beyond it in scope, magnitude, conditions, or population.
- `indeterminate` — There isn't enough context to judge, including a citation with no specific quote.
- `differs_from_source` — The quote's words don't match this server's copy of the cited paper. Decided by the source check, never by the model.

## Ground rules

- Do not open `key.json` until every row has a verdict. It holds the existing
  labels and what Audit said, and reading any of it ends the blind review.
- Judge the quote against the claim, not against what you know to be true of
  the subject. A correct claim can still be wrongly cited.
- `indeterminate` is a real answer. Use it rather than guessing.

Then run:

    python -m athenaeum.evaluation.review reveal --dir eval/audit/review/2026-09-26-set-a1

## What this review can and cannot establish

One reviewer, so there is no second reading to measure agreement against. The
corrected labels are better evidence than the originals, which were assigned
for a different task, but they carry an error of their own that nothing here
measures. Every number derived from them has to say so.

The three groups of items were drawn at different rates and are reported
separately. Nothing about the set as a whole follows from any one of them
without reweighting by the group sizes recorded in `key.json`.

Group sizes here: disagreement 43, support_agreement 35, contradict_agreement 20 (which group an item is in is not in `sheet.csv`, and is not recoverable from its review id).
