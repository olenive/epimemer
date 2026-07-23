"""Semantic similarity segmentation strategy (TextTiling-style).

Splits a document into segments by detecting drops in embedding similarity
between adjacent sentences. No LLM calls — pure embedding + computation.

Petri net flow:
    [RawDocument] -> split_sentences -> [SentenceList]
    [SentenceList] -> compute_similarities -> [SimilarityScores]
    [SimilarityScores] -> detect_boundaries_and_form_segments -> [Segment]
"""

import re

from pydantic import BaseModel, Field

from petritype import petri_net
from petritype.core.executable_graph_components import (
    ArgumentEdgeToTransition,
    ExecutableGraph,
    ExecutableGraphOperations,
    FunctionTransitionNode,
    ListPlaceNode,
    ReturnedEdgeFromTransition,
)

from epimemer.core.types import RawDocument, Segment
from epimemer.embeddings.protocol import EmbeddingProvider


# --- Intermediate token types ---


class SentenceSpan(BaseModel):
    """A single sentence with its character offsets in the source document."""
    text: str
    start: int
    end: int


class SentenceList(BaseModel):
    """List of sentences extracted from a document, preserving span information."""
    source_id: str
    source_content: str
    sentences: list[SentenceSpan] = Field(default_factory=list)


class SimilarityScores(BaseModel):
    """Cosine similarity scores between adjacent sentences."""
    source_id: str
    source_content: str
    scores: list[float] = Field(default_factory=list)
    sentences: list[SentenceSpan] = Field(default_factory=list)


# --- Transition functions ---


def _split_into_sentences(text: str) -> list[tuple[str, int, int]]:
    """Split text into sentences, returning (text, start, end) tuples.

    Uses a regex-based sentence boundary detector. Splits on sentence-ending
    punctuation followed by whitespace, preserving character offsets.
    """
    # Match sentence-ending punctuation followed by whitespace
    pattern = r'(?<=[.!?])\s+'
    parts = re.split(pattern, text)

    sentences = []
    offset = 0
    for part in parts:
        part_stripped = part.strip()
        if not part_stripped:
            offset += len(part) + 1
            continue
        start = text.find(part_stripped, offset)
        if start == -1:
            start = offset
        end = start + len(part_stripped)
        sentences.append((part_stripped, start, end))
        offset = end

    return sentences


async def split_sentences(doc: RawDocument) -> SentenceList:
    """Split a RawDocument into sentences with character offsets."""
    raw_sentences = _split_into_sentences(doc.content)
    spans = [
        SentenceSpan(text=text, start=start, end=end)
        for text, start, end in raw_sentences
    ]
    return SentenceList(
        source_id=doc.id,
        source_content=doc.content,
        sentences=spans,
    )


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """Compute cosine similarity between two vectors."""
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(x * x for x in b) ** 0.5
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


async def compute_similarities(
    sentence_list: SentenceList,
    embedding_provider: EmbeddingProvider,
) -> SimilarityScores:
    """Compute cosine similarity between adjacent sentence embeddings."""
    sentences = sentence_list.sentences
    if len(sentences) <= 1:
        return SimilarityScores(
            source_id=sentence_list.source_id,
            source_content=sentence_list.source_content,
            scores=[],
            sentences=sentences,
        )

    texts = [s.text for s in sentences]
    vectors = await embedding_provider.embed(texts)

    scores = []
    for i in range(len(vectors) - 1):
        sim = _cosine_similarity(vectors[i], vectors[i + 1])
        scores.append(sim)

    return SimilarityScores(
        source_id=sentence_list.source_id,
        source_content=sentence_list.source_content,
        scores=scores,
        sentences=sentences,
    )


