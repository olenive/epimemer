import marimo

__generated_with = "0.21.1"
app = marimo.App(width="medium")


@app.cell
def _(mo):
    mo.md("""
    # Graph Construction Pipeline

    Step through edge creation from a decomposed segment. Shows how typed edges are produced between Topics, Facts, and Inferences.
    """)
    return


@app.cell
def _():
    import marimo as mo
    from petritype.core.executable_graph_components import ExecutableGraphOperations
    from petritype.plotting.rustworkx_graph import RustworkxGraph
    from petritype.plotting.simple_graphviz import SimpleGraphvizVisualization
    from epimemer.core.types import Fact, Inference, Segment, Topic
    from epimemer.pipelines.graph_construction.edge_creation import DecomposedSegment, edge_creation_net

    return (
        DecomposedSegment,
        ExecutableGraphOperations,
        Fact,
        Inference,
        RustworkxGraph,
        Segment,
        SimpleGraphvizVisualization,
        Topic,
        edge_creation_net,
        mo,
    )


@app.cell
def _(mo):
    step = mo.ui.slider(0, 1, value=0, label="Execution step", full_width=True)
    step
    return (step,)


@app.cell
async def _(
    DecomposedSegment,
    ExecutableGraphOperations,
    Fact,
    Inference,
    RustworkxGraph,
    Segment,
    SimpleGraphvizVisualization,
    Topic,
    edge_creation_net,
    mo,
    step,
):
    seg = Segment(source_id="demo", text="AI uses neural networks.", span_start=0, span_end=24)
    decomposed = DecomposedSegment(
        segment=seg,
        topics=[Topic(content="Artificial intelligence", source_id=seg.id)],
        facts=[Fact(content="AI uses neural networks", source_id=seg.id)],
        inferences=[Inference(content="AI will continue advancing", source_id=seg.id)],
    )

    graph = edge_creation_net(decomposed)
    if step.value > 0:
        graph, fired = await ExecutableGraphOperations.execute_graph(graph, max_transitions=step.value)
    else:
        fired = 0

    pydigraph = RustworkxGraph.from_executable_graph(graph)
    diagram = SimpleGraphvizVisualization.graph(pydigraph)

    mo.md(f"**Transitions fired:** {fired}")
    return decomposed, diagram, graph


@app.cell
def _(diagram, mo):
    mo.image(diagram)
    return


@app.cell
def _(graph, mo):
    _place = graph.place_named("Edges")
    _tokens = _place.tokens if _place else []
    _lines = [f"### Edges ({len(_tokens)} tokens)"]
    if _tokens:
        for _e in _tokens:
            _lines.append(f"- **id:** `{_e.id[:8]}...`")
            _lines.append(f"  **type:** {_e.type.value}")
            _lines.append(f"  **src:** `{_e.src_id[:8]}...` **dst:** `{_e.dst_id[:8]}...`")
            _lines.append(f"  **weight:** {_e.weight}")
            _lines.append("")
    else:
        _lines.append("*No edges created yet — advance the slider*")
    _output = mo.md("\n".join(_lines))
    _output
    return


@app.cell
def _(decomposed, graph, mo):
    import graphviz

    _edges_place = graph.place_named("Edges")

    if not (_edges_place and _edges_place.tokens):
        kg_output = mo.md("*Advance the slider to see the knowledge graph*")
    else:
        _edges = _edges_place.tokens

        _nodes = {}
        _seg = decomposed.segment
        _nodes[_seg.id] = ("Segment", _seg.text[:40])
        for _t in decomposed.topics:
            _nodes[_t.id] = ("Topic", _t.content[:40])
        for _f in decomposed.facts:
            _nodes[_f.id] = ("Fact", _f.content[:40])
        for _i in decomposed.inferences:
            _nodes[_i.id] = ("Inference", _i.content[:40])

        _type_colors = {
            "Segment": "#4a90d9",
            "Topic": "#d4a017",
            "Fact": "#50c878",
            "Inference": "#c77dff",
        }

        _dot = graphviz.Digraph("knowledge_graph", format="png")
        _dot.attr(bgcolor="#1e1e2e", rankdir="TB")
        _dot.attr("node", style="filled", fontcolor="white", fontsize="11")
        _dot.attr("edge", fontcolor="#aaaaaa", fontsize="9", color="#666666")

        for _nid, (_ntype, _label) in _nodes.items():
            _dot.node(
                _nid,
                f"{_ntype}\n{_label}",
                fillcolor=_type_colors.get(_ntype, "#555555"),
                shape="box" if _ntype == "Segment" else "ellipse",
            )

        for _e in _edges:
            if _e.src_id in _nodes and _e.dst_id in _nodes:
                _dot.edge(_e.src_id, _e.dst_id, label=_e.type.value)

        kg_output = mo.vstack([
            mo.md("### Knowledge Graph"),
            mo.image(_dot.pipe(format="png")),
        ])

    kg_output
    return


@app.cell
def _():
    return


if __name__ == "__main__":
    app.run()
