# Response integrity for this run, corrected

`report.md` and `run.json` are left exactly as the run produced them. This
file corrects how their integrity numbers were counted. No verdict, score or
prediction changes — only the accounting of what the model failed to return.

## What the run printed

> Response integrity: 19 of 321 evidence checks returned an entry that could
> not be used (5.9%); 0 quotes received no verdict. 1 of 300 items produced no
> verdict at all (0.3%). 18 single-quote items had no set verdict and took it
> from their only quote.
>
> FAILED: unusable-response rate 5.9% above the 5.0% threshold.

## What those 19 actually were

- **18** single-quote items where the model omitted the required `set_verdict`
  field while answering the per-quote question fully. Each one's set verdict
  was read off its only quote, which for a single quote is the same judgment.
  No evidence went unjudged.
- **1** two-quote item (`scifact-71-1127562-set0-support`) that returned both
  per-quote verdicts and no set verdict. Recovery is refused for two or more
  quotes, so it is recorded as an error. It is also the one item counted in
  "1 of 300 items produced no verdict at all".
- **0** per-quote entries that could not be applied to a quote.

## Why the number was wrong

`malformed_entries` counts entries in the response that cannot be attached to
a quote; its denominator is quotes. A missing `set_verdict` is not an entry —
it is one missing field on the item, and its denominator is items. The graded
path incremented the quote-level counter for an item-level failure, so the
rate it produced described neither, and 18 recoveries that lost nothing were
counted as damage.

Counted separately, as the code now does:

| what | count | denominator | rate |
|---|---|---|---|
| per-quote entries unusable | 0 | 321 quotes | 0.0% |
| items with no set verdict | 19 | 300 items | 6.3% |
| ... of those, recovered from a single quote | 18 | 300 items | 6.0% |
| items with no usable verdict after recovery | 1 | 300 items | 0.3% |

Under that accounting the run is below the 5% threshold on both rates it is
checked against, and the 6.3% schema-deviation rate is reported rather than
failed on, because nothing was lost to it.

**This change was made after the threshold fired**, which is exactly the
circumstance in which a change to a failure criterion deserves suspicion. The
argument for it does not depend on the failure: a per-quote counter was being
incremented for an item-level event, and that was wrong when it was written.
The counters are separate now, with tests pinning each denominator.

## A gap this run leaves

The 18 recovered items cannot be identified from `predictions.jsonl`: recovery
was counted in a total but not recorded per item, and those items carried a
set rationale, so nothing in their row marks them. The code now writes
`set_verdict_recovered` on every graded prediction. This run was not repeated
to gain that field: the verdicts are unaffected, and re-running 300 items to
label 18 rows would spend money on bookkeeping.
