# Audit evaluation: `audit-graded:claude-sonnet-5` on `set-a1-scifact`

300 labeled citations.

Response integrity: 19 of 321 evidence checks returned an entry that could not be used (5.9%); 0 quotes received no verdict. 1 of 300 items produced no verdict at all (0.3%). 18 single-quote items had no set verdict and took it from their only quote (evaluation/graded.py).

## 1. Confusion matrix

| true verdict | predicted `supported` | predicted `misread` | predicted `overreach` | predicted `indeterminate` | predicted `error` | total |
|---|---|---|---|---|---|---|
| `supported` | 30 | 18 | 49 | 2 | 1 | 100 |
| `misread` | 0 | 98 | 2 | 0 | 0 | 100 |
| `overreach` | 0 | 0 | 0 | 0 | 0 | 0 |
| `indeterminate` | 0 | 0 | 0 | 0 | 0 | 0 |
| `differs_from_source` | 0 | 87 | 0 | 13 | 0 | 100 |

*Each cell counts citations whose true verdict is the row and whose predicted verdict is the column.*

## 2. Per-verdict scores

| verdict | precision | recall | F1 | support |
|---|---|---|---|---|
| `supported` | 1.000 | 0.300 | 0.462 | 100 |
| `misread` | 0.483 | 0.980 | 0.647 | 100 |
| `overreach` | 0.000 | n/a | n/a | 0 |
| `indeterminate` | 0.000 | n/a | n/a | 0 |

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

Macro-F1 over `supported`, `misread`: 0.554.
Left out because no citation in the set carries them: `overreach`, `indeterminate`.
