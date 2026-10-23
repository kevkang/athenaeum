# Set A1: what kind of disagreement?

300 labeled citations in the set. 54 of them were re-run with the graded variant.

The binary run and the graded re-run are different predictors asked different questions. The graded verdicts below describe the graded variant only; they are not a re-scoring of the binary run.

3 single-quote items returned no set verdict and take it from their only quote's verdict (the rule in evaluation/graded.py). With one quote the set is the quote. Items: `scifact-355-12800122-set0-support`, `scifact-356-6144337-set1-support`, `scifact-67-21295300-set1-support`.

## 1. The disagreements, by graded verdict

43 items the set labels `supported` and the binary run called not supported: 36 with a single-sentence rationale, 7 with a multi-sentence one.

Graded verdict given to each of those items on the re-run:

| group | `supported` | `misread` | `overreach` | `indeterminate` | `error` | total |
|---|---|---|---|---|---|---|
| single-sentence rationale (n=36) | 0 | 12 | 22 | 2 | 0 | 36 |
| multi-sentence rationale (n=7) | 2 | 3 | 2 | 0 | 0 | 7 |

*Counts, not rates: this is the disagreeing subset, chosen because the two runs disagreed on it, so no proportion of it describes the set.*

## 2. Disagreements the graded variant calls `supported`

How many of those items the graded variant put in the label's own category. The rest it assigned to one of the fault categories above.

- Single-sentence disagreements the graded variant calls `supported`: 0 of 36 (0.0%), 95% CI 0.0% to 9.6%.
- Multi-sentence disagreements the graded variant calls `supported`: 2 of 7. No rate is given: 7 items cannot carry one.

## 3. `indeterminate` across the full set

- Items labeled `indeterminate` in the set: 0 of 300.
- Items the binary run called `indeterminate`: 0. The binary predictor emits only `supported`, `not_supported` and `error`, so this is zero by construction rather than by observation, and nothing was excluded from binary scoring on account of it.
- Items the graded re-run called `indeterminate`: 3 of 54 re-run.

## 4. Multi-quote items: one set verdict against the old rollups

18 items carry more than one quote. The binary run answered per quote and its item verdict came from a rollup rule applied afterwards. The graded re-run asked once about the quotes together.

| item | quotes | true verdict | strict (all quotes) | any (one quote) | set verdict |
|---|---|---|---|---|---|
| `scifact-1274-4406819-set0-support` | 2 | `supported` | `not_supported` | `not_supported` | `misread` |
| `scifact-443-10165258-set0-support` | 3 | `supported` | `supported` | `supported` | `supported` |
| `scifact-1028-13923140-set0-support` | 2 | `supported` | `not_supported` | `not_supported` | `overreach` |
| `scifact-792-3610080-set0-support` | 2 | `supported` | `not_supported` | `supported` | `supported` |
| `scifact-314-4347374-set0-support` | 2 | `supported` | `not_supported` | `supported` | `misread` |
| `scifact-71-1127562-set0-support` | 2 | `supported` | `not_supported` | `not_supported` | `misread` |
| `scifact-977-39264456-set0-support` | 2 | `supported` | `not_supported` | `not_supported` | `overreach` |
| `scifact-1263-3981729-set0-support` | 2 | `supported` | `not_supported` | `supported` | `supported` |
| `scifact-566-16120395-set0-contradict` | 2 | `misread` | `not_supported` | `not_supported` | `misread` |
| `scifact-290-15048300-set0-contradict` | 2 | `misread` | `not_supported` | `not_supported` | `misread` |
| `scifact-466-22544171-set0-contradict` | 3 | `misread` | `not_supported` | `not_supported` | `misread` |
| `scifact-590-10984005-set0-contradict` | 2 | `misread` | `not_supported` | `not_supported` | `misread` |
| `scifact-873-1180972-set0-contradict` | 2 | `misread` | `not_supported` | `not_supported` | `misread` |
| `scifact-1404-38355793-set2-swapped_quotes` | 3 | `differs_from_source` | `not_supported` | `not_supported` | `misread` |
| `scifact-1003-4319844-set0-swapped_quotes` | 2 | `differs_from_source` | `not_supported` | `not_supported` | `misread` |
| `scifact-1163-15305881-set0-swapped_quotes` | 2 | `differs_from_source` | `not_supported` | `not_supported` | `indeterminate` |
| `scifact-432-8002887-set0-swapped_quotes` | 2 | `differs_from_source` | `not_supported` | `not_supported` | `misread` |
| `scifact-1119-13907928-set1-swapped_quotes` | 2 | `differs_from_source` | `not_supported` | `not_supported` | `misread` |

*strict and any are the two rollups computed from the binary run's per-quote verdicts. The set verdict is what the graded variant answered when asked about the quotes as one body of evidence. The two columns come from different prompts, so a row where they differ does not isolate the rollup rule: the question changed as well. Counts only; no rate is given for a group of 18.*

- The set verdict's supported/not-supported split matches the strict rollup on 16 of 18 items, and the any rollup on 17.

## 5. Response integrity

- Binary run: 0 of 300 items produced no verdict; 0 items carried at least one quote the model left unjudged.
- Graded re-run: 0 of 54 items produced no usable verdict after recovery.

