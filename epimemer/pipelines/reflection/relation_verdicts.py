"""What an agent decided about a nominated pair of relation labels (#74 FC1).

The label layer's answer to `similarity_decisions.py`, and the same defect one
tier down. `sweep_similar_relation_pairs` re-derives from scratch on every
`reflect` and **recorded nothing about declines**: reflection nominated, a merge
happened only if the agent called `apply_reflection(relation_merges=[…])`, and
declining therefore meant *not making that call* and left no trace anywhere. The
next session a different agent scans the same edges, embeds the same strings,
gets the same cosine, and is asked the same question. For ever.

**Getting it right is what caused the loop.** Accepting a merge made one label
stop existing, so that pair could never be nominated again — accepting was
self-suppressing and declining was not, and the graph therefore applied quiet
pressure toward the wrong answer, on a fresh agent each time who could not see
the previous refusals.

**Merging is gone as of 2026-08-28** (`RELATION_LABELS.md` §5), which removes
the asymmetry rather than this module: with nothing self-suppressing, a verdict
is the *only* thing that stops a pair coming back, so what was the fix for a
bias is now the whole mechanism.

#64 closed exactly this for fact pairs with the `assessed` edge, and relation
labels could not have one: that edge runs **between two nodes**, and `works_for`
and `employed_by` are not nodes. Stage 1 gave them records, which is what makes
this row addressable at all — the same identity that resolves #69's question
about a relation decision's subjects.

**Both verdicts suppress.** `distinct` is *different relationships that look
alike* — the worked example is a servant who *works for* a master in a culture
with no employment relation, beside a corporation that formally *employs* a
consultant who does very little work: near-identical strings, opposite meanings,
and the nominator sees only strings. `synonymous` is *the same relationship
written two ways*, and it acts on nothing — no edge is relabelled and neither
label stops existing. Recording *"yes, these are
synonyms, and I am not merging them"* is a real judgment, and leaving it
unrecordable would be FC1 again for the affirmative answer; whatever
consolidates labels can then act on standing verdicts rather than re-asking.

**A label with no record is not refused; it gets one.** The verdict creates it,
carrying no judge — nobody is claiming to have introduced the word. The first
draft refused here and pointed at `epimemer relations backfill`, which was a
dead end: the CLI refuses embedded backends, which is the default development
configuration, and an agent cannot run it in any case. *A remedy the agent
cannot issue, on a backend where it refuses, is not a remedy.*
"""

from typing import Sequence

from pydantic import BaseModel

from epimemer.core.types import (
    JudgeRef,
    RELATION_VERDICTS,
    RelationLabel,
    RelationVerdict,
    relation_pair_key,
)
from epimemer.storage.protocol import StorageBackend


class RelationVerdictRefused(BaseModel):
    """Why one verdict about a label pair was not recorded.

    Prose rather than a code, matching `SimilarityRefused` and `BoundaryRefused`:
    the reasons do not form a vocabulary anything branches on.
    """

    pair: list[str]
    reason: str


class RelationVerdictRecorded(BaseModel):
    """What one accepted verdict wrote.

    `label_ids` are the two records the journal row will name — created here if
    the labels had none — and are what makes this decision addressable.

    `created` says **this call** wrote a verdict row rather than confirming one
    the pair already carried. The caller needs it because the two are different
    journal rows: a decision, and a confirmation citing the decision it agrees
    with. It is the same distinction `_ensure_symmetric_edge` reports for node
    pairs, arrived at the same way.
    """

    pair: list[str]
    kind: str
    verdict: str
    label_ids: list[str]
    verdict_id: str | None = None
    created: bool = True
    labels_created: list[str] = []


def _same_judge(a: JudgeRef | None, b: JudgeRef | None) -> bool:
    """Whether two decisions were made by the same judge.

    On `agent_id` alone: a judge that re-described itself is the same judge, so
    comparing the whole ref would let a reworded description turn a retry into a
    confirmation. The decision journal's `judged_by` index is built on the same
    reasoning.

    **Two unnamed judges compare equal, and that is the deliberate direction.**
    Where a graph does not require a judge, an anonymous repeat is
    indistinguishable from a replayed batch, and the two want opposite
    treatments — a confirmation row for the first, silence for the second.
    Refusing costs an unnamed agent the ability to confirm, which the journal's
    first row already records; accepting would let a retried call manufacture
    agreement out of nobody.
    """
    return (a.agent_id if a else None) == (b.agent_id if b else None)


async def _label_record(
    storage: StorageBackend, name: str, kind: str, judge: JudgeRef | None
) -> tuple[RelationLabel, bool]:
    """The record for one label, created judge-less if it has none (§2.3).

    `judge` is accepted and deliberately **not** written: `RelationLabel.judged_by`
    is the coiner and never the describer, the judger or the backfiller. Taking
    the argument and dropping it is how that rule stays visible at the one call
    site most likely to reach for it.
    """
    existing = await storage.get_relation_label(name, kind)
    if existing is not None:
        return existing, False
    record = RelationLabel(name=name, kind=kind)
    await storage.store_relation_label(record)
    return record, True


