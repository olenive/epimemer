"""The agent's own cut, stated as anchors.

`segments_from_anchors` turns a list of anchors, each the text a passage begins
with, into passages, or refuses the whole list and names every problem in it.
The contract is `docs/INGEST.md` §4: each anchor is found at its
first occurrence after the end of the one before, a passage runs to the next
anchor, and text before the first anchor is refused rather than dropped.

The last class checks the anchor minimum against the anchors an agent would
write for the shapes the guide names (§7), since that minimum is a starting
guess: does it refuse anchors a careful agent writes, or only the ones that
match too early?
"""

import unicodedata

import pytest

from epimemer.core.types import RawDocument, Segment
from epimemer.pipelines.segmentation.anchors import (
    ANCHOR_MIN_CHARS,
    ANCHOR_MIN_WORDS,
    AnchorsRefused,
    refusal_message,
    segments_from_anchors,
)

PROSE = (
    "The committee met on Tuesday to review the budget. Several members raised "
    "concerns about the cost of the new building. "
    "After a long discussion, the proposal was sent back to the finance office "
    "for revision. A vote on the budget is expected next month."
)


def document(content: str) -> RawDocument:
    return RawDocument(id="doc-1", content=content)


def passages(content: str, anchors: list[str]) -> list[Segment]:
    cut = segments_from_anchors(document(content), anchors)
    assert isinstance(cut, list), cut
    return cut


def refused(content: str, anchors: list[str]) -> list[str]:
    cut = segments_from_anchors(document(content), anchors)
    assert isinstance(cut, AnchorsRefused), cut
    return cut.problems


class TestCoverage:
    def test_two_anchors_make_two_passages_covering_the_document(self):
        cut = passages(PROSE, ["The committee met on", "After a long discussion"])

        assert [s.text for s in cut] == [
            "The committee met on Tuesday to review the budget. Several members raised "
            "concerns about the cost of the new building.",
            "After a long discussion, the proposal was sent back to the finance office "
            "for revision. A vote on the budget is expected next month.",
        ]

    def test_every_passage_belongs_to_the_document(self):
        cut = passages(PROSE, ["The committee met on", "After a long discussion"])

        assert {s.source_id for s in cut} == {"doc-1"}

    def test_the_last_passage_runs_to_the_end(self):
        cut = passages(PROSE, ["The committee met on", "A vote on the budget"])

        assert cut[-1].text == "A vote on the budget is expected next month."
        assert cut[-1].span_end == len(PROSE)

    def test_one_anchor_at_the_start_is_one_passage(self):
        """The agent is allowed to say the document is one passage."""
        cut = passages(PROSE, ["The committee met on"])

        assert len(cut) == 1
        assert cut[0].text == PROSE
        assert (cut[0].span_start, cut[0].span_end) == (0, len(PROSE))

    def test_each_passage_is_a_fresh_record(self):
        cut = passages(PROSE, ["The committee met on", "After a long discussion"])

        assert len({s.id for s in cut}) == 2


class TestFirstOccurrence:
    def test_an_anchor_takes_its_first_occurrence_after_the_one_before(self):
        content = "One two three four. The budget rose. Five six seven. The budget rose again."

        cut = passages(content, ["One two three four", "The budget rose"])

        assert cut[1].text == "The budget rose. Five six seven. The budget rose again."

    def test_a_lengthened_anchor_selects_a_later_occurrence(self):
        content = "One two three four. The budget rose. Five six seven. The budget rose again."

        cut = passages(content, ["One two three four", "The budget rose again"])

        assert [s.text for s in cut] == [
            "One two three four. The budget rose. Five six seven.",
            "The budget rose again.",
        ]

    def test_a_later_anchor_is_searched_from_the_end_of_the_one_before(self):
        """A phrase inside the previous anchor is not where the next one starts."""
        content = "Minutes of the meeting held on Tuesday. Minutes of the next meeting follow."

        cut = passages(content, ["Minutes of the meeting", "Minutes of the"])

        assert cut[1].text == "Minutes of the next meeting follow."

    def test_an_occurrence_before_the_previous_cut_is_passed_over_for_the_next(self):
        """`The harbour toll` first occurs inside the opening passage, before
        `Beta report` begins; the search starts after `Beta report`, so the
        second occurrence is the one cut at (§4.1)."""
        content = (
            "Alpha report: The harbour toll rose. Beta report: costs were flat. "
            "The harbour toll fell in May."
        )

        cut = passages(content, ["Alpha report: The", "Beta report: costs", "The harbour toll"])

        assert [s.text for s in cut] == [
            "Alpha report: The harbour toll rose.",
            "Beta report: costs were flat.",
            "The harbour toll fell in May.",
        ]
        assert cut[2].span_start == content.rindex("The harbour toll")


