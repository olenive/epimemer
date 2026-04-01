import marimo

__generated_with = "0.21.1"
app = marimo.App(width="medium")


@app.cell
def _(mo):
    mo.md("""
    # Decomposition Pipeline

    Step through the LLM decomposition Petri net. Extracts Topics, Facts, and Inferences from a Segment using mock providers.
    """)
    return


@app.cell
def _():
    import marimo as mo
    from petritype.core.executable_graph_components import ExecutableGraphOperations
    from petritype.core.rustworkx_graph import RustworkxGraph
    from petritype.plotting.simple_graphviz import SimpleGraphvizVisualization
    from epimemer.core.types import Segment
    from epimemer.llm.mock import MockDecompositionProvider
    from epimemer.pipelines.decomposition.llm_decomposition import llm_decomposition_net

    return (
        ExecutableGraphOperations,
        MockDecompositionProvider,
        RustworkxGraph,
        Segment,
        SimpleGraphvizVisualization,
        llm_decomposition_net,
        mo,
    )


@app.cell
def _(mo):
    segment_text = mo.ui.text_area(
        value="Machine learning models require large datasets for training. Neural networks use gradient descent to optimize parameters.",
        label="Segment text",
        full_width=True,
    )
    segment_text
    return (segment_text,)


@app.cell
def _(mo):
    step = mo.ui.slider(0, 1, value=0, label="Execution step", full_width=True)
    step
    return (step,)


@app.cell
async def _(
    ExecutableGraphOperations,
    MockDecompositionProvider,
    RustworkxGraph,
    Segment,
    SimpleGraphvizVisualization,
    llm_decomposition_net,
    mo,
    segment_text,
    step,
):
    seg = Segment(source_id="demo", text=segment_text.value, span_start=0, span_end=len(segment_text.value))
    provider = MockDecompositionProvider()
    graph = llm_decomposition_net(seg, provider)

    if step.value > 0:
        graph, fired = await ExecutableGraphOperations.execute_graph(graph, max_transitions=step.value)
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
    _summary = []
    for _pn in ["Topics", "Facts", "Inferences"]:
        _pl = graph.place_named(_pn)
        if _pl and _pl.tokens:
            _summary.append(f"### {_pn}")
            for _tk in _pl.tokens:
                _summary.append(f"- {_tk.content}")
    if _summary:
        _output = mo.md("\n".join(_summary))
    else:
        _output = mo.md("*No nodes extracted yet — advance the slider*")
    _output
    return


@app.cell
def _(graph, mo):
    # Print the tokens in each of the final cells.
    _lines = []
    for _name in ["Topics", "Facts", "Inferences"]:
        _p = graph.place_named(_name)
        _tks = _p.tokens if _p else []
        _lines.append(f"### {_name} ({len(_tks)} tokens)")
        if _tks:
            for _t in _tks:
                _lines.append(f"- **id:** `{_t.id[:8]}...`")
                _lines.append(f"  **content:** {_t.content}")
                _lines.append(f"  **source_id:** `{_t.source_id[:8]}...`")
                _lines.append(f"  **extraction_method:** {_t.extraction_method}")
                _lines.append("")
        else:
            _lines.append("*empty*\n")
    _output2 = mo.md("\n".join(_lines))
    _output2
    return


if __name__ == "__main__":
    app.run()
