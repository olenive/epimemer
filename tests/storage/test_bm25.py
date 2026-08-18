"""The lexical scorer, tested as a pure function.

`text_search` is a thin shell around this: partition a corpus, score it, drop
the zeros, take the top k. Everything that can be got wrong about *scoring*
lives here, where it needs no store and no event loop.

The numbers in `TestEngineParity` were measured against SurrealDB 3.0.5 and are
the reason this file exists at all. `LEXICAL_SEARCH.md` §4 says exact score
parity between the backends is unachievable — that is true of the *analyzer*
(SurrealDB stems, this does not; see `test_analyze_does_not_stem`), and false
of the arithmetic. Pinning the arithmetic to real measurements is what keeps
"approximately BM25" from drifting into a second, differently-wrong ranking.
"""

import math

import pytest

from epimemer.storage.bm25 import analyze, bm25_scores


class TestAnalyze:
    """The tokenizer both backends have to agree on before anything else can."""

    def test_analyze_splits_an_identifier_into_its_rare_token(self):
        """`LEXICAL_SEARCH.md` §2.3, measured against the engine's analyzer.

        This is the whole mechanism: `4417` survives as a token of its own, and
        a token nothing else in the graph contains is what BM25 can rank on.
        """
        assert analyze("Ticket JIRA-4417 E_TIMEOUT_503") == [
            "ticket", "jira", "-", "4417", "e", "_", "timeout", "_", "503",
        ]

    def test_analyze_groups_runs_of_one_character_class(self):
        """`class` tokenization: a run of punctuation is one token, not many."""
        assert analyze("a%(b .,; c") == ["a", "%(", "b", ".,;", "c"]

    def test_analyze_folds_case_and_accents(self):
        assert analyze("Café NAÏVE") == ["cafe", "naive"]

    def test_analyze_keeps_scripts_that_do_not_fold_to_ascii(self):
        """Folding drops combining marks; it does not delete whole alphabets.

        The engine's `ascii` filter is less forgiving here. Discarding a token
        because it is Cyrillic would make non-Latin content lexically
        unreachable on this backend, which is a worse answer than a small
        disagreement with the engine about a corpus neither ranks well.
        """
        assert analyze("Москва 2026") == ["москва", "2026"]

    def test_analyze_does_not_stem(self):
        """The one deliberate divergence from the engine, pinned so it is a
        decision rather than a surprise.

        SurrealDB runs `snowball(english)`: it analyzes both of these to
        `deploy`. Re-implementing Snowball to agree with it on every edge case
        is not achievable (§4), and a partial stemmer disagrees in *both*
        directions instead of one. So this analyzer stems nothing, and the
        guarantee the backends share is set parity on rare terms — identifiers,
        names, error codes — which no stemmer touches.
        """
        assert analyze("deployment deployments") == ["deployment", "deployments"]


class TestZeroRule:
    """§2.5 / R1: a term more common than half the corpus contributes nothing."""

    def test_bm25_idf_is_zero_for_a_term_in_most_of_the_corpus(self):
        corpus = {
            "a": "shared token alpha",
            "b": "shared token beta",
            "c": "shared token gamma",
            "d": "unrelated entirely",
        }
        # `shared` is in 3 of 4 documents: classic IDF goes negative and clamps.
        assert bm25_scores(corpus, ["shared"]) == {"a": 0.0, "b": 0.0, "c": 0.0}

    def test_bm25_idf_is_zero_at_exactly_half_the_corpus(self):
        """The boundary the formula puts at zero: n = N/2, log(1) = 0.

        Worth its own test because the smoothed IDF that Python BM25 recipes
        overwhelmingly use — `log(1 + (N - n + 0.5) / (n + 0.5))` — is positive
        here, and adopting it by habit would silently delete the floor the
        engine gives us for free.
        """
        corpus = {"a": "half alpha", "b": "half beta", "c": "none", "d": "zero"}
        assert bm25_scores(corpus, ["half"]) == {"a": 0.0, "b": 0.0}

    def test_a_zero_scored_match_is_still_a_match(self):
        """The distinction R1 exists to act on.

        The clamp zeroes the score, not the membership — so the scorer reports
        the row, and dropping it is `text_search`'s job. A scorer that filtered
        here would leave callers no way to tell "matched, but says nothing"
        from "did not match".
        """
        corpus = {"a": "common word", "b": "common thing", "c": "nothing", "d": "x"}
        scores = bm25_scores(corpus, ["common"])
        assert set(scores) == {"a", "b"}
        assert all(score == 0.0 for score in scores.values())