class TestWhitespace:
    def test_passages_are_trimmed_and_spans_stay_exact(self):
        content = "\n\n  First passage starts here.\n\n\n   Second passage starts here.  \n"

        cut = passages(content, ["First passage starts", "Second passage starts"])

        assert [s.text for s in cut] == [
            "First passage starts here.",
            "Second passage starts here.",
        ]
        for segment in cut:
            assert content[segment.span_start : segment.span_end] == segment.text

    def test_whitespace_before_the_first_anchor_is_not_refused(self):
        content = "   \n\nThe committee met on Tuesday."

        cut = passages(content, ["The committee met on"])

        assert cut[0].span_start == content.index("The")

    def test_an_anchor_carrying_its_own_leading_whitespace_still_trims(self):
        content = "Alpha beta gamma delta.\n\nEpsilon zeta eta theta."

        cut = passages(content, ["Alpha beta gamma", "\n\nEpsilon zeta eta"])

        assert cut[1].text == "Epsilon zeta eta theta."
        assert cut[1].span_start == content.index("Epsilon")


class TestTextAsCopied:
    """An anchor matches character for character (§4.1), so the characters a
    copy can change without changing how the text looks decide whether it is
    found: a non-breaking space, a decomposed accent, a line ending, a
    byte-order mark."""

    def test_a_non_breaking_space_where_the_document_has_a_space_is_refused_and_shown(self):
        """The quote folds only ASCII whitespace, so the non-breaking space
        stays in it, escaped, and the agent can see why the anchor missed."""
        (problem,) = refused(PROSE, ["The committee met on", "After a\xa0long discussion"])

        assert "cuts[1]" in problem
        assert "not found after the end of cuts[0]" in problem
        assert "'After a\\xa0long discussion'" in problem

    def test_ascii_whitespace_in_a_quote_still_folds_to_one_space(self):
        (problem,) = refused(PROSE, ["The committee met on", "After a\r\n\t long   meeting"])

        assert "'After a long meeting'" in problem

    def test_a_decomposed_accent_where_the_document_is_composed_is_refused_and_named(self):
        """A decomposed accent prints like a composed one, so escaping cannot
        show the difference in the quote; the problem names it instead."""
        content = "Résumé of the meeting held on Tuesday. Café opening hours were discussed."
        decomposed = unicodedata.normalize("NFD", "Café opening hours")
        assert decomposed != "Café opening hours"

        (problem,) = refused(content, ["Résumé of the", decomposed])

        assert problem == (
            f"cuts[1] {decomposed!r} is not found after the end of cuts[0]: it differs "
            "from the document's text only in Unicode normalisation, and the document "
            "has it in NFC"
        )

    def test_a_composed_accent_where_the_document_is_decomposed_is_named_too(self):
        content = unicodedata.normalize(
            "NFD", "Résumé of the meeting held on Tuesday. Café opening hours were discussed."
        )

        problems = refused(content, ["Résumé of the meeting", "Café opening hours"])

        assert [p[:8] for p in problems] == ["cuts[0] ", "cuts[1] "]
        assert all(p.endswith("the document has it in NFD") for p in problems)

    def test_an_anchor_missing_for_another_reason_carries_no_normalisation_hint(self):
        (problem,) = refused(PROSE, ["The committee met on", "Café opening hours"])

        assert problem == "cuts[1] 'Café opening hours' is not found after the end of cuts[0]"

    def test_crlf_line_endings_keep_every_span_exact(self):
        content = (
            "INTERVIEWER: Where were you?\r\nWITNESS: At home.\r\n\r\n"
            "INTERVIEWER: Let us turn to the claim.\r\nWITNESS: I filed it.\r\n"
        )

        cut = passages(content, ["INTERVIEWER: Where were", "INTERVIEWER: Let us turn"])

        assert [s.text for s in cut] == [
            "INTERVIEWER: Where were you?\r\nWITNESS: At home.",
            "INTERVIEWER: Let us turn to the claim.\r\nWITNESS: I filed it.",
        ]
        for segment in cut:
            assert content[segment.span_start : segment.span_end] == segment.text

    def test_an_anchor_across_a_crlf_break_must_carry_the_cr(self):
        """The short markdown heading is the shape whose anchor has to cross a
        line break (`TestRealAnchorsAgainstTheMinimum`), and so the one where
        the line ending is part of what is copied."""
        content = MARKDOWN.replace("\n", "\r\n")

        problems = refused(content, ["# Changelog\n\nFirst", "## Fees\n\nThe fee"])

        assert len(problems) == 2
        assert all("not found" in p for p in problems)

        cut = passages(content, ["# Changelog\r\n\r\nFirst", "## Fees\r\n\r\nThe fee"])

        assert cut[1].text == "## Fees\r\n\r\nThe fee is five pounds a month."
        for segment in cut:
            assert content[segment.span_start : segment.span_end] == segment.text

    def test_a_byte_order_mark_before_the_first_anchor_is_refused_and_shown(self):
        """U+FEFF is not whitespace, so it is text the first anchor leaves
        uncovered. The quote escapes it, which is the only place an agent that
        never saw the mark can learn it is there."""
        (problem,) = refused("﻿" + PROSE, ["The committee met on"])

        assert "before cuts[0]" in problem
        assert "'\\ufeff'" in problem

    def test_a_first_anchor_carrying_the_byte_order_mark_is_accepted(self):
        content = "﻿" + PROSE

        cut = passages(content, ["﻿The committee met on", "After a long discussion"])

        assert cut[0].span_start == 0
        assert cut[0].text.startswith("﻿The committee")
        for segment in cut:
            assert content[segment.span_start : segment.span_end] == segment.text


