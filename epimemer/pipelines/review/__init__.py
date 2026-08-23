"""Reading the decision journal back, and writing what a reviewer concluded
(`dev-docs/REVIEW_MODE.md` §5, §6).

Not to be confused with `pipelines/reflection/review.py`, which plans the
supersessions of the *epistemic* review loop. This package is about the journal.

- `modes.py` — which decisions a call is looking at, and how it is narrowed.
- `difficulty.py` — what order they arrive in, in two tiers that never mix.
- `apply.py` — the two things a reviewer can write, neither of which changes the
  graph.

The split is §6's own: *which*, *what order*, and *whether narrowed further* are
different questions, and the first draft ran them together.
"""