class TestTermSemantics:
    """§2.4: a term is a conjunction of its tokens; terms are ORed together."""

    def test_a_multi_token_term_requires_every_token(self):
        """The behaviour the whole feature is named after: 4417, not 4418."""
        corpus = {
            "a": "Ticket JIRA-4417 was closed after the deployment rollback",
            "b": "Ticket JIRA-4418 remains open pending the deployment review",
            "c": "The deployment pipeline was rewritten last quarter",
            "d": "Unrelated note about gardening",
        }
        assert set(bm25_scores(corpus, ["JIRA-4417"])) == {"a"}

    def test_terms_are_ored_and_an_absent_term_costs_nothing(self):
        """§2.4's trap, at the scorer: one missing term must not zero the rest.

        A single conjunctive match over both terms returns nothing at all —
        which, fused, degrades silently to vector-only.
        """
        corpus = {
            "a": "the deployment was rolled back",
            "b": "the deployment succeeded",
            "c": "gardening notes",
            "d": "lunch plans",
            "e": "weather report",
        }
        both = bm25_scores(corpus, ["deployment", "zzzznotpresent"])
        alone = bm25_scores(corpus, ["deployment"])
        assert set(both) == {"a", "b"}
        assert both == alone

    def test_a_document_matching_two_terms_scores_the_sum(self):
        """Additive *within* a document — which is the only place it holds.

        Across documents it does not: length normalisation means a's score for
        one term is not b's score for the same term. Asserting the cross-document
        version would be asserting that BM25 ignores document length.
        """
        corpus = {
            "a": "rollback and rewrite",
            "b": "rollback only",
            "c": "rewrite only",
            "d": "neither",
            "e": "nothing here",
        }
        both = bm25_scores(corpus, ["rollback", "rewrite"])
        rollback = bm25_scores(corpus, ["rollback"])
        rewrite = bm25_scores(corpus, ["rewrite"])
        assert both["a"] == pytest.approx(rollback["a"] + rewrite["a"], rel=1e-9)

    def test_an_empty_term_list_scores_nothing(self):
        assert bm25_scores({"a": "anything"}, []) == {}

    def test_an_empty_corpus_scores_nothing(self):
        assert bm25_scores({}, ["anything"]) == {}


class TestEngineParity:
    """Scores measured against SurrealDB 3.0.5, reproduced exactly.

    Five documents, `zzqq` in two of them. Both figures come from a live probe
    of the running engine, not from this implementation — so a change to k1, b,
    the IDF form or the document-length term breaks these rather than quietly
    redefining what "the same ranking" means.
    """

    CORPUS = {
        "a": "ZZQQ-4417 deployment closed",
        "b": "ZZQQ-4418 review pending",
        "c": "filler about weather today",
        "d": "filler about traffic today",
        "e": "filler about coffee today",
    }

    def test_single_token_term_matches_the_measured_score(self):
        scores = bm25_scores(self.CORPUS, ["zzqq"])
        assert scores["a"] == pytest.approx(0.3186938, abs=1e-6)
        assert scores["b"] == pytest.approx(0.3186938, abs=1e-6)

    def test_multi_token_term_matches_the_measured_score(self):
        """`ZZQQ-4417` is three tokens — `zzqq`, `-`, `4417` — and the term's
        score is their sum, which is how the engine reports it."""
        scores = bm25_scores(self.CORPUS, ["ZZQQ-4417"])
        assert set(scores) == {"a"}
        assert scores["a"] == pytest.approx(1.6779520, abs=1e-6)

    def test_the_idf_is_the_classic_unsmoothed_form(self):
        """Stated as arithmetic rather than left implicit in a magic constant."""
        n_docs, n_matching = 5, 2
        idf = math.log((n_docs - n_matching + 0.5) / (n_matching + 0.5))
        assert idf == pytest.approx(math.log(1.4), rel=1e-12)
