# Third-party data in these sets

These files are labeled citations used to evaluate Audit. Some are built from
datasets published by others; their terms travel with anything derived from them.

## SciFact

Sets whose items have `"source": "set-a-scifact"` are built from SciFact:

> Wadden et al., 2020. "Fact or Fiction: Verifying Scientific Claims."
> Proceedings of EMNLP 2020. https://aclanthology.org/2020.emnlp-main.609/

Released by the Allen Institute for AI under
[CC BY-NC 2.0](https://creativecommons.org/licenses/by-nc/2.0/): attribution
required, non-commercial use only.

Claims and abstract sentences are reproduced verbatim from SciFact. Labels are
derived from SciFact's own SUPPORT and CONTRADICT annotations, or from a
documented corruption of them, as described in
`src/athenaeum/evaluation/sets/scifact.py`.
