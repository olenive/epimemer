import marimo

__generated_with = "0.21.1"
app = marimo.App(width="medium")


@app.cell
def _(mo):
    mo.md("""
    # Orchestration Pipeline

    Step through the top-level orchestration net that routes requests to sub-pipelines. Select an action and watch it route through the Petri net.
    """)
    return


@app.cell
def _():
    import marimo as mo
    from petritype.core.executable_graph_components import ExecutableGraphOperations
    from petritype.core.rustworkx_graph import RustworkxGraph
    from petritype.plotting.simple_graphviz import SimpleGraphvizVisualization
    from epimemer.embeddings.mock import MockEmbeddingProvider
    from epimemer.llm.mock import MockDecompositionProvider
    from epimemer.mcp.config import ServerConfig
    from epimemer.pipelines.orchestration.orchestration_net import MemoryRequest, orchestration_net
    from epimemer.storage.memory import InMemoryStorage

    return (
        ExecutableGraphOperations,
        InMemoryStorage,
        MemoryRequest,
        MockDecompositionProvider,
        MockEmbeddingProvider,
        RustworkxGraph,
        ServerConfig,
        SimpleGraphvizVisualization,
        mo,
        orchestration_net,
    )


@app.cell
def _(mo):
    action = mo.ui.dropdown(
        options=["ingest", "search", "reflect"],
        value="ingest",
        label="Action",
    )
    action
    return (action,)


@app.cell
def _(mo):
    content = mo.ui.text_area(
        value="Neural networks learn from large datasets.",
        label="Content (for ingest) / Query (for search)",
        full_width=True,
    )
    content
    return (content,)


@app.cell
def _(mo):
    step = mo.ui.slider(0, 2, value=0, label="Execution step (route + execute)", full_width=True)
    step
    return (step,)


@app.cell
async def _(
    ExecutableGraphOperations,
    InMemoryStorage,
    MemoryRequest,
    MockDecompositionProvider,
    MockEmbeddingProvider,
    RustworkxGraph,
    ServerConfig,
    SimpleGraphvizVisualization,
    action,
    content,
    mo,
    orchestration_net,
    step,
):
    storage = InMemoryStorage()
    emb = MockEmbeddingProvider(model_id="mock", dimension=8)
    decomp = MockDecompositionProvider()
    config = ServerConfig(storage_backend="memory", embedding_provider="mock", decomposition_provider="mock")

    if action.value == "ingest":
        payload = {"content": content.value}
    elif action.value == "search":
        payload = {"query": content.value, "k": 5}
    else:
        payload = {}

    request = MemoryRequest(action=action.value, payload=payload)
    graph = orchestration_net(request, storage, emb, decomp, config)

    if step.value > 0:
        graph, fired = await ExecutableGraphOperations.execute_graph(graph, max_transitions=step.value)
    else:
        fired = 0

    pydigraph = RustworkxGraph.from_executable_graph(graph)
    diagram = SimpleGraphvizVisualization.graph(pydigraph)

    mo.md(f"**Action:** {action.value} | **Transitions fired:** {fired}")
    return diagram, graph


@app.cell
def _(diagram, mo):
    mo.image(diagram)
    return


@app.cell
def _(graph, mo):
    _mrp = graph.place_named("MemoryResult")
    if _mrp and _mrp.tokens:
        _mr = _mrp.tokens[0]
        _output = mo.md(f"### Result (action: {_mr.action})\n```json\n{_mr.model_dump_json(indent=2)}\n```")
    else:
        _occ = []
        for _pn in ["IngestInput", "SearchInput", "ReflectInput"]:
            _pp = graph.place_named(_pn)
            if _pp and _pp.tokens:
                _occ.append(_pn)
        if _occ:
            _output = mo.md(f"*Routed to: {', '.join(_occ)} — advance slider to execute*")
        else:
            _output = mo.md("*Advance the slider to route the request*")
    _output
    return


if __name__ == "__main__":
    app.run()
