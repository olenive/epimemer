import marimo

__generated_with = "0.21.1"
app = marimo.App(width="medium")


@app.cell
def _(mo):
    mo.md("""
    # Foundation — the three node types, the store, and vector search

    The entry point. Everything else in this directory assumes the three things
    below, so this notebook builds them from nothing.

    **A graph of three node types.** A `Topic` is what a body of text is *about*,
    a `Fact` is an atomic claim tied to a source, and an `Inference` is something
    derived from claims and marked provisional. They are separate types because
    they answer to different rules — only a `Fact` carries a `claim_kind` and a
    validity period, and only an `Inference` can have its evidence go stale.

    **A store behind one protocol.** `InMemoryStorage` and `SurrealDBStorage`
    implement the same `StorageBackend`, and every test runs against both. This
    notebook uses the in-memory one, which needs no server.

    **Vector search over embeddings.** Retrieval starts by finding nodes whose
    embedding is near the query's. `MockEmbeddingProvider` is deterministic — the
    same text always gives the same vector — so the numbers below are stable.
    """)
    return


@app.cell
def _():
    import marimo as mo
    import graphviz
    from epimemer.core.types import (
        ClaimKind,
        EdgeType,
        EmbeddingRecord,
        Fact,
        Inference,
        NodeEdge,
        NodeType,
        Topic,
    )
    from epimemer.embeddings.mock import MockEmbeddingProvider
    from epimemer.storage.memory import InMemoryStorage

    return (
        ClaimKind,
        EdgeType,
        EmbeddingRecord,
        Fact,
        Inference,
        InMemoryStorage,
        MockEmbeddingProvider,
        NodeEdge,
        NodeType,
        Topic,
        graphviz,
        mo,
    )


@app.cell
def _(graphviz, mo):
    _types = graphviz.Digraph(graph_attr={"rankdir": "LR", "bgcolor": "transparent"})
    _types.attr("node", shape="box", style="rounded,filled", fillcolor="#eef2ff",
                color="#4338ca", fontname="Helvetica", fontsize="11")
    _types.node("Topic", "Topic\nwhat text is about")
    _types.node("Fact", "Fact\nan atomic claim\nclaim_kind, validity")
    _types.node("Inference", "Inference\nderived, provisional")
    _types.node("Document", "RawDocument\nthe material", fillcolor="#f1f5f9", color="#64748b")

    _types.attr("edge", fontname="Helvetica", fontsize="9", color="#475569")
    _types.edge("Fact", "Document", label="sourced_from")
    _types.edge("Inference", "Document", label="sourced_from")
    _types.edge("Topic", "Document", label="sourced_from")
    _types.edge("Fact", "Topic", label="about")
    _types.edge("Inference", "Fact", label="derived_from")
    _types.edge("Topic", "Topic", label="subtopic_of")

    type_diagram = _types.pipe(format="png")
    mo.md("### The type model\nEdges are one enum, `EdgeType`; behaviour is finite and the vocabulary of user relations is open.")
    return (type_diagram,)


@app.cell
def _(mo, type_diagram):
    mo.image(type_diagram)
    return


@app.cell
def _(mo):
    query = mo.ui.text_area(
        value="Ada Lovelace and the analytical engine",
        label="Query the store",
        full_width=True,
    )
    query
    return (query,)


@app.cell
def _(mo):
    k = mo.ui.slider(1, 6, value=3, label="How many results (k)", full_width=True)
    k
    return (k,)


@app.cell
async def _(
    ClaimKind,
    EdgeType,
    EmbeddingRecord,
    Fact,
    Inference,
    InMemoryStorage,
    MockEmbeddingProvider,
    NodeEdge,
    Topic,
    mo,
):
    store = InMemoryStorage()
    embedder = MockEmbeddingProvider(model_id="mock-embed", dimension=16)

    _topic = Topic(content="Early computing", source_id="doc-1")
    _facts = [
        Fact(content="Ada Lovelace wrote the first published algorithm.",
             source_id="doc-1", claim_kind=ClaimKind.EVENT),
        Fact(content="The analytical engine was never completed.",
             source_id="doc-1", claim_kind=ClaimKind.STATE),
        Fact(content="Charles Babbage designed the analytical engine.",
             source_id="doc-1", claim_kind=ClaimKind.EVENT),
    ]
    _inference = Inference(
        content="Lovelace's algorithm was written for a machine that never ran.",
        source_id="doc-1",
    )

    _nodes = [_topic, *_facts, _inference]
    for _node in _nodes:
        await store.store_node(_node)
        _vector = (await embedder.embed([_node.content]))[0]
        await store.store_embedding(EmbeddingRecord(
            item_id=_node.id, model_id=embedder.model_id, vector=_vector,
        ))

    # The two edges that make this a graph rather than a list.
    for _fact in _facts:
        await store.store_edge(NodeEdge(
            src_id=_fact.id, dst_id=_topic.id, type=EdgeType.ABOUT,
        ))
    for _fact in _facts[:2]:
        await store.store_edge(NodeEdge(
            src_id=_inference.id, dst_id=_fact.id, type=EdgeType.DERIVED_FROM,
        ))

    mo.md(f"**Stored:** {len(_nodes)} nodes — 1 topic, {len(_facts)} facts, 1 inference.")
    return embedder, store


@app.cell
async def _(embedder, k, mo, query, store):
    _query_vector = (await embedder.embed([query.value]))[0]
    _hits = await store.vector_search(
        _query_vector, model_id=embedder.model_id, k=k.value
    )

    _lines = [f"### Vector search — top {k.value}", ""]
    if _hits:
        # `vector_search` returns (node_id, score): the seed is an id, and the
        # node is fetched only for the ids that survive ranking. The node's
        # *type* is its Python class — there is no discriminator field, which
        # is why `query_nodes` takes a `NodeType` and a node does not carry one.
        _lines.append("| score | type | content |")
        _lines.append("|---|---|---|")
        for _node_id, _score in _hits:
            _node = await store.get_node(_node_id)
            _lines.append(f"| {_score:.4f} | {type(_node).__name__} | {_node.content} |")
        _lines.append("")
        _lines.append(
            "*Scores come from a deterministic mock embedder, so they show the "
            "mechanism rather than real semantic distance. Swap in a real "
            "provider and only the numbers change.*"
        )
    else:
        _lines.append("*Nothing stored yet.*")
    mo.md("\n".join(_lines))
    return


@app.cell
async def _(NodeType, mo, store):
    _by_type = {}
    for _t in (NodeType.TOPIC, NodeType.FACT, NodeType.INFERENCE):
        _by_type[_t.value] = len(await store.query_nodes(node_type=_t))
    _edges = await store.get_edges_for(
        [_n.id for _n in await store.query_nodes()], direction="from"
    )
    _edge_count = sum(len(_e) for _e in _edges.values())

    mo.md(
        "### What is in the store\n\n"
        + "\n".join(f"- **{_name}**: {_count}" for _name, _count in _by_type.items())
        + f"\n- **edges**: {_edge_count}\n\n"
        "Both backends answer all of this identically — that parity is asserted "
        "by a fixture that runs every storage test twice."
    )
    return


if __name__ == "__main__":
    app.run()
