"""The server's doubts about its own cut.

`doubt_cut` looks at the passages a programmatic strategy produced and reports
structural signs that the cut is poor. It refuses nothing and calls no model;
the agent reads the doubts and decides. Each kind is tested on a document
built to raise that kind and only it, because a check that fires alongside
another tells the agent nothing it could not already see.
"""

from epimemer.core.types import Segment
from epimemer.pipelines.segmentation.doubts import (
    FRAGMENT_MAX_CHARS,
    FRAGMENTS_MIN_PASSAGES,
    OUTSIZED_MIN_PASSAGES,
    OUTSIZED_RATIO,
    SINGLE_PASSAGE_MIN_CHARS,
    TITLE_MAX_CHARS,
    Doubt,
    DoubtKind,
    doubt_cut,
)

DOC_ID = "doc-1"


def cut(parts: list[str]) -> tuple[str, list[Segment]]:
    """A document made of `parts` separated by blank lines, cut at each one."""
    content = "\n\n".join(parts)
    segments = []
    offset = 0
    for part in parts:
        segments.append(
            Segment(source_id=DOC_ID, text=part, span_start=offset, span_end=offset + len(part))
        )
        offset += len(part) + 2
    return content, segments


def kinds(doubts: list[Doubt]) -> list[DoubtKind]:
    return [d.kind for d in doubts]


def sentence_of(length: int) -> str:
    """A capitalised sentence of exactly `length` characters, ending in a full stop."""
    return "A" + "a" * (length - 2) + "."


class TestTheVocabulary:
    def test_the_four_kinds_and_only_those(self):
        assert {k.value for k in DoubtKind} == {
            "single_passage",
            "fragments",
            "outsized",
            "mid_sentence",
        }

    def test_a_doubt_dumps_to_plain_json(self):
        doubt = Doubt(kind=DoubtKind.FRAGMENTS, detail="passages 0, 1")
        assert doubt.model_dump(mode="json") == {"kind": "fragments", "detail": "passages 0, 1"}


class TestOrdinaryProseRaisesNothing:
    def test_two_paragraphs(self):
        content, segments = cut(
            [
                "The committee met on Tuesday to review the budget. Several members "
                "raised concerns about the cost of the new building.",
                "After a long discussion, the proposal was sent back to the finance "
                "office for revision. A vote is expected next month.",
            ]
        )
        assert doubt_cut(content, segments) == []

    def test_no_passages_raises_nothing(self):
        assert doubt_cut("", []) == []


class TestSinglePassage:
    def test_a_long_document_left_whole(self):
        content, segments = cut([sentence_of(SINGLE_PASSAGE_MIN_CHARS + 1)])

        doubts = doubt_cut(content, segments)

        assert kinds(doubts) == [DoubtKind.SINGLE_PASSAGE]
        assert "passage 0" in doubts[0].detail
        assert str(len(content)) in doubts[0].detail

    def test_the_threshold_is_a_dozen_sentences_not_a_paragraph(self):
        """Measured: session notes of one paragraph, 850 to 2,000 characters,
        were the whole of what 600 caught."""
        assert SINGLE_PASSAGE_MIN_CHARS == 1200

    def test_a_paragraph_sized_note_left_whole_is_the_authors_cut(self):
        content, segments = cut([sentence_of(900)])
        assert doubt_cut(content, segments) == []

    def test_at_the_threshold_it_is_not_raised(self):
        content, segments = cut([sentence_of(SINGLE_PASSAGE_MIN_CHARS)])
        assert doubt_cut(content, segments) == []

    def test_the_document_length_counts_not_the_passage(self):
        """Surrounding whitespace is part of the document the rule looked at."""
        text = sentence_of(SINGLE_PASSAGE_MIN_CHARS)
        content = "\n" + text + "\n"
        segment = Segment(source_id=DOC_ID, text=text, span_start=1, span_end=1 + len(text))
        assert kinds(doubt_cut(content, [segment])) == [DoubtKind.SINGLE_PASSAGE]


class TestFragments:
    def test_most_passages_too_short_to_stand_alone(self):
        # Long enough that the one full passage stays within the outsized
        # ratio of the median, so only `fragments` is raised.
        short = [
            "Apples are sold at the market.",
            "Pears are sold at the market too.",
            "Plums are sold there on Fridays.",
        ]
        content, segments = cut([*short, sentence_of(60)])

        doubts = doubt_cut(content, segments)

        assert kinds(doubts) == [DoubtKind.FRAGMENTS]
        assert "3 of 4" in doubts[0].detail
        for index, text in enumerate(short):
            assert f"passage {index}" in doubts[0].detail
            assert text in doubts[0].detail
        assert "passage 3" not in doubts[0].detail

    def test_exactly_half_is_not_more_than_half(self):
        content, segments = cut(["Apples.", "Pears.", sentence_of(60), sentence_of(70)])
        assert doubt_cut(content, segments) == []

    def test_too_few_passages_is_not_a_fragmented_cut(self):
        parts = ["Apples.", "Pears.", "Plums."]
        assert len(parts) == FRAGMENTS_MIN_PASSAGES - 1
        content, segments = cut(parts)
        assert doubt_cut(content, segments) == []

    def test_the_fragment_length_is_strictly_under_the_limit(self):
        at_limit = sentence_of(FRAGMENT_MAX_CHARS)
        under = sentence_of(FRAGMENT_MAX_CHARS - 1)
        content, segments = cut([at_limit, at_limit, at_limit, under])
        assert doubt_cut(content, segments) == []

        content, segments = cut([under, under, under, at_limit])
        assert kinds(doubt_cut(content, segments)) == [DoubtKind.FRAGMENTS]