async def apply_relation_verdict(
    storage: StorageBackend,
    *,
    label_a: str,
    label_b: str,
    kind: str,
    verdict: str,
    because: str,
    judge: JudgeRef | None = None,
) -> RelationVerdictRefused | RelationVerdictRecorded:
    """Record one verdict about one nominated label pair, or say why not.

    Refusals are ordered permanent-first, on `fact_dedup`'s reasoning: a
    malformed request will never become well-formed, while a label no edge
    carries may be coined tomorrow. Reporting the second while the first also
    stands sends an agent to do work that changes nothing.

    **A different judge recording a verdict the pair already carries has
    confirmed, not decided.** No second row is written — the verdict stands
    untouched, exactly as a contradiction edge does — and the caller journals a
    confirmation citing the original. That is what stops a third agent doing the
    work a fourth time.

    **A different verdict from anyone is a fresh decision and is recorded as
    one.** Nothing is withdrawn and nothing is overruled: the table is
    append-only, both rows survive with their judges and their reasons, and
    since suppression is what both verdicts do, a disagreement changes nothing
    operationally. It is a disagreement made visible rather than resolved, and
    resolving it is not this call's business (`ISSUES.md` #80).
    """
    pair = [label_a, label_b]

    if verdict not in RELATION_VERDICTS:
        return RelationVerdictRefused(
            pair=pair,
            reason=(
                f"'{verdict}' is not a verdict about a label pair. Expected one "
                f"of: {', '.join(RELATION_VERDICTS)} — 'distinct' where the two "
                f"name different relationships that look alike, 'synonymous' "
                f"where they are one relationship written two ways."
            ),
        )
    if not because.strip():
        return RelationVerdictRefused(
            pair=pair,
            reason=(
                "`because` is required: this verdict suppresses the pair from "
                "every future nomination, so the graph has to carry why. "
                "Without one the next agent skips the pair without knowing "
                "whether it was examined or waved through."
            ),
        )
    if label_a == label_b:
        return RelationVerdictRefused(
            pair=pair,
            reason="a label is already itself; a pair needs two labels.",
        )

    kinds = {name: await storage.get_relation_kind(name) for name in pair}
    unused = [name for name, in_force in kinds.items() if in_force is None]
    if unused:
        return RelationVerdictRefused(
            pair=pair,
            reason=(
                f"no edge in this graph carries the relation "
                f"{', '.join(repr(n) for n in unused)}. `list_relations` shows "
                f"the vocabulary that exists; a verdict about a word nothing "
                f"uses would suppress a nomination that can never be made."
            ),
        )
    if kinds[label_a] != kinds[label_b]:
        return RelationVerdictRefused(
            pair=pair,
            reason=(
                f"'{label_a}' is a '{kinds[label_a]}' relation and '{label_b}' "
                f"is a '{kinds[label_b]}' one. The kind decides whether "
                f"retrieval follows the edge, so two labels of different kinds "
                f"are never one relationship — and the sweep never nominates "
                f"them as a pair."
            ),
        )
    in_force = kinds[label_a]
    if in_force != kind:
        return RelationVerdictRefused(
            pair=pair,
            reason=(
                f"this pair is '{in_force}' in this graph, not '{kind}'. The "
                f"kind is in force on the edges and a record only mirrors it, "
                f"so a nomination naming a different one is stale — re-run "
                f"`reflect` rather than recording against it."
            ),
        )

    record_a, created_a = await _label_record(storage, label_a, in_force, judge)
    record_b, created_b = await _label_record(storage, label_b, in_force, judge)
    label_ids = list(relation_pair_key(record_a.id, record_b.id))
    labels_created = [
        name
        for name, created in ((label_a, created_a), (label_b, created_b))
        if created
    ]

    standing: Sequence[RelationVerdict] = await storage.relation_verdicts_for(
        label_ids
    )
    agreeing = [v for v in standing if v.verdict == verdict]
    if any(_same_judge(v.judged_by, judge) for v in agreeing):
        return RelationVerdictRefused(
            pair=pair,
            reason=(
                f"you have already judged this pair '{verdict}', and a retry is "
                f"not a second opinion. The pair is suppressed; nothing further "
                f"is needed. A verdict that should be revisited is "
                f"`ISSUES.md` #80, not a second row."
            ),
        )
    if agreeing:
        # Confirmed rather than decided. The standing verdict is untouched and
        # no row is written here — the confirmation is the caller's journal row,
        # which is where a *second* agent agreeing belongs (§3.4).
        return RelationVerdictRecorded(
            pair=pair,
            kind=in_force,
            verdict=verdict,
            label_ids=label_ids,
            verdict_id=agreeing[0].id,
            created=False,
            labels_created=labels_created,
        )

    row = RelationVerdict(
        label_ids=label_ids, verdict=verdict, because=because, judged_by=judge
    )
    verdict_id = await storage.record_relation_verdict(row)
    return RelationVerdictRecorded(
        pair=pair,
        kind=in_force,
        verdict=verdict,
        label_ids=label_ids,
        verdict_id=verdict_id,
        created=True,
        labels_created=labels_created,
    )


__all__ = [
    "RelationVerdictRecorded",
    "RelationVerdictRefused",
    "apply_relation_verdict",
]
