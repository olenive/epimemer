import marimo

__generated_with = "0.21.1"
app = marimo.App(width="medium")


@app.cell
def _(mo):
    mo.md("""
    # Query Pipeline

    Step through hybrid retrieval: vector search → graph expansion → result assembly. Pre-populates a small graph with nodes and embeddings.
    """)
    return


@app.cell
def _():
    import marimo as mo
    from petritype.core.executable_graph_components import ExecutableGraphOperations
    from petritype.plotting.rustworkx_graph import RustworkxGraph
    from petritype.plotting.simple_graphviz import SimpleGraphvizVisualization
    from epimemer.core.types import EdgeType, EmbeddingRecord, Fact, Inference, NodeEdge, Topic
    from epimemer.embeddings.mock import MockEmbeddingProvider
    from epimemer.pipelines.query.hybrid_retrieval import hybrid_retrieval_net
    from epimemer.pipelines.query.types import QueryRequest
    from epimemer.storage.memory import InMemoryStorage

    return (
        EdgeType,
        EmbeddingRecord,
        ExecutableGraphOperations,
        Fact,
        InMemoryStorage,
        Inference,
        MockEmbeddingProvider,
        NodeEdge,
        QueryRequest,
        RustworkxGraph,
        SimpleGraphvizVisualization,
        Topic,
        hybrid_retrieval_net,
        mo,
    )


@app.cell
async def _(
    EdgeType,
    EmbeddingRecord,
    Fact,
    InMemoryStorage,
    Inference,
    MockEmbeddingProvider,
    NodeEdge,
    Topic,
):
    # Build a small graph with embeddings
    storage = InMemoryStorage()
    emb_provider = MockEmbeddingProvider(model_id="mock", dimension=8)

    topic = Topic(content="Machine learning and neural networks", source_id="seg-1")
    fact = Fact(content="Neural networks require training data", source_id="seg-1")
    inference = Inference(content="ML will improve with more data", source_id="seg-1")

    for node in [topic, fact, inference]:
        await storage.store_node(node)
        vecs = await emb_provider.embed([node.content])
        await storage.store_embedding(EmbeddingRecord(item_id=node.id, model_id="mock", vector=vecs[0]))

    await storage.store_edge(NodeEdge(src_id=fact.id, dst_id=topic.id, type=EdgeType.SUPPORTS))
    await storage.store_edge(NodeEdge(src_id=inference.id, dst_id=topic.id, type=EdgeType.ABSTRACTS))
    return emb_provider, storage


@app.cell
def _(mo):
    query_text = mo.ui.text(value="Neural networks require training data", label="Search query", full_width=True)
    query_text
    return (query_text,)


@app.cell
def _(mo):
    step = mo.ui.slider(0, 3, value=0, label="Execution step (3 transitions total)", full_width=True)
    step
    return (step,)


@app.cell
async def _(
    ExecutableGraphOperations,
    QueryRequest,
    RustworkxGraph,
    SimpleGraphvizVisualization,
    emb_provider,
    hybrid_retrieval_net,
    mo,
    query_text,
    step,
    storage,
):
    request = QueryRequest(query_text=query_text.value, k=5, graph_hops=1, model_id="mock")
    graph = hybrid_retrieval_net(request, emb_provider, storage)

    if step.value > 0:
        graph, fired = await ExecutableGraphOperations.execute_graph(graph, max_transitions=step.value)
    else:
        fired = 0

    pydigraph = RustworkxGraph.from_executable_graph(graph)
    diagram = SimpleGraphvizVisualization.graph(pydigraph)

    mo.md(f"**Transitions fired:** {fired} / 3")
    return diagram, graph


@app.cell
def _(diagram, mo):
    mo.image(diagram)
    return


@app.cell
def _(graph, mo):
    _rp = graph.place_named("QueryResult")
    if _rp and _rp.tokens:
        _qr = _rp.tokens[0]
        _lines = [
            f"### Query Result",
            f"- **Nodes returned:** {len(_qr.nodes)}",
            f"- **Edges returned:** {len(_qr.edges)}",
            f"- **Source types:** {_qr.metadata.source_types}",
            f"- **Vector search time:** {_qr.metadata.vector_search_time_ms:.2f}ms",
            f"- **Graph expansion time:** {_qr.metadata.graph_expansion_time_ms:.2f}ms",
            "",
            "#### Nodes",
        ]
        for _n in _qr.nodes:
            _lines.append(f"- **{type(_n).__name__}** `{_n.id[:8]}...`: {_n.content}")
        _lines.append("")
        _lines.append("#### Edges")
        for _e in _qr.edges:
            _lines.append(f"- `{_e.src_id[:8]}...` **{_e.type.value}** `{_e.dst_id[:8]}...`")
        _output = mo.md("\n".join(_lines))
    else:
        _output = mo.md("*No results yet — advance the slider to step 3*")
    _output
    return


if __name__ == "__main__":
    app.run()
