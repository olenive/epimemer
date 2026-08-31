import marimo

__generated_with = "0.21.1"
app = marimo.App(width="medium")


@app.cell
def _(mo):
    mo.md("""
    # Segmentation Pipeline

    Step through the paragraph-split segmentation Petri net. Use the slider to
    control how many transitions fire.
    """)
    return


@app.cell
def _():
    import marimo as mo
    from petritype.core.executable_graph_components import ExecutableGraphOperations
    from petritype.plotting.rustworkx_graph import RustworkxGraph
    from petritype.plotting.simple_graphviz import SimpleGraphvizVisualization

    from epimemer.core.types import RawDocument
    from epimemer.pipelines.segmentation.paragraph_split import paragraph_split_segmentation_net

    return (
        ExecutableGraphOperations,
        RawDocument,
        RustworkxGraph,
        SimpleGraphvizVisualization,
        mo,
        paragraph_split_segmentation_net,
    )


@app.cell
def _(mo):
    test_content = mo.ui.text_area(
        value=(
            "Machine learning models learn from data.\n\n"
            "Neural networks are inspired by the brain.\n\n"
            "Deep learning uses multiple layers."
        ),
        label="Document content",
        full_width=True,
    )
    test_content
    return (test_content,)


@app.cell
def _(mo):
    step = mo.ui.slider(0, 1, value=0, label="Execution step", full_width=True)
    step
    return (step,)


@app.cell
async def _(
    ExecutableGraphOperations,
    RawDocument,
    RustworkxGraph,
    SimpleGraphvizVisualization,
    mo,
    paragraph_split_segmentation_net,
    step,
    test_content,
):
    doc = RawDocument(content=test_content.value)
    graph = paragraph_split_segmentation_net(doc)

    if step.value > 0:
        graph, fired = await ExecutableGraphOperations.execute_graph(
            graph, max_transitions=step.value
        )
    else:
        fired = 0

    pydigraph = RustworkxGraph.from_executable_graph(graph)
    diagram = SimpleGraphvizVisualization.graph(pydigraph)

    mo.md(f"**Transitions fired:** {fired}")
    return diagram, graph


@app.cell
def _(diagram, mo):
    mo.image(diagram)
    return


@app.cell
def _(graph, mo):
    _place = graph.place_named("Segments")
    _tokens = _place.tokens if _place else []
    _lines = [f"### Segments ({len(_tokens)} tokens)"]
    if _tokens:
        for _s in _tokens:
            _lines.append(f"- **id:** `{_s.id[:8]}...`")
            _lines.append(f"  **text:** {_s.text[:100]}")
            _lines.append(f"  **span:** {_s.span_start}:{_s.span_end}")
            _lines.append(f"  **source_id:** `{_s.source_id[:8]}...`")
            _lines.append("")
    else:
        _lines.append("*No segments produced yet — advance the slider*")
    _output = mo.md("\n".join(_lines))
    _output
    return


if __name__ == "__main__":
    app.run()