def _detect_boundaries(scores: list[float], threshold_factor: float = 1.0) -> list[int]:
    """Detect boundary indices where similarity drops below mean - threshold_factor * std.

    Returns indices into the scores list. A boundary at index i means
    there is a topic shift between sentence i and sentence i+1.
    """
    if not scores:
        return []

    mean = sum(scores) / len(scores)
    variance = sum((s - mean) ** 2 for s in scores) / len(scores)
    std = variance ** 0.5

    threshold = mean - threshold_factor * std
    boundaries = [i for i, s in enumerate(scores) if s < threshold]
    return boundaries


async def detect_boundaries_and_form_segments(
    similarity_scores: SimilarityScores,
    threshold_factor: float = 1.0,
) -> list[Segment]:
    """Detect topic boundaries and form segments from similarity scores.

    Each segment spans from one boundary to the next, covering the full
    text of the source document without overlap.
    """
    sentences = similarity_scores.sentences
    source_content = similarity_scores.source_content
    source_id = similarity_scores.source_id

    if not sentences:
        return []

    if len(sentences) == 1:
        return [
            Segment(
                source_id=source_id,
                text=sentences[0].text,
                span_start=sentences[0].start,
                span_end=sentences[0].end,
            )
        ]

    boundaries = _detect_boundaries(similarity_scores.scores, threshold_factor)

    # Build segments from boundary indices
    segments = []
    segment_starts = [0] + [b + 1 for b in boundaries]
    segment_ends = [b + 1 for b in boundaries] + [len(sentences)]

    for start_idx, end_idx in zip(segment_starts, segment_ends):
        if start_idx >= end_idx:
            continue
        span_sentences = sentences[start_idx:end_idx]
        span_start = span_sentences[0].start
        span_end = span_sentences[-1].end
        text = source_content[span_start:span_end]
        segments.append(
            Segment(
                source_id=source_id,
                text=text,
                span_start=span_start,
                span_end=span_end,
            )
        )

    return segments


# --- Petri net factory ---


@petri_net(
    name="semantic-similarity-segmentation",
    mode="batch",
    description="TextTiling-style segmentation using embedding similarity between adjacent sentences.",
)
def semantic_similarity_segmentation_net(
    document: RawDocument,
    embedding_provider: EmbeddingProvider,
    threshold_factor: float = 1.0,
) -> ExecutableGraph:
    """Build a Petri net for semantic similarity segmentation.

    Args:
        document: The document to segment.
        embedding_provider: Provider for computing sentence embeddings.
        threshold_factor: Controls boundary sensitivity. Higher = fewer boundaries.

    Returns:
        An ExecutableGraph ready to execute.
    """
    return ExecutableGraphOperations.construct_graph([
        # Places
        ListPlaceNode("RawDocument", RawDocument, [document]),
        ListPlaceNode("SentenceList", SentenceList),
        ListPlaceNode("SimilarityScores", SimilarityScores),
        ListPlaceNode("Segments", Segment),

        # Transition 1: split_sentences
        FunctionTransitionNode("split_sentences", split_sentences),
        ArgumentEdgeToTransition("RawDocument", "split_sentences", "doc"),
        ReturnedEdgeFromTransition("split_sentences", "SentenceList"),

        # Transition 2: compute_similarities
        FunctionTransitionNode(
            "compute_similarities",
            compute_similarities,
            kwargs={"embedding_provider": embedding_provider},
        ),
        ArgumentEdgeToTransition("SentenceList", "compute_similarities", "sentence_list"),
        ReturnedEdgeFromTransition("compute_similarities", "SimilarityScores"),

        # Transition 3: detect_boundaries_and_form_segments
        FunctionTransitionNode(
            "detect_boundaries_and_form_segments",
            detect_boundaries_and_form_segments,
            kwargs={"threshold_factor": threshold_factor},
        ),
        ArgumentEdgeToTransition(
            "SimilarityScores", "detect_boundaries_and_form_segments", "similarity_scores"
        ),
        ReturnedEdgeFromTransition("detect_boundaries_and_form_segments", "Segments"),
    ], expect_acyclic=True)
