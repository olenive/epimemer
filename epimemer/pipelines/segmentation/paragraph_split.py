"""Paragraph-based segmentation strategy.

Simple strategy that splits a document on double newlines.
Useful for testing and as a baseline.

Petri net flow:
    [RawDocument] -> split_paragraphs -> [Segment]
"""

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


# --- Transition function ---


async def split_paragraphs(doc: RawDocument) -> list[Segment]:
    """Split a document into segments at double-newline boundaries.

    Produces one Segment per non-empty paragraph, with accurate character
    offsets into the source document.
    """
    segments: list[Segment] = []
    parts = doc.content.split("\n\n")
    offset = 0

    for part in parts:
        if part.strip():
            # Find the start of non-whitespace content within the part
            stripped = part.strip()
            content_start = offset + part.index(stripped)
            content_end = content_start + len(stripped)
            segments.append(
                Segment(
                    source_id=doc.id,
                    text=stripped,
                    span_start=content_start,
                    span_end=content_end,
                )
            )
        offset += len(part) + 2  # +2 for the \n\n separator

    # If no segments were found (e.g., all whitespace), return one segment covering everything
    if not segments and doc.content.strip():
        stripped = doc.content.strip()
        start = doc.content.index(stripped)
        segments.append(
            Segment(
                source_id=doc.id,
                text=stripped,
                span_start=start,
                span_end=start + len(stripped),
            )
        )

    return segments


# --- Petri net factory ---


@petri_net(
    name="paragraph-split-segmentation",
    mode="batch",
    description="Simple paragraph-based segmentation that splits on double newlines.",
)
def paragraph_split_segmentation_net(
    document: RawDocument,
) -> ExecutableGraph:
    """Build a Petri net for paragraph-based segmentation.

    Args:
        document: The document to segment.

    Returns:
        An ExecutableGraph ready to execute.
    """
    return ExecutableGraphOperations.construct_graph([
        # Places
        ListPlaceNode("RawDocument", RawDocument, [document]),
        ListPlaceNode("Segments", Segment),

        # Single transition: split on double newlines
        FunctionTransitionNode("split_paragraphs", split_paragraphs),
        ArgumentEdgeToTransition("RawDocument", "split_paragraphs", "doc"),
        ReturnedEdgeFromTransition("split_paragraphs", "Segments"),
    ])