class TestRefusals:
    def test_no_anchors_at_all(self):
        (problem,) = refused(PROSE, [])

        assert "no cuts" in problem

    def test_an_anchor_that_is_not_in_the_document_is_quoted(self):
        (problem,) = refused(PROSE, ["The committee met on", "The board adjourned early"])

        assert "cuts[1]" in problem
        assert "'The board adjourned early'" in problem
        assert "not found after the end of cuts[0]" in problem

    def test_no_anchor_found_at_all(self):
        problems = refused(PROSE, ["The board adjourned early", "Nobody spoke at all"])

        assert len(problems) == 2
        assert "cuts[0]" in problems[0] and "not found in the document" in problems[0]
        assert "cuts[1]" in problems[1] and "not found after the end of cuts[0]" in problems[1]

    def test_an_anchor_out_of_order_is_not_found_after_the_one_before(self):
        problems = refused(PROSE, ["After a long discussion", "The committee met on"])

        assert any("cuts[1]" in p and "not found after the end of cuts[0]" in p for p in problems)

    def test_an_anchor_shorter_than_the_minimum(self):
        problems = refused(PROSE, ["The committee met on", "After a"])

        assert len(problems) == 1
        assert "cuts[1]" in problems[0]
        assert "too short" in problems[0]

    def test_text_before_the_first_anchor_is_quoted_in_part(self):
        (problem,) = refused(PROSE, ["After a long discussion"])

        assert "before cuts[0]" in problem
        assert "'The committee met on Tuesday" in problem

    def test_an_anchor_that_leaves_an_empty_passage(self):
        """Only reachable by an anchor that is nothing but whitespace, since a
        passage otherwise holds at least its own anchor; reported beside the
        anchor being too short, because both are true of it."""
        content = "Alpha beta gamma delta.   Epsilon zeta eta theta."

        problems = refused(content, ["Alpha beta gamma", "   ", "Epsilon zeta eta"])

        assert any("cuts[1]" in p and "too short" in p for p in problems)
        assert any("cuts[1]" in p and "is empty" in p for p in problems)

    def test_every_problem_is_named_at_once(self):
        problems = refused(
            PROSE,
            [
                "Several members raised",  # leaves the opening sentence uncovered
                "the cost",  # too short
                "The board adjourned early",  # not in the document
            ],
        )

        assert len(problems) == 3
        assert any("too short" in p and "cuts[1]" in p for p in problems)
        assert any("not found" in p and "cuts[2]" in p for p in problems)
        assert any("before cuts[0]" in p for p in problems)

    def test_the_message_leads_with_what_was_written(self):
        cut = segments_from_anchors(document(PROSE), ["After a"])
        assert isinstance(cut, AnchorsRefused)

        message = refusal_message(cut, tool="segment")

        assert message.startswith("segment wrote nothing: the cuts have 2 problems.")
        for problem in cut.problems:
            assert problem in message