class TestOutsized:
    def test_one_passage_dwarfs_the_rest(self):
        content, segments = cut([sentence_of(100), sentence_of(401), sentence_of(100)])

        doubts = doubt_cut(content, segments)

        assert kinds(doubts) == [DoubtKind.OUTSIZED]
        assert "passage 1" in doubts[0].detail
        assert "401" in doubts[0].detail
        assert "passage 0" not in doubts[0].detail

    def test_exactly_the_ratio_is_not_more_than_it(self):
        assert OUTSIZED_RATIO == 4
        content, segments = cut([sentence_of(100), sentence_of(400), sentence_of(100)])
        assert doubt_cut(content, segments) == []

    def test_two_passages_have_no_meaningful_median(self):
        assert OUTSIZED_MIN_PASSAGES == 3
        content, segments = cut([sentence_of(100), sentence_of(500)])
        assert doubt_cut(content, segments) == []


# Opens a passage so that it is longer than a title line, which the
# mid-sentence check exempts; the cut under test is at the passage's end.
OPENING = "After the second visit to the site, which took most of a long and rainy afternoon, "


def passage(ending: str) -> str:
    """A passage too long to be a title, ending in `ending`."""
    text = OPENING + ending
    assert len(text) >= TITLE_MAX_CHARS
    return text


class TestMidSentence:
    def test_a_cut_that_splits_a_sentence(self):
        content, segments = cut(
            [
                passage("the inspector found the wiring sound, although the report"),
                "noted that the fuse box would need replacing within the year.",
            ]
        )

        doubts = doubt_cut(content, segments)

        assert kinds(doubts) == [DoubtKind.MID_SENTENCE]
        assert "passage 0" in doubts[0].detail
        assert "passage 1" in doubts[0].detail
        assert "the report" in doubts[0].detail
        assert "noted that" in doubts[0].detail

    def test_every_split_is_named(self):
        content, segments = cut(
            [
                passage("the first part of the sentence runs on"),
                "into the second part, which also runs on through a long clause about "
                "the weather and the state of the roads",
                "into the third part and ends here.",
            ]
        )

        (doubt,) = doubt_cut(content, segments)

        assert doubt.kind is DoubtKind.MID_SENTENCE
        assert "passage 0" in doubt.detail
        assert "passage 1" in doubt.detail
        assert "passage 2" in doubt.detail

    def test_terminal_punctuation_ends_a_passage(self):
        for ending in (".", "!", "?", ":", ";"):
            content, segments = cut(
                [passage(f"the list of items follows{ending}"), "apples and pears are on it."]
            )
            assert doubt_cut(content, segments) == [], ending

    def test_closing_quotes_and_brackets_after_terminal_punctuation(self):
        for ending in ('."', ".'", ".\u201d", ".\u2019", ".)", "?]", '!")', ".}"):
            content, segments = cut(
                [passage(f"she said it was over{ending}"), "then she left the room quietly."]
            )
            assert doubt_cut(content, segments) == [], ending

    def test_a_closing_bracket_without_punctuation_is_still_open(self):
        content, segments = cut(
            [passage("the meeting (held in March)"), "went on longer than planned."]
        )
        assert kinds(doubt_cut(content, segments)) == [DoubtKind.MID_SENTENCE]

    def test_an_upper_case_start_is_a_new_sentence(self):
        content, segments = cut([passage("and without a full stop"), "The paragraph under it."])
        assert doubt_cut(content, segments) == []

    def test_a_non_letter_start_is_not_lower_case(self):
        content, segments = cut([passage("items to buy"), "- apples and pears."])
        assert doubt_cut(content, segments) == []


class TestHeadingsAndTitlesAreNotUnfinishedSentences:
    """A title or heading ends without punctuation and is its own passage.

    The measurement over the real graphs found exactly one mid-sentence doubt,
    and it was this: a markdown heading cut from the paragraph under it, which
    began with a lower-case identifier.
    """

    def test_the_measured_false_positive_raises_nothing(self):
        content, segments = cut(
            [
                "## What the system is",
                "freeagent_agent is a service that reads invoices and files them "
                "against the matching bank transaction.",
            ]
        )
        assert doubt_cut(content, segments) == []

    def test_a_heading_followed_by_a_paragraph_raises_nothing(self):
        heading = "# " + OPENING.strip()
        assert len(heading) >= TITLE_MAX_CHARS, "exempt as a heading, not as a short line"
        content, segments = cut([heading, "the paragraph under it begins in lower case."])
        assert doubt_cut(content, segments) == []

    def test_a_short_single_line_is_a_title(self):
        title = "a" * (TITLE_MAX_CHARS - 1)
        content, segments = cut([title, "then the text under the title."])
        assert doubt_cut(content, segments) == []

    def test_a_line_at_the_title_limit_is_a_sentence(self):
        line = "a" * TITLE_MAX_CHARS
        content, segments = cut([line, "then the rest of the sentence."])
        assert kinds(doubt_cut(content, segments)) == [DoubtKind.MID_SENTENCE]

    def test_short_text_over_two_lines_is_not_a_title(self):
        content, segments = cut(["The report said\nthat", "the roof would hold."])
        assert kinds(doubt_cut(content, segments)) == [DoubtKind.MID_SENTENCE]

    def test_a_genuine_split_still_raises(self):
        content, segments = cut(
            [
                passage("the council agreed that the bridge"),
                "would be closed for repairs until the spring.",
            ]
        )
        assert kinds(doubt_cut(content, segments)) == [DoubtKind.MID_SENTENCE]


class TestOrder:
    def test_passages_are_read_in_document_order(self):
        content, segments = cut([passage("the report said that"), "the roof would hold."])
        assert kinds(doubt_cut(content, list(reversed(segments)))) == [DoubtKind.MID_SENTENCE]
