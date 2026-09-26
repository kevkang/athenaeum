# Audit evaluation: `audit-binary:claude-sonnet-5` on `set-a1-scifact`

300 labeled citations.

## 1. Confusion matrix

| true verdict | predicted `supported` | predicted `not_supported` | predicted `error` | total |
|---|---|---|---|---|
| `supported` | 57 | 43 | 0 | 100 |
| `misread` | 0 | 100 | 0 | 100 |
| `overreach` | 0 | 0 | 0 | 0 |
| `indeterminate` | 0 | 0 | 0 | 0 |
| `differs_from_source` | 0 | 100 | 0 | 100 |

*Each cell counts citations whose true verdict is the row and whose predicted verdict is the column.*

## 2. Per-verdict scores

| verdict | precision | recall | F1 | support |
|---|---|---|---|---|
| `supported` | 1.000 | 0.570 | 0.726 | 100 |

*Precision: of citations predicted as the verdict, the share that truly carry it. Recall: of citations that truly carry it, the share predicted as it. n/a: undefined, because the verdict was never predicted or never occurs in the set.*

## 3. Bad citations predicted `supported`

| true verdict | predicted `supported` | citations | rate | 95% interval |
|---|---|---|---|---|
| `misread` | 0 | 100 | 0.0% | 0.0% to 3.7% |
| `overreach` | 0 | 0 | n/a | n/a |
| `differs_from_source` | 0 | 100 | 0.0% | 0.0% to 3.7% |
| all bad verdicts | 0 | 200 | 0.0% | 0.0% to 1.9% |

*Rate: share of citations with the row's true verdict that received the `supported` prediction. Interval: Wilson score interval.*

## 4. Macro-F1

Not computed: `audit-binary:claude-sonnet-5` does not emit every judgment verdict (`supported`, `misread`, `overreach`, `indeterminate`).