class TestTheMinimum:
    @pytest.mark.parametrize(
        "anchor",
        [
            "one two three",  # three words, however short
            "Transcript:x",  # twelve characters, one word
        ],
    )
    def test_either_half_is_enough(self, anchor):
        assert len(anchor.split()) >= ANCHOR_MIN_WORDS or len(anchor) >= ANCHOR_MIN_CHARS

        cut = passages(f"{anchor} and the rest of the document.", [anchor])

        assert len(cut) == 1

    @pytest.mark.parametrize("anchor", ["two words", "   padded   "])
    def test_short_on_both_counts_is_refused(self, anchor):
        problems = refused(f"{anchor} and the rest of the document.", [anchor])

        assert any("too short" in p for p in problems)


# The shapes the guide tells an agent to cut by hand (`docs/INGEST.md` §5),
# with the anchors an agent would write by copying the first words of each
# passage.
TRANSCRIPT = (
    "INTERVIEWER: Where were you on the night of the fire?\n"
    "WITNESS: At home, with my sister.\n"
    "INTERVIEWER: Did either of you go out?\n"
    "WITNESS: No. We heard the sirens around midnight.\n"
    "INTERVIEWER: Let us turn to the insurance claim.\n"
    "WITNESS: I filed it the following week."
)

CONTRACT = (
    "1. Definitions\n"
    "In this agreement the Supplier means Acme Ltd.\n"
    "2. Term\n"
    "This agreement runs for two years from signature.\n"
    "3. Termination\n"
    "Either party may end it on ninety days' notice."
)

CHAT = (
    "[09:12] ana: did the deploy go out?\n"
    "[09:13] ben: yes, at nine\n"
    "[09:13] ana: great, thanks\n"
    "[09:40] ben: separate thing, the invoice for March is wrong\n"
    "[09:41] ana: wrong how?"
)

MARKDOWN = "# Changelog\n\nFirst entry.\n\n## Fees\n\nThe fee is five pounds a month."


class TestRealAnchorsAgainstTheMinimum:
    def test_a_transcript_cut_at_question_and_answer(self):
        cut = passages(
            TRANSCRIPT,
            ["INTERVIEWER: Where were you", "INTERVIEWER: Let us turn"],
        )

        assert [s.text.splitlines()[0] for s in cut] == [
            "INTERVIEWER: Where were you on the night of the fire?",
            "INTERVIEWER: Let us turn to the insurance claim.",
        ]

    def test_a_bare_speaker_label_is_refused_and_would_have_matched_too_early(self):
        """`INTERVIEWER:` is twelve characters and passes; `WITNESS:` is the
        anchor the minimum exists for, since it matches the first turn of that
        speaker, wherever the agent meant."""
        assert refused(TRANSCRIPT, ["INTERVIEWER: Where", "WITNESS:"])

    def test_a_contract_cut_at_numbered_clauses(self):
        """A numbered heading clears the minimum on characters alone when its
        title is a long word, and needs a word of the clause when it is not."""
        assert len("3. Termination") >= ANCHOR_MIN_CHARS
        assert len("2. Term") < ANCHOR_MIN_CHARS

        cut = passages(CONTRACT, ["1. Definitions", "2. Term\nThis", "3. Termination"])

        assert [s.text.splitlines()[0] for s in cut] == [
            "1. Definitions",
            "2. Term",
            "3. Termination",
        ]

    def test_a_chat_log_cut_at_a_change_of_subject(self):
        cut = passages(CHAT, ["[09:12] ana: did", "[09:40] ben: separate thing"])

        assert len(cut) == 2
        assert cut[1].text.startswith("[09:40] ben: separate thing")

    def test_a_short_markdown_heading_needs_the_line_after_it(self):
        """The one shape where a natural anchor trips the minimum: a heading of
        one short word, which is two words counting the marker. The agent
        lengthens it across the line break."""
        problems = refused(MARKDOWN, ["# Changelog", "## Fees"])

        assert len(problems) == 2
        assert all("too short" in p for p in problems)

        cut = passages(MARKDOWN, ["# Changelog\n\nFirst", "## Fees\n\nThe fee"])

        assert cut[1].text == "## Fees\n\nThe fee is five pounds a month."
